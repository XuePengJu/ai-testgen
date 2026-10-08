"""记忆遗忘与隐私（V7.1：TTL / 重要性 / 过期 / 宽限清理 / 用户真删）。

职责边界（分层铁律）：
- 本模块只管「条目随时间退化」的策略与执行：初始重要性、命中强化、
  TTL 过期让位、宽限期物理清理、用户硬删
- kind 常量唯一来源：TTL 天数 / 半衰期天数 / 重要性权重在此导出，
  写入侧（items.py）只 import 不复制（V7.2 检索的衰减因子同样读这里）
- 过期**不物理删**：status=expired + dedupe_key=NULL 让出 active 位，
  物理删只发生在 purge_expired（宽限期后）与 hard_delete_item（用户真删）
- run_forgetting 挂在 chat-memory 的 JobRun 里执行（不新增 job_id 不新增
  锁），异常由调用方（run_daily）记录后继续
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    AITF_MEMORY_FORGET_ENABLED,
    AITF_MEMORY_PURGE_GRACE_DAYS,
)
from app.core.utils import utcnow
from app.models.memory import MemoryItem

logger = logging.getLogger("memory.forget")

# kind → TTL 天数（0 = 永不过期；待办与临时事实短命，规则画像长命）
_KIND_TTL_DAYS = {"todo": 30, "fact": 180, "preference": 365, "rule": 0, "profile": 0}
# kind → 时间衰减半衰期天数（V7.2 检索排序的 decay 因子只读这里）
_KIND_HALF_LIFE = {"rule": 365, "preference": 365, "profile": 365, "fact": 180, "todo": 14}
# kind → 重要性权重（initial_importance 公式的第二项）
_KIND_WEIGHT = {"rule": 1.0, "preference": 0.9, "profile": 0.9, "fact": 0.6, "todo": 0.5}

# 命中强化步长：每次被检索命中 importance +0.05（上限 1.0）
_HIT_BUMP = 0.05

# 未知 kind 的兜底默认（防 LLM 输出新枚举时 KeyError）
_DEFAULT_TTL = 180
_DEFAULT_HALF_LIFE = 180
_DEFAULT_WEIGHT = 0.6


def kind_ttl_days(kind: str) -> int:
    """该 kind 的 TTL 天数（0 = 不过期）；未知 kind 兜底 180 天。"""
    return int(_KIND_TTL_DAYS.get(kind, _DEFAULT_TTL))


def kind_half_life(kind: str) -> int:
    """该 kind 的半衰期天数（V7.2 检索时间衰减用）；未知 kind 兜底 180。"""
    return int(_KIND_HALF_LIFE.get(kind, _DEFAULT_HALF_LIFE))


def expiry_for(kind: str, now: datetime | None = None) -> datetime | None:
    """按 kind 计算过期时间；TTL=0 的 kind（rule/profile）返回 None（永不过期）。"""
    ttl = kind_ttl_days(kind)
    if ttl <= 0:
        return None
    return (now or utcnow()) + timedelta(days=ttl)


def initial_importance(confidence: float, kind: str,
                       source: str = "conversation", prov: str = "infer") -> float:
    """初始重要性（[0,1]，四舍五入 4 位）。

    公式：0.45*confidence + 0.25*kind_weight (+0.15 用户手写) (+0.15 显式声明)
    - 用户手写（source='user'）：用户亲自维护的记忆天然更重要
    - 显式声明（prov='explicit'）：「记住/我偏好/我们约定」类强信号
    """
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = min(1.0, max(0.0, conf))
    v = 0.45 * conf + 0.25 * _KIND_WEIGHT.get(kind, _DEFAULT_WEIGHT)
    if source == "user":
        v += 0.15
    if prov == "explicit":
        v += 0.15
    return round(min(1.0, v), 4)


def bump_importance_on_hit(db: Session, item: MemoryItem,
                           delta: float = _HIT_BUMP) -> None:
    """检索命中强化：importance +delta（上限 1.0）、hit_count+1、last_hit_at=now。

    V7.2 hybrid_search 命中时调用；当前批次无调用方（预留接口，测试覆盖）。
    """
    before = round(float(item.importance or 0.0), 4)
    item.importance = round(min(1.0, before + delta), 4)
    item.hit_count = int(item.hit_count or 0) + 1
    item.last_hit_at = utcnow()
    db.commit()


def _clear_item_vectors(item_id: str) -> None:
    """清掉单个条目的全部向量（V7.2 接入 vectorstore.delete_item_vectors）。

    向量是旁路：清理失败只告警，绝不阻断行删除/宽限清理主流程。
    """
    try:
        from app.services.knowledge.vectorstore import delete_item_vectors
        delete_item_vectors(item_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("清理条目 %s 向量失败：%s", item_id, e)


def expire_items(db: Session, user_id: int, now: datetime | None = None) -> int:
    """TTL 过期：active 且 expires_at < now → status=expired + dedupe_key=NULL。

    刻意**不物理删**：expired 行保留供审计回溯与宽限期判定；dedupe_key 置
    NULL 让出 (user_id, dedupe_key) 唯一位——同主题新事实可立即入库成 active。
    返回本轮过期条数。
    """
    now = now or utcnow()
    rows = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
            MemoryItem.expires_at.isnot(None),
            MemoryItem.expires_at < now,
        )
    ).scalars().all()
    for item in rows:
        # 延迟 import：items.py 顶部 import 本模块（常量），运行时反向取
        # _snap/_audit 不会循环（两个模块此时均已加载完毕）
        from app.services.memory.items import _audit, _snap
        before = _snap(item)
        item.status = "expired"
        item.dedupe_key = None  # 让出 active 位
        item.updated_at = utcnow()
        db.flush()
        _audit(db, item, "expired", "job", before,
               f"TTL 到期（expires_at={item.expires_at.isoformat()}）")
    if rows:
        db.commit()
        logger.info("用户 %s 本轮过期 %d 条", user_id, len(rows))
    return len(rows)


def purge_expired(db: Session, user_id: int, now: datetime | None = None) -> int:
    """宽限清理：expired/deleted 状态超过 PURGE_GRACE_DAYS 的行物理删除。

    宽限时钟 = updated_at（状态切换时刻：过期让位/软删打标都会刷新它）；
    物理删前先清向量（当前为空守卫），**审计行保留**（隐私追溯底线）。
    返回本轮物理删除条数。
    """
    now = now or utcnow()
    cutoff = now - timedelta(days=AITF_MEMORY_PURGE_GRACE_DAYS)
    rows = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.status.in_(("expired", "deleted")),
            MemoryItem.updated_at < cutoff,
        )
    ).scalars().all()
    for item in rows:
        # 物理删前落 'purged' 审计（item 行虽删、审计行保留，全生命周期可追溯）
        from app.services.memory.items import _audit, _snap
        before = _snap(item)
        _clear_item_vectors(item.id)  # V7.2 接入向量清理（当前空守卫）
        db.delete(item)
        _audit(db, item, "purged", "job", before,
               f"宽限期 {AITF_MEMORY_PURGE_GRACE_DAYS} 天到，物理清理")
    if rows:
        db.commit()
        logger.info("用户 %s 宽限清理物理删除 %d 条", user_id, len(rows))
    return len(rows)


def hard_delete_item(db: Session, item: MemoryItem, actor: str = "user",
                     reason: str = "用户手动硬删除") -> None:
    """用户真删：立即物理删条目行，**审计保留**（谁删的、删了什么可追溯）。

    - 先落 'deleted' 审计（before 快照留档），再清向量（空守卫），最后删行
    - 独立小事务（本函数内部 commit）：API 层调用即生效
    """
    # 延迟 import 规避循环（items.py 顶部 import 本模块；见 expire_items 注释）
    from app.services.memory.items import _audit, _snap
    before = _snap(item)
    db.flush()
    _audit(db, item, "deleted", actor, before, reason)
    _clear_item_vectors(item.id)  # V7.2 接入向量清理（当前空守卫）
    db.delete(item)
    db.commit()
    logger.info("条目 %s 已被用户硬删（审计保留）", item.id)


def run_forgetting(db: Session, user_id: int) -> dict:
    """遗忘任务入口（挂在 chat-memory JobRun 的每用户循环末尾）。

    顺序：先 expire（让位）再 purge（清尸）——purge 的宽限时钟从过期时刻
    起算，刚过期的行本轮不会被误删。FORGET_ENABLED=0 直接跳过（只增不减）。
    异常不上抛：由调用方 run_daily 记入 summary["errors"] 后继续。
    """
    if not AITF_MEMORY_FORGET_ENABLED:
        return {"skipped": True}
    n_expired = expire_items(db, user_id)
    n_purged = purge_expired(db, user_id)
    return {"expired": n_expired, "purged": n_purged}
