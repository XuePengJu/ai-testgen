"""LLM 配置模型（V2.4 FR-I）：每用户双槽位（text / vision）+ 平台默认。

- user_id：所属用户；**0 = 平台默认**（admin 配置，未配置个人模型的用户兜底）
- slot：text（默认文本模型）/ vision（图像识别模型，可选）
- api_key：AES-256-GCM 加密落库（密钥由 JWT_SECRET + owner 派生），永不明文、不回显全文
"""
from datetime import datetime

from app.core.utils import utcnow

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LLMConfig(Base):
    __tablename__ = "llm_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_llm_user_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_tail: Mapped[str | None] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
