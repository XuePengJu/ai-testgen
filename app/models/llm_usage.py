"""LLM 调用用量记录（V5.12 可观测性）：每一次真实模型调用落一行。

埋点位置：app/services/langchain_client.py 的 chat() / chat_stream() 出口
（LLMError 不经过 mock，测试替身不会写入本表）。

- user_id：发起调用的用户；0 = 平台级（连通测试、未带上下文的调用）
- slot：text / vision（embedding 走独立通道，暂不埋点）
- action：chat（一次性收集）/ stream（逐段流式）
- tokens 不落库（流式响应拿不到 usage），以 prompt_chars / completion_chars 粗估用量
- 写入走独立 Session、静默失败：任何异常都不得影响主调用
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    slot: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
