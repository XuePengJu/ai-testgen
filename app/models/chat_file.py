"""聊天附件数据模型（P0：聊天文件与个人知识库打通）。

/api/files 上传的聊天附件落库后，可被「存入知识库」流程引用：
- conversation_id 为空 = 建会话失败时的孤儿附件（仍可通过 user_id 归属排查）
- knowledge_id 指向附件入库后的 knowledges 行（回落指针）
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


def _nid() -> str:
    """短随机主键（与会话/知识库 id 风格一致）。"""
    return uuid.uuid4().hex[:16]


class ChatAttachment(Base):
    """聊天附件：一次 /api/files 上传的文件记录（可关联会话与知识库文档）。"""
    __tablename__ = "chat_attachments"
    # file_id 唯一（对应 /api/files 返回的 file_id，防重复登记；显式命名跨方言一致，
    # 老库由 app/core/db.py 迁移幂等补建）
    __table_args__ = (
        Index("uq_chat_attachments_file_id", "file_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    # 会话关联；为空 = 建会话失败时的孤儿附件
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 上传者（User.id 为自增 Integer）
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 对应 /api/files 的 file_id
    file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # uploads/chat 下的原文件路径
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 附件入库后的 knowledges 行 id（回落指针；未入库为 NULL）
    knowledge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
