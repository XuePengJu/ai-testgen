"""后台任务运行记录模型（P0：调度任务抢占锁与运行审计）。

job_runs 用 (job_id, biz_date) 唯一约束做 DB 抢占锁：
插入冲突 = 当天已有实例在跑，后来者直接退出，避免多实例重复执行。
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


def _nid() -> str:
    """短随机主键（与会话/知识库 id 风格一致）。"""
    return uuid.uuid4().hex[:16]


class JobRun(Base):
    """一次后台任务运行（按 job_id + 业务日期唯一）。"""
    __tablename__ = "job_runs"
    # DB 抢占锁：同一任务同一业务日期只允许一条记录（显式命名跨方言一致，
    # 老库由 app/core/db.py 迁移幂等补建）
    __table_args__ = (
        Index("uq_job_runs_job_date", "job_id", "biz_date", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 业务日期 YYYY-MM-DD（按天唯一）
    biz_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    # running / success / failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # MySQL 不允许 TEXT 带 DEFAULT（1101），Python 层兜底
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
