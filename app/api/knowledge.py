"""知识库管理 API（V4.0 RAG 知识库）。

模块：知识库 CRUD（权限 private/global）· 文档上传/列表/详情/更新/删除/重建索引 ·
分块查看/编辑/修订/回滚 · 检索测试台。

权限模型：
- 读（列表/详情/分块/检索）：global 全平台可见；private 仅创建者；admin 全可见
- 写（建库/传文档/编辑/删库/改权限）：创建者或 admin；global 库对非创建者只读
- 建库：注册用户（user/admin），访客 403
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_user
from app.core.config import UPLOAD_DIR
from app.core.db import get_db
from app.models.knowledge import Chunk, ChunkRevision, Knowledge, KnowledgeBase
from app.models.user import User
from app.services.doc_extract import ExtractError, UnsupportedFormatError, extract_text, is_supported, supported_hint
from app.services.knowledge import vectorstore
from app.services.knowledge.classify import normalize_category, parse_llm_category
from app.services import llm_service
from src.utils import jsonx
from app.services.knowledge.ingest import (
    IngestError,
    delete_document_vectors,
    doc_summary,
    extract_summary_text,
    ingest_document,
    visible_kb_ids,
)

logger = logging.getLogger("api.knowledge")
router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 单文件 10MB
KB_FILE_DIR = UPLOAD_DIR / "knowledge"


# ---------------- 辅助 ----------------

def _get_kb(db: Session, kb_id: str) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or kb.deleted_at:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def _can_read(user: User, kb: KnowledgeBase) -> bool:
    return user.role == "admin" or kb.visibility == "global" or kb.user_id == user.id


def _can_manage(user: User, kb: KnowledgeBase) -> bool:
    return user.role == "admin" or kb.user_id == user.id


def _ensure_manage(user: User, kb: KnowledgeBase) -> None:
    if not _can_manage(user, kb):
        raise HTTPException(status_code=403, detail="仅创建者或管理员可管理该知识库")


def _get_doc(db: Session, doc_id: str) -> Knowledge:
    doc = db.get(Knowledge, doc_id)
    if not doc or doc.deleted_at:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


def _doc_stats(db: Session, kb_ids: list[str]) -> dict[str, dict]:
    """每个知识库的文档数与分块数。"""
    out: dict[str, dict] = {}
    for kb_id in kb_ids:
        out[kb_id] = {"doc_count": 0, "chunk_count": 0}
    if not kb_ids:
        return out
    rows = db.execute(
        select(Knowledge.knowledge_base_id, func.count())
        .where(Knowledge.knowledge_base_id.in_(kb_ids), Knowledge.deleted_at.is_(None))
        .group_by(Knowledge.knowledge_base_id)
    ).all()
    for kb_id, n in rows:
        out[kb_id]["doc_count"] = n
    rows = db.execute(
        select(Chunk.knowledge_base_id, func.count())
        .where(Chunk.knowledge_base_id.in_(kb_ids))
        .group_by(Chunk.knowledge_base_id)
    ).all()
    for kb_id, n in rows:
        out[kb_id]["chunk_count"] = n
    return out


def _kb_json(kb: KnowledgeBase, stats: dict) -> dict:
    s = stats.get(kb.id, {"doc_count": 0, "chunk_count": 0})
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description or "",
        "visibility": kb.visibility,
        "type": kb.type,
        "user_id": kb.user_id,
        "doc_count": s["doc_count"],
        "chunk_count": s["chunk_count"],
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


# ---------------- 知识库 CRUD ----------------

class KBCreate(BaseModel):
    name: str
    description: str = ""
    visibility: str = "private"   # private | global
    type: str = "document"        # document | wiki | faq


class KBUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


@router.get("/knowledge/bases")
async def list_bases(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前用户可见的知识库列表（含统计）。"""
    if user.role == "admin":
        rows = db.execute(
            select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None))
            .order_by(KnowledgeBase.created_at.desc())
        ).scalars().all()
    else:
        rows = db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.deleted_at.is_(None),
                (KnowledgeBase.visibility == "global") | (KnowledgeBase.user_id == user.id),
            ).order_by(KnowledgeBase.created_at.desc())
        ).scalars().all()
    stats = _doc_stats(db, [kb.id for kb in rows])
    return {"items": [_kb_json(kb, stats) for kb in rows]}


@router.post("/knowledge/bases", status_code=201)
async def create_base(
    body: KBCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """创建知识库（创建者私有 / 全局共享只读）。"""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    visibility = body.visibility if body.visibility in ("private", "global") else "private"
    kb = KnowledgeBase(
        user_id=user.id,
        name=name[:255],
        description=(body.description or "")[:5000],
        visibility=visibility,
        type=body.type if body.type in ("document", "wiki", "faq") else "document",
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _kb_json(kb, _doc_stats(db, [kb.id]))


@router.patch("/knowledge/bases/{kb_id}")
async def update_base(
    kb_id: str,
    body: KBUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """改名/描述/权限。仅创建者或 admin；改权限时同步后续检索口径（visible_kb_ids 即时生效）。"""
    kb = _get_kb(db, kb_id)
    _ensure_manage(user, kb)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="知识库名称不能为空")
        kb.name = name[:255]
    if body.description is not None:
        kb.description = body.description[:5000]
    if body.visibility is not None:
        if body.visibility not in ("private", "global"):
            raise HTTPException(status_code=400, detail="visibility 仅支持 private/global")
        kb.visibility = body.visibility
    db.commit()
    db.refresh(kb)
    return _kb_json(kb, _doc_stats(db, [kb.id]))


@router.delete("/knowledge/bases/{kb_id}")
async def delete_base(
    kb_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除知识库（软删除 + 清全部向量）。仅创建者或 admin。"""
    kb = _get_kb(db, kb_id)
    _ensure_manage(user, kb)
    kb.deleted_at = _now()
    for doc in db.execute(
        select(Knowledge).where(Knowledge.knowledge_base_id == kb_id, Knowledge.deleted_at.is_(None))
    ).scalars():
        doc.deleted_at = _now()
    db.commit()
    vectorstore.delete_kb_vectors(kb_id)
    return {"ok": True}


# ---------------- 文档管理 ----------------

@router.post("/knowledge/bases/{kb_id}/documents", status_code=201)
async def upload_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """上传文档并立即入库（解析 → 分块 → 向量化，状态机全程可见）。"""
    kb = _get_kb(db, kb_id)
    _ensure_manage(user, kb)
    name = file.filename or ""
    if not is_supported(name):
        raise HTTPException(status_code=400, detail=f"暂不支持该格式，请上传 {supported_hint()} 文件")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件过大，单个文档上限 10MB")

    KB_FILE_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4().hex[:16]
    ext = Path(name).suffix.lower()
    raw_path = KB_FILE_DIR / kb_id / f"{doc_id}{ext}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)

    # 创建文档记录（状态机起点 unprocessed）
    doc = Knowledge(
        user_id=user.id,
        knowledge_base_id=kb.id,
        type="document",
        title=Path(name).stem[:255],
        file_name=name[:255],
        file_type=ext.lstrip("."),
        file_size=len(raw),
        file_path=str(raw_path),
        file_hash=hashlib_hex(raw),
        parse_status="unprocessed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 抽取 + 入库；失败由 ingest 置 failed，error_message 可见
    try:
        text = extract_text(raw_path)
    except (UnsupportedFormatError, ExtractError, OSError, ValueError) as e:
        doc.parse_status = "failed"
        doc.error_message = f"文档解析失败：{e}"[:2000]
        db.commit()
        raise HTTPException(status_code=400, detail=f"文档解析失败：{e}")
    try:
        ingest_document(db, kb, doc, text)
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # M7 归类时机前移：入库成功后后台自动生成摘要+分类（不阻塞上传响应）
    background_tasks.add_task(_bg_summarize_doc, kb.id, doc.id, user.id)
    return doc_summary(doc)


class TextDocCreate(BaseModel):
    """在线新建文本文档：标题 + 正文，直接分块入库（无需上传文件）。"""
    title: str
    content: str


@router.post("/knowledge/bases/{kb_id}/documents/text", status_code=201)
async def create_text_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    body: TextDocCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """在线新建文档：输入标题+正文，直接抽取→分块→向量化入库。"""
    kb = _get_kb(db, kb_id)
    _ensure_manage(user, kb)
    title = (body.title or "").strip()
    text = (body.content or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if not text:
        raise HTTPException(status_code=400, detail="正文不能为空")
    raw = text.encode("utf-8")
    doc = Knowledge(
        user_id=user.id,
        knowledge_base_id=kb.id,
        type="document",
        title=title[:255],
        file_name=(title[:250] + ".txt"),
        file_type="txt",
        file_size=len(raw),
        file_path="",
        file_hash=hashlib_hex(raw),
        parse_status="unprocessed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    try:
        ingest_document(db, kb, doc, text)
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # M7 归类时机前移：同上传文档，后台自动摘要+分类
    background_tasks.add_task(_bg_summarize_doc, kb.id, doc.id, user.id)
    return doc_summary(doc)


@router.get("/knowledge/bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """文档列表（含状态机与分块数）。"""
    kb = _get_kb(db, kb_id)
    if not _can_read(user, kb):
        raise HTTPException(status_code=403, detail="无权查看该知识库")
    docs = db.execute(
        select(Knowledge).where(
            Knowledge.knowledge_base_id == kb_id, Knowledge.deleted_at.is_(None)
        ).order_by(Knowledge.created_at.desc())
    ).scalars().all()
    return {"items": [doc_summary(d) for d in docs]}


@router.get("/knowledge/documents/{doc_id}")
async def get_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """文档详情：概要 + 分块列表（内容可审、可干预）。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    if not _can_read(user, kb):
        raise HTTPException(status_code=403, detail="无权查看该文档")
    chunks = db.execute(
        select(Chunk).where(Chunk.knowledge_id == doc_id, Chunk.deleted_at.is_(None))
        .order_by(Chunk.chunk_index)
    ).scalars().all()
    return {
        **doc_summary(doc),
        "chunks": [{
            "id": c.id,
            "chunk_index": c.chunk_index,
            "content": c.content,
            "context_header": c.context_header or "",
            "chunk_type": c.chunk_type,
            "content_revision": c.content_revision,
            "is_enabled": c.is_enabled,
        } for c in chunks],
    }


class DocUpdate(BaseModel):
    """手动更新文档内容：全文替换 → 重建分块与向量（旧版本留档在 revision 基线）。"""

    text: str


@router.put("/knowledge/documents/{doc_id}")
async def update_document(
    doc_id: str,
    body: DocUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """手动更新文档（全文替换，重建索引）。仅创建者或 admin。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文档内容不能为空")
    # 清旧分块与向量 → 重建（Chunk 物理删除，revision 基线保留可追溯）
    delete_document_vectors(db, doc_id)
    try:
        ingest_document(db, kb, doc, text)
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return doc_summary(doc)


class DocMetaUpdate(BaseModel):
    """自定义元数据字段：[{key, value}, ...]。"""
    fields: list[dict]


@router.put("/knowledge/documents/{doc_id}/meta")
async def update_doc_meta(
    doc_id: str,
    body: DocMetaUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """更新文档自定义元数据。仅创建者或 admin。"""
    import json
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    data = {}
    for f in body.fields or []:
        k = str(f.get("key") or "").strip()
        if k:
            data[k] = str(f.get("value") or "").strip()
    doc.custom_meta = json.dumps(data, ensure_ascii=False)
    db.commit()
    return doc_summary(doc)


class DocSummaryUpdate(BaseModel):
    summary: str


@router.put("/knowledge/documents/{doc_id}/summary")
async def update_doc_summary(
    doc_id: str,
    body: DocSummaryUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """手动编辑文档摘要。仅创建者或 admin。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    doc.wiki_summary = (body.summary or "").strip()
    db.commit()
    return doc_summary(doc)


@router.post("/knowledge/documents/{doc_id}/summary")
async def regen_doc_summary(
    doc_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """用当前文本模型重新生成单文档摘要。仅创建者或 admin。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    chunks = db.query(Chunk).filter(
        Chunk.knowledge_id == doc.id, Chunk.deleted_at.is_(None)
    ).order_by(Chunk.chunk_index).all()
    if not chunks:
        raise HTTPException(status_code=400, detail="文档暂无分块，无法生成摘要")
    text = "\n".join((c.content or "")[:500] for c in chunks[:10])
    ai = await _llm_summarize(db, user, text, doc.title)
    # 兜底清洗：LLM 可能返回 {"summary": "..."} 形状的 JSON 字符串，入库前剥壳
    doc.wiki_summary = extract_summary_text(ai.get("summary", ""))
    doc.wiki_category = normalize_category(ai.get("category"), fallback=doc.wiki_category or "未分类")
    db.commit()
    return doc_summary(doc)


@router.post("/knowledge/documents/{doc_id}/reindex")
async def reindex_document(
    doc_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """重建索引：用原始文件重新抽取 → 分块 → 向量化（修复 failed / 换模型后重跑）。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    if not doc.file_path or not Path(doc.file_path).exists():
        raise HTTPException(status_code=400, detail="原始文件已丢失，无法重建，请重新上传")
    try:
        text = extract_text(Path(doc.file_path))
    except (UnsupportedFormatError, ExtractError, OSError, ValueError) as e:
        doc.parse_status = "failed"
        doc.error_message = f"文档解析失败：{e}"[:2000]
        db.commit()
        raise HTTPException(status_code=400, detail=f"文档解析失败：{e}")
    delete_document_vectors(db, doc_id)
    try:
        ingest_document(db, kb, doc, text)
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return doc_summary(doc)


@router.delete("/knowledge/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除文档（软删除记录 + 清分块与向量）。仅创建者或 admin。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    _ensure_manage(user, kb)
    delete_document_vectors(db, doc_id)
    doc.deleted_at = _now()
    db.commit()
    return {"ok": True}


# ---------------- 分块编辑（可审可干预） ----------------

class ChunkUpdate(BaseModel):
    content: str


@router.get("/knowledge/documents/{doc_id}/chunks")
async def list_chunks(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分块列表（同 get_document 的 chunks，独立端点供前端复用）。"""
    doc = _get_doc(db, doc_id)
    kb = _get_kb(db, doc.knowledge_base_id)
    if not _can_read(user, kb):
        raise HTTPException(status_code=403, detail="无权查看该文档")
    chunks = db.execute(
        select(Chunk).where(Chunk.knowledge_id == doc_id, Chunk.deleted_at.is_(None))
        .order_by(Chunk.chunk_index)
    ).scalars().all()
    return {"items": [{
        "id": c.id, "chunk_index": c.chunk_index, "content": c.content,
        "context_header": c.context_header or "", "chunk_type": c.chunk_type,
        "content_revision": c.content_revision, "is_enabled": c.is_enabled,
    } for c in chunks]}


@router.put("/knowledge/chunks/{chunk_id}")
async def update_chunk(
    chunk_id: str,
    body: ChunkUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """编辑分块：旧内容入 revision 留档 → 更新内容 → 同步向量。仅创建者或 admin。"""
    chunk = db.get(Chunk, chunk_id)
    if not chunk or chunk.deleted_at:
        raise HTTPException(status_code=404, detail="分块不存在")
    kb = _get_kb(db, chunk.knowledge_base_id)
    _ensure_manage(user, kb)
    new_content = (body.content or "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="分块内容不能为空")

    # 注入 embedding 配置（库创建者个人配置 > 平台 > env > mock；同步向量与入库同一模型）
    from app.services import llm_service
    vectorstore.configure_embedding(llm_service.resolve_embedding(db, kb.user_id).get("cfg"))

    # 修订留档（保存旧版本快照）
    db.add(ChunkRevision(
        user_id=user.id,
        knowledge_base_id=chunk.knowledge_base_id,
        knowledge_id=chunk.knowledge_id,
        chunk_id=chunk.id,
        revision=chunk.content_revision,
        content=chunk.content,
        edit_source="user",
    ))
    chunk.source_content = chunk.content  # diff 基线同步为旧内容
    chunk.content = new_content
    chunk.content_revision += 1
    db.commit()

    # 同步向量（保持检索用最新内容）
    doc = db.get(Knowledge, chunk.knowledge_id)
    vectorstore.update_chunk_vectors(
        chunk.id, new_content,
        metadata={
            "kb_id": chunk.knowledge_base_id,
            "knowledge_id": chunk.knowledge_id,
            "user_id": chunk.user_id,
            "visibility": kb.visibility,
            "chunk_index": chunk.chunk_index,
            "file_name": (doc.file_name if doc and doc.file_name else "")[:200],
        },
    )
    return {"id": chunk.id, "content": chunk.content, "content_revision": chunk.content_revision}


@router.get("/knowledge/chunks/{chunk_id}/revisions")
async def chunk_revisions(
    chunk_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分块修订历史（每次编辑留档，可 diff）。"""
    chunk = db.get(Chunk, chunk_id)
    if not chunk or chunk.deleted_at:
        raise HTTPException(status_code=404, detail="分块不存在")
    kb = _get_kb(db, chunk.knowledge_base_id)
    if not _can_read(user, kb):
        raise HTTPException(status_code=403, detail="无权查看")
    revs = db.execute(
        select(ChunkRevision).where(ChunkRevision.chunk_id == chunk_id)
        .order_by(ChunkRevision.revision.desc())
    ).scalars().all()
    return {"items": [{
        "revision": r.revision, "content": r.content, "edit_source": r.edit_source,
        "edited_at": r.edited_at.isoformat() if r.edited_at else None,
    } for r in revs]}


class RollbackBody(BaseModel):
    revision: int


@router.post("/knowledge/chunks/{chunk_id}/rollback")
async def rollback_chunk(
    chunk_id: str,
    body: RollbackBody,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """回滚到指定修订版：内容恢复 + 新增一条回滚修订 + 同步向量。"""
    chunk = db.get(Chunk, chunk_id)
    if not chunk or chunk.deleted_at:
        raise HTTPException(status_code=404, detail="分块不存在")
    kb = _get_kb(db, chunk.knowledge_base_id)
    _ensure_manage(user, kb)
    rev = db.execute(
        select(ChunkRevision).where(
            ChunkRevision.chunk_id == chunk_id, ChunkRevision.revision == body.revision
        )
    ).scalar_one_or_none()
    if not rev:
        raise HTTPException(status_code=404, detail="修订记录不存在")
    # 注入 embedding 配置（库创建者视角，与入库同一模型）
    from app.services import llm_service
    vectorstore.configure_embedding(llm_service.resolve_embedding(db, kb.user_id).get("cfg"))
    # 当前内容入档，再恢复目标版本
    db.add(ChunkRevision(
        user_id=user.id,
        knowledge_base_id=chunk.knowledge_base_id,
        knowledge_id=chunk.knowledge_id,
        chunk_id=chunk.id,
        revision=chunk.content_revision,
        content=chunk.content,
        edit_source="user",
    ))
    chunk.source_content = chunk.content
    chunk.content = rev.content
    chunk.content_revision += 1
    db.commit()
    vectorstore.update_chunk_vectors(chunk.id, chunk.content)
    return {"id": chunk.id, "content": chunk.content, "content_revision": chunk.content_revision}


# ---------------- 检索测试台 ----------------

@router.get("/knowledge/search")
async def search_knowledge(
    q: str = Query(..., description="检索问题"),
    kb_id: str | None = Query(None, description="限定知识库"),
    top_k: int = Query(6, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """RAG 检索测试台：按当前用户可见范围检索，返回分块 + 相似度。"""
    from app.services import llm_service
    vectorstore.configure_embedding(llm_service.resolve_embedding(db, user.id).get("cfg"))
    if not (q or "").strip():
        raise HTTPException(status_code=400, detail="检索内容不能为空")
    vids = visible_kb_ids(db, user.id, admin=user.role == "admin")
    if kb_id and kb_id not in vids:
        raise HTTPException(status_code=403, detail="无权检索该知识库")
    hits = vectorstore.search(q, vids if not kb_id else [kb_id], top_k=top_k)
    return {"items": hits, "kb_scope": vids}


# ---------------- 内部小工具 ----------------

def _now():
    from app.core.utils import utcnow
    return utcnow()


def hashlib_hex(raw: bytes) -> str:
    import hashlib
    return hashlib.md5(raw).hexdigest()


# ---------------- Wiki 索引（AI 自动摘要） ----------------

async def _llm_summarize(db: Session, user: User, text: str, doc_title: str) -> dict:
    """调 LLM 对文档生成 Wiki 摘要 + 分类（非流式）。"""
    from openai import AsyncOpenAI
    eff = llm_service.resolve_effective(db, user)
    cfg = eff.get("text")
    if not cfg:
        return {"summary": "", "category": "未分类"}
    client = AsyncOpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        timeout=30.0,
    )
    prompt = f"""请为以下文档生成 Wiki 摘要和主题分类。

文档标题：{doc_title}

文档内容（节选）：
{text[:3000]}

请按以下 JSON 格式输出（不要有其他解释）：
{{"summary": "150字以内摘要，概括核心知识点和适用场景", "category": "主题分类（2-6字，如：测试基础/工具配置/开发实践/网络协议）"}}"""
    # max_tokens 须给思考链留预算：deepseek 等思考模型的 reasoning token 计入
    # max_tokens，400 时思考未完回答即被截断 → JSON 断半截 → 摘要静默为空（09-29 实锤）
    max_tokens = 1200
    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=cfg["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            # 提取 JSON：改用 jsonx 配对解析。旧写法 r'\{[^}]+\}' 遇嵌套对象会截断成
            # 非法 JSON（如 {"summary":{"k":1}}）→ 摘要静默退化为全文，用户看不出失败。
            data = jsonx.find_dict(content) or {}
            if isinstance(data, dict) and ("summary" in data or "category" in data):
                return {"summary": data.get("summary", ""), "category": normalize_category(data.get("category"))}
            # 输出被截断（finish=length）且首轮：预算翻倍重试一次
            if choice.finish_reason == "length" and attempt == 0:
                max_tokens *= 2
                continue
            return {"summary": content, "category": "未分类"}
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                max_tokens *= 2
                continue
            return {"summary": f"（摘要生成失败：{e}）", "category": "未分类"}


def _doc_chunk_text(db: Session, doc: Knowledge, max_chunks: int = 10, per_chunk: int = 500) -> str:
    """取文档前 N 个分块内容拼接（摘要/分类共用取样口径）。"""
    chunks = db.query(Chunk).filter(
        Chunk.knowledge_id == doc.id, Chunk.deleted_at.is_(None)
    ).order_by(Chunk.chunk_index).all()
    return "\n".join((c.content or "")[:per_chunk] for c in chunks[:max_chunks])


async def _llm_classify(db: Session, user: User, text: str, doc_title: str) -> str:
    """轻量独立分类调用（一键修复用）：只输出分类标签。

    与摘要合并调用的取舍：一键修复面对的是「已有摘要、缺分类」的存量文档，
    重跑摘要既浪费 token 又可能改掉用户认可的摘要文本；这里用短 prompt +
    max_tokens=256 的独立轻量调用（输出仅 2-6 字标签，256 主要给思考模型
    的 reasoning 留预算）。
    """
    from openai import AsyncOpenAI
    eff = llm_service.resolve_effective(db, user)
    cfg = eff.get("text")
    if not cfg:
        return DEFAULT_UNCLASSIFIED
    client = AsyncOpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=30.0)
    prompt = f"""给以下文档打一个主题分类标签。

文档标题：{doc_title}

文档内容（节选）：
{text[:2000]}

要求：直接输出 2-6 字的分类标签（如：测试基础/工具配置/开发实践），不要解释，不要标点。"""
    try:
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": prompt}],
            # 思考模型的 reasoning token 计入 max_tokens，16 连思考都装不下必截断
            max_tokens=256,
            temperature=0.1,
        )
        return parse_llm_category(resp.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("轻量分类调用失败 doc=%s: %s", doc_title, e)
        return DEFAULT_UNCLASSIFIED


DEFAULT_UNCLASSIFIED = "未分类"


async def _bg_summarize_doc(kb_id: str, doc_id: str, user_id: int) -> None:
    """后台自动摘要+分类（M7 归类时机前移）。

    上传/新建文档入库成功后触发；用独立 SessionLocal（请求级 session 在
    响应返回后已关闭）。失败只打日志，不影响文档可用性——用户随时可以
    手动「重新生成 Wiki 索引」兜底。
    """
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        kb = db.get(KnowledgeBase, kb_id)
        doc = db.get(Knowledge, doc_id)
        user = db.get(User, user_id)
        if not kb or not doc or doc.deleted_at or not user:
            return
        text = _doc_chunk_text(db, doc)
        if not text:
            return
        ai = await _llm_summarize(db, user, text, doc.title)
        summary_text = extract_summary_text(ai.get("summary", ""))
        category = normalize_category(ai.get("category"))
        if summary_text:
            doc.wiki_summary = summary_text
        # 已有有效分类时不覆盖（后台任务晚于用户手动归类到达的场景）
        if category and category != DEFAULT_UNCLASSIFIED:
            doc.wiki_category = category
        db.commit()
        logger.info("后台自动摘要+分类完成 doc=%s category=%s", doc_id, doc.wiki_category)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("后台自动摘要+分类失败 doc=%s: %s", doc_id, e)
    finally:
        db.close()


@router.post("/knowledge/bases/{kb_id}/wiki/index")
async def wiki_index_kb(kb_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """对知识库所有文档生成 Wiki 摘要。"""
    kb = db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    _ensure_manage(user, kb)

    docs = db.query(Knowledge).filter(
        Knowledge.knowledge_base_id == kb_id,
        Knowledge.type == "document",
        Knowledge.deleted_at.is_(None),
    ).all()
    results = []
    for doc in docs:
        # 拿分块内容拼起来
        chunks = db.query(Chunk).filter(Chunk.knowledge_id == doc.id).order_by(Chunk.chunk_index).all()
        text = "\n".join((c.content or "")[:500] for c in chunks[:10])
        ai = await _llm_summarize(db, user, text, doc.title)
        # 清洗标题：去掉 01_、02_ 前缀
        import re
        clean_title = re.sub(r'^\d+[_\-\s]*', '', doc.title or doc.file_name or "")
        # 落库（摘要 + AI 主题分类）；extract_summary_text 兜底剥 {"summary":...} 壳
        summary_text = extract_summary_text(ai["summary"])
        doc.wiki_summary = summary_text
        # V5.10.1 分类保护：LLM 本次未分出有效类别（空/拒答/未知 → 归一化为「未分类」）
        # 时保留原有分类，不清掉用户已有归类；LLM 给出有效新分类则正常覆盖。
        # 与 _bg_summarize_doc / regen_doc_summary / wiki_classify_kb 三处既有保护对齐。
        doc.wiki_category = normalize_category(
            ai.get("category"), fallback=doc.wiki_category or DEFAULT_UNCLASSIFIED)
        db.commit()
        results.append({
            "doc_id": doc.id,
            "title": clean_title,
            "raw_title": doc.title,
            "summary": summary_text,
            "category": ai["category"],
            "chunk_count": len(chunks),
        })
    return {"items": results, "kb_id": kb_id}


@router.post("/knowledge/bases/{kb_id}/wiki/classify")
async def wiki_classify_kb(kb_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """一键修复未分类文档（M7）：对缺分类/未分类的文档批量跑轻量分类调用。

    只补分类、不动已有摘要；无分块内容的文档跳过。返回逐篇结果供前端刷新。
    """
    kb = db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    _ensure_manage(user, kb)
    docs = db.query(Knowledge).filter(
        Knowledge.knowledge_base_id == kb_id,
        Knowledge.type == "document",
        Knowledge.deleted_at.is_(None),
    ).all()
    results: list[dict] = []
    classified = 0
    skipped = 0
    for doc in docs:
        cat = (doc.wiki_category or "").strip()
        if cat and cat != DEFAULT_UNCLASSIFIED:
            continue  # 已有有效分类，不动
        text = _doc_chunk_text(db, doc)
        if not text:
            skipped += 1
            continue
        new_cat = await _llm_classify(db, user, text, doc.title)
        if new_cat and new_cat != DEFAULT_UNCLASSIFIED:
            doc.wiki_category = new_cat
            classified += 1
        results.append({"doc_id": doc.id, "title": doc.title, "category": doc.wiki_category or DEFAULT_UNCLASSIFIED})
    db.commit()
    return {"items": results, "classified": classified, "skipped": skipped, "kb_id": kb_id}


@router.get("/knowledge/bases/{kb_id}/wiki")
async def wiki_get(kb_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """拿知识库 Wiki 索引结果（简单版：返回文档列表+分块统计）。"""
    kb = db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if not _can_read(user, kb):
        raise HTTPException(status_code=403, detail="无权访问")

    docs = db.query(Knowledge).filter(
        Knowledge.knowledge_base_id == kb_id,
        Knowledge.type == "document",
        Knowledge.deleted_at.is_(None),
    ).all()
    items = []
    for doc in docs:
        items.append({
            "doc_id": doc.id,
            "title": doc.title,
            "summary": doc.wiki_summary or "",
            "category": doc.wiki_category or "未分类",
            "chunk_count": doc.chunk_count or 0,
            "file_type": doc.file_type or "",
            "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        })
    return {"items": items, "kb_id": kb_id}
