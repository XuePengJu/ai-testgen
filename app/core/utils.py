"""时间工具：统一使用无时区的 UTC 时间（与 SQLite 存储的 naive datetime 一致）。"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """替代已弃用的 datetime.utcnow()：取 UTC 当前时间并去掉时区信息，
    保证与 SQLite 中存储的 naive datetime 可直接比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
