"""注册 / 登录 / 改密 / 当前用户信息。"""
import re
import time
from datetime import datetime

from app.core.utils import utcnow

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import crypto, security
from app.core.db import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["认证"])

# ---- 登录限速（内存计数，单进程）：失败 5 次锁 10 分钟 ----
_LOGIN_FAILS: dict[str, list[float]] = {}
_LOCK_SECONDS = 600
_MAX_FAILS = 5


def _check_lock(username: str) -> None:
    fails = [t for t in _LOGIN_FAILS.get(username, []) if time.time() - t < _LOCK_SECONDS]
    _LOGIN_FAILS[username] = fails
    if len(fails) >= _MAX_FAILS:
        raise HTTPException(status_code=429, detail="失败次数过多，请 10 分钟后再试")


def _record_fail(username: str) -> None:
    _LOGIN_FAILS.setdefault(username, []).append(time.time())


# ---- 请求/响应模型 ----

class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=1)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    expires_at: datetime | None = None   # guest 倒计时用
    remaining_hours: float | None = None

    class Config:
        from_attributes = True


def _to_out(user: User) -> UserOut:
    remaining = None
    if user.role == "guest" and user.expires_at:
        remaining = max(0.0, (user.expires_at - utcnow()).total_seconds() / 3600)
    return UserOut(
        id=user.id, username=user.username, email=user.email, role=user.role,
        is_active=user.is_active, expires_at=user.expires_at, remaining_hours=remaining,
    )


def _validate_password(pwd: str) -> None:
    if len(pwd) < 8 or not re.search(r"[A-Za-z]", pwd) or not re.search(r"\d", pwd):
        raise HTTPException(status_code=422, detail="密码需 ≥8 位且同时包含字母和数字")
    if len(pwd.encode("utf-8")) > 72:  # bcrypt 只取前 72 字节
        raise HTTPException(status_code=422, detail="密码过长（≤72 字节）")


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    _validate_password(body.password)
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")
    if db.execute(select(User).where(User.email == body.email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="邮箱已被注册")

    # 首个注册用户 = admin
    role = "admin" if db.execute(
        select(func.count()).select_from(User).where(User.role != "guest")
    ).scalar_one() == 0 else "user"
    user = User(
        username=body.username, email=body.email,
        password_hash=security.hash_password(body.password),
        role=role, data_dir="",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user.data_dir = f"u_{user.id}"  # data_dir 依赖自增 id
    db.commit()

    # 新账号播种示例分类与示例任务（失败不影响注册）
    from app.services.sample_seeder import seed_sample_tasks
    seed_sample_tasks(db, user)

    # 注册即登录：直接下发 token + 加密会话密钥（密钥分发通道，响应永远明文）
    token = security.create_token(user.id, user.username, user.role)
    out = _to_out(user).model_dump()
    out["access_token"] = token
    out["enc_key"] = crypto.derive_key(user.id)
    return out


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    _check_lock(form.username)
    user = db.execute(select(User).where(User.username == form.username)).scalar_one_or_none()
    # 用户不存在与密码错误同文案，防枚举
    if not user or not security.verify_password(form.password, user.password_hash or ""):
        _record_fail(form.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="访客无需登录，请使用游客体验入口")

    user.last_login_at = utcnow()
    db.commit()
    ttl = None
    if user.role == "guest":
        ttl = int((user.expires_at - utcnow()).total_seconds() // 3600) + 1
    token = security.create_token(user.id, user.username, user.role, ttl_hours=ttl)
    return {"access_token": token, "token_type": "bearer", "role": user.role,
            "enc_key": crypto.derive_key(user.id)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_out(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.password_hash and not security.verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="旧密码错误")
    _validate_password(body.new_password)
    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    return {"ok": True}
