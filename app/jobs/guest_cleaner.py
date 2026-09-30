"""访客数据管理（V2 简化后：单一固定共享 guest）。

设计变更（相对旧版）：
- 不再有「按 IP 动态建访客 / 24h TTL 过期 / 每小时清理调度 / 单 IP 限频 / 访客转正」那套。
- 全站只有一个固定 guest 账号（username='guest'），所有人免注册共用，数据混在一起。
- ensure_shared_guest：启动幂等建账号，并清掉旧动态 guest_* 遗留账号（迁移用，只跑一次）。
- reset_shared_guest_data：管理员触发，清空共享 guest 的任务与文件，但保留账号本身。
"""
import logging
import shutil

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.models.category import Category
from app.models.task import Task, StepLog
from app.models.user import User

logger = logging.getLogger("guest")

SHARED_GUEST_USERNAME = "guest"


def ensure_shared_guest(db: Session) -> User:
    """幂等：建/复用固定共享 guest 账号；顺手清掉旧动态 guest_* 遗留账号。"""
    purge_legacy_guests(db)
    u = db.execute(
        select(User).where(User.username == SHARED_GUEST_USERNAME)
    ).scalar_one_or_none()
    if u:
        return u
    u = User(
        username=SHARED_GUEST_USERNAME,
        role="guest",
        data_dir="guest_shared",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    # 新账号播种示例分类与示例任务（失败不影响启动）
    from app.services.sample_seeder import seed_sample_tasks
    try:
        seed_sample_tasks(db, u)
    except Exception as e:  # noqa: BLE001
        logger.warning("seed sample tasks for shared guest failed: %s", e)
    logger.info("seeded shared guest account (id=%s)", u.id)
    return u


def purge_legacy_guests(db: Session) -> int:
    """迁移用：删除旧动态 guest_* 账号（role=guest 且 username != 'guest'）及其数据。"""
    legacy = db.execute(
        select(User).where(
            User.role == "guest", User.username != SHARED_GUEST_USERNAME
        )
    ).scalars().all()
    n = 0
    for g in legacy:
        _delete_user_data(db, g)
        db.delete(g)          # 账号本身也删（_delete_user_data 只清数据、保留用户行）
        n += 1
    if n:
        db.commit()
        logger.info("purged %s legacy dynamic guest account(s)", n)
    return n


def reset_shared_guest_data(db: Session) -> dict:
    """清空共享 guest 的任务与文件，保留账号（管理员触发）。"""
    guest = db.execute(
        select(User).where(User.username == SHARED_GUEST_USERNAME)
    ).scalar_one_or_none()
    if not guest:
        return {"deleted_tasks": 0, "deleted_files": 0}
    return _delete_user_data(db, guest)


def _delete_user_data(db: Session, guest: User) -> dict:
    """级联删 tasks / step_logs / 示例分类 + 删文件目录，保留用户记录。"""
    deleted_tasks = db.execute(
        select(func.count()).select_from(Task).where(Task.user_id == guest.id)
    ).scalar_one()
    db.execute(delete(Category).where(Category.user_id == guest.id))
    task_rows = db.execute(select(Task.id).where(Task.user_id == guest.id)).all()
    task_ids = [r[0] for r in task_rows]
    if task_ids:
        db.execute(delete(StepLog).where(StepLog.task_id.in_(task_ids)))
        db.execute(delete(Task).where(Task.user_id == guest.id))
    deleted_files = 0
    if guest.data_dir:
        for base in (UPLOAD_DIR, OUTPUT_DIR):
            d = base / guest.data_dir
            if d.exists() and d.name == guest.data_dir:  # 防路径逃逸
                deleted_files += sum(1 for _ in d.rglob("*") if _.is_file())
                shutil.rmtree(d, ignore_errors=True)
    db.commit()
    logger.info("cleared data for guest %s (%s tasks, %s files)",
                guest.username, deleted_tasks, deleted_files)
    return {"deleted_tasks": deleted_tasks, "deleted_files": deleted_files}
