"""演示站模型配置自动同步任务（V9.6）。

每 AITF_DEMO_MODEL_SYNC_INTERVAL_MIN 分钟（默认 30）从正式站拉一次平台池，
重新加密后替换本实例平台池；启动后先跑一次，之后定时。

只在演示实例启动（AITF_DEMO_ENABLED=1 且 AITF_DEMO_MODEL_SYNC_ENABLED=1）。
失败只记 WARNING，保留旧配置，不阻塞演示站启动/请求。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import config, demo_config
from app.core.db import SessionLocal
from app.services import model_sync

logger = logging.getLogger("jobs.model_sync")

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
_JOB_ID = "demo_model_sync"


def _resolve_tz():
    try:
        return ZoneInfo(demo_config.AITF_DEMO_RESET_TZ)
    except Exception:  # noqa: BLE001  非法时区兜底 UTC，绝不让配置炸掉调度器
        logger.warning("非法时区 %r，兜底 UTC", demo_config.AITF_DEMO_RESET_TZ)
        return ZoneInfo("UTC")


def run_once() -> dict | None:
    """拉一次正式站平台池并应用；独立 Session，永不抛异常。"""
    db = SessionLocal()
    try:
        result = model_sync.pull_and_apply(db)
        logger.info("演示站模型自动同步完成：%s", result.get("counts"))
        return result
    except Exception as e:  # noqa: BLE001  后台任务不能把调度线程打崩
        logger.warning("演示站模型自动同步失败（保留旧配置）：%s", e)
        return None
    finally:
        db.close()


def init_model_sync_scheduler() -> None:
    """初始化并启动自动同步调度器（仅演示实例；幂等）。"""
    global _scheduler
    if not demo_config.AITF_DEMO_ENABLED:
        return
    if not demo_config.AITF_DEMO_MODEL_SYNC_ENABLED:
        logger.info("AITF_DEMO_MODEL_SYNC_ENABLED=0，模型自动同步不启动")
        return
    if not config.MODEL_SYNC_TOKEN:
        logger.warning("AITF_MODEL_SYNC_TOKEN 未配置，模型自动同步不启动")
        return

    minutes = max(1, demo_config.AITF_DEMO_MODEL_SYNC_INTERVAL_MIN)
    with _lock:
        if _scheduler is not None:
            return
        tz = _resolve_tz()
        scheduler = BackgroundScheduler(timezone=tz)
        scheduler.add_job(
            run_once,
            IntervalTrigger(minutes=minutes, timezone=tz),
            id=_JOB_ID,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
            # 启动后先跑一次（延迟 10s，避开 lifespan 初始化其他组件）
            next_run_time=datetime.now(tz) + timedelta(seconds=10),
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info("演示站模型自动同步调度器已启动：每 %d 分钟", minutes)


def shutdown_model_sync_scheduler() -> None:
    """关闭调度器（幂等；wait=False 不阻塞退出）。"""
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            logger.warning("模型自动同步调度器关闭异常（忽略）", exc_info=True)
        _scheduler = None
        logger.info("演示站模型自动同步调度器已关闭")