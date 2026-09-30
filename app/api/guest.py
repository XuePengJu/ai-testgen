"""访客身份：单一固定共享 guest（免注册，所有人共用）。

- 不再按 IP 动态建访客、不限频、不过期、不转正（见 app/jobs/guest_cleaner）。
- /guest/token：返回固定 guest 账号的 JWT + 加密会话密钥；前端无需任何参数。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import crypto, security
from app.core.db import get_db
from app.jobs.guest_cleaner import ensure_shared_guest
from app.models.user import User

router = APIRouter(prefix="/guest", tags=["访客"])


@router.post("/token")
def guest_token(db: Session = Depends(get_db)):
    """返回固定共享 guest 的 JWT + 加密会话密钥（所有人共用同一账号）。"""
    guest: User = ensure_shared_guest(db)
    token = security.create_token(guest.id, guest.username, "guest")
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "guest",
        "username": guest.username,
        "enc_key": crypto.derive_key(guest.id),
    }
