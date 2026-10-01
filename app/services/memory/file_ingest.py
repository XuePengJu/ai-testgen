"""附件入库补跑（P1 契约模块）。

对话附件上传时已在 /api/files 里登记 ChatAttachment 并建 Knowledge 行，
入库（分块+向量化）由后台任务执行，可能因进程重启等原因漏跑。本模块提供
按会话补跑入口：后续记忆调度阶段会懒加载 import 本函数，**签名不可变更**。

正文来源优先级：附件缓存 {file_id}.txt（图片描述已同步写入）→
缺失时从持久副本（doc.file_path）重抽。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR
from app.models.chat_file import ChatAttachment
from app.models.knowledge import Knowledge, KnowledgeBase

logger = logging.getLogger("memory.file_ingest")


def _attachment_text(att: ChatAttachment, doc: Knowledge) -> str:
    """取附件正文：缓存 {file_id}.txt 优先，缺失/为空则从持久副本重抽。"""
    from app.services.doc_extract import extract_text_safe

    txt_path = UPLOAD_DIR / "chat" / f"{att.file_id}.txt"
    if txt_path.exists():
        try:
            text = txt_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if text.strip():
            return text
    # 缓存被 24h 清扫或写缓存时失败 → 从持久副本重抽（图片会得到 ""，由空守卫兜底）
    return extract_text_safe(doc.file_path or "")


def ingest_pending_attachments(db: Session, conversation_id: str) -> dict:
    """扫描该会话 ChatAttachment 中 knowledge.parse_status != "done" 的记录，
    重跑抽取/描述+向量化。返回 {"total": n, "ingested": n, "skipped": n}。幂等，可重复调。

    说明：知识库状态机里「完成」即 parse_status == "ready"（unprocessed →
    parsing → chunking → processing → ready | failed），故以 ready 为完成口径。
    单条失败只置 failed 状态并记日志，不阻断其余附件。
    """
    from app.services.knowledge.ingest import IngestError, ingest_document

    atts = db.execute(
        select(ChatAttachment).where(
            ChatAttachment.conversation_id == conversation_id,
            ChatAttachment.knowledge_id.isnot(None),
        )
    ).scalars().all()
    total = len(atts)
    ingested = 0
    skipped = 0
    for att in atts:
        doc = db.get(Knowledge, att.knowledge_id)
        if not doc or doc.deleted_at or doc.parse_status == "ready":
            skipped += 1
            continue
        kb = db.get(KnowledgeBase, doc.knowledge_base_id)
        if not kb or kb.deleted_at:
            skipped += 1
            continue
        text = _attachment_text(att, doc)
        try:
            ingest_document(db, kb, doc, text)
            ingested += 1
        except IngestError as e:
            # ingest_document 内部已把 parse_status 置为 failed
            logger.warning("附件补入库失败 doc=%s: %s", doc.id, e)
    logger.info("会话 %s 附件补入库完成：total=%s ingested=%s skipped=%s",
                conversation_id, total, ingested, skipped)
    return {"total": total, "ingested": ingested, "skipped": skipped}
