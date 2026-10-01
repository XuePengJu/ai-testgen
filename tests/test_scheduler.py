"""P3 调度器测试：init 幂等 / 开关 / 关闭 / run_daily_job / 启动回填 / 时区兜底。

关键手法：chat_memory.py 由并行同事交付，这里用 monkeypatch.setitem 往 sys.modules
注入伪模块（types.SimpleNamespace(run_daily=...)），`from app.jobs.chat_memory import
run_daily` 会优先命中 sys.modules，从而与真实文件是否存在完全解耦。
"""
import sys
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import config as cfg
from app.core.db import SessionLocal
from app.jobs import scheduler as sched
from app.models.job import JobRun

# 与默认 AITF_MEMORY_TZ 一致；回填用例统一用它算「昨天」，避免本机时区跨午夜的不确定
_TZ = ZoneInfo("Asia/Shanghai")


# ---------- 夹具与工具 ----------

@pytest.fixture(autouse=True)
def _no_scheduler():
    """每个用例前后都保证调度器处于关闭态，防单例状态跨用例串扰。"""
    sched.shutdown_scheduler()
    yield
    sched.shutdown_scheduler()


def _enable(monkeypatch, *, on=True, memory=True):
    """打开调度器相关配置开关（config 属性在调用时读取，可直接 monkeypatch）。"""
    monkeypatch.setattr(cfg, "AITF_SCHEDULER", on)
    monkeypatch.setattr(cfg, "AITF_MEMORY_ENABLED", memory)


def _fake_chat_memory(monkeypatch, calls, *, raise_exc=None):
    """注入伪 app.jobs.chat_memory 模块；run_daily 记录调用并返回 {"ok": n}。"""
    def fake_run_daily(db=None):
        if raise_exc is not None:
            raise raise_exc
        calls.append(db)
        return {"ok": len(calls)}

    monkeypatch.setitem(
        sys.modules, "app.jobs.chat_memory",
        types.SimpleNamespace(run_daily=fake_run_daily),
    )


def _clear_job_runs():
    """清空 chat-memory 的 JobRun 记录，保证回填用例从干净状态开始。"""
    db = SessionLocal()
    try:
        db.query(JobRun).filter(JobRun.job_id == "chat-memory").delete()
        db.commit()
    finally:
        db.close()


def _add_success(biz_date: str):
    """插入一条 chat-memory 的 success 运行记录。"""
    db = SessionLocal()
    try:
        db.add(JobRun(job_id="chat-memory", biz_date=biz_date, status="success"))
        db.commit()
    finally:
        db.close()


# ---------- init_scheduler ----------

def test_init_idempotent(monkeypatch):
    """连调两次 init_scheduler 只有一个 job，且触发参数符合契约。"""
    _enable(monkeypatch)
    sched.init_scheduler()
    sched.init_scheduler()  # 幂等：不报错、不重复注册
    assert sched._scheduler is not None
    jobs = sched._scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "chat-memory"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == cfg.AITF_MEMORY_MISFIRE_SEC


def test_init_disabled(monkeypatch):
    """AITF_SCHEDULER=0 时 init 不创建 scheduler。"""
    monkeypatch.setattr(cfg, "AITF_SCHEDULER", False)
    monkeypatch.setattr(cfg, "AITF_MEMORY_ENABLED", True)
    sched.init_scheduler()
    assert sched._scheduler is None


def test_init_memory_disabled(monkeypatch):
    """AITF_MEMORY_ENABLED=0 时也不创建 scheduler。"""
    _enable(monkeypatch, memory=False)
    sched.init_scheduler()
    assert sched._scheduler is None


def test_init_bad_tz_falls_back_utc(monkeypatch):
    """非法 AITF_MEMORY_TZ 兜底 UTC，初始化不抛异常。"""
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "AITF_MEMORY_TZ", "Not/AZone")
    sched.init_scheduler()  # 不抛
    assert sched._scheduler is not None
    tz = sched._scheduler.timezone
    # 兼容 zoneinfo（有 .key）与 pytz 两种表示，只要语义是 UTC 即可
    assert "UTC" in str(getattr(tz, "key", tz))


# ---------- shutdown_scheduler ----------

def test_shutdown_clears_scheduler(monkeypatch):
    """shutdown 后单例清空，且重复关闭幂等不抛。"""
    _enable(monkeypatch)
    sched.init_scheduler()
    assert sched._scheduler is not None
    sched.shutdown_scheduler()
    assert sched._scheduler is None  # 无悬挂调度器实例
    sched.shutdown_scheduler()  # 幂等：再次关闭不抛


# ---------- run_daily_job ----------

def test_run_daily_job_invokes_run_daily(monkeypatch):
    """触发成功：懒加载命中伪模块，run_daily 被调用且不传 db。"""
    calls = []
    _fake_chat_memory(monkeypatch, calls)
    sched.run_daily_job()
    assert len(calls) == 1
    assert calls[0] is None  # 未传 db → run_daily 自建 SessionLocal


def test_run_daily_job_import_error_swallowed(monkeypatch):
    """chat_memory 模块缺失（sys.modules 置 None → ImportError）时不上抛。"""
    monkeypatch.setitem(sys.modules, "app.jobs.chat_memory", None)
    sched.run_daily_job()  # 不抛即通过


def test_run_daily_job_exception_swallowed(monkeypatch):
    """run_daily 抛异常时被全量兜底，调度器线程不外泄异常。"""
    calls = []
    _fake_chat_memory(monkeypatch, calls, raise_exc=RuntimeError("夜间任务炸了"))
    sched.run_daily_job()  # 不抛即通过
    assert calls == []


# ---------- maybe_backfill_missing_days ----------

def test_backfill_disabled(monkeypatch):
    """开关关闭时回填直接跳过。"""
    monkeypatch.setattr(cfg, "AITF_SCHEDULER", False)
    calls = []
    _fake_chat_memory(monkeypatch, calls)
    sched.maybe_backfill_missing_days()
    assert calls == []


def test_backfill_no_record_runs_once(monkeypatch):
    """无任何 success 记录 → 立即补跑一次。"""
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "AITF_MEMORY_TZ", "Asia/Shanghai")
    _clear_job_runs()
    calls = []
    _fake_chat_memory(monkeypatch, calls)
    sched.maybe_backfill_missing_days()
    assert len(calls) == 1


def test_backfill_yesterday_success_skips(monkeypatch):
    """昨天有 success → 无缺失，不跑。"""
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "AITF_MEMORY_TZ", "Asia/Shanghai")
    _clear_job_runs()
    yesterday = (datetime.now(_TZ) - timedelta(days=1)).date().isoformat()
    _add_success(yesterday)
    calls = []
    _fake_chat_memory(monkeypatch, calls)
    sched.maybe_backfill_missing_days()
    assert calls == []


def test_backfill_old_record_capped_once(monkeypatch):
    """最近 success 很久以前 → 缺失超过上限也只按 3 天处理，且只跑一次。"""
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "AITF_MEMORY_TZ", "Asia/Shanghai")
    _clear_job_runs()
    long_ago = (datetime.now(_TZ) - timedelta(days=30)).date().isoformat()
    _add_success(long_ago)
    calls = []
    _fake_chat_memory(monkeypatch, calls)
    sched.maybe_backfill_missing_days()
    assert len(calls) == 1  # 补跑一次而非逐日 30 次
