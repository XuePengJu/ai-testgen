"""admin 用户管理与治理接口。"""
import shutil
from datetime import datetime
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_admin
from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import get_db
from app.models.task import Task, StepLog
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
    rows = []
    for u in db.execute(select(User).order_by(User.id)).scalars().all():
        n = db.execute(
            select(func.count()).select_from(Task).where(Task.user_id == u.id)
        ).scalar_one()
        rows.append(UserRow(id=u.id, username=u.username, email=u.email, role=u.role,
                            is_active=u.is_active, expires_at=u.expires_at,
                            tasks=n, created_at=u.created_at))
    return rows


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

    # 级联：step_logs → tasks → 文件目录 → 用户
    task_rows = db.execute(select(Task.id).where(Task.user_id == u.id)).all()
    task_ids = [r[0] for r in task_rows]
    if task_ids:
        db.execute(delete(StepLog).where(StepLog.task_id.in_(task_ids)))
        db.execute(delete(Task).where(Task.user_id == u.id))
    if u.data_dir:
        for base in (UPLOAD_DIR, OUTPUT_DIR):
            shutil.rmtree(base / u.data_dir, ignore_errors=True)
    db.delete(u)
    db.commit()
    return {"ok": True, "deleted_user_id": user_id, "deleted_tasks": len(task_ids)}


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
