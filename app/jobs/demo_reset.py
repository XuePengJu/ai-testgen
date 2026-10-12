"""演示站每日重置任务（V9.0）。

职责：每天 AITF_DEMO_RESET_HOUR:MINUTE（默认 02:00）清空演示库全部业务
数据，并按需重播种示例数据，让演示站「每天都像新的一样」。

设计要点：
- 复用 app/services/user_purge.purge_user_data（唯一级联清理入口，
  覆盖 conversations/messages/tasks/step_logs/知识库/记忆/附件等全表）。
- 清空策略（AITF_DEMO_PURGE_ADMIN）：
  * 1（默认）：把 admin 的业务数据也一起清（演示站就该归零），保留账号行；
  * 0：只清其他角色用户，admin 数据保留。
- 重播种：AITF_DEMO_SEED_SAMPLE=1 时对 admin（及其余保留的账号）重新
  执行 seed_sample_tasks，保证进来就有可演示的示例任务/对话。
- 容错：任一步失败只记 WARNING/ERROR，不抛异常 —— 重置是后台任务，
  绝不能因为一次失败让调度线程反复报错崩掉。
- 幂等：APScheduler cron 单实例 + max_instances=1，天然防重。

调度接入：
- main.py lifespan 里 init_demo_reset_scheduler()（仅 AITF_DEMO_ENABLED=1 时启动）
- 手动触发：POST /api/demo/reset（管理员；同样只在演示实例可用）
"""
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core import demo_config
from app.models.user import User

logger = logging.getLogger("jobs.demo_reset")

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _resolve_tz():
    try:
        return ZoneInfo(demo_config.AITF_DEMO_RESET_TZ)
    except Exception:  # noqa: BLE001  非法时区兜底 UTC，绝不让配置炸掉调度器
        logger.warning("非法重置时区 %r，兜底 UTC", demo_config.AITF_DEMO_RESET_TZ)
        return ZoneInfo("UTC")


def reset_demo_data(db: Session) -> dict:
    """清空演示库业务数据（保留账号），返回删除计数。

    幂等；供每日任务与手动接口共用。不抛异常，失败只记日志。
    """
    from app.services.user_purge import purge_user_data

    counts: dict = {"users_cleaned": 0}
    users = db.execute(select(User)).scalars().all()
    for u in users:
        # 策略：AITF_DEMO_PURGE_ADMIN=0 时跳过 admin（保留其数据）
        if u.role == "admin" and not demo_config.AITF_DEMO_PURGE_ADMIN:
            continue
        try:
            c = purge_user_data(db, u)
            counts["users_cleaned"] += 1
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + (v or 0)
        except Exception:  # noqa: BLE001
            logger.warning("重置清理用户 %s(%s) 失败", u.id, u.username, exc_info=True)

    # 重播种：对 admin（或全部保留账号）重新播种示例数据
    if demo_config.AITF_DEMO_SEED_SAMPLE:
        from app.services.sample_seeder import seed_sample_tasks
        for u in db.execute(select(User)).scalars().all():
            try:
                seed_sample_tasks(db, u)
            except Exception:  # noqa: BLE001
                logger.warning("重置后播种用户 %s 失败", u.username, exc_info=True)

    db.commit()
    return counts


def perform_reset() -> dict:
    """每日重置入口：独立 Session，记录日志，永不抛异常。"""
    started = datetime.now().isoformat(timespec="seconds")
    logger.info("===== 演示站每日重置开始（%s）=====", started)
    db = SessionLocal()
    try:
        counts = reset_demo_data(db)
        logger.info("演示站每日重置完成：%s", counts)
        return counts
    except Exception:  # noqa: BLE001
        logger.exception("演示站每日重置失败")
        return {}
    finally:
        db.close()


def init_demo_reset_scheduler() -> None:
    """初始化并启动每日重置调度器（仅演示实例；幂等）。"""
    global _scheduler
    if not demo_config.AITF_DEMO_ENABLED:
        logger.info("AITF_DEMO_ENABLED=0，演示站重置调度器不启动")
        return
    with _lock:
        if _scheduler is not None:
            return
        tz = _resolve_tz()
        scheduler = BackgroundScheduler(timezone=tz)
        scheduler.add_job(
            perform_reset,
            CronTrigger(
                hour=demo_config.AITF_DEMO_RESET_HOUR,
                minute=demo_config.AITF_DEMO_RESET_MINUTE,
                timezone=tz,
            ),
            id="demo_daily_reset",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            "演示站每日重置调度器已启动：每天 %02d:%02d（tz=%s）",
            demo_config.AITF_DEMO_RESET_HOUR, demo_config.AITF_DEMO_RESET_MINUTE,
            demo_config.AITF_DEMO_RESET_TZ,
        )


def shutdown_demo_reset_scheduler() -> None:
    """关闭调度器（幂等；wait=False 不阻塞退出）。"""
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            logger.warning("演示站重置调度器关闭异常（忽略）", exc_info=True)
        _scheduler = None
        logger.info("演示站重置调度器已关闭")
