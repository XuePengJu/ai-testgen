"""admin 用户管理与治理接口。"""
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_admin
from app.core.db import get_db
from app.models.task import Task
from app.models.user import User
from app.jobs.guest_cleaner import (
    SHARED_GUEST_USERNAME,
    reset_shared_guest_data,
)

router = APIRouter(tags=["管理"])


class UserPatch(BaseModel):
    is_active: bool | None = None


class UserRow(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    expires_at: datetime | None
    tasks: int
    created_at: datetime | None


@router.get("/users", response_model=list[UserRow])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """用户列表（含每人任务数）。

    ⚠️ 任务数必须用**一次 GROUP BY 聚合**取回，不要按用户循环 count。
    这里曾经是 N+1：`for u in 全部用户: db.execute(count(...))`，用户数一多就退化
    （2026-10-08 实测：124 个用户 × 远程 MySQL 单次往返 ~180ms → 单请求 22.7 秒）。
    现在固定 2 条 SQL，与用户数无关。
    """
    users = db.execute(select(User).order_by(User.id)).scalars().all()
    task_counts = dict(db.execute(
        select(Task.user_id, func.count()).group_by(Task.user_id)
    ).all())
    return [
        UserRow(id=u.id, username=u.username, email=u.email, role=u.role,
                is_active=u.is_active, expires_at=u.expires_at,
                tasks=int(task_counts.get(u.id, 0)), created_at=u.created_at)
        for u in users
    ]


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch,
               admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="不能操作自己的账号")

    if u.role == "guest":
        # 共享 guest 账号不禁用（禁用=所有人用不了）；「禁用」语义改为清空其体验数据
        if body.is_active is False:
            stat = reset_shared_guest_data(db)
            return {"ok": True, "cleaned": stat}
        raise HTTPException(status_code=400, detail="共享访客仅支持清空数据（禁用）")

    if body.is_active is not None:
        u.is_active = body.is_active
        db.commit()
    return {"ok": True, "user_id": u.id, "is_active": u.is_active}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="管理员不可删除自己")
    if u.username == SHARED_GUEST_USERNAME:
        raise HTTPException(status_code=400, detail="共享访客账号不可删除（如需清空请点「清空共享访客数据」）")

    # 级联：会话/消息 → 任务/步骤日志 → 附件与模型配置 → 知识库+记忆(+向量) → 磁盘文件。
    # V7.4：统一走 services/user_purge.purge_user_data（此前这里漏删 conversations /
    # messages / categories / chat_attachments / 模型池 / 提示词覆盖 / 检索埋点，
    # 而 conversations.user_id 无外键约束 → 删用户会留下永久孤儿会话）。
    from app.services.user_purge import purge_user_data

    counts = purge_user_data(db, u)
    db.delete(u)
    db.commit()
    return {
        "ok": True,
        "deleted_user_id": user_id,
        "deleted_tasks": counts.get("tasks", 0),
        "purged": counts,
    }


@router.post("/admin/guest/shared/reset")
def reset_shared_guest(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """清空共享访客的任务与文件，保留账号本身（所有人仍可继续使用）。"""
    stat = reset_shared_guest_data(db)
    return {"ok": True, **stat}


@router.get("/admin/stats")
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "registered_users": db.execute(
            select(func.count()).select_from(User).where(User.role != "guest")
        ).scalar_one(),
        # 共享 guest 恒不过期：存在即为 1
        "active_guests": db.execute(
            select(func.count()).select_from(User).where(
                User.username == SHARED_GUEST_USERNAME)
        ).scalar_one(),
        "total_tasks": db.execute(select(func.count()).select_from(Task)).scalar_one(),
    }
