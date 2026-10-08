"""对话记忆提炼任务（P2 核心）。

职责：
- collect_new_messages：按水位（mem_last_msg_id）取增量消息
- process_conversation：单会话提炼（transcript → memory_chat → upsert 记忆文档
  → 推进水位）+ 附件兜底入库 + 当日日报刷新
- build_daily_index：聚合某用户当日全部会话记忆文档为一份「记忆日报」
- run_daily：调度器契约入口（夜间 02:00 / 启动回填共用），DB 抢占锁防多实例

事务口径：run_daily 逐会话独立小事务（单会话失败记录后继续，不拖垮整轮）；
提炼失败绝不推进水位（mem_dirty 不清、mem_last_msg_id 不动），下轮自动重试。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import (
    AITF_MEMORY_DIGEST_DAILY,
    AITF_MEMORY_ITEMS_ENABLED,
    AITF_MEMORY_MAX_CONV_PER_RUN,
    AITF_MEMORY_MAX_USERS_PER_RUN,
    AITF_MEMORY_MIN_USER_MSGS,
    AITF_MEMORY_SKIP_GUEST,
    AITF_MEMORY_TZ,
)
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.knowledge import Knowledge
from app.models.user import User
from app.services.memory.store import (
    get_personal_kb_id,
    read_memory_text,
    upsert_memory_doc,
)

logger = logging.getLogger("jobs.chat_memory")

# transcript 组装上限：单条消息截 500 字，整段 6000 字（token 花销与提炼质量平衡）
_MSG_LIMIT = 500
_TRANSCRIPT_LIMIT = 6000


def _biz_tz() -> ZoneInfo:
    """调度/日报的业务时区（AITF_MEMORY_TZ，解析失败兜底 UTC；与 P3 调度线同口径）。"""
    try:
        return ZoneInfo(AITF_MEMORY_TZ)
    except Exception:  # noqa: BLE001  非法时区名
        return timezone.utc  # type: ignore[return-value]


def _biz_date(dt: datetime | None = None) -> str:
    """业务日期（YYYY-MM-DD）：DB 里的 naive UTC 时间按 AITF_MEMORY_TZ 换算后取日期。

    统一切日口径：日报聚合、mem:daily source_key、JobRun.biz_date 全部走这里，
    避免凌晨 0-8 点（北京时间）的会话被 UTC 切日错归到前一日日报。
    """
    dt = dt or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_biz_tz()).strftime("%Y-%m-%d")


def collect_new_messages(db: Session, conv: Conversation) -> list[Message]:
    """取该会话 mem_last_msg_id 之后的全部消息（水位为 0/None = 首次全量）。"""
    stmt = select(Message).where(Message.conversation_id == conv.id)
    if conv.mem_last_msg_id:
        stmt = stmt.where(Message.id > conv.mem_last_msg_id)
    return list(db.execute(stmt.order_by(Message.id)).scalars().all())


def _latest_msg_id(db: Session, conv: Conversation) -> int:
    """会话内最新消息 id（无消息返回 0），推进水位用。"""
    return db.execute(
        select(Message.id).where(Message.conversation_id == conv.id)
        .order_by(Message.id.desc()).limit(1)
    ).scalar() or 0


def _build_transcript(msgs: list[Message]) -> str:
    """user/assistant 交替拼 transcript（role 标注；单条截 500 字，总 6000 字）。"""
    lines: list[str] = []
    for m in msgs:
        content = (m.content or "").strip()
        if not content:
            continue
        role = "用户" if m.role == "user" else "AI"
        lines.append(f"{role}: {content[:_MSG_LIMIT]}")
    return "\n".join(lines)[:_TRANSCRIPT_LIMIT]


def _ingest_pending_attachments(db: Session, conv: Conversation) -> dict:
    """附件兜底补跑（P1 的 file_ingest 由并行同事交付，缺失时零值兜底不报错）。

    返回键与 file_ingest.ingest_pending_attachments 完全对齐：
    {"total", "ingested", "skipped"}（旧 {"total", "done"} 键已废弃，两处统一）。
    调用契约：ingest_pending_attachments(db, conversation_id: str) 的第二参是
    conversation_id **字符串**——必须传 conv.id，传 conv ORM 对象会抛 ArgumentError。
    """
    try:
        from app.services.memory.file_ingest import ingest_pending_attachments
    except ImportError:  # noqa: BLE001  P1 模块尚未合入
        return {"total": 0, "ingested": 0, "skipped": 0}
    try:
        return (ingest_pending_attachments(db, conv.id)
                or {"total": 0, "ingested": 0, "skipped": 0})
    except Exception as e:  # noqa: BLE001  附件兜底失败不阻断记忆提炼，但必须留痕
        # 带堆栈摘要记 warning（静默吞异常是本兜底曾整体失效且漏检的根因）
        logger.warning("会话 %s 附件兜底补跑失败：%s: %s",
                       conv.id, type(e).__name__, str(e)[:200], exc_info=True)
        return {"total": 0, "ingested": 0, "skipped": 0}


def _sync_memory_items(db: Session, conv: Conversation, msgs: list[Message],
                       transcript: str) -> dict:
    """V7.0 条目双写编排：extract_facts → sync_conversation_items。

    只做编排（分层铁律：写入逻辑全在 services/memory/items.py）：
    - AITF_MEMORY_ITEMS_ENABLED=0 → 直接返回 {}（行为与 V6.0 逐字节一致）
    - 增量里 user 消息 < MIN_USER_MSGS 或 transcript 为空 → 跳过（过滤寒暄）
    - extract_facts 已知主题词表防 subject 漂移（三层防线的第一层）
    返回 sync_conversation_items 的 summary；异常由调用方捕获后整体回滚。
    """
    if not AITF_MEMORY_ITEMS_ENABLED:
        return {}
    user_msgs = [m for m in msgs if m.role == "user" and (m.content or "").strip()]
    if len(user_msgs) < AITF_MEMORY_MIN_USER_MSGS or not (transcript or "").strip():
        return {}
    # 延迟 import 便于测试 monkeypatch（与 memory_chat 同款路数）
    from app.services.memory.items import list_known_subjects, sync_conversation_items
    from app.services.memory.llm import extract_facts
    known = list_known_subjects(db, conv.user_id)
    facts = extract_facts(transcript, known_subjects=known)
    if not facts:
        return {}
    return sync_conversation_items(db, conv, facts, msgs)


def process_conversation(db: Session, conv: Conversation, manual: bool = False) -> dict:
    """提炼单个会话的记忆文档，返回 {"memory", "doc_id", "attachments", "daily"}。

    - memory: created（首建）/ updated（覆盖）/ skipped（无增量或幂等短路）
    - 幂等短路：非 manual 且 mem_dirty=False → 直接跳过（夜间空转不烧 LLM）；
      manual 且水位已到最新且记忆文档有效 → 连点不烧 LLM（附件兜底仍继续）
    - LLM 失败：抛异常（不推进水位、不清 mem_dirty），由调用方决定记录方式
    """
    user = db.get(User, conv.user_id)
    # guest 不做记忆（config 关掉 skip 时也不给共享访客建记忆，双保险）
    if user is None or (AITF_MEMORY_SKIP_GUEST and user.role == "guest"):
        return {"memory": "skipped", "doc_id": None, "attachments": {},
                "daily": False}

    latest_id = _latest_msg_id(db, conv)
    # 幂等短路 1：夜间任务只处理 dirty 会话
    if not manual and not conv.mem_dirty:
        return {"memory": "skipped", "doc_id": conv.mem_doc_id,
                "attachments": _ingest_pending_attachments(db, conv), "daily": False}
    # 幂等短路 2：手动连点——水位已到最新且旧记忆文档仍在 → 不再烧 LLM
    doc_valid = bool(conv.mem_doc_id) and db.get(Knowledge, conv.mem_doc_id) is not None
    if manual and conv.mem_last_msg_id and conv.mem_last_msg_id >= latest_id and doc_valid:
        return {"memory": "skipped", "doc_id": conv.mem_doc_id,
                "attachments": _ingest_pending_attachments(db, conv), "daily": False}

    # 1) 组 transcript + 读上一版记忆（滚动摘要合并去重）
    msgs = collect_new_messages(db, conv)
    transcript = _build_transcript(msgs)
    prev_memory = ""
    if conv.mem_doc_id:
        try:
            prev_memory = read_memory_text(db, conv.mem_doc_id)
        except Exception as e:  # noqa: BLE001  旧文档读不到就当无上一版
            logger.warning("会话 %s 旧记忆读取失败：%s", conv.id, e)

    # 2) LLM 提炼（无增量消息时仍允许手动重建；transcript 为空且无上一版则跳过）
    if not transcript.strip() and not prev_memory.strip():
        return {"memory": "skipped", "doc_id": conv.mem_doc_id,
                "attachments": _ingest_pending_attachments(db, conv), "daily": False}

    from app.services.memory.llm import memory_chat  # 延迟 import 便于测试 monkeypatch
    md_text = memory_chat(transcript, prev_memory=prev_memory)

    # 3) 入库 + 推进水位（失败在上面抛出，水位不动）
    from app.models.knowledge import KnowledgeBase
    kb_id = get_personal_kb_id(db, conv.user_id)
    if not kb_id:
        from app.services.memory.store import ensure_personal_kb
        user = db.get(User, conv.user_id)
        kb = ensure_personal_kb(db, user)
    else:
        kb = db.get(KnowledgeBase, kb_id)
    doc_id = upsert_memory_doc(db, kb, conv, md_text)

    # 3.5) V7.0 条目级记忆双写（与文档链路并行；任何异常只回滚条目变更，
    # 绝不阻断文档链路——文档已 commit，rollback 只清未提交的条目事务）
    try:
        items_summary = _sync_memory_items(db, conv, msgs, transcript)
    except Exception as e:  # noqa: BLE001  条目链路故障必须留痕但不上抛
        db.rollback()
        items_summary = {}
        logger.warning("会话 %s 记忆条目双写失败（文档链路不受影响）：%s: %s",
                       conv.id, type(e).__name__, str(e)[:200], exc_info=True)

    conv.mem_dirty = False
    conv.mem_last_msg_id = _latest_msg_id(db, conv)
    conv.mem_doc_id = doc_id
    conv.mem_at = utcnow()
    conv.mem_error = None
    db.commit()

    # 4) 附件兜底入库（P1 交付的 ingest_pending_attachments）
    attachments = _ingest_pending_attachments(db, conv)

    # 5) 刷新当日日报（只在记忆有实际变化时重建，避免每次连点都重嵌入）
    daily = False
    if AITF_MEMORY_DIGEST_DAILY:
        build_daily_index(db, conv.user_id, _biz_date())
        daily = True
    return {"memory": "updated" if doc_valid else "created", "doc_id": doc_id,
            "attachments": attachments, "daily": daily}


def build_daily_index(db: Session, user_id: int, date_str: str) -> str:
    """聚合某用户当日（mem_at 落在 date_str，按 AITF_MEMORY_TZ 切日）的会话记忆文档
    为「记忆日报」。

    纯拼装（目录 + 每篇 2~3 行摘要句），不调 LLM；source_key =
    mem:daily:{user_id}:{date_str}，与会话记忆同款「先删向量再 ingest」幂等覆盖。
    当日无会话记忆 → 返回空串（不生成空日报）。
    """
    from app.models.knowledge import KnowledgeBase
    from app.services.knowledge.ingest import delete_document_vectors, ingest_document
    from app.services.memory.store import _md5

    # 业务时区当日 0 点 → 对应的 UTC 窗口 [day_start, day_end)（mem_at 存 naive UTC）
    tz = _biz_tz()
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        local_midnight = datetime(y, m, d, tzinfo=tz)
    except (ValueError, AttributeError):  # date_str 非法 → 按今天的业务日期兜底
        date_str = _biz_date()
        y, m, d = (int(x) for x in date_str.split("-"))
        local_midnight = datetime(y, m, d, tzinfo=tz)
    day_start = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    day_end = day_start + timedelta(days=1)

    rows = db.execute(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.mem_doc_id.isnot(None),
            Conversation.mem_at >= day_start,
            Conversation.mem_at < day_end,
        ).order_by(Conversation.mem_at)
    ).scalars().all()
    docs: list[tuple[Conversation, str]] = []
    for c in rows:
        text = read_memory_text(db, c.mem_doc_id)
        if text.strip():
            docs.append((c, text))
    if not docs:
        return ""

    # 目录 + 每篇 2~3 行摘要句（取「关键结论」小节优先，缺省回落正文开头）
    lines = [f"# 记忆日报 · {date_str}", ""]
    for c, text in docs:
        lines.append(f"## 会话「{c.title or '新会话'}」")
        snippet = ""
        for block in text.split("\n"):
            if block.strip().startswith("## 关键结论"):
                snippet = text.split("## 关键结论", 1)[1].lstrip(":\n ")[:200]
                break
        if not snippet:
            snippet = text[:200]
        lines.append(snippet.strip() or "（无有效内容）")
        lines.append("")
    md_text = "\n".join(lines)

    kb_id = get_personal_kb_id(db, user_id)
    if not kb_id:
        return ""
    kb = db.get(KnowledgeBase, kb_id)
    source_key = f"mem:daily:{user_id}:{date_str}"
    title = f"记忆日报 · {date_str}"
    doc = db.execute(
        select(Knowledge).where(Knowledge.source_key == source_key)
    ).scalars().first()
    if doc is not None:
        delete_document_vectors(db, doc.id)
        doc.title = title
        doc.file_hash = _md5(source_key)
        doc.parse_status = "parsing"
        doc.error_message = ""
        doc.deleted_at = None
        db.commit()
        db.refresh(doc)
    else:
        doc = Knowledge(
            user_id=user_id,
            knowledge_base_id=kb.id,
            type="document",
            title=title,
            file_name="",
            file_type="txt",
            file_path="",
            file_size=len(md_text.encode("utf-8")),
            file_hash=_md5(source_key),
            source_key=source_key,
            parse_status="parsing",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    ingest_document(db, kb, doc, md_text)
    return doc.id


def run_daily(db: Session | None = None) -> dict:
    """每日提炼入口（调度器契约，签名一字不差；db=None 时自建会话）。

    - JobRun (job_id="chat-memory", biz_date=今天) 唯一约束做 DB 抢占锁：
      插入冲突 = 当天已有实例在跑 → 直接 {"skipped": True}
    - 遍历非 guest 用户（上限 MAX_USERS_PER_RUN）→ 扫 mem_dirty 会话
      （上限 MAX_CONV_PER_RUN）→ 逐个 process_conversation（单会话独立小事务，
      单会话失败记录后继续）→ 每用户 build_daily_index
    - JobRun 写 success/failed + duration_ms + payload 摘要
    """
    from app.core.db import SessionLocal
    from app.models.job import JobRun

    own_db = db is None
    if own_db:
        db = SessionLocal()
    t0 = time.perf_counter()
    try:
        # biz_date 与 P3 调度线回填查询同口径（AITF_MEMORY_TZ 切日）
        biz_date = _biz_date()
        run = JobRun(job_id="chat-memory", biz_date=biz_date, status="running")
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            # 唯一键冲突 = 今天已有实例在跑（多进程/重复触发），后来者直接退出
            db.rollback()
            logger.info("chat-memory 当日任务已存在，跳过本轮（biz_date=%s）", biz_date)
            return {"skipped": True}

        summary: dict = {"users": 0, "convs": 0, "created": 0, "updated": 0,
                         "skipped": 0, "failed": 0, "dailies": 0, "errors": []}
        try:
            users = db.execute(
                select(User).where(User.role != "guest").order_by(User.id)
                .limit(AITF_MEMORY_MAX_USERS_PER_RUN)
            ).scalars().all()
            for u in users:
                convs = db.execute(
                    select(Conversation)
                    .where(Conversation.user_id == u.id,
                           Conversation.mem_dirty.is_(True))
                    .order_by(Conversation.updated_at.desc())
                    .limit(AITF_MEMORY_MAX_CONV_PER_RUN)
                ).scalars().all()
                summary["users"] += 1
                for conv in convs:
                    try:
                        r = process_conversation(db, conv, manual=False)
                        summary["convs"] += 1
                        summary[r.get("memory", "skipped")] = \
                            summary.get(r.get("memory", "skipped"), 0) + 1
                    except Exception as e:  # noqa: BLE001  单会话失败不拖垮整轮
                        db.rollback()
                        summary["failed"] += 1
                        summary["errors"].append(f"conv:{conv.id}:{str(e)[:120]}")
                        logger.warning("会话 %s 记忆提炼失败：%s", conv.id, e)
                if AITF_MEMORY_DIGEST_DAILY:
                    try:
                        if build_daily_index(db, u.id, biz_date):
                            summary["dailies"] += 1
                    except Exception as e:  # noqa: BLE001
                        db.rollback()
                        summary["errors"].append(f"daily:{u.id}:{str(e)[:120]}")
                        logger.warning("用户 %s 日报生成失败：%s", u.id, e)
                # V7.1 遗忘：TTL 过期让位 + 宽限物理清理。挂在同一个 JobRun
                # （不新增 job_id、不新增一把锁）；异常只记 errors，绝不影响主提炼
                try:
                    from app.services.memory.forget import run_forgetting
                    run_forgetting(db, u.id)
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    summary["errors"].append(f"forget:{u.id}:{str(e)[:120]}")
                    logger.warning("用户 %s 遗忘任务失败：%s", u.id, e)
            run.status = "success"
        except Exception as e:  # noqa: BLE001  整轮级异常也要落审计
            db.rollback()
            run.status = "failed"
            run.error = f"{type(e).__name__}: {str(e)[:500]}"
            logger.exception("chat-memory 整轮失败")
        finally:
            run.finished_at = utcnow()
            run.duration_ms = int((time.perf_counter() - t0) * 1000)
            payload = {k: v for k, v in summary.items() if k != "errors"}
            payload["errors"] = summary["errors"][:20]
            run.payload = json.dumps(payload, ensure_ascii=False)
            db.commit()
        return {"skipped": False, **summary}
    finally:
        if own_db:
            db.close()
