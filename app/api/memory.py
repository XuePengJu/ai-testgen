"""记忆条目接口（V7.0(c) 读 + V7.1 写）。

读（V7.0）：
- GET /api/memory/items?kind=&status=&q=&limit=200  条目列表（仅本人）
- GET /api/memory/items/{id}                        条目详情 + 版本链（prev/next）
- GET /api/memory/stats                             记忆健康度聚合数（仅本人）

写（V7.1，全部走 services/memory/items.py 唯一写入口）：
- PATCH  /api/memory/items/{id}                 手动编辑（走版本链取代，source=user）
- DELETE /api/memory/items/{id}?hard=true|false 软删（status=deleted）/ 硬删（物理删、审计保留）
- POST   /api/memory/items/{id}/adopt           冲突条目一键采纳（取代被冲突的 active）
- POST   /api/memory/items/{id}/restore        恢复 superseded 旧版本（按置信度竞争）
- GET    /api/memory/items/{id}/audits         该条审计链（append-only）

鉴权铁律：每个接口硬过滤 MemoryItem.user_id == current_user.id——admin 也不
跨用户看他人记忆内容（内容属隐私，admin 只有平台级 /stats 聚合）；guest 一律
403（require_user）。本层只做鉴权与参数校验，业务全在 services 层。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.core.db import get_db
from app.models.memory import MemoryAudit, MemoryItem
from app.models.user import User
from app.services.memory.forget import hard_delete_item
from app.services.memory.items import (
    adopt_item,
    edit_item,
    restore_item,
    soft_delete_item,
)

router = APIRouter(prefix="/memory", tags=["记忆条目"])

# 版本链回溯步数上限（防脏数据成环死循环）
_CHAIN_LIMIT = 50


class ItemEditIn(BaseModel):
    """手动编辑入参（subject/content 至少给一个；缺省项保持原值）。"""
    subject: str | None = Field(default=None, max_length=120)
    content: str | None = Field(default=None, max_length=500)


def _item_out(item: MemoryItem) -> dict:
    """条目序列化（API 出参统一形状，前端 MemoryPanel 直接消费）。"""
    return {
        "id": item.id,
        "user_id": item.user_id,
        "kind": item.kind,
        "subject": item.subject,
        "content": item.content,
        "evidence": item.evidence,
        "source": item.source,
        "conversation_id": item.conversation_id,
        "message_id": item.message_id,
        "confidence": item.confidence,
        "importance": item.importance,
        "status": item.status,
        "root_id": item.root_id,
        "prev_id": item.prev_id,
        "superseded_by": item.superseded_by,
        "conflict_with": item.conflict_with,
        "version": item.version,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "half_life_days": item.half_life_days,
        "hit_count": item.hit_count,
        "last_hit_at": item.last_hit_at.isoformat() if item.last_hit_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _own_item(db: Session, user: User, item_id: str) -> MemoryItem:
    """按 id 取本人条目（硬过滤 user_id；他人条目一律 404 不泄露存在性）。"""
    item = db.execute(
        select(MemoryItem).where(
            MemoryItem.id == item_id,
            MemoryItem.user_id == user.id,
        )
    ).scalars().first()
    if item is None:
        raise HTTPException(status_code=404, detail="记忆条目不存在")
    return item


@router.get("/items")
def list_items(
    kind: str | None = Query(None, description="kind 过滤：fact/preference/rule/todo/profile"),
    status: str | None = Query(None, description="状态过滤：active/superseded/conflict/expired/deleted"),
    q: str | None = Query(None, max_length=120, description="subject/content 模糊搜索"),
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """条目列表（仅本人；默认全状态，按 updated_at 倒序）。"""
    stmt = select(MemoryItem).where(MemoryItem.user_id == user.id)
    if kind:
        stmt = stmt.where(MemoryItem.kind == kind)
    if status:
        stmt = stmt.where(MemoryItem.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(MemoryItem.subject.like(like),
                              MemoryItem.content.like(like)))
    items = db.execute(
        stmt.order_by(MemoryItem.updated_at.desc()).limit(limit)
    ).scalars().all()
    return {"items": [_item_out(i) for i in items], "total": len(items)}


@router.get("/items/{item_id}")
def get_item(item_id: str, user: User = Depends(require_user),
             db: Session = Depends(get_db)):
    """条目详情 + 版本链（prev 链回溯到链头 / next 链走到最新 active）。"""
    item = _own_item(db, user, item_id)

    # prev 链：沿 prev_id 一路回到链头（旧版本在前）
    prev_chain: list[dict] = []
    seen = {item.id}
    cur = item
    for _ in range(_CHAIN_LIMIT):
        if not cur.prev_id or cur.prev_id in seen:
            break
        p = db.execute(
            select(MemoryItem).where(
                MemoryItem.id == cur.prev_id,
                MemoryItem.user_id == user.id,
            )
        ).scalars().first()
        if p is None:
            break
        prev_chain.append(_item_out(p))
        seen.add(p.id)
        cur = p

    # next 链：沿 superseded_by 走到最新版本（新版本在前）
    next_chain: list[dict] = []
    seen = {item.id}
    cur = item
    for _ in range(_CHAIN_LIMIT):
        if not cur.superseded_by or cur.superseded_by in seen:
            break
        n = db.execute(
            select(MemoryItem).where(
                MemoryItem.id == cur.superseded_by,
                MemoryItem.user_id == user.id,
            )
        ).scalars().first()
        if n is None:
            break
        next_chain.append(_item_out(n))
        seen.add(n.id)
        cur = n

    # 冲突对照：本条挂起的冲突对象（conflict_with）+ 指向本条的冲突条目
    conflict_ref = None
    if item.conflict_with:
        c = db.execute(
            select(MemoryItem).where(
                MemoryItem.id == item.conflict_with,
                MemoryItem.user_id == user.id,
            )
        ).scalars().first()
        if c is not None:
            conflict_ref = _item_out(c)
    conflicts_in = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user.id,
            MemoryItem.conflict_with == item.id,
        ).order_by(MemoryItem.updated_at.desc()).limit(20)
    ).scalars().all()

    return {
        "item": _item_out(item),
        "prev_chain": prev_chain,
        "next_chain": next_chain,
        "conflict_with_item": conflict_ref,
        "conflicting_items": [_item_out(i) for i in conflicts_in],
    }


@router.get("/stats")
def memory_stats(user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    """记忆健康度聚合数（仅本人：按状态/种类计数、平均置信度、版本链数）。"""
    base = MemoryItem.user_id == user.id

    def _count(*cond) -> int:
        return int(db.execute(
            select(func.count()).select_from(MemoryItem).where(base, *cond)
        ).scalar() or 0)

    by_status_rows = db.execute(
        select(MemoryItem.status, func.count()).where(base)
        .group_by(MemoryItem.status)
    ).all()
    by_kind_rows = db.execute(
        select(MemoryItem.kind, func.count()).where(base, MemoryItem.status == "active")
        .group_by(MemoryItem.kind)
    ).all()
    avg_conf = db.execute(
        select(func.avg(MemoryItem.confidence)).where(base, MemoryItem.status == "active")
    ).scalar()
    # 版本链数：active 条目去重 root_id（一条链只有一个 active 头）
    chains = db.execute(
        select(func.count(func.distinct(MemoryItem.root_id)))
        .where(base, MemoryItem.status == "active")
    ).scalar()

    return {
        "total": _count(),
        "by_status": {str(s): int(n) for s, n in by_status_rows},
        "by_kind_active": {str(k): int(n) for k, n in by_kind_rows},
        "active": _count(MemoryItem.status == "active"),
        "conflict": _count(MemoryItem.status == "conflict"),
        "superseded": _count(MemoryItem.status == "superseded"),
        "expired": _count(MemoryItem.status == "expired"),
        "avg_confidence_active": round(float(avg_conf), 4) if avg_conf is not None else 0.0,
        "chains_active": int(chains or 0),
    }


# ============ V7.1 写接口（业务全在 services/memory/items.py） ============

@router.patch("/items/{item_id}")
def patch_item(item_id: str, body: ItemEditIn,
               user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    """手动编辑：走版本链取代（旧版 superseded、新版 active v+1、source=user）。"""
    item = _own_item(db, user, item_id)
    if item.status != "active":
        raise HTTPException(status_code=400, detail="仅生效中（active）的条目可编辑")
    if not body.subject and not body.content:
        raise HTTPException(status_code=400, detail="subject 与 content 至少提供一个")
    try:
        action = edit_item(db, user.id, item,
                           subject=body.subject, content=body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "action": action}


@router.delete("/items/{item_id}")
def delete_item(item_id: str, hard: bool = Query(False, description="true=物理删（审计保留）；缺省软删"),
                user: User = Depends(require_user),
                db: Session = Depends(get_db)):
    """删除条目：软删（status=deleted，宽限期后物理清）或硬删（立即物理删）。"""
    item = _own_item(db, user, item_id)
    if hard:
        hard_delete_item(db, item, actor="user", reason="用户手动硬删除")
        return {"ok": True, "mode": "hard"}
    soft_delete_item(db, item, actor="user", reason="用户手动删除（软删）")
    return {"ok": True, "mode": "soft"}


@router.post("/items/{item_id}/adopt")
def adopt(item_id: str, user: User = Depends(require_user),
          db: Session = Depends(get_db)):
    """一键采纳冲突条目：conflict 升级 active，取代被冲突的条目。"""
    item = _own_item(db, user, item_id)
    if item.status != "conflict" or not item.conflict_with:
        raise HTTPException(status_code=400, detail="仅冲突（conflict）状态的条目可采纳")
    adopt_item(db, user.id, item)
    return {"ok": True, "active_id": item.id}


@router.post("/items/{item_id}/restore")
def restore(item_id: str, user: User = Depends(require_user),
            db: Session = Depends(get_db)):
    """恢复 superseded 旧版本：与当前 active 按置信度竞争（不足则拒绝）。"""
    item = _own_item(db, user, item_id)
    if item.status != "superseded":
        raise HTTPException(status_code=400, detail="仅已被取代（superseded）的条目可恢复")
    try:
        result = restore_item(db, user.id, item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/items/{item_id}/audits")
def item_audits(item_id: str, user: User = Depends(require_user),
                db: Session = Depends(get_db)):
    """该条目的审计链（append-only，按时间正序；硬删后审计仍保留）。"""
    item = _own_item(db, user, item_id)  # 先校验条目归属（硬删后 404）
    rows = db.execute(
        select(MemoryAudit).where(
            MemoryAudit.user_id == user.id,
            MemoryAudit.item_id == item.id,
        ).order_by(MemoryAudit.id)
    ).scalars().all()
    return {"audits": [{
        "id": a.id,
        "item_id": a.item_id,
        "action": a.action,
        "actor": a.actor,
        "before": a.before_json,
        "after": a.after_json,
        "reason": a.reason,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows]}
