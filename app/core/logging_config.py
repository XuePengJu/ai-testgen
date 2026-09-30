"""运维日志配置（M10.1）：root logger 统一落盘 logs/，按小时切片 + ERROR 单独成档。

- logs/app.log：全量日志（INFO+），TimedRotatingFileHandler 按**小时**滚动，
  保留 72 份历史（3 天），滚动文件名形如 app.log.2026-09-28_22；
- logs/error.log：仅 ERROR 及以上，按小时滚动，保留 168 份（7 天）——
  排障时直接看这一个文件，不用在全量日志里翻；
- uvicorn 接管：uvicorn / uvicorn.error 沿层级 propagate 进 root 统一落盘；
  uvicorn.access 关闭（handler 清空 + propagate=False）——纯 method/path/status
  的裸访问行由 api.access 中间件（access_log.py）的带出入参版本替代，避免双份噪音；
- 幂等：root 上已挂同一个日志文件 handler 时直接返回，重复调用无副作用。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import LOG_LEVEL

# 日志格式与时间格式（团队约定，与单测断言保持一致）
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# uvicorn 主 logger 沿层级 propagate 进 root；access 由 api.access 中间件替代
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")

# 滚动策略：全量按小时 × 72 份（3 天）；错误按小时 × 168 份（7 天）
_ROTATE_WHEN = "H"
_APP_BACKUPS = 72
_ERROR_BACKUPS = 168


def _make_hourly_handler(path: Path, level: int, backups: int) -> TimedRotatingFileHandler:
    """创建按小时滚动的文件 handler（目录不存在则创建）。"""
    handler = TimedRotatingFileHandler(
        path, when=_ROTATE_WHEN, backupCount=backups, encoding="utf-8"
    )
    handler.suffix = "%Y-%m-%d_%H"  # 滚动文件名：app.log.2026-09-28_22
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    return handler


def _existing_file_handler(root: logging.Logger, log_dir: Path) -> TimedRotatingFileHandler | None:
    """检查 root 上是否已挂同一个 app.log 的文件 handler（幂等判定用，精确路径比对）。"""
    target = str((log_dir / "app.log").resolve())
    for h in root.handlers:
        if isinstance(h, TimedRotatingFileHandler) and h.baseFilename == target:
            return h
    return None


def setup_logging(log_dir: str | Path | None = None, level: str | None = None) -> None:
    """初始化应用日志（幂等，可在进程生命周期内重复调用）。

    Args:
        log_dir: 日志目录。缺省依次读环境变量 AITF_LOG_DIR（测试进程重定向用，
                 见根目录 conftest.py）、再回落项目根下 `logs/`；
                 单测可显式传临时目录隔离。
        level:   日志级别名（DEBUG/INFO/...），缺省读 LOG_LEVEL 环境变量，
                 再缺省 INFO。非法值回落 INFO。
    """
    level_name = (level or LOG_LEVEL or "INFO").strip().upper()
    numeric_level = getattr(logging, level_name, logging.INFO)

    log_path = Path(log_dir or os.environ.get("AITF_LOG_DIR", "logs"))
    root = logging.getLogger()  # root logger（不带名字参数）

    # 幂等：app.log 已挂 handler → 只同步级别，不再追加（避免测试/热重载双写）
    if _existing_file_handler(root, log_path):
        root.setLevel(numeric_level)
        return

    root.setLevel(numeric_level)
    log_path.mkdir(parents=True, exist_ok=True)
    root.addHandler(_make_hourly_handler(log_path / "app.log", logging.INFO, _APP_BACKUPS))
    # ERROR 单独成档：排障看 error.log 即可，不用翻全量
    root.addHandler(_make_hourly_handler(log_path / "error.log", logging.ERROR, _ERROR_BACKUPS))

    # uvicorn 接管：error 沿层级进 root 统一落盘；access 关闭——裸访问行
    # （只有 method/path/status）由 api.access 中间件的带出入参版本替代，避免双份噪音
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    access_lg = logging.getLogger("uvicorn.access")
    access_lg.handlers = []
    access_lg.propagate = False


def get_log_level() -> str:
    """返回当前生效的日志级别名（诊断/health 接口展示用）。"""
    return logging.getLevelName(logging.getLogger().getEffectiveLevel())
