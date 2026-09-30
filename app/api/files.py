"""对话附件上传端点。

为什么拆成两步：主对话 `/api/chat/stream` 是 SSE 流式响应，没法同时带 multipart
上传。所以前端先 POST `/api/files` 拿 file_id，再把 file_id 随 SSE 请求发过去。

上传时立刻用 doc_extract 抽取纯文本并落盘缓存（`{file_id}.txt`），
对话端点按 id 读回注入模型上下文（`llm_service.chat_stream` 的 attached_text）。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import UPLOAD_DIR
from app.models.user import User
from app.services.doc_extract import (
    ExtractError,
    UnsupportedFormatError,
    extract_text,
    is_supported,
    supported_hint,
)

router = APIRouter()

# 附件缓存目录
CHAT_FILE_DIR = UPLOAD_DIR / "chat"
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


@router.post("/files")
async def upload_chat_file(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传对话附件 → 抽取文本 → 返回 file_id。"""
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
    return {"file_id": file_id, "name": name, "chars": len(text)}


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
