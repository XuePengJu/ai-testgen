"""任务与步骤日志的数据模型（SQLAlchemy）。"""
from datetime import datetime

from app.core.utils import utcnow

from sqlalchemy import String, Integer, Text, DateTime, Float, ForeignKey, Boolean
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 大文本列：默认（SQLite 等）仍为 TEXT；仅在 MySQL 方言用 LONGTEXT。
# 背景：MySQL 的 TEXT 上限 = 65,535 **字节**，utf8mb4 下中文每字 3 字节 → 4 万余字符的用例
# JSON ≈ 130KB，直接溢出（DataError 1406 "Data too long"）。partial_cases_json（过程态快照）
# 曾因此把整个生成任务拖成 failed；cases_json（最终产物）/ report_json 同样有溢出风险，
# 故三列统一升级。SQLite 侧 with_variant 不生效、仍是 TEXT，不影响现有测试。
_BIGTEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")


class Task(Base):
    """一次测试用例生成任务。"""
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="未命名任务")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="file")
    input_ref: Mapped[str | None] = mapped_column(Text, default="")
    formats: Mapped[str | None] = mapped_column(String(64), default="xlsx,json")
    roles: Mapped[str | None] = mapped_column(Text, default='["qa"]')  # 多角色协作（V3.1）：pm/qa/dev JSON 数组
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # 用例库资产化（V5.5）：评审状态 draft=草稿 / reviewed=已评审。
    # 与「生成过程状态」status（pending/running/…）分离：库页只看资产状态，过程状态留在会话流。
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 历史遗留列：早期「全链路/探索式」任务的被测系统外键。功能已裁剪，
    # 仅保留列以兼容存量库（新任务恒为 NULL）
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    input_summary: Mapped[str | None] = mapped_column(Text, default="")
    cases_count: Mapped[int | None] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float | None] = mapped_column(Float, default=0.0)
    cases_json: Mapped[str | None] = mapped_column(_BIGTEXT, default="[]")
    # V1.3 生成过程可见性：running 期每完成一个子任务就累积写入的「已产出用例」快照。
    # 与 cases_json 的区别：cases_json 只在整条工作流收尾时一次性写入（最终产物，含统一编号）；
    # 本列是过程态预览，收尾成功后清空。列表接口不返回，避免拖慢列表。
    partial_cases_json: Mapped[str | None] = mapped_column(_BIGTEXT, nullable=True)
    report_json: Mapped[str | None] = mapped_column(_BIGTEXT, default="{}")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    def user_data_dir(self, db) -> str:
        """任务归属用户的文件目录；无归属（存量迁移前）返回平铺目录。"""
        if self.user_id:
            from app.models.user import User
            u = db.get(User, self.user_id)
            if u and u.data_dir:
                return u.data_dir
        return ""


class StepLog(Base):
    """工作流中每个 Agent 步骤的执行日志（可观测）。"""
    __tablename__ = "step_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[str | None] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    input_summary: Mapped[str | None] = mapped_column(Text, default="")
    output_summary: Mapped[str | None] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, default="")
