"""个人记忆库存量回填任务（P0）。

给还没有个人记忆库的存量用户批量补建（复用 ensure_personal_kb，幂等）。
启动时由 main.py lifespan 调一次；后续调度阶段可复用。
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase
from app.models.user import User
from app.services.memory.store import ensure_personal_kb

logger = logging.getLogger("jobs.personal_kb")


def backfill_personal_kbs(db: Session, limit: int = 500) -> int:
    """给没有个人库的存量用户批量补建，返回本次补建数。

    幂等：已有个人库（user_id + is_personal + 未软删命中）的用户直接跳过；
    单个用户补建失败只记日志，不阻断其余用户。
    """
    users = db.execute(select(User).order_by(User.id).limit(limit)).scalars().all()
    created = 0
    for u in users:
        hit = db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.user_id == u.id,
                KnowledgeBase.is_personal.is_(True),
                KnowledgeBase.deleted_at.is_(None),
            ).limit(1)
        ).first()
        if hit:
            continue
        try:
            ensure_personal_kb(db, u)
            created += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("backfill personal kb for user %s failed: %s", u.id, e)
    if created:
        logger.info("个人记忆库回填完成：本次补建 %s 个", created)
    return created
