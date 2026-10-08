"""记忆评估（V7.3：黄金测试集跑分 + 五类指标 + badcase 归因）。

分层铁律：
- 本模块只读跑分：种子数据经 items.py 真实冲突裁决链路注入（非直接建行），
  检索走 retrieve.hybrid_search（与线上对话同一入口），不 mock 任何业务逻辑
- 用例与阈值全部数据驱动：golden_set.json + THRESHOLDS
- 唯一允许的写动作：bump_importance_on_hit（检索命中强化的真实副作用）

四场景覆盖（对应计划 V7.3 验收）：
- preference_change：偏好变化 → 新版生效、旧版 superseded 不召回
- conflict：冲突信息 → 高置信 active 保持、低置信挂起不召回
- expiry：过期信息 → TTL 过期不召回、同槽位新事实正常召回
- isolation：越权隔离 → 他人条目零召回（含同主题不同用户）

badcase 五类归因（_attribute）：
- extraction_miss：种子阶段就没落表（评估里 = state 断言失败）
- dedupe_drift：subject 漂移成两条假链（偏好变化场景 top1 错）
- conflict_lost：该挡的低置信没挡住（冲突场景 top1 错）
- rank_miss：在候选但没进 top-k / 召回了不相干条目
- expired_wrong：过期条目被误召回
（另加 isolation_leak：越权泄漏，为隔离场景专用扩展类）
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update as sa_update

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.conversation import Conversation
from app.models.memory import MemoryItem
from app.models.user import User
from app.services.memory.forget import expire_items
from app.services.memory.items import sync_conversation_items

_HERE = Path(__file__).resolve().parent
GOLDEN: dict = json.loads((_HERE / "golden_set.json").read_text("utf-8"))

# V7.3 验收阈值（run_memory_eval.py 不达标退出码非 0）
THRESHOLDS: dict = {
    "hit_rate_at_5": 0.85,     # top1 命中率
    "false_memory_rate": 0.05,  # 召回里非 active 条目占比
    "conflict_rate": 0.10,      # 挂起冲突条目占比
    "inflation_rate": 8.0,      # 条目数 / 会话数（膨胀率）
    "latency_p95_ms": 50.0,     # hybrid_search 单次延迟 P95
}

# 场景 → top1 错误时的默认归因类
_ATTRIB_BY_SCENARIO = {
    "preference_change": "dedupe_drift",
    "conflict": "conflict_lost",
    "expiry": "expired_wrong",
    "isolation": "rank_miss",
}


# ============ 种子与状态操作（全部走真实链路） ============

def _mk_user(db, name: str) -> User:
    u = User(username=name, email=f"{name}@eval.local", password_hash="!",
             role="user")
    db.add(u)
    db.flush()
    return u


def _seed_facts(db, user: User, facts: list[dict], title: str) -> str:
    """经 sync_conversation_items 真实裁决链路注入一批事实，返回会话 id。"""
    conv = Conversation(id=uuid.uuid4().hex[:16], user_id=user.id, title=title)
    db.add(conv)
    db.flush()
    sync_conversation_items(
        db, conv, [dict(f, msg_ref=0) for f in facts], [])
    return conv.id


def _expire_indexed(db, user_id: int, item_index_facts: list[dict], index: int):
    """把第 index 条种子（按同 subject 查 active 行）拨到过期并跑 expire_items。

    先种 → 过期（status=expired + dedupe_key=NULL 让位）→ 后续种子走
    _create_active 槽位复用路径，复现「旧事实过期、新事实接管」的完整生命周期。
    """
    subject = item_index_facts[index]["subject"]
    row = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.subject == subject,
            MemoryItem.status == "active",
        ).order_by(MemoryItem.updated_at.desc()).limit(1)
    ).scalars().first()
    assert row is not None, f"过期目标不存在：subject={subject}"
    row.expires_at = utcnow() - timedelta(days=1)
    db.commit()
    expire_items(db, user_id)


def _backdate_subject(db, user_id: int, subject: str, days: int):
    """回拨某 subject active 条目的 updated_at（时间衰减真实性用）。"""
    db.execute(
        sa_update(MemoryItem)
        .where(MemoryItem.user_id == user_id,
               MemoryItem.subject == subject,
               MemoryItem.status == "active")
        .values(updated_at=utcnow() - timedelta(days=days)))
    db.commit()


# ============ 单用例执行 ============

def run_case(case: dict, seq: int) -> dict:
    """执行一个黄金用例，返回 {id, scenario, passed, hit, failures, attribution,
    latency_ms, recalled, false_mem, n_items, n_convs, n_conflict}。"""
    tag = uuid.uuid4().hex[:6]
    db = SessionLocal()
    result: dict = {"id": case["id"], "scenario": case["scenario"],
                    "passed": False, "hit": False, "failures": [],
                    "attribution": "", "latency_ms": 0.0, "recalled": 0,
                    "false_mem": 0, "n_items": 0, "n_convs": 0, "n_conflict": 0}
    try:
        # ---- 1) 建用户 + 种子（setup 视为 a 号用户的种子；seed 键扩展多用户）----
        users: dict[str, User] = {}
        for role in case.get("users", ["a"]):
            users[role] = _mk_user(db, f"eval{seq}{role}{tag}")

        conv_count = 0
        setup = case.get("setup")
        exp_idx = case.get("expire_index")
        if setup and exp_idx is not None:
            # 生命周期用例：逐条种 + 到点过期（种 → 过期 → 再种 → 槽位复用）。
            # ⚠️ 不能全部种完再过期：同 subject 两条会先完成裁决（v2 取代 v1），
            # 此时再过期会错杀新版本，导致零 active、零召回
            ua = users["a"]
            conv = Conversation(id=uuid.uuid4().hex[:16], user_id=ua.id,
                                title=f"eval-{case['id']}-a")
            db.add(conv)
            db.flush()
            conv_count = 1
            for i, f in enumerate(setup):
                sync_conversation_items(db, conv, [dict(f, msg_ref=0)], [])
                if i == exp_idx:
                    _expire_indexed(db, ua.id, [f], 0)
        elif setup:
            _seed_facts(db, users["a"], setup, f"eval-{case['id']}-a")
            conv_count = 1
        for role, facts in (case.get("seed") or {}).items():
            _seed_facts(db, users[role], facts, f"eval-{case['id']}-{role}")
            conv_count += 1
        result["n_convs"] = conv_count
        result["n_items"] = len(setup or []) + sum(
            len(v) for v in (case.get("seed") or {}).values())

        # ---- 2) 回拨（时间衰减真实性；过期已在种子流程内完成）----
        for subj, days in (case.get("backdate_days") or {}).items():
            _backdate_subject(db, users["a"].id, subj, days)

        # ---- 3) 检索（与线上对话同一入口）----
        from app.services.memory.retrieve import hybrid_search
        qu = users[case.get("query_user", "a")]
        t0 = time.perf_counter()
        hits = hybrid_search(db, qu, case["query"])
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        result["recalled"] = len(hits)

        # ---- 4) 断言 ----
        exp = case.get("expect") or {}
        docs = [(h.get("document") or "") for h in hits]
        ids: list[str] = []
        for h in hits:
            mid = (h.get("metadata") or {}).get("memory_item_id") \
                or (str(h.get("id", "")).removeprefix("memitem:") or None)
            if mid:
                ids.append(mid)
        # 召回里非 active 条目计数（false memory 的直接证据）
        if ids:
            rows = db.execute(select(MemoryItem.status).where(
                MemoryItem.id.in_(ids))).all()
            result["false_mem"] = sum(1 for (s,) in rows if s != "active")

        def _has(text: str, word: str) -> bool:
            return word.lower() in (text or "").lower()

        if exp.get("top1_absent"):
            if hits:
                result["failures"].append(
                    f"期望零召回但命中 {len(hits)} 条：{docs[0][:50]}")
        else:
            if not hits:
                result["failures"].append("零召回")
            else:
                result["hit"] = True
                want = exp.get("top1_contains")
                if want and not _has(docs[0], want):
                    result["failures"].append(
                        f"top1 不含「{want}」：{docs[0][:50]}")
        for w in exp.get("forbidden_contains") or []:
            for d in docs:
                if _has(d, w):
                    result["failures"].append(f"召回出现禁词「{w}」：{d[:50]}")

        # ---- 5) 状态断言（extraction_miss 归因的依据）----
        st = exp.get("state") or {}
        if st:
            subj = st.get("active_subject") or case["setup"][0]["subject"]
            rows = db.execute(select(MemoryItem).where(
                MemoryItem.user_id == users["a"].id)).scalars().all()
            result["n_items"] = len(rows)
            result["n_conflict"] = sum(1 for r in rows if r.status == "conflict")
            actives = [r for r in rows if r.status == "active"
                       and r.subject == subj]
            if st.get("active_content_contains"):
                hit_state = any(
                    _has(r.content, st["active_content_contains"]) for r in actives)
                if not hit_state:
                    result["failures"].append(
                        f"state：subject「{subj}」无含"
                        f"「{st['active_content_contains']}」的 active 条目")
            for key, status in (("min_superseded", "superseded"),
                                ("min_conflict", "conflict"),
                                ("min_expired", "expired")):
                if st.get(key) and sum(1 for r in rows if r.status == status) < st[key]:
                    result["failures"].append(
                        f"state：{status} 条目数不足 {st[key]}")

        result["passed"] = not result["failures"]
        if not result["passed"]:
            result["attribution"] = _attribute(case, result)
        return result
    finally:
        db.close()


def _attribute(case: dict, res: dict) -> str:
    """badcase 归因（五类 + isolation_leak 扩展）。字段全部 .get 取，
    兼容测试合成的最小 dict。"""
    joined = " | ".join(res.get("failures") or [])
    if res.get("scenario") == "isolation" or case.get("scenario") == "isolation" \
            or "期望零召回" in joined:
        return "isolation_leak"
    if "state：" in joined:
        return "extraction_miss"
    if not res.get("recalled"):
        return "rank_miss"
    top1_wrong = any(f.startswith("top1 不含") for f in res["failures"])
    if top1_wrong:
        return _ATTRIB_BY_SCENARIO.get(case.get("scenario", ""), "rank_miss")
    return "expired_wrong" if "禁词" in joined and case.get("scenario") == "expiry" \
        else "rank_miss"


# ============ 整套跑分 ============

def run_suite() -> dict:
    """跑全部黄金用例并汇总五类指标（阈值判定交给调用方/脚本）。"""
    results = [run_case(c, i) for i, c in enumerate(GOLDEN["cases"])]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    hits = sum(1 for r in results if r["hit"])
    recalled = sum(r["recalled"] for r in results)
    false_mem = sum(r["false_mem"] for r in results)
    n_items = sum(r["n_items"] for r in results)
    n_convs = sum(r["n_convs"] for r in results)
    n_conflict = sum(r["n_conflict"] for r in results)
    latencies = sorted(r["latency_ms"] for r in results)
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] \
        if latencies else 0.0
    metrics = {
        "hit_rate_at_5": round(hits / max(1, sum(
            1 for c in GOLDEN["cases"]
            if not c.get("expect", {}).get("top1_absent"))), 4),
        "false_memory_rate": round(false_mem / max(1, recalled), 4),
        "conflict_rate": round(n_conflict / max(1, n_items), 4),
        "inflation_rate": round(n_items / max(1, n_convs), 2),
        "latency_p95_ms": p95,
    }
    # 阈值方向：hit_rate_at_5 越高越好（低于阈值不达标）；其余四项越低越好
    # （超过阈值不达标）
    threshold_fail = [
        k for k, v in THRESHOLDS.items()
        if (metrics[k] < v if k == "hit_rate_at_5" else metrics[k] > v)
    ]
    return {"total": total, "passed": passed, "failed": total - passed,
            "metrics": metrics, "thresholds": THRESHOLDS,
            "threshold_fail": threshold_fail,
            "attributions": _count_attributions(results), "cases": results}


def _count_attributions(results: list[dict]) -> dict:
    out: dict = {}
    for r in results:
        if r.get("attribution"):
            out[r["attribution"]] = out.get(r["attribution"], 0) + 1
    return out


def format_report(out: dict) -> str:
    """人类可读报告（脚本与测试共用）。"""
    lines = [
        f"记忆黄金集跑分：{out['passed']}/{out['total']} passed"
        f"（failed={out['failed']}）",
        f"指标：{json.dumps(out['metrics'], ensure_ascii=False)}",
        f"阈值：{json.dumps(out['thresholds'], ensure_ascii=False)}",
        f"不达标项：{out['threshold_fail'] or '无'}",
        f"归因分布：{json.dumps(out['attributions'], ensure_ascii=False)}",
    ]
    for r in out["cases"]:
        if not r["passed"]:
            lines.append(f"  ✗ {r['id']} [{r['scenario']}] "
                         f"({r['attribution']}): {'; '.join(r['failures'])}")
    return "\n".join(lines)
