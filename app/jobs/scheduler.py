"""APScheduler 调度器（P3：对话记忆每晚 02:00 自动提炼）。

职责：
- init_scheduler()：按配置创建 BackgroundScheduler 并注册 chat-memory Cron 任务
- run_daily_job()：夜间任务入口（函数内懒加载 app.jobs.chat_memory.run_daily）
- shutdown_scheduler()：幂等关闭调度器
- maybe_backfill_missing_days()：启动时检查 JobRun，回填最近 N 天漏跑的提炼

线程安全与幂等：
- _ingest_lock 保护 _scheduler 的创建/销毁，避免并发 init/shutdown 产生双调度器
  或「初始化一半」的状态（多线程调用 / 测试场景）。
- 业务级幂等由 JobRun 表 (job_id, biz_date) 唯一键 DB 抢占锁保证（见 chat_memory.run_daily，
  唯一键冲突 = 当天已有实例在跑 → 返回 {"skipped": True}），调度器自身不重复加锁。

契约（chat_memory.py 由并行同事交付，本模块只 import 不修改）：
    def run_daily(db: Session | None = None) -> dict: ...
"""
import logging
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core import config as cfg  # 调用时取属性，便于测试 monkeypatch
from app.core.db import SessionLocal
from app.models.job import JobRun

logger = logging.getLogger("app.scheduler")

# 模块级单例：当前调度器实例；None = 未初始化或已关闭
_scheduler: BackgroundScheduler | None = None

# init/shutdown 幂等锁：防止并发初始化创建两个调度器实例（与记忆提炼无直接关系，
# 命名沿用任务规格中的 _ingest_lock）
_ingest_lock = threading.Lock()

# 与 chat_memory.run_daily 写入 JobRun 的 job_id 保持一致（DB 抢占锁的业务键）
_JOB_ID = "chat-memory"


def _resolve_tz():
    """解析调度时区；非法/缺失时兜底 UTC，绝不让配置错误炸掉启动。"""
    try:
        return ZoneInfo(cfg.AITF_MEMORY_TZ)
    except Exception as e:  # noqa: BLE001  ZoneInfoNotFoundError/KeyError/ValueError 等
        logger.warning("非法时区 %r（%s），调度器兜底使用 UTC", cfg.AITF_MEMORY_TZ, e)
        return ZoneInfo("UTC")


def init_scheduler() -> None:
    """按配置初始化并启动调度器；开关关闭或已初始化时幂等直接返回。"""
    global _scheduler
    if not cfg.AITF_SCHEDULER:
        logger.info("AITF_SCHEDULER=0，后台调度器不启动")
        return
    if not cfg.AITF_MEMORY_ENABLED:
        logger.info("AITF_MEMORY_ENABLED=0，对话记忆调度任务不注册")
        return
    with _ingest_lock:
        if _scheduler is not None:
            logger.debug("调度器已初始化，幂等跳过")
            return
        tz = _resolve_tz()
        scheduler = BackgroundScheduler(timezone=tz)
        scheduler.add_job(
            run_daily_job,
            CronTrigger(
                hour=cfg.AITF_MEMORY_CRON_HOUR,
                minute=cfg.AITF_MEMORY_CRON_MINUTE,
                timezone=tz,
            ),
            coalesce=True,                       # 积压多次触发合并为一次
            max_instances=1,                     # 同一时刻只允许一个实例在跑
            misfire_grace_time=cfg.AITF_MEMORY_MISFIRE_SEC,  # 错过触发的宽限窗口
            id=_JOB_ID,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            "记忆提炼调度器已启动：每日 %02d:%02d（tz=%s），misfire 宽限 %ds，job_id=%s",
            cfg.AITF_MEMORY_CRON_HOUR, cfg.AITF_MEMORY_CRON_MINUTE,
            cfg.AITF_MEMORY_TZ, cfg.AITF_MEMORY_MISFIRE_SEC, _JOB_ID,
        )


def run_daily_job() -> None:
    """Cron 触发的夜间任务入口。

    懒加载 chat_memory.run_daily：并行交付时序下模块可能尚不存在，
    ImportError 只记日志不上抛，避免调度器线程反复报错崩掉。
    """
    try:
        from app.jobs.chat_memory import run_daily  # noqa: 函数内懒加载，解耦交付时序
    except ImportError as e:
        logger.error("chat_memory 模块不可用，本次夜间记忆提炼跳过：%s", e)
        return
    try:
        result = run_daily()
        logger.info("夜间记忆提炼完成：%s", result)
    except Exception as e:  # noqa: BLE001  全量兜底：夜间任务异常不外泄
        logger.exception("夜间记忆提炼执行异常：%s", e)


def shutdown_scheduler() -> None:
    """关闭调度器并清理单例（wait=False 不阻塞退出）；未初始化时幂等返回。"""
    global _scheduler
    with _ingest_lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001  已停/未启动等异常不影响退出流程
            logger.warning("调度器关闭时出现异常（忽略）：%s", e)
        _scheduler = None
        logger.info("记忆提炼调度器已关闭")


def maybe_backfill_missing_days() -> None:
    """启动回填：检查最近一次 success 的 biz_date，缺 N 天（≤ 上限）就立即补跑一次。

    - 无任何 success 记录 → 视为从未跑过，按上限天数补跑一次
    - 昨天有 success → 无缺失，不跑
    - 缺失天数超过 AITF_MEMORY_BACKFILL_DAYS → 只按上限处理（跑一次即可，不逐日）
    - run_daily 自身有 DB 抢占锁防重，这里不需要额外加锁
    - 用完即关：查询使用独立 SessionLocal，不占用/泄漏请求级会话
    """
    if not cfg.AITF_SCHEDULER or not cfg.AITF_MEMORY_ENABLED:
        logger.debug("调度器或记忆功能未开启，跳过启动回填")
        return
    backfill_days = cfg.AITF_MEMORY_BACKFILL_DAYS
    if backfill_days <= 0:
        logger.debug("AITF_MEMORY_BACKFILL_DAYS=%d，不回填", backfill_days)
        return
    try:
        from app.jobs.chat_memory import run_daily  # noqa: 函数内懒加载，解耦交付时序
    except ImportError as e:
        logger.error("chat_memory 模块不可用，跳过启动回填：%s", e)
        return

    tz = _resolve_tz()
    today = datetime.now(tz).date()
    db = SessionLocal()
    try:
        last = (
            db.query(JobRun)
            .filter(JobRun.job_id == _JOB_ID, JobRun.status == "success")
            .order_by(JobRun.biz_date.desc())
            .first()
        )
    finally:
        db.close()

    if last is not None and last.biz_date:
        try:
            last_date = date.fromisoformat(last.biz_date)
        except ValueError:
            logger.warning("JobRun.biz_date=%r 格式异常，按无记录处理", last.biz_date)
            missing = backfill_days
        else:
            # 缺失 = 最近一次成功之后到今天还差几个「夜班」；昨天成功 → 0 → 不跑
            missing = (today - last_date).days - 1
    else:
        missing = backfill_days

    if missing <= 0:
        logger.debug("无缺失日期（最近 success=%s），不回填", getattr(last, "biz_date", None))
        return
    missing = min(missing, backfill_days)  # 超过上限只按上限处理，跑一次即可
    logger.info("启动回填：检测到 %d 天未提炼（上限 %d 天），立即补跑一次", missing, backfill_days)
    try:
        result = run_daily()
        logger.info("启动回填完成：%s", result)
    except Exception as e:  # noqa: BLE001  回填失败不阻断启动
        logger.exception("启动回填执行异常：%s", e)
