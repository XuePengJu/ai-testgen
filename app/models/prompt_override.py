"""用户级提示词自定义（V5.10 FR-AK：角色 pill 旁 ✎ 入口）。

- key 粒度：
    chat_role_{role}_{think|plain}   对话系统提示词（qa/pm/dev/kb × 2 变体，8 个）
    gen_tpl_{api|req}_{qa|pm|dev}    用例生成模板（api/requirement × 3 角色，6 个）
- 无记录 = 用内置默认（代码 / prompts 文件为源，**不进库**）；有记录即整段覆盖
- 独立新表：init_db 走 create_all 自动建，零迁移成本（同 llm_model_pool 约定）
- 删除记录 = 恢复默认
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class PromptOverride(Base):
    __tablename__ = "prompt_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_prompt_user_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
