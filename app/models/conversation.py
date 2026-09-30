"""会话与消息数据模型（对话记录持久化）。

会话（conversation）承载首页的多轮对话；消息（message）存每条 user/assistant
发言，assistant 消息可关联一个生成任务（task_id），聊天流回放时按 task_id 实时拉
该任务的节点步骤（StepLog）与用例（Task.cases_json），避免冗余存储。
"""
from datetime import datetime

from app.core.utils import utcnow

from sqlalchemy import String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Conversation(Base):
    """一次对话会话（所有对话都落库，跨刷新可回放）。"""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), default="新会话")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # V4.1：会话模式与知识库归属——kb_qa 会话只在知识库页显示，与首页工作流隔离
    mode: Mapped[str | None] = mapped_column(String(16), default="workflow")
    kb_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Message(Base):
    """会话中的一条消息（role: user / assistant）。"""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, default="")
    thinking: Mapped[str | None] = mapped_column(Text, default="")
    # V4.5.2：RAG 引用溯源持久化（SSE citations 事件 items 的 JSON 文本），
    # 切换会话/刷新后历史消息仍可展示引用来源
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
