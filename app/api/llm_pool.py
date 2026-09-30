"""模型池端点（V5.0 P1）。

个人池（需注册用户，guest 403）：
    GET/POST            /api/llm/pool/{slot}
    PUT/DELETE          /api/llm/pool/{slot}/{item_id}
    POST                /api/llm/pool/{slot}/reorder
    PATCH               /api/llm/pool/{slot}/{item_id}/enabled
    POST                /api/llm/pool/{slot}/{item_id}/test
    GET                 /api/llm/pool/{slot}/health

平台池（GET 所有登录用户只读；写操作仅 admin）：
    /api/llm/platform-pool/{slot} 同构

⚠️ 路由注册顺序：固定段（reorder / health）必须排在 `{item_id}` 之前，
否则 starlette 会把 "reorder" 当成 item_id 交给 pydantic 校验 → 422。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_user
from app.core.db import get_db
from app.core.providers import FREE_PROVIDERS, is_provider, provider_label
from app.core.utils import utcnow
from app.models.llm_pool import LLMModelPool
from app.models.user import User
from app.schemas.llm_pool import PoolEnabledIn, PoolItemIn, PoolItemOut, PoolReorderIn
from app.services import llm_pool, llm_service

router = APIRouter()

_SLOTS = ("text", "vision", "embedding")


# ============ 内部实现（个人池 / 平台池共用，仅 owner_id 不同） ============

def _check_slot(slot: str) -> None:
    if slot not in _SLOTS:
        raise HTTPException(400, detail="slot 只能是 text / vision / embedding")


def _rows(db: Session, owner_id: int, slot: str) -> list[LLMModelPool]:
    return list(db.execute(
        select(LLMModelPool).where(
            LLMModelPool.user_id == owner_id, LLMModelPool.slot == slot,
        ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
    ).scalars().all())


def _get_or_404(db: Session, owner_id: int, slot: str, item_id: int) -> LLMModelPool:
    row = db.execute(select(LLMModelPool).where(
        LLMModelPool.id == item_id,
        LLMModelPool.user_id == owner_id,
        LLMModelPool.slot == slot,
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="该模型配置不存在")
    return row


def _effective_owner(db: Session, user: User | None, slot: str) -> int:
    """当前生效的池归属：用户池有启用条目 → 该用户；否则平台池（0）。仅 UI 标注用。

    判据改为复用 llm_service.resolve_pool_ex —— 池卡的「生效中」徽标与
    /llm/effective 的 pools 摘要必须是同一套判据，否则同一件事两处说法不同
    （原实现自己写了一份等价查询，容易随池规则演进悄悄漂移）。
    """
    _, owner = llm_service.resolve_pool_ex(db, user, slot)
    if owner == "personal" and user is not None:
        return user.id
    return 0


def _to_out(row: LLMModelPool, effective: bool) -> PoolItemOut:
    return PoolItemOut(
        id=row.id, slot=row.slot, provider=row.provider,
        provider_label=provider_label(row.provider),
        base_url=row.base_url, model=row.model,
        api_key_masked=(f"****{row.api_key_tail}" if row.api_key_tail else ""),
        priority=row.priority, enabled=bool(row.enabled), paid=bool(row.paid),
        note=row.note or "", key_fingerprint=row.key_fingerprint,
        cooldown_until=row.cooldown_until.isoformat() if row.cooldown_until else None,
        cooling=bool(row.cooldown_until and row.cooldown_until > utcnow()),
        last_error=row.last_error, success_count=row.success_count or 0,
        fail_count=row.fail_count or 0, effective=effective,
    )


def _dup_guard(db: Session, owner_id: int, slot: str, base_url: str, model: str,
               fp: str, exclude_id: int | None = None) -> None:
    """重复配置校验：归属 + 槽位 + 端点 + 模型 + Key 指纹全同 → 409。"""
    rows = _rows(db, owner_id, slot)
    for idx, r in enumerate(rows, start=1):
        if exclude_id is not None and r.id == exclude_id:
            continue
        if (r.base_url == base_url and r.model == model and r.key_fingerprint == fp):
            raise HTTPException(
                409,
                detail=f"与第 {idx} 条配置重复（同端点 + 同模型 + 同 Key），无需重复添加",
            )


def _apply_key(row: LLMModelPool, key_plain: str, owner_id: int, provider: str) -> str:
    """写入 Key（免费厂商留空则靠服务端环境变量），返回 key_fingerprint。"""
    if key_plain:
        row.api_key_enc = llm_service.encrypt_key(key_plain, owner_id)
        row.api_key_tail = key_plain[-4:] if len(key_plain) > 4 else "****"
        return llm_service.key_fingerprint(key_plain)
    server_k = llm_service._server_key(provider)
    if server_k:
        row.api_key_enc = ""
        row.api_key_tail = ""
        return llm_service.key_fingerprint(server_k)
    row.api_key_enc = ""
    row.api_key_tail = ""
    return llm_service.key_fingerprint(f"{provider}:{row.base_url}")


def _impl_list(db: Session, owner_id: int, slot: str, user: User | None) -> list[PoolItemOut]:
    _check_slot(slot)
    eff_owner = _effective_owner(db, user, slot)
    return [_to_out(r, r.user_id == eff_owner) for r in _rows(db, owner_id, slot)]


def _impl_add(db: Session, owner_id: int, slot: str, body: PoolItemIn,
              user: User | None) -> PoolItemOut:
    _check_slot(slot)
    if not is_provider(body.provider):
        raise HTTPException(400, detail="未知的厂商预设")
    key_plain = (body.api_key or "").strip()
    base_url, model = body.base_url.strip(), body.model.strip()
    if not key_plain and body.provider not in FREE_PROVIDERS:
        raise HTTPException(400, detail="该厂商必须填写 API Key（免费厂商由平台提供时可留空）")
    if slot == "embedding" and not key_plain and not llm_service._server_key(body.provider):
        raise HTTPException(400, detail="Embedding 配置必须填写 API Key")

    # 判重指纹必须与最终写入的指纹同源（_apply_key 的取值口径），否则唯一约束会先炸成 500
    eff_key = key_plain or llm_service._server_key(body.provider)
    fp = (llm_service.key_fingerprint(eff_key) if eff_key
          else llm_service.key_fingerprint(f"{body.provider}:{base_url}"))
    _dup_guard(db, owner_id, slot, base_url, model, fp)

    row = LLMModelPool(
        user_id=owner_id, slot=slot, provider=body.provider,
        base_url=base_url, model=model,
        priority=body.priority if body.priority is not None else 100,
        enabled=body.enabled, paid=body.paid, note=(body.note or "")[:64],
    )
    fp = _apply_key(row, key_plain, owner_id, body.provider)
    row.key_fingerprint = fp
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row, True)


def _impl_update(db: Session, owner_id: int, slot: str, item_id: int, body: PoolItemIn,
                 user: User | None) -> PoolItemOut:
    _check_slot(slot)
    row = _get_or_404(db, owner_id, slot, item_id)
    if not is_provider(body.provider):
        raise HTTPException(400, detail="未知的厂商预设")
    base_url, model = body.base_url.strip(), body.model.strip()
    fp = row.key_fingerprint
    key_plain = (body.api_key or "").strip()
    if body.api_key is not None:
        if key_plain:
            fp = _apply_key(row, key_plain, owner_id, body.provider)
        elif body.provider in FREE_PROVIDERS:
            fp = _apply_key(row, "", owner_id, body.provider)   # 改回"由平台提供"
        # 显式传空串且非免费厂商 → 保留原 Key（与 llm_config 约定一致）
    elif not row.api_key_enc and body.provider in FREE_PROVIDERS:
        fp = _apply_key(row, "", owner_id, body.provider)

    _dup_guard(db, owner_id, slot, base_url, model, fp, exclude_id=row.id)
    row.provider, row.base_url, row.model = body.provider, base_url, model
    row.key_fingerprint = fp
    row.paid, row.enabled = body.paid, body.enabled
    row.note = (body.note or "")[:64]
    if body.priority is not None:
        row.priority = body.priority
    row.cooldown_until = None      # 配置变更 → 清冷却，用户可立即复测
    db.commit()
    db.refresh(row)
    return _to_out(row, True)


def _impl_delete(db: Session, owner_id: int, slot: str, item_id: int) -> None:
    _check_slot(slot)
    row = _get_or_404(db, owner_id, slot, item_id)
    db.delete(row)
    db.commit()


def _impl_reorder(db: Session, owner_id: int, slot: str, ids: list[int],
                  user: User | None) -> list[PoolItemOut]:
    _check_slot(slot)
    rows = {r.id: r for r in _rows(db, owner_id, slot)}
    order = [i for i in ids if i in rows] + [i for i in rows if i not in ids]
    for idx, rid in enumerate(order, start=1):
        rows[rid].priority = idx * 10
    db.commit()
    return _impl_list(db, owner_id, slot, user)


def _impl_enabled(db: Session, owner_id: int, slot: str, item_id: int, enabled: bool) -> PoolItemOut:
    _check_slot(slot)
    row = _get_or_404(db, owner_id, slot, item_id)
    row.enabled = enabled
    if enabled:
        row.cooldown_until = None   # 重新启用 → 清冷却
    db.commit()
    db.refresh(row)
    return _to_out(row, True)


def _impl_test(db: Session, owner_id: int, slot: str, item_id: int) -> dict:
    _check_slot(slot)
    row = _get_or_404(db, owner_id, slot, item_id)
    cfg = llm_service._row_to_cfg(row, owner_id)   # 解密失败置空 + 免费厂商服务端 Key 兜底
    if not cfg.get("api_key"):
        return {"ok": False, "err_type": "not_configured",
                "error_label": llm_pool.ERR_LABEL["not_configured"],
                "model": row.model, "error": "该条未配置可用 Key"}
    res = llm_service.test_connectivity(
        cfg["base_url"], cfg["api_key"], cfg["model"],
        kind="embedding" if slot == "embedding" else "chat",
    )
    out = {**res, "model": row.model, "provider_label": provider_label(row.provider)}
    if not out.get("ok"):
        et = llm_pool.classify_error(out.get("error", ""))
        out["err_type"] = et
        out["error_label"] = llm_pool.ERR_LABEL.get(et, llm_pool.ERR_LABEL["other"])
    return out


def _impl_health(db: Session, owner_id: int, slot: str) -> dict:
    _check_slot(slot)
    rows = _rows(db, owner_id, slot)
    now = utcnow()
    items = [{
        "id": r.id, "model": r.model, "enabled": bool(r.enabled),
        "cooling": bool(r.cooldown_until and r.cooldown_until > now),
        "cooldown_until": r.cooldown_until.isoformat() if r.cooldown_until else None,
        "last_error": r.last_error,
        "success_count": r.success_count or 0,
        "fail_count": r.fail_count or 0,
    } for r in rows]
    return {
        "slot": slot,
        "total": len(rows),
        "enabled": sum(1 for r in rows if r.enabled),
        "available": sum(1 for r in rows
                         if r.enabled and not (r.cooldown_until and r.cooldown_until > now)),
        "items": items,
    }


# ============ 个人池路由 ============
# ⚠️ 固定段（reorder / health）必须排在 /{item_id} 之前

@router.get("/llm/pool/{slot}", response_model=list[PoolItemOut])
def list_own_pool(slot: str, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    return _impl_list(db, user.id, slot, user)


@router.post("/llm/pool/{slot}", response_model=PoolItemOut)
def add_own_pool(slot: str, body: PoolItemIn, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    return _impl_add(db, user.id, slot, body, user)


@router.post("/llm/pool/{slot}/reorder", response_model=list[PoolItemOut])
def reorder_own_pool(slot: str, body: PoolReorderIn, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    return _impl_reorder(db, user.id, slot, body.ids, user)


@router.get("/llm/pool/{slot}/health")
def health_own_pool(slot: str, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    return _impl_health(db, user.id, slot)


@router.put("/llm/pool/{slot}/{item_id}", response_model=PoolItemOut)
def update_own_pool(slot: str, item_id: int, body: PoolItemIn,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _impl_update(db, user.id, slot, item_id, body, user)


@router.delete("/llm/pool/{slot}/{item_id}", status_code=204)
def delete_own_pool(slot: str, item_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    _impl_delete(db, user.id, slot, item_id)
    return None


@router.patch("/llm/pool/{slot}/{item_id}/enabled", response_model=PoolItemOut)
def toggle_own_pool(slot: str, item_id: int, body: PoolEnabledIn,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _impl_enabled(db, user.id, slot, item_id, body.enabled)


@router.post("/llm/pool/{slot}/{item_id}/test")
def test_own_pool(slot: str, item_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    return _impl_test(db, user.id, slot, item_id)


# ============ 平台池路由（GET 登录可见；写操作仅 admin） ============

@router.get("/llm/platform-pool/{slot}", response_model=list[PoolItemOut])
def list_platform_pool(slot: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return _impl_list(db, 0, slot, user)


@router.post("/llm/platform-pool/{slot}", response_model=PoolItemOut)
def add_platform_pool(slot: str, body: PoolItemIn, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    return _impl_add(db, 0, slot, body, admin)


@router.post("/llm/platform-pool/{slot}/reorder", response_model=list[PoolItemOut])
def reorder_platform_pool(slot: str, body: PoolReorderIn, db: Session = Depends(get_db),
                          admin: User = Depends(require_admin)):
    return _impl_reorder(db, 0, slot, body.ids, admin)


@router.get("/llm/platform-pool/{slot}/health")
def health_platform_pool(slot: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    return _impl_health(db, 0, slot)


@router.put("/llm/platform-pool/{slot}/{item_id}", response_model=PoolItemOut)
def update_platform_pool(slot: str, item_id: int, body: PoolItemIn,
                         db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return _impl_update(db, 0, slot, item_id, body, admin)


@router.delete("/llm/platform-pool/{slot}/{item_id}", status_code=204)
def delete_platform_pool(slot: str, item_id: int, db: Session = Depends(get_db),
                         admin: User = Depends(require_admin)):
    _impl_delete(db, 0, slot, item_id)
    return None


@router.patch("/llm/platform-pool/{slot}/{item_id}/enabled", response_model=PoolItemOut)
def toggle_platform_pool(slot: str, item_id: int, body: PoolEnabledIn,
                         db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return _impl_enabled(db, 0, slot, item_id, body.enabled)


@router.post("/llm/platform-pool/{slot}/{item_id}/test")
def test_platform_pool(slot: str, item_id: int, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return _impl_test(db, 0, slot, item_id)
