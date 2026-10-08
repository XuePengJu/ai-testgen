"""条目级记忆数据模型（V7.0 数据地基）。

分层铁律（写进代码注释，防止后人越层）：
- 本文件只定义表结构（models 层零业务逻辑）
- memory_items 的所有行变更唯一写入口：app/services/memory/items.py
- 只读检索：app/api/memory.py（V7.2 后迁往 services/memory/retrieve.py）
- 遗忘策略（TTL/重要性/过期）：services/memory/forget.py（V7.1）
- api 层只做鉴权与参数校验；jobs 层只做编排

跨方言铁律（对齐 db.py 既有注释，SQLite / MySQL 双方言）：
- TEXT 列一律**不带 DEFAULT**（MySQL 1101），空值由 Python 层兜底
- 不用 Boolean；后续如需布尔列必须 TINYINT(1) NOT NULL DEFAULT 0
- DateTime 全 naive UTC（app.core.utils.utcnow）
- dedupe_key 用 md5 hex(32)，不做长文本索引（utf8mb4 索引 3072 bytes 上限）
- 「部分唯一」实现 active 位独占：uq_memory_items_active(user_id, dedupe_key)
  UNIQUE —— superseded/expired/conflict 状态的行把 dedupe_key 置 NULL 让出
  active 位（MySQL/SQLite 唯一索引都允许多个 NULL，天然部分唯一）
"""
from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


def _nid() -> str:
    """短随机主键（与会话/知识库 id 风格一致，16 位 hex）。"""
    import uuid
    return uuid.uuid4().hex[:16]


class MemoryItem(Base):
    """一条结构化记忆条目（事实/偏好/规则/待办/画像）。

    与文档级记忆（Knowledge source_key=mem:conv:*）并行双写：文档链路保证
    人可读的滚动摘要，条目链路承载置信度 / TTL / 冲突 / 溯源等元数据。

    版本链：同一语义槽位的多次更新形成 root_id → prev_id → superseded_by
    链，旧版本永不物理覆盖删除（可回溯审计），active 位永远只有最新一条。
    """
    __tablename__ = "memory_items"
    # 显式命名索引（跨方言一致；老库由 app/core/db.py 迁移幂等补建）
    __table_args__ = (
        # active 位独占：同一用户同一 dedupe_key 只允许一条非 NULL 行
        Index("uq_memory_items_active", "user_id", "dedupe_key", unique=True),
        Index("idx_memory_items_user_status", "user_id", "status"),
        Index("idx_memory_items_user_kind", "user_id", "kind"),
        Index("idx_memory_items_status_expires", "status", "expires_at"),
        Index("idx_memory_items_conv", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # fact（事实）/ preference（偏好）/ rule（规则）/ todo（待办）/ profile（画像）
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="fact")
    # 主题短语（语义槽位名，冲突裁决按 subject 对齐；近重复判定用 Jaccard）
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # md5 hex(32)：= md5(user_id + 归一化 subject)；非 active 状态置 NULL 让位
    dedupe_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 条目正文（≤200 字，Python 层截断）
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 原文片段（来源消息的 evidence 摘录，前端溯源展示用；TEXT 无 DEFAULT）
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # conversation（会话抽取）/ user（用户手动写入）/ file（文档提取）
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="conversation")
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 置信度 [0,1]：Python 侧按来源分档硬夹（LLM 自报不可信）
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # 重要性 [0,1]：V7.1 initial_importance 公式计算；本批先建列免二次 ALTER
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # active / superseded / conflict / expired / deleted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # ---- 版本链（旧版本永不覆盖删除）----
    root_id: Mapped[str | None] = mapped_column(String(64), nullable=True)      # 链头（首个版本）
    prev_id: Mapped[str | None] = mapped_column(String(64), nullable=True)      # 上一版本
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 取代我的新版本
    conflict_with: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 与哪条 active 冲突
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # ---- TTL / 重要性（V7.1 用，本批先建好免二次 ALTER）----
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    half_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 扩展元数据 JSON 文本（prov 来源分档等；MySQL 不允许 TEXT 带 DEFAULT）
    item_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoryAudit(Base):
    """记忆条目变更审计（append-only，谁在何时把条目从什么改成什么）。

    所有 memory_items 行变更（创建/刷新/取代/挂起/过期/删除/采纳）都必须
    落一条审计；hard_delete 物理删条目时审计保留（隐私追溯的法律底线）。
    """
    __tablename__ = "memory_audits"
    __table_args__ = (
        Index("idx_memory_audits_item", "item_id"),
        Index("idx_memory_audits_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # created / refreshed / superseded / conflict / expired / deleted / adopted / restored
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    # system（系统内联逻辑）/ user（用户手动）/ job（离线任务）
    actor: Mapped[str] = mapped_column(String(16), nullable=False, default="job")
    # 变更前后快照 JSON 文本（TEXT 无 DEFAULT，Python 层兜底 None）
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)


class MemoryRetrievalLog(Base):
    """记忆检索埋点（V7.3 地基）：_build_rag_context 出口采样一行。

    写入走独立 Session + 采样率 AITF_MEMORY_LOG_SAMPLE + 任何异常静默失败
    （照 llm_usage.py 范式）；V7.3 评估按本表算 hit_rate / latency 分位 /
    条目命中占比。跨方言铁律同上：hit_ids 为 TEXT 无 DEFAULT。
    """
    __tablename__ = "memory_retrieval_logs"
    __table_args__ = (
        Index("idx_mem_rlog_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    query: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    hits_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 命中 chunk_id 前 10 个的 JSON 数组文本（TEXT 无 DEFAULT，Python 层兜底）
    hit_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)


class MemoryEvalRun(Base):
    """一次记忆评估套件运行结果（V7.3：golden_set 五指标落库，按天一条）。

    (biz_date) 唯一约束做幂等：同一天重复跑评估覆盖更新而非堆行。
    """
    __tablename__ = "memory_eval_runs"
    __table_args__ = (
        Index("uq_memory_eval_biz_date", "biz_date", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    biz_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 五指标 JSON 文本（TEXT 无 DEFAULT，Python 层兜底）
    metrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
