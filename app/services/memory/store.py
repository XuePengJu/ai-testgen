"""个人记忆库读写核心（P0 数据地基，后续阶段的契约模块）。

- ensure_personal_kb：幂等建个人库，注册 / 访客引导 / 存量回填三处共用
- get_personal_kb_id：只读查询个人库 id，检索路径用，绝不建库
- purge_user_knowledge：删用户时级联清知识库行数据 + Chroma 向量

可见性说明：个人库 visibility="private" 本身就等价于「仅本人可见」，
列表/检索的可见性过滤沿用既有口径（visibility='global' OR user_id=me），
无需为个人库增加额外过滤逻辑。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import Chunk, ChunkRevision, Knowledge, KnowledgeBase

logger = logging.getLogger("memory.store")


def _personal_kb_filters(user_id: int) -> tuple:
    """个人库的统一查找条件：属主 + is_personal + 未软删。"""
    return (
        KnowledgeBase.user_id == user_id,
        KnowledgeBase.is_personal.is_(True),
        KnowledgeBase.deleted_at.is_(None),
    )


def ensure_personal_kb(db: Session, user) -> KnowledgeBase:
    """确保用户有个人记忆库；有则返回，无则创建。

    个人库固定属性：visibility="private"、type="document"、is_personal=True、
    name=f"{user.username} 的记忆库"。
    幂等：按 user_id + is_personal + deleted_at IS NULL 查找，命中即返回既有库。
    """
    kb = db.execute(
        select(KnowledgeBase).where(*_personal_kb_filters(user.id))
    ).scalars().first()
    if kb:
        return kb
    kb = KnowledgeBase(
        user_id=user.id,
        name=f"{user.username} 的记忆库",
        visibility="private",
        type="document",
        is_personal=True,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    logger.info("已创建用户 %s 的个人记忆库（kb=%s）", user.id, kb.id)
    return kb


def get_personal_kb_id(db: Session, user_id: int) -> str | None:
    """只读查询个人库 id（检索路径用，绝不建库）。找不到返回 None。"""
    return db.execute(
        select(KnowledgeBase.id).where(*_personal_kb_filters(user_id))
    ).scalar_one_or_none()


def purge_user_knowledge(db: Session, user_id: int) -> dict:
    """删用户时级联清知识库：行数据 + Chroma 向量。

    覆盖该用户的 knowledges / chunks / chunk_revisions / knowledge_bases 全部行
    （含软删行，物理删除不留孤儿），向量按 knowledge_id / kb_id 粒度逐个删除。
    返回 {"docs": n, "chunks": n, "bases": n}。
    """
    from app.services.knowledge.vectorstore import (
        delete_kb_vectors,
        delete_knowledge_vectors,
    )

    kb_rows = db.execute(
        select(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
    ).scalars().all()
    doc_rows = db.execute(
        select(Knowledge).where(Knowledge.user_id == user_id)
    ).scalars().all()
    chunk_rows = db.execute(
        select(Chunk).where(Chunk.user_id == user_id)
    ).scalars().all()
    rev_rows = db.execute(
        select(ChunkRevision).where(ChunkRevision.user_id == user_id)
    ).scalars().all()

    # 先删向量（Chroma 里没有对应记录不报错；删除失败只告警，不阻断行数据清理）
    for doc in doc_rows:
        delete_knowledge_vectors(doc.id)
    for kb in kb_rows:
        delete_kb_vectors(kb.id)

    n_docs, n_chunks, n_bases = len(doc_rows), len(chunk_rows), len(kb_rows)
    for rev in rev_rows:
        db.delete(rev)
    for chunk in chunk_rows:
        db.delete(chunk)
    for doc in doc_rows:
        db.delete(doc)
    for kb in kb_rows:
        db.delete(kb)
    db.commit()
    logger.info("已清理用户 %s 的知识库数据（docs=%s chunks=%s bases=%s）",
                user_id, n_docs, n_chunks, n_bases)
    return {"docs": n_docs, "chunks": n_chunks, "bases": n_bases}


# ============ P2：会话记忆文档落库（幂等覆盖） ============

def _md5(text: str) -> str:
    """短 md5（记忆文档 file_hash 用：固定为 md5(source_key)，防「每夜必变」膨胀）。"""
    import hashlib
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def read_memory_text(db: Session, knowledge_id: str, limit: int = 0) -> str:
    """按分块顺序拼出记忆文档正文（记忆文档无 file_path，正文只存在 chunks 里）。

    limit > 0 时截断到 limit 字符；文档不存在或无分块返回空串。
    """
    from app.models.knowledge import Chunk

    rows = db.execute(
        select(Chunk).where(Chunk.knowledge_id == knowledge_id).order_by(Chunk.chunk_index)
    ).scalars().all()
    text = "".join(c.content or "" for c in rows)
    return text[:limit] if limit else text


def upsert_memory_doc(db: Session, kb: KnowledgeBase, conv, md_text: str) -> str:
    """把提炼出的记忆文档写入/覆盖到个人库，返回文档 id（写入 conv.mem_doc_id 用）。

    幂等键：source_key = mem:conv:{user_id}:{conv_id}（唯一索引）。
    - 命中旧文档：先删旧向量与旧分块再重建（chunks 数不翻倍、向量不残留），
      title / content / parse_status 更新，file_hash 固定为 md5(source_key)
      （内容虽变但哈希不变，下游去重逻辑不会把它当「每夜必变的新文档」）；
    - 未命中：新建 Knowledge（file_type="txt"、file_path=""、纯文本入库），
      再走既有 ingest_document（分块 + 向量化）。
    失败向上抛异常，由调用方决定是否推进提炼水位。
    """
    from app.services.knowledge.ingest import delete_document_vectors, ingest_document

    source_key = f"mem:conv:{conv.user_id}:{conv.id}"
    title = f"会话记忆 · {conv.title or '新会话'}"[:255]
    doc = db.execute(
        select(Knowledge).where(Knowledge.source_key == source_key)
    ).scalars().first()
    if doc is not None:
        # 幂等覆盖：先清旧向量 + 旧分块（ingest 内部会重建分块与向量）
        delete_document_vectors(db, doc.id)
        doc.knowledge_base_id = kb.id
        doc.title = title
        doc.file_hash = _md5(source_key)
        doc.parse_status = "parsing"
        doc.error_message = ""
        doc.deleted_at = None
        db.commit()
        db.refresh(doc)
    else:
        doc = Knowledge(
            user_id=conv.user_id,
            knowledge_base_id=kb.id,
            type="document",
            title=title,
            file_name="",
            file_type="txt",
            file_path="",
            file_size=len((md_text or "").encode("utf-8")),
            file_hash=_md5(source_key),
            source_key=source_key,
            parse_status="parsing",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    ingest_document(db, kb, doc, md_text)
    logger.info("会话记忆文档已入库 conv=%s doc=%s", conv.id, doc.id)
    return doc.id
