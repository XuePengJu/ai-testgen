"""任务分类（多级树，用户隔离）数据模型。"""
from datetime import datetime

from app.core.utils import utcnow

from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Category(Base):
    """自定义任务分类节点，parent_id 构成树；user_id 隔离，各用户独立分类树。

    历史遗留列（功能已裁剪，保留以兼容存量库）：
    - target_id：早期站点节点关联的被测系统 id，新数据恒为 NULL
    - is_auto：早期页面自动派生节点标记，新数据恒为 False
    """
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
