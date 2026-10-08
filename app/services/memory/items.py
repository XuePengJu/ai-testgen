"""条目级记忆写入核心（V7.0：事实入库 / 冲突裁决 / 版本链 / 审计；
V7.1：手动编辑 / 采纳 / 恢复 / 软删；V7.2：active 条目向量索引钩子）。

唯一写入口：所有 memory_items 行变更都必须经过本模块——api 层只做鉴权
与参数校验，jobs 层（chat_memory.py）只做编排，绝不在此之外直写表。

冲突裁决规则（需求本体，语义勿改）：
- 同 subject 且内容 Jaccard ≥ SAME_THRESHOLD(0.85) → **刷新**：不新增版本，
  confidence 取 max（防版本链膨胀），溯源更新到最新出现位置
- new.conf ≥ old.conf - CONF_EPS(0.05) → **取代**：old→superseded
  （dedupe_key=NULL 让出 active 位、superseded_by=new.id）；new→active、
  version+1、挂 prev_id、root_id 沿袭链头
- new.conf < old.conf - CONF_EPS(0.05) → **挂起**：new→conflict、
  conflict_with=old.id、dedupe_key=NULL；old 保持 active 且 content 不变
- old.source='user' → old 置信度**虚拟 +USER_BOOST(0.2)** 再比（手动记忆更难被覆盖）
- new.source='user'（用户手动写入）→ **无条件取代**（不看置信度）

置信度分档硬夹（Python 侧执行，不信任 LLM 自报）：
- 用户显式声明（prov=explicit）≤0.95 / 明确断言（prov=assert）≤0.90 /
  assistant 结论（prov=assistant）≤0.70 / LLM 推断（prov=infer）≤0.70
- clamp 后 < AITF_MEMORY_MIN_CONFIDENCE(0.5) 的条目直接丢弃不入表

V7.1 起 importance/expires_at/half_life_days 的唯一来源是 forget.py 的
initial_importance / expiry_for / kind_half_life（本模块只 import 不复制，
杜绝双轨）。
V7.2 起「成为 active」的条目在 commit 后统一调 index_memory_vectors 补向量
（mock embedding 内部自动跳过；钩子只在 commit 之后，flush 前调用会索引到
未落库的脏数据）。
"""
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    AITF_MEMORY_CONF_EPS,
    AITF_MEMORY_MAX_PER_CONV,
    AITF_MEMORY_MIN_CONFIDENCE,
    AITF_MEMORY_NEAR_DUP_THRESHOLD,
    AITF_MEMORY_SAME_THRESHOLD,
    AITF_MEMORY_USER_BOOST,
)
from app.core.utils import utcnow
from app.models.memory import MemoryAudit, MemoryItem
from app.services.memory.forget import expiry_for, initial_importance, kind_half_life

logger = logging.getLogger("memory.items")

# 合法枚举（越界条目在 _upsert_item 侧丢弃，不整批失败）
_KINDS = {"fact", "preference", "rule", "todo", "profile"}
# 置信度分档硬夹：按事实来源 prov（LLM 输出字段）封顶
_PROV_CAPS = {"explicit": 0.95, "assert": 0.90, "assistant": 0.70, "infer": 0.70}
# near-dup 扫描上限（单用户 active 条目全扫在大库下太贵，取最近更新的一批）
_NEAR_DUP_SCAN_LIMIT = 200
# 已知主题词表上限（塞进抽取 prompt 防 subject 漂移）
_KNOWN_SUBJECTS_LIMIT = 40


def _index_active_items(items: list[MemoryItem]) -> None:
    """commit 后把成为 active 的条目写入向量库（V7.2 钩子；失败只告警）。

    必须在调用方 commit 之后调用：向量索引读的是内存对象，但只有行数据
    落库后向量才是可信副本；任何异常都不影响已提交的行数据。
    """
    if not items:
        return
    try:
        from app.services.knowledge.vectorstore import index_memory_vectors
        n = index_memory_vectors(items)
        if n:
            logger.info("已索引 %d 条记忆条目向量", n)
    except Exception as e:  # noqa: BLE001  向量是旁路，失败不回滚行数据
        logger.warning("记忆条目向量索引失败：%s", e)


# ============ 基础工具（分词 / 相似度 / 归一化） ============

def _tokens(text: str) -> set[str]:
    """轻量分词：CJK 单字 + 英文/数字整词（小写）。供 Jaccard 相似度用。

    刻意不引 jieba（未安装）：subject/content 都是短句，单字集 + 英文整词
    的 Jaccard 已足够区分「语义等价 / 近重复 / 不同主题」三档。
    """
    out: set[str] = set()
    buf: list[str] = []
    for ch in (text or "").lower():
        if "\u4e00" <= ch <= "\u9fff":  # CJK 单字
            if buf:
                out.add("".join(buf))
                buf = []
            out.add(ch)
        elif ch.isalnum():  # 英文/数字整词
            buf.append(ch)
        else:
            if buf:
                out.add("".join(buf))
                buf = []
    if buf:
        out.add("".join(buf))
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度；任一侧为空返回 0（空集不参与相似判定）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _clamp01(v) -> float:
    """数值安全夹到 [0, 1]（非数/NaN 兜底 0.0）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return min(1.0, max(0.0, f))


def _dedupe_key(user_id: int, subject: str) -> str:
    """语义槽位键：md5 hex(32)（user_id + 归一化 subject）。

    归一化只做 strip + lower + 去空白——同义改写交给 near-dup 扫描兜底，
    键本身保持稳定可预测。
    """
    import hashlib
    norm = "".join((subject or "").lower().split())
    return hashlib.md5(f"{user_id}:{norm}".encode("utf-8")).hexdigest()


# ============ 快照与审计 ============

def _snap(item: MemoryItem) -> dict:
    """条目关键字的平铺快照（审计 before/after 用；content 截 200 字防膨胀）。"""
    return {
        "id": item.id,
        "kind": item.kind,
        "subject": item.subject,
        "status": item.status,
        "confidence": round(float(item.confidence or 0.0), 4),
        "importance": round(float(item.importance or 0.0), 4),
        "version": item.version,
        "source": item.source,
        "dedupe_key": item.dedupe_key,
        "content": (item.content or "")[:200],
    }


def _audit(db: Session, item: MemoryItem, action: str, actor: str,
           before: dict | None, reason: str) -> None:
    """落一条变更审计（append-only；随调用方事务一起提交，不单独 commit）。

    after 快照取 _audit 调用时刻的对象状态——调用方必须先把字段改完再审计。
    """
    db.add(MemoryAudit(
        user_id=item.user_id,
        item_id=item.id,
        action=action,
        actor=actor,
        before_json=json.dumps(before, ensure_ascii=False) if before else None,
        after_json=json.dumps(_snap(item), ensure_ascii=False),
        reason=(reason or "")[:500] or None,
    ))


# ============ 只读辅助（jobs 编排 / prompt 防漂移用） ============

def list_known_subjects(db: Session, user_id: int,
                        limit: int = _KNOWN_SUBJECTS_LIMIT) -> list[str]:
    """该用户 active 条目的 subject 词表（塞进抽取 prompt 防 subject 漂移）。"""
    rows = db.execute(
        select(MemoryItem.subject).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
        ).order_by(MemoryItem.updated_at.desc()).limit(limit)
    ).scalars().all()
    return [s for s in rows if s]


# ============ 主入口：会话条目同步 ============

def sync_conversation_items(db: Session, conv, facts: list[dict],
                            msgs: list) -> dict:
    """把一次抽取得到的事实条目写入 memory_items（含冲突裁决与审计）。

    - 事务边界：整批一个小事务（内部逐条 flush、最后统一 commit）；
      中途异常由调用方（chat_memory）rollback，条目变更整体原子回滚
    - 上限：单会话最多入库 MAX_PER_CONV 条（防 LLM 失控输出刷爆表）
    - commit 后对本批成为 active 的条目补向量（V7.2 钩子）
    - 返回 {"received", "created", "refreshed", "superseded", "conflict", "dropped"}
    """
    summary = {"received": len(facts), "created": 0, "refreshed": 0,
               "superseded": 0, "conflict": 0, "dropped": 0}
    to_index: list[MemoryItem] = []
    taken = 0
    for f in facts:
        if not isinstance(f, dict):
            summary["dropped"] += 1
            continue
        if taken >= AITF_MEMORY_MAX_PER_CONV:
            summary["dropped"] += 1
            continue
        action, item = _upsert_item(db, conv.user_id, conv.id, f, msgs)
        if action == "dropped":
            summary["dropped"] += 1
            continue
        summary[action] = summary.get(action, 0) + 1
        taken += 1
        if item is not None and action in ("created", "superseded"):
            to_index.append(item)  # 刷新不换内容，无需重建向量
    db.commit()
    _index_active_items(to_index)
    if taken or summary["dropped"]:
        logger.info("会话 %s 条目同步完成：%s", conv.id, summary)
    return summary


def _upsert_item(db: Session, user_id: int, conversation_id: str | None,
                 fact: dict, msgs: list,
                 source: str = "conversation") -> tuple[str, MemoryItem | None]:
    """单条事实入库（校验 / 去重 / 冲突裁决），返回 (动作名, 涉及的 active 条目)。

    动作 ∈ {created, refreshed, superseded, conflict, dropped}；item 为本动作
    后处于 active 位的新条目（created/superseded 的新行、refreshed 的旧行；
    conflict/dropped 为 None）。本函数只 flush 不 commit。
    V7.1 起签名改为 user_id + conversation_id（手动编辑路径无 conv 对象）。
    """
    # ---- 1) 逐条校验与硬夹（丢坏条目而非整批失败）----
    kind = str(fact.get("kind") or "fact").strip().lower()
    if kind not in _KINDS:
        return "dropped", None
    subject = str(fact.get("subject") or "").strip()[:120]
    content = str(fact.get("content") or "").strip()[:200]
    if not subject or not content:
        return "dropped", None
    prov = fact.get("prov") if fact.get("prov") in _PROV_CAPS else "infer"
    conf = min(_clamp01(fact.get("confidence")), _PROV_CAPS[prov])
    if conf < AITF_MEMORY_MIN_CONFIDENCE:
        return "dropped", None  # 低置信噪声不入表

    # ---- 2) 消息溯源（msg_ref 越界/非法 → 置 0）----
    msg_ref = fact.get("msg_ref")
    if not isinstance(msg_ref, int) or msg_ref < 0 or msg_ref >= len(msgs):
        msg_ref = 0
    msg = msgs[msg_ref] if msgs else None
    msg_id = msg.id if msg is not None else None
    evidence = ((msg.content or "").strip()[:200]) if msg is not None else ""

    # ---- 3) 找同语义槽位的 active 条目（精确 key → near-dup 扫描兜底）----
    key = _dedupe_key(user_id, subject)
    old = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
            MemoryItem.dedupe_key == key,
        )
    ).scalars().first()
    if old is None:
        # subject 措辞漂移兜底：同 kind 下 Jaccard ≥ NEAR_DUP_THRESHOLD 视为同槽位
        cands = db.execute(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.status == "active",
                MemoryItem.kind == kind,
            ).order_by(MemoryItem.updated_at.desc()).limit(_NEAR_DUP_SCAN_LIMIT)
        ).scalars().all()
        sub_toks = _tokens(subject)
        for cand in cands:
            if _jaccard(_tokens(cand.subject), sub_toks) >= AITF_MEMORY_NEAR_DUP_THRESHOLD:
                old = cand
                break

    # ---- 4) 分支：新建 / 刷新 / 冲突裁决 ----
    if old is None:
        return _create_active(db, user_id, conversation_id, kind, subject,
                              content, key, conf, prov, source, msg_id, evidence)
    if _jaccard(_tokens(old.content), _tokens(content)) >= AITF_MEMORY_SAME_THRESHOLD:
        return _refresh(db, conversation_id, old, conf, msg_id, evidence)
    return _resolve_conflict(db, user_id, conversation_id, old, kind, subject,
                             content, conf, prov, source, msg_id, evidence)


def _create_active(db: Session, user_id: int, conversation_id: str | None,
                   kind: str, subject: str, content: str, key: str,
                   conf: float, prov: str, source: str,
                   msg_id: int | None, evidence: str) -> tuple[str, MemoryItem]:
    """新语义槽位：建 active 条目（版本链头，root_id 指向自己）。

    importance/expires_at/half_life_days 全部来自 forget.py 的公式与常量
    （V7.1 起唯一来源，杜绝双轨）。
    """
    item_id = uuid.uuid4().hex[:16]
    item = MemoryItem(
        id=item_id,
        user_id=user_id,
        kind=kind,
        subject=subject,
        dedupe_key=key,
        content=content,
        evidence=evidence or None,
        source=source,
        conversation_id=conversation_id,
        message_id=msg_id,
        confidence=conf,
        importance=initial_importance(conf, kind, source=source, prov=prov),
        status="active",
        root_id=item_id,  # 链头即自己
        version=1,
        expires_at=expiry_for(kind),  # TTL=0 的 kind（rule/profile）为 None
        half_life_days=kind_half_life(kind),
        item_meta=json.dumps({"prov": prov}, ensure_ascii=False),
    )
    db.add(item)
    db.flush()  # 让同批后续条目的槽位查询能看见本行
    _audit(db, item, "created", "job", None,
           f"新槽位入库（prov={prov}, conf={conf:.2f}）")
    return "created", item


def _refresh(db: Session, conversation_id: str | None, old: MemoryItem,
             conf: float, msg_id: int | None, evidence: str) -> tuple[str, MemoryItem]:
    """语义等价刷新：不新增版本，confidence 取 max，溯源更新到最新出现位置。"""
    before = _snap(old)
    if conf > float(old.confidence or 0.0):
        old.confidence = conf
    old.updated_at = utcnow()
    # 等价事实再次出现：溯源指向最新上下文（旧会话/消息引用价值递减）
    old.conversation_id = conversation_id
    if msg_id is not None:
        old.message_id = msg_id
    if evidence:
        old.evidence = evidence
    db.flush()
    _audit(db, old, "refreshed", "job", before,
           f"语义等价刷新（Jaccard≥{AITF_MEMORY_SAME_THRESHOLD}），confidence 取 max")
    return "refreshed", old


def _resolve_conflict(db: Session, user_id: int, conversation_id: str | None,
                      old: MemoryItem, kind: str, subject: str, content: str,
                      conf: float, prov: str, source: str,
                      msg_id: int | None, evidence: str) -> tuple[str, MemoryItem | None]:
    """冲突裁决：取代 / 挂起（规则表见模块 docstring）。"""
    # old 是用户手动记忆 → 置信度虚拟 +USER_BOOST 再比（更难被覆盖）
    old_eff = float(old.confidence or 0.0) + (
        AITF_MEMORY_USER_BOOST if old.source == "user" else 0.0)
    # new 是用户手动写入 → 无条件取代（编辑/手动写入路径走这里）
    new_manual = source == "user"

    if new_manual or conf >= old_eff - AITF_MEMORY_CONF_EPS:
        # ---- 取代：old 让出 active 位，new 继承 key 成为新 active ----
        reason = (f"被新版本取代（新 conf={conf:.2f} ≥ 旧有效 conf={old_eff:.2f}"
                  f"-{AITF_MEMORY_CONF_EPS}"
                  f"{'，用户手动写入' if new_manual else ''}）")
        new = _supersede_with(db, old=old, kind=old.kind, subject=old.subject,
                              content=content, conf=conf, prov=prov,
                              source=source, conversation_id=conversation_id,
                              msg_id=msg_id, evidence=evidence,
                              actor="job", reason=reason)
        return "superseded", new

    # ---- 挂起：new→conflict（dedupe_key=NULL 不占 active 位），old 不动 ----
    new_id = uuid.uuid4().hex[:16]
    new = MemoryItem(
        id=new_id,
        user_id=user_id,
        kind=kind,
        subject=subject,
        dedupe_key=None,  # 冲突条目不占 active 位（唯一索引允许多 NULL）
        content=content,
        evidence=evidence or None,
        source=source,
        conversation_id=conversation_id,
        message_id=msg_id,
        confidence=conf,
        importance=initial_importance(conf, kind, source=source, prov=prov),
        status="conflict",
        conflict_with=old.id,
        version=1,
        expires_at=expiry_for(kind),
        half_life_days=kind_half_life(kind),
        item_meta=json.dumps({"prov": prov}, ensure_ascii=False),
    )
    db.add(new)
    db.flush()
    _audit(db, new, "conflict", "job", None,
           f"置信度不足挂起（新 conf={conf:.2f} < 旧有效 conf={old_eff:.2f}"
           f"-{AITF_MEMORY_CONF_EPS}），与 active 条目 {old.id} 冲突")
    return "conflict", None


def _supersede_with(db: Session, old: MemoryItem, kind: str, subject: str,
                    content: str, conf: float, prov: str, source: str,
                    conversation_id: str | None, msg_id: int | None,
                    evidence: str, actor: str = "job", reason: str = "") -> MemoryItem:
    """把 old 打成 superseded 并新建 active 新版本（取代路径的唯一实现）。

    kind/subject 由调用方决定：会话抽取路径沿用 old 的（防主题漂移）；
    手动编辑路径可传新 subject（用户明确改主题）。TTL 随新版本重置
    （新事实新时效）。只 flush 不 commit（事务归调用方管）。
    ⚠️ 必须先改 old + flush 再 INSERT new：SQLAlchemy UoW 默认先 INSERT 后
    UPDATE，若 new 带同 key 先插入会撞 uq_memory_items_active 唯一索引。
    """
    new_id = uuid.uuid4().hex[:16]
    root_id = old.root_id or old.id
    inherit_key = old.dedupe_key or _dedupe_key(old.user_id, subject)
    before_old = _snap(old)
    old.status = "superseded"
    old.dedupe_key = None  # 让出 (user_id, dedupe_key) 唯一位
    old.superseded_by = new_id
    old.updated_at = utcnow()
    db.flush()
    new = MemoryItem(
        id=new_id,
        user_id=old.user_id,
        kind=kind,
        subject=subject,
        dedupe_key=inherit_key,
        content=content,
        evidence=evidence or None,
        source=source,
        conversation_id=conversation_id,
        message_id=msg_id,
        confidence=conf,
        importance=initial_importance(conf, kind, source=source, prov=prov),
        status="active",
        root_id=root_id,
        prev_id=old.id,
        version=(old.version or 1) + 1,
        expires_at=expiry_for(kind),
        half_life_days=kind_half_life(kind),
        item_meta=json.dumps({"prov": prov}, ensure_ascii=False),
    )
    db.add(new)
    db.flush()
    _audit(db, old, "superseded", actor, before_old, reason or "被新版本取代")
    _audit(db, new, "created", actor, None,
           f"取代旧版本 {old.id}（v{new.version}，prev={old.id}）")
    return new


# ============ V7.1：用户手动操作入口（api/memory.py 调用） ============

def edit_item(db: Session, user_id: int, item: MemoryItem,
              subject: str | None = None, content: str | None = None) -> str:
    """用户手动编辑：无条件走版本链取代（source='user'，手动记忆难被覆盖）。

    - 返回 "noop"（内容未变）或 "superseded"（旧版让位、新版 active）
    - 改 subject 时语义槽位 key 仍沿旧条目继承（dedupe_key 不变），新版挂
      新 subject——主题演进与槽位稳定两不误
    - 置信度按「用户显式声明」档位取 max(原值, 0.90) 且硬夹 ≤0.95
    - 仅 active 条目可编辑（api 层校验；编辑已退场的旧版本语义混乱）
    - commit 后对新 active 版本补向量（V7.2 钩子）
    """
    new_subject = (subject or item.subject or "").strip()[:120]
    new_content = (content if content is not None else (item.content or "")).strip()[:200]
    if not new_subject or not new_content:
        raise ValueError("subject 与 content 均不能为空")
    if new_subject == (item.subject or "") and new_content == (item.content or ""):
        return "noop"
    conf = min(0.95, max(_clamp01(item.confidence), 0.90))
    new = _supersede_with(db, old=item, kind=item.kind, subject=new_subject,
                          content=new_content, conf=conf, prov="explicit",
                          source="user", conversation_id=item.conversation_id,
                          msg_id=None, evidence="", actor="user", reason="用户手动编辑")
    db.commit()
    _index_active_items([new])
    return "superseded"


def adopt_item(db: Session, user_id: int, item: MemoryItem) -> str:
    """一键采纳冲突条目：item(conflict) 升级 active，取代 conflict_with 指向的条目。

    - target（被冲突的 active）→ superseded + dedupe_key=NULL + superseded_by=item.id
    - item → active + 继承 target 的语义槽位 key + version=target.version+1
      + prev_id=target.id + root_id 沿袭 + conflict_with 清空
    - target 已不存在（被删/越权）→ 按自身 subject 重建 key 直接扶正
    - commit 后对扶正的条目补向量（V7.2 钩子）
    """
    if item.status != "conflict" or not item.conflict_with:
        raise ValueError("仅冲突（conflict）状态的条目可采纳")
    target = db.get(MemoryItem, item.conflict_with)
    if target is None or target.user_id != user_id:
        before = _snap(item)
        item.status = "active"
        item.dedupe_key = _dedupe_key(user_id, item.subject)
        item.conflict_with = None
        item.updated_at = utcnow()
        db.flush()
        _audit(db, item, "adopted", "user", before, "被冲突对象已不存在，直接扶正")
        db.commit()
        _index_active_items([item])
        return "adopted"

    before_t, before_i = _snap(target), _snap(item)
    slot_key = target.dedupe_key or _dedupe_key(user_id, item.subject)
    root_id = target.root_id or target.id
    # target 让位（先 flush：item 即将拿走同一个 slot key）
    target.status = "superseded"
    target.dedupe_key = None
    target.superseded_by = item.id
    target.updated_at = utcnow()
    db.flush()
    # item 扶正，接上版本链（prev 指向被采纳对象，版本续在其上）
    item.status = "active"
    item.dedupe_key = slot_key
    item.prev_id = target.id
    item.root_id = root_id
    item.version = (target.version or 1) + 1
    item.conflict_with = None
    item.updated_at = utcnow()
    db.flush()
    _audit(db, target, "superseded", "user", before_t,
           f"被冲突条目 {item.id} 一键采纳取代")
    _audit(db, item, "adopted", "user", before_i, f"用户采纳，取代 {target.id}")
    db.commit()
    _index_active_items([item])
    return "adopted"


def restore_item(db: Session, user_id: int, item: MemoryItem) -> dict:
    """恢复 superseded 旧版本：与链上当前 active 按置信度竞争。

    - item.conf(+USER_BOOST 若 source=user) ≥ active.conf - CONF_EPS → 恢复
      取胜：active→superseded 让位，item→active（version 取链上最大 +1；
      prev_id 不改写——历史链保持原序，靠 superseded_by 前向链接到恢复点）
    - 置信度不足 → 拒绝（链不动，返回 restored=False + 原因）
    - 链上已无 active（被删/过期）→ 直接扶正
    - commit 后对恢复的条目补向量（V7.2 钩子）
    """
    if item.status != "superseded":
        raise ValueError("仅已被取代（superseded）状态的条目可恢复")
    # 沿 superseded_by 找链上当前 active（带 seen 防环）
    cur, seen = item, {item.id}
    while cur.superseded_by and cur.superseded_by not in seen:
        nxt = db.get(MemoryItem, cur.superseded_by)
        if nxt is None or nxt.user_id != user_id:
            break
        cur = nxt
        seen.add(nxt.id)
    if cur.id == item.id or cur.status != "active":
        before = _snap(item)
        item.status = "active"
        item.dedupe_key = _dedupe_key(user_id, item.subject)
        item.superseded_by = None
        item.updated_at = utcnow()
        db.flush()
        _audit(db, item, "restored", "user", before, "链上无 active 竞争者，直接恢复")
        db.commit()
        _index_active_items([item])
        return {"restored": True, "action": "restored_no_rival"}

    eff = float(item.confidence or 0.0) + (
        AITF_MEMORY_USER_BOOST if item.source == "user" else 0.0)
    active_conf = float(cur.confidence or 0.0)
    if eff >= active_conf - AITF_MEMORY_CONF_EPS:
        before_cur, before_it = _snap(cur), _snap(item)
        slot_key = cur.dedupe_key or _dedupe_key(user_id, item.subject)
        max_ver = max(int(item.version or 1), int(cur.version or 1))
        # 当前 active 让位（先 flush：item 即将拿走同一个 slot key）
        cur.status = "superseded"
        cur.dedupe_key = None
        cur.superseded_by = item.id
        cur.updated_at = utcnow()
        db.flush()
        # 旧版本恢复为 active（prev_id 不动：历史链原序不变）
        item.status = "active"
        item.dedupe_key = slot_key
        item.superseded_by = None
        item.version = max_ver + 1
        item.root_id = item.root_id or cur.root_id or cur.id
        item.updated_at = utcnow()
        db.flush()
        _audit(db, cur, "superseded", "user", before_cur,
               f"被用户恢复的旧版本 {item.id} 取代")
        _audit(db, item, "restored", "user", before_it,
               f"用户恢复旧版本（有效 conf={eff:.2f} ≥ active conf"
               f"-{AITF_MEMORY_CONF_EPS}）")
        db.commit()
        _index_active_items([item])
        return {"restored": True, "action": "superseded_current"}
    return {"restored": False,
            "reason": f"置信度不足（{eff:.2f} < active {active_conf:.2f}"
                      f"-{AITF_MEMORY_CONF_EPS}），未恢复"}


def soft_delete_item(db: Session, item: MemoryItem, actor: str = "user",
                     reason: str = "用户手动删除") -> None:
    """软删：status=deleted + dedupe_key=NULL（让出 active 位）+ deleted_at 打标。

    不物理删行——宽限期（AITF_MEMORY_PURGE_GRACE_DAYS）后由 purge_expired
    物理清理；期间审计与行都在，可追溯。
    """
    before = _snap(item)
    item.status = "deleted"
    item.dedupe_key = None
    item.deleted_at = utcnow()
    item.updated_at = utcnow()
    db.flush()
    _audit(db, item, "deleted", actor, before, reason)
    db.commit()
