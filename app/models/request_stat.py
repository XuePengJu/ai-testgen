"""HTTP 请求量统计（V5.12 可观测性）：access_log 中间件内存聚合的落库形态。

- 一行 = 某一分钟(bucket) × 归一路径 × 方法 × 状态码 的请求数与总耗时
- 归一路径：/api/tasks/{id} → /api/tasks/*（防路径爆炸），保留前 3 段
- 由 access_log.start_stats_flusher() 后台线程周期 flush；高频轮询接口同样计数
  （INFO 日志里轮询降噪不记，但统计不能漏）
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class RequestStat(Base):
    __tablename__ = "request_stats"
    __table_args__ = (
        UniqueConstraint("bucket", "path", "method", "status", name="uq_request_stat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # YYYYMMDDHHMM
    path: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
