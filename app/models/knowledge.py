"""知识库数据模型（V4.0 RAG 知识库）。

7 张表，设计借鉴 WeKnora（MIT）：
- knowledge_bases     知识库（含权限：private 私有 / global 全局共享只读）
- knowledges          文档条目 + 状态机（parse_status / error_message 可见）
- chunks              分块（可编辑：content/source_content 双存 + 版本号 + 父子分块）
- chunk_revisions     分块编辑修订历史（diff / 回滚）
- wiki_pages          Wiki 页面（V4.1，表结构 V4.0 同步建齐）
- graph_entities      图谱实体（V4.1）
- graph_relationships 图谱关系（V4.1）

跨方言约定：JSON 类字段统一存 Text（MySQL 不允许 JSON 默认值、SQLite 行为差异大），
与 tasks.roles 的既有做法保持一致；读侧用 json.loads 解析。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


def _nid() -> str:
    """短随机主键（与会话 id 风格一致）。"""
    return uuid.uuid4().hex[:16]


class KnowledgeBase(Base):
    """知识库：文档与分块的容器，创建时设置权限。"""
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 创建者
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default="")
    # 权限：private（仅创建者可见可检索可管理）| global（全平台共享，只读）
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="document")  # document | wiki | faq
    embedding_model_id: Mapped[str | None] = mapped_column(String(64), default="")
    chunking_config: Mapped[str | None] = mapped_column(Text, default="{}")  # JSON 字符串
    extract_config: Mapped[str | None] = mapped_column(Text, default="{}")   # 图谱抽取配置
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Knowledge(Base):
    """文档条目：一个入库文件，状态机全程可见。"""
    __tablename__ = "knowledges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="document")
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str | None] = mapped_column(Text, default="")
    file_name: Mapped[str | None] = mapped_column(String(255), default="")
    file_type: Mapped[str | None] = mapped_column(String(50), default="")
    file_size: Mapped[int | None] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(Text, default="")
    file_hash: Mapped[str | None] = mapped_column(String(64), default="")  # 去重
    # 状态机：unprocessed → parsing → chunking → processing → ready | failed
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unprocessed")
    error_message: Mapped[str | None] = mapped_column(Text, default="")
    chunk_count: Mapped[int | None] = mapped_column(Integer, default=0)
    doc_meta: Mapped[str | None] = mapped_column(Text, default="{}")
    custom_meta: Mapped[str | None] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    wiki_summary: Mapped[str | None] = mapped_column(Text, default="")
    wiki_category: Mapped[str | None] = mapped_column(String(50), default="")


class Chunk(Base):
    """分块：检索单元，可被用户编辑（内容双存 + 修订历史 + 父子分块）。"""
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")          # 当前内容（可编辑）
    source_content: Mapped[str] = mapped_column(Text, default="")   # 解析原始内容（diff 基线）
    content_revision: Mapped[int] = mapped_column(Integer, default=0)  # 版本号
    context_header: Mapped[str | None] = mapped_column(Text, default="")  # 上下文标题（父级路径）
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64), default="")
    chunk_type: Mapped[str] = mapped_column(String(20), default="text")  # text | table | json
    start_at: Mapped[int | None] = mapped_column(Integer, default=0)
    end_at: Mapped[int | None] = mapped_column(Integer, default=0)
    relation_chunks: Mapped[str | None] = mapped_column(Text, default="[]")  # 图谱关联块 JSON
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # 软删除
    index_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class ChunkRevision(Base):
    """分块编辑修订历史：每次编辑存快照，可 diff、可回滚。"""
    __tablename__ = "chunk_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    edit_source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user | agent
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)


class WikiPage(Base):
    """Wiki 页面（V4.1：Agent 自动生成 + 手动编辑 + 修订历史）。"""
    __tablename__ = "wiki_pages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), default="")  # 页面树层级
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str | None] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class GraphEntity(Base):
    """图谱实体（V4.1：LLM 从文档块抽取，节点）。"""
    __tablename__ = "graph_entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), default="")
    description: Mapped[str | None] = mapped_column(Text, default="")
    frequency: Mapped[int | None] = mapped_column(Integer, default=0)
    degree: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)


class GraphRelationship(Base):
    """图谱关系（V4.1：实体间连线）。"""
    __tablename__ = "graph_relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_nid)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(100), default="")
    strength: Mapped[int | None] = mapped_column(Integer, default=0)  # 1-10
    description: Mapped[str | None] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
