"""模型池模型（V5.0 P1）：每用户每槽位可配多条候选，撞限流自动切换。

设计要点（见 docs/项目1-模型池与思考控制执行方案-V1.0.md §4.2）：

- user_id：所属用户；**0 = 平台默认池**（与 llm_configs 同约定，admin 配置）
- slot：text / vision / embedding（三槽各自成池，不做跨槽优先级区分）
- key_fingerprint：sha256(api_key)[:16] —— 判重专用。crypto.encrypt_obj 用随机 nonce，
  同一 Key 加密两次结果不同（实测 a == b → False），唯一约束不能落在 api_key_enc 上
- 运行时健康状态（cooldown_until / last_error / success_count / fail_count）随行落库，
  避免多 worker 或重启后丢失熔断记忆
- 独立新表而非改 llm_configs：init_db 走 create_all，新表零迁移成本；且老表上已有的
  scalar_one_or_none() 查询在 (user_id, slot) 出现多行时会抛 MultipleResultsFound
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class LLMModelPool(Base):
    __tablename__ = "llm_model_pool"
    __table_args__ = (
        # 重复配置校验：归属 + 槽位 + 端点 + 模型 + Key 指纹全同才算重复
        UniqueConstraint(
            "user_id", "slot", "base_url", "model", "key_fingerprint",
            name="uq_pool_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    slot: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_tail: Mapped[str | None] = mapped_column(String(16), default="")
    key_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # 小 = 优先
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # 仅 UI 标注，不参与调度
    note: Mapped[str | None] = mapped_column(String(64), default="")             # 如「备用-免费」

    # —— 运行时健康状态 ——
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
