"""M10.1 日志配置单测：小时切片、error.log 单独成档、uvicorn.access 静默。

覆盖点：
1. setup_logging(tmp) 后 logs/app.log 与 logs/error.log 同时存在，INFO 入 app.log 不入 error.log；
2. ERROR 同时写 app.log 与 error.log（排障只看 error.log 即可）；
3. 切片策略：TimedRotatingFileHandler when="H"，全量保留 72 份 / 错误 168 份；
4. 重复调用幂等（app.log 只挂 1 个 handler，不双写）；
5. uvicorn/uvicorn.error propagate=True；uvicorn.access propagate=False（裸访问行由 api.access 替代）。
"""
import logging
from logging.handlers import TimedRotatingFileHandler

import pytest

from app.core.logging_config import setup_logging


@pytest.fixture()
def log_dir(tmp_path):
    """每个用例独立的日志目录（参数化 handler 路径，避免污染真实 logs/）。"""
    return tmp_path / "logs"


def _handler_of(log_dir, filename):
    """从 root logger 上找到指向 log_dir/<filename> 的文件 handler。"""
    target = str((log_dir / filename).resolve())
    for h in logging.getLogger().handlers:
        if isinstance(h, TimedRotatingFileHandler) and h.baseFilename == target:
            return h
    return None


def test_setup_creates_app_and_error_logs(log_dir):
    """app.log 与 error.log 同时创建；INFO 只进 app.log，ERROR 两边都有。"""
    setup_logging(log_dir=log_dir)

    assert (log_dir / "app.log").exists()
    assert (log_dir / "error.log").exists()

    logging.getLogger("test.m101").info("info-only-line")
    logging.getLogger("test.m101").error("error-both-line")

    _handler_of(log_dir, "app.log").flush()
    _handler_of(log_dir, "error.log").flush()

    app_text = (log_dir / "app.log").read_text(encoding="utf-8")
    err_text = (log_dir / "error.log").read_text(encoding="utf-8")
    assert "info-only-line" in app_text
    assert "info-only-line" not in err_text, "INFO 不应进 error.log"
    assert "error-both-line" in app_text and "error-both-line" in err_text


def test_hourly_rotation_policy(log_dir):
    """切片策略：按小时滚动；app.log 保留 72 份、error.log 保留 168 份。"""
    setup_logging(log_dir=log_dir)

    app_h = _handler_of(log_dir, "app.log")
    err_h = _handler_of(log_dir, "error.log")
    assert isinstance(app_h, TimedRotatingFileHandler)
    assert app_h.when == "H", "全量日志应按小时滚动"
    assert app_h.backupCount == 72
    assert app_h.suffix == "%Y-%m-%d_%H"
    assert err_h.when == "H"
    assert err_h.backupCount == 168
    assert err_h.level == logging.ERROR, "error.log handler 只收 ERROR+"


def test_setup_logging_idempotent(log_dir):
    """重复调用不重复挂 handler，app.log 不双写。"""
    setup_logging(log_dir=log_dir)
    setup_logging(log_dir=log_dir)
    setup_logging(log_dir=log_dir)

    handlers = [h for h in logging.getLogger().handlers
                if isinstance(h, TimedRotatingFileHandler)
                and h.baseFilename == str((log_dir / "app.log").resolve())]
    assert len(handlers) == 1

    logging.getLogger("test.m101").info("idempotent-check")
    handlers[0].flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert content.count("idempotent-check") == 1


def test_uvicorn_access_silenced(log_dir):
    """uvicorn/uvicorn.error propagate=True；uvicorn.access 静默（裸访问行由 api.access 替代）。"""
    setup_logging(log_dir=log_dir)

    assert logging.getLogger("uvicorn").propagate is True
    assert logging.getLogger("uvicorn.error").propagate is True
    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False, "uvicorn.access 应静默，避免与 api.access 双份"

    logging.getLogger("uvicorn.access").info("bare-access-should-not-appear")
    logging.getLogger("uvicorn.error").info("uvicorn-error-merged")
    _handler_of(log_dir, "app.log").flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "bare-access-should-not-appear" not in content
    assert "uvicorn-error-merged" in content


def test_setup_logging_respects_level(log_dir):
    """LOG_LEVEL=ERROR 时 INFO 日志不落盘（级别经参数显式传入）。"""
    setup_logging(log_dir=log_dir, level="ERROR")

    logging.getLogger("test.m101").info("should-not-appear")
    logging.getLogger("test.m101").error("should-appear")

    h = _handler_of(log_dir, "app.log")
    h.flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "should-not-appear" not in content
    assert "should-appear" in content


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """用例前后清理 root logger 上本测试挂的 handler，不污染其他测试。"""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, TimedRotatingFileHandler):
            h.close()
            root.removeHandler(h)
