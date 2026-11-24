"""内部同步接口（正式站 ↔ 演示站，仅本机回环 + 共享令牌）。

- GET  /api/internal/model-pool/export：正式站导出平台池（含明文 Key）
- POST /api/internal/model-pool/import：演示站导入并替换自己的平台池

安全：`AITF_MODEL_SYNC_TOKEN` 未配置时接口一律 403；令牌用
`hmac.compare_digest` 常量时间比较；带 `X-Forwarded-For` 的请求（经 Nginx
从公网进来的）也拒绝 —— 只有 127.0.0.1 直连的两站内部调用能过。
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core import config, demo_config
from app.core.db import get_db
from app.services import model_sync

logger = logging.getLogger("api.internal")

router = APIRouter(prefix="/internal", tags=["内部同步"])


def _require_sync_token(request: Request) -> None:
    expected = config.MODEL_SYNC_TOKEN
    if not expected:
        raise HTTPException(status_code=403, detail="内部同步未开启（AITF_MODEL_SYNC_TOKEN 未配置）")
    # Nginx 反代会补 X-Forwarded-For；内部 httpx 直连不会有该头
    if request.headers.get("x-forwarded-for"):
        raise HTTPException(status_code=403, detail="仅允许本机直连")
    got = request.headers.get("x-aitf-sync-token", "")
    if not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=403, detail="同步令牌无效")


@router.get("/model-pool/export")
def export_model_pool(request: Request, response: Response, db: Session = Depends(get_db)):
    """导出正式站平台模型池（明文 Key，仅本机内部调用）。"""
    _require_sync_token(request)
    response.headers["Cache-Control"] = "no-store"
    payload = model_sync.build_export_payload(db)
    logger.info("导出平台模型池：%s", {k: len(v) for k, v in payload["slots"].items()})
    return payload


@router.post("/model-pool/import")
def import_model_pool(request: Request, payload: dict, db: Session = Depends(get_db)):
    """演示站导入正式站平台池（重新加密落库）。"""
    _require_sync_token(request)
    if not demo_config.AITF_DEMO_ENABLED:
        raise HTTPException(status_code=403, detail="仅演示站接受导入")
    try:
        result = model_sync.apply_payload(db, payload, clear_admin_personal=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    logger.info("导入平台模型池完成：%s", result["counts"])
    return {"ok": True, **result}