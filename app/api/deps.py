"""鉴权依赖：get_current_user（解码后回查 DB）+ require_admin。"""
import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core import security
from app.core.db import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """解码 JWT → 回查 DB（禁用即时生效 + 过期 guest 懒清理）→ 返回用户。"""
    if not token:
        raise HTTPException(status_code=401, detail="未登录或缺少 Token")

    try:
        payload = security.decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        # 禁用/删除即时生效：无状态 JWT 的吊销用轻量回查折中
        raise HTTPException(status_code=401, detail="账号不可用")

    # 共享 guest 账号无过期（expires_at 恒空），无需懒清理
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def require_user(user: User = Depends(get_current_user)) -> User:
    """已注册用户（user/admin），排除访客——用于个人配置等注册后才能用的接口。"""
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="访客无法使用该功能，请注册账号")
    return user


async def require_guest(user: User = Depends(get_current_user)) -> User:
    """仅 guest 可调（访客转正接口）。"""
    if user.role != "guest":
        raise HTTPException(status_code=403, detail="该接口仅访客可用")
    return user
