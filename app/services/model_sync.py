"""正式站 → 演示站模型配置同步（平台池搬运）。

为什么需要「解密再加密」：
- 两站 JWT_SECRET 不同（V9 隔离要求），`api_key_enc` 在另一实例解不开；
- 且 `crypto.derive_key` 把 owner_id 也算进密钥，密文不能跨实例复用。
所以正式站导出时解出明文 Key（只在内存/本机回环传输），演示站用
`llm_service.encrypt_key(plain, 0)` 重新加密后写入自己的平台池。

搬运范围：`llm_model_pool` 中 `user_id=0`（平台池）的 text / vision /
embedding / rerank 全部行（含 disabled）；不搬个人池、不搬冷却/健康计数。

两站实际可用的兜底配置也要一致：某槽位没有「启用且有 Key」的行时，
text / embedding 追加一条 env 兜底候选（与 resolve_effective /
resolve_embedding 的解析链一致），vision / rerank 无 env 兜底。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import config, demo_config
from app.core.providers import FREE_PROVIDERS
from app.core.utils import utcnow
from app.models.llm_pool import LLMModelPool
from app.models.user import User
from app.services import llm_service

logger = logging.getLogger("services.model_sync")

SLOTS = ("text", "vision", "embedding", "rerank")
_HTTP_TIMEOUT = 30.0


# ============ 导出（正式站侧） ============


def _platform_rows(db: Session, slot: str) -> list[LLMModelPool]:
    return list(db.execute(
        select(LLMModelPool).where(
            LLMModelPool.user_id == 0,
            LLMModelPool.slot == slot,
        ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
    ).scalars().all())


def _row_payload(row: LLMModelPool) -> dict[str, Any]:
    """一行 → 可跨实例传输的静态配置（含明文 Key，不含运行时健康状态）。"""
    cfg = llm_service._row_to_cfg(row, row.user_id)
    return {
        "provider": row.provider,
        "base_url": row.base_url,
        "model": row.model,
        # 免费厂商未存 Key 时这里是服务端 env Key；对方重新加密落库后即可独立运行
        "api_key": cfg.get("api_key") or "",
        "priority": row.priority,
        "enabled": bool(row.enabled),
        "paid": bool(row.paid),
        "note": row.note or "",
    }


def _env_fallback(db: Session, slot: str) -> dict[str, Any] | None:
    """槽位没有任何可用池行时，导出与本站解析链一致的 env 兜底配置。"""
    if slot == "text":
        if not config.DASHSCOPE_API_KEY:
            return None
        return {
            "provider": "bailian",
            "base_url": llm_service._BAILIAN_COMPAT,
            "model": config.MODEL_NAME or "qwen-plus",
            "api_key": config.DASHSCOPE_API_KEY,
            "priority": 900,
            "enabled": True,
            "paid": False,
            "note": "env 兜底",
        }
    if slot == "embedding":
        resolved = llm_service.resolve_embedding(db, None)
        cfg = resolved.get("cfg")
        if resolved.get("source") != "env" or not cfg or not cfg.get("api_key"):
            return None
        return {
            "provider": cfg.get("provider") or "bailian",
            "base_url": cfg.get("base_url") or "",
            "model": cfg.get("model") or "",
            "api_key": cfg["api_key"],
            "priority": 900,
            "enabled": True,
            "paid": False,
            "note": "env 兜底",
        }
    # vision / rerank 没有 env 兜底
    return None


def build_export_payload(db: Session) -> dict[str, Any]:
    """导出平台池四槽位（含明文 Key）。供内部接口与一键同步共用。"""
    slots: dict[str, list[dict[str, Any]]] = {}
    for slot in SLOTS:
        items = [_row_payload(r) for r in _platform_rows(db, slot)]
        usable = any(i["enabled"] and i["api_key"] for i in items)
        if not usable:
            env_item = _env_fallback(db, slot)
            if env_item:
                items.append(env_item)
        slots[slot] = items
    return {
        "version": 1,
        "source": "main",
        "exported_at": utcnow().isoformat() + "Z",
        "slots": slots,
    }


# ============ 导入（演示站侧） ============


def _effective_embedding(db: Session) -> dict[str, str] | None:
    """当前生效的 embedding（启用 + Key 可用），用于判断同步前后是否换模型。"""
    rows = db.execute(
        select(LLMModelPool).where(
            LLMModelPool.user_id == 0,
            LLMModelPool.slot == "embedding",
            LLMModelPool.enabled.is_(True),
        ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
    ).scalars().all()
    for r in rows:
        cfg = llm_service._row_to_cfg(r, r.user_id)
        if cfg.get("api_key"):
            return {"model": r.model, "base_url": r.base_url}
    return None


def _clear_admin_personal_pool(db: Session) -> int:
    """清演示 admin 的个人池：个人池优先级高于平台池，不清会遮蔽刚同步的配置。"""
    admin = db.execute(
        select(User).where(
            User.username == demo_config.AITF_DEMO_ADMIN_USERNAME,
            User.role == "admin",
        )
    ).scalar_one_or_none()
    if not admin:
        return 0
    res = db.execute(delete(LLMModelPool).where(LLMModelPool.user_id == admin.id))
    return res.rowcount or 0


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_row(slot: str, item: dict[str, Any], idx: int) -> tuple[str | None, LLMModelPool | None]:
    """构造一行（不落库），返回 (去重指纹, row)；配置不完整返回 (None, None)。"""
    provider = str(item.get("provider") or "").strip()
    base_url = str(item.get("base_url") or "").strip()
    model = str(item.get("model") or "").strip()
    if not provider or not base_url or not model:
        return None, None

    api_key = str(item.get("api_key") or "")
    if api_key:
        api_key_enc = llm_service.encrypt_key(api_key, 0)
        api_key_tail = api_key[-4:] if len(api_key) > 4 else "****"
        fingerprint = llm_service.key_fingerprint(api_key)
    else:
        # 免费厂商允许空 Key，由演示站自己的 env 兜底；指纹口径与 llm_pool._apply_key 一致
        api_key_enc = ""
        api_key_tail = ""
        server_key = llm_service._server_key(provider) if provider in FREE_PROVIDERS else ""
        fingerprint = llm_service.key_fingerprint(
            server_key or (f"{provider}:{base_url}" if provider in FREE_PROVIDERS else "")
        )

    row = LLMModelPool(
        user_id=0,
        slot=slot,
        provider=provider,
        base_url=base_url,
        model=model,
        api_key_enc=api_key_enc,
        api_key_tail=api_key_tail,
        key_fingerprint=fingerprint,
        priority=_int_or(item.get("priority"), (idx + 1) * 10),
        enabled=bool(item.get("enabled", True)),
        paid=bool(item.get("paid", False)),
        note=str(item.get("note") or "")[:64],
    )
    return f"{base_url}\x00{model}\x00{fingerprint}", row


def apply_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    clear_admin_personal: bool = False,
) -> dict[str, Any]:
    """用 payload 替换演示站平台池。

    - 只替换 payload.slots 中出现的槽位（缺省槽位保持不动）；
    - 空列表 = 显式清空该槽位（镜像正式站）；
    - 同一槽位内按 base_url+model+Key 指纹去重，避免唯一约束 409；
    - 任一步异常回滚，保留旧配置。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("slots"), dict):
        raise ValueError("payload.slots 必须是对象")

    embedding_before = _effective_embedding(db)
    counts = {slot: 0 for slot in SLOTS}
    skipped = 0

    try:
        for slot in SLOTS:
            if slot not in payload["slots"]:
                continue
            raw_items = payload["slots"][slot]
            if not isinstance(raw_items, list):
                raise ValueError(f"slot {slot} 必须是数组")

            db.execute(delete(LLMModelPool).where(
                LLMModelPool.user_id == 0,
                LLMModelPool.slot == slot,
            ))
            seen: set[str] = set()
            for idx, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                fingerprint, row = _build_row(slot, item, idx)
                if fingerprint is None or row is None:
                    skipped += 1
                    continue
                if fingerprint in seen:
                    skipped += 1
                    continue
                seen.add(fingerprint)
                db.add(row)
                counts[slot] += 1

        cleared_personal = _clear_admin_personal_pool(db) if clear_admin_personal else 0
        db.flush()
        embedding_after = _effective_embedding(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    embedding_changed = embedding_before != embedding_after
    if embedding_changed:
        logger.warning(
            "演示站 embedding 模型已同步变更：%s -> %s（知识库快照需用新模型重建）",
            embedding_before, embedding_after,
        )

    return {
        "counts": counts,
        "skipped": skipped,
        "cleared_personal": cleared_personal,
        "embedding_changed": embedding_changed,
        "embedding_before": embedding_before,
        "embedding_after": embedding_after,
    }


# ============ 跨实例调用（仅本机回环） ============


def _sync_token() -> str:
    token = config.MODEL_SYNC_TOKEN
    if not token:
        raise RuntimeError("AITF_MODEL_SYNC_TOKEN 未配置，无法同步")
    return token


def push_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """正式站 → 演示站：把导出结果推到演示站 import 接口。"""
    url = demo_config.AITF_DEMO_MODEL_SYNC_TARGET_URL.rstrip("/") + "/api/internal/model-pool/import"
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.post(url, json=payload, headers={"X-AITF-Sync-Token": _sync_token()})
    if r.status_code != 200:
        raise RuntimeError(f"演示站导入失败 HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def pull_and_apply(db: Session) -> dict[str, Any]:
    """演示站 ← 正式站：拉 export 后本地替换平台池。"""
    url = demo_config.AITF_DEMO_MODEL_SYNC_MAIN_URL.rstrip("/") + "/api/internal/model-pool/export"
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.get(url, headers={"X-AITF-Sync-Token": _sync_token()})
    if r.status_code != 200:
        raise RuntimeError(f"正式站导出失败 HTTP {r.status_code}: {r.text[:300]}")
    return apply_payload(db, r.json(), clear_admin_personal=True)