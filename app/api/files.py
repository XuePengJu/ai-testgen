"""对话附件上传端点。

为什么拆成两步：主对话 `/api/chat/stream` 是 SSE 流式响应，没法同时带 multipart
上传。所以前端先 POST `/api/files` 拿 file_id，再把 file_id 随 SSE 请求发过去。

上传时立刻用 doc_extract 抽取纯文本并落盘缓存（`{file_id}.txt`），
对话端点按 id 读回注入模型上下文（`llm_service.chat_stream` 的 attached_text）。

P1 起登录用户的附件同时登记进个人记忆库（对话记忆素材）：
- ② sha256 + ChatAttachment 落库（按 file_id 幂等；conversation_id 可空=建会话失败孤儿）
- ③ ensure_personal_kb → 建/复用 Knowledge 行（source_key=file:{uid}:{sha[:32]} 幂等覆盖）
  + 副本落 uploads/knowledge/{kb_id}/{doc_id}{ext}（不受 24h 清扫影响）
- ④ 后台任务读 {file_id}.txt → ingest_document 入库（分块 + 向量化）
图片附件的描述在请求内同步生成（image_to_text），保证对话注入立即可用。
访客（且未开 AITF_FILE_INGEST_GUEST）保持旧语义：只做对话缓存，不入库。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import UPLOAD_DIR, AITF_FILE_INGEST_GUEST
from app.core.db import get_db
from app.models.chat_file import ChatAttachment
from app.models.knowledge import Knowledge, KnowledgeBase
from app.models.user import User
from app.services.doc_extract import (
    ExtractError,
    UnsupportedFormatError,
    extract_text,
    extract_text_safe,
    is_supported,
    supported_hint,
)
from app.services.image_caption import IMAGE_EXTS, image_to_text
from app.services.memory.store import ensure_personal_kb

logger = logging.getLogger("api.files")

router = APIRouter()

# 附件缓存目录
CHAT_FILE_DIR = UPLOAD_DIR / "chat"
# 附件持久副本目录（uploads/knowledge/{kb_id}/{doc_id}{ext}）
KB_DOC_DIR = UPLOAD_DIR / "knowledge"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # 单文件 10MB
CACHE_TTL_SECONDS = 24 * 3600            # 附件缓存保留 24h（对话用完即弃）


def _sweep_expired() -> None:
    """顺手清理过期附件缓存（best-effort，失败不影响上传）。"""
    try:
        if not CHAT_FILE_DIR.exists():
            return
        deadline = time.time() - CACHE_TTL_SECONDS
        for p in CHAT_FILE_DIR.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < deadline:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        return


def _bg_ingest_attachment(doc_id: str, file_id: str) -> None:
    """后台把附件文本入库（分块 + 向量化）。

    用独立 SessionLocal（请求级 session 在响应返回后已关闭，照
    knowledge._bg_summarize_doc 范式）。失败只记日志——文档 parse_status
    停在 failed 界面可见，且 ingest_pending_attachments 可按会话补跑。
    """
    from app.core.db import SessionLocal
    from app.services.knowledge.ingest import IngestError, ingest_document

    db = SessionLocal()
    try:
        doc = db.get(Knowledge, doc_id)
        if not doc or doc.deleted_at:
            return
        kb = db.get(KnowledgeBase, doc.knowledge_base_id)
        if not kb or kb.deleted_at:
            return
        # 正文：优先附件缓存 {file_id}.txt（图片描述已同步写入）；
        # 缓存缺失则从持久副本重抽（空文本由 ingest 的空守卫直接 ready 处理）
        txt_path = CHAT_FILE_DIR / f"{file_id}.txt"
        text = ""
        if txt_path.exists():
            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        if not text.strip():
            text = extract_text_safe(doc.file_path or "")
        try:
            ingest_document(db, kb, doc, text)
            logger.info("附件后台入库完成 doc=%s file=%s", doc_id, file_id)
        except IngestError as e:
            logger.warning("附件后台入库失败 doc=%s: %s", doc_id, e)
    except Exception as e:  # noqa: BLE001 - 后台任务绝不向上抛
        logger.warning("附件后台入库异常 doc=%s: %s", doc_id, e)
    finally:
        db.close()


@router.post("/files")
async def upload_chat_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传对话附件 → 抽取文本 → （登录用户）登记入库 → 返回 file_id。"""
    name = file.filename or ""
    if not is_supported(name):
        raise HTTPException(
            status_code=400, detail=f"暂不支持该格式，请上传 {supported_hint()} 文件"
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件过大，单个附件上限 10MB")

    _sweep_expired()
    CHAT_FILE_DIR.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex[:16]
    ext = Path(name).suffix.lower()
    raw_path = CHAT_FILE_DIR / f"{file_id}{ext}"
    raw_path.write_bytes(raw)

    # ① 文本抽取：图片走多模态描述（同步生成，保证对话立即读到）；其余走 doc_extract
    if ext in IMAGE_EXTS:
        text = image_to_text(db, user, raw_path)
    else:
        try:
            text = extract_text(raw_path)
        except (UnsupportedFormatError, ExtractError, OSError, ValueError) as e:
            raw_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"文档解析失败：{e}")

    (CHAT_FILE_DIR / f"{file_id}.txt").write_text(text, encoding="utf-8")
    (CHAT_FILE_DIR / f"{file_id}.json").write_text(
        json.dumps({"name": name, "ext": ext, "chars": len(text)}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 访客（且未开启 AITF_FILE_INGEST_GUEST）：只做对话缓存，与旧语义一致
    if user.role == "guest" and not AITF_FILE_INGEST_GUEST:
        return {"file_id": file_id, "name": name, "chars": len(text), "knowledge_id": None}

    # ②③ 登记附件 + 同步入个人记忆库（失败降级为纯对话缓存，不阻断上传）
    knowledge_id: str | None = None
    need_ingest = False
    try:
        sha_hex = hashlib.sha256(raw).hexdigest()

        # ② ChatAttachment（按 file_id 幂等；conversation_id 为空=建会话失败孤儿）
        att = db.execute(
            select(ChatAttachment).where(ChatAttachment.file_id == file_id)
        ).scalar_one_or_none()
        if att is None:
            att = ChatAttachment(
                conversation_id=(conversation_id or None),
                user_id=user.id,
                file_id=file_id,
                file_name=name[:255],
                file_path=str(raw_path)[:512],
                sha256=sha_hex,
                size=len(raw),
            )
            db.add(att)
        elif conversation_id:
            att.conversation_id = conversation_id

        # ③ ensure_personal_kb → Knowledge 行（source_key 幂等：同文件重传复用不重复建行）
        kb = ensure_personal_kb(db, user)
        source_key = f"file:{user.id}:{sha_hex[:32]}"
        doc = db.execute(
            select(Knowledge).where(
                Knowledge.source_key == source_key,
                Knowledge.deleted_at.is_(None),
            )
        ).scalars().first()
        if doc is None:
            doc = Knowledge(
                user_id=user.id,
                knowledge_base_id=kb.id,
                type="document",
                title=name[:255],
                file_name=name[:255],
                file_type=ext.lstrip("."),
                file_size=len(raw),
                parse_status="pending",
                source_key=source_key,
            )
            db.add(doc)
            db.flush()
            need_ingest = True
        else:
            # 复用：更新体积/文件名；已 ready 的不重复入库（避免分块重复）
            doc.file_size = len(raw)
            need_ingest = doc.parse_status != "ready"

        # 持久副本：uploads/knowledge/{kb_id}/{doc_id}{ext}（不受 24h 清扫影响）
        copy_dir = KB_DOC_DIR / doc.knowledge_base_id
        copy_dir.mkdir(parents=True, exist_ok=True)
        copy_path = copy_dir / f"{doc.id}{ext}"
        shutil.copy2(raw_path, copy_path)
        doc.file_path = str(copy_path)

        att.knowledge_id = doc.id
        db.commit()
        knowledge_id = doc.id
    except Exception as e:  # noqa: BLE001 - 入库失败不阻断附件对话功能
        db.rollback()
        logger.warning("附件登记入库失败（降级为纯对话缓存）：file=%s err=%s", file_id, e)

    # ④ 后台入库（正文从 {file_id}.txt 读；空文本由 ingest 空守卫直接 ready 处理）
    if knowledge_id and need_ingest:
        background_tasks.add_task(_bg_ingest_attachment, knowledge_id, file_id)

    return {"file_id": file_id, "name": name, "chars": len(text), "knowledge_id": knowledge_id}


def load_chat_file(file_id: str) -> tuple[str, str] | None:
    """按 file_id 读回 (文件名, 抽取文本)。不存在/被清理返回 None。

    file_id 由服务端生成（hex），这里再做一次字符白名单校验，防止路径穿越。
    """
    if not file_id or not file_id.isalnum():
        return None
    txt_path = CHAT_FILE_DIR / f"{file_id}.txt"
    if not txt_path.exists():
        return None
    try:
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name = "附件"
    meta_path = CHAT_FILE_DIR / f"{file_id}.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            name = str(meta.get("name") or name)
        except (OSError, ValueError):
            pass
    return name, text
