"""会话/消息 Pydantic 响应模型。"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class MessageOut(BaseModel):
    id: int
    role: str
    content: str = ""
    thinking: str = ""
    # V4.5.2：RAG 引用溯源（assistant 消息持久化的 citations 列表，JSON 解析后返回）
    citations: Optional[list[dict[str, Any]]] = None
    task_id: Optional[str] = None
    created_at: Optional[datetime] = None
    # 关联任务摘要（仅详情接口填充）：含节点步骤 steps + 用例 cases
    task: Optional[dict[str, Any]] = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    message_count: int = 0
    task_count: int = 0
    messages: list[MessageOut] = []
    mode: Optional[str] = "workflow"   # V4.1：workflow=首页工作流；kb_qa=知识库问答
    kb_id: Optional[str] = None        # V4.1：知识库问答绑定的库

    class Config:
        from_attributes = True
