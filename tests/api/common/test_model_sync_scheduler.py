"""演示站模型自动同步调度器（V9.6）契约测试。"""
import pytest

from app.core import config, demo_config
from app.jobs import model_sync as ms


@pytest.fixture(autouse=True)
def _no_scheduler():
    ms.shutdown_model_sync_scheduler()
    yield
    ms.shutdown_model_sync_scheduler()


def _enable(monkeypatch, **kw):
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", True)
    monkeypatch.setattr(demo_config, "AITF_DEMO_MODEL_SYNC_ENABLED", True)
    monkeypatch.setattr(demo_config, "AITF_DEMO_MODEL_SYNC_INTERVAL_MIN", 5)
    monkeypatch.setattr(demo_config, "AITF_DEMO_RESET_TZ", "Asia/Shanghai")
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    for k, v in kw.items():
        monkeypatch.setattr(demo_config, k, v)


def test_not_started_when_demo_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", False)
    ms.init_model_sync_scheduler()
    assert ms._scheduler is None


def test_not_started_when_sync_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(demo_config, "AITF_DEMO_MODEL_SYNC_ENABLED", False)
    ms.init_model_sync_scheduler()
    assert ms._scheduler is None


def test_not_started_without_token(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "")
    ms.init_model_sync_scheduler()
    assert ms._scheduler is None


def test_scheduler_contract_and_idempotent(monkeypatch):
    _enable(monkeypatch)
    ms.init_model_sync_scheduler()
    assert ms._scheduler is not None

    jobs = ms._scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "demo_model_sync"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.trigger.interval.total_seconds() == 5 * 60

    ms.init_model_sync_scheduler()   # 幂等
    assert len(ms._scheduler.get_jobs()) == 1


def test_run_once_swallows_errors(monkeypatch):
    """后台任务失败不能把调度线程打崩。"""
    def boom(_db):
        raise RuntimeError("正式站不可达")

    monkeypatch.setattr(ms.model_sync, "pull_and_apply", boom)
    assert ms.run_once() is None