"""密码哈希与 JWT 签发/校验。"""
from datetime import timedelta

from app.core.utils import utcnow

import jwt
from passlib.context import CryptContext

from app.core import config

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001  hash 为空/格式错时按失败处理
        return False


def create_token(user_id: int, username: str, role: str,
                 ttl_hours: int | None = None) -> str:
    """签发 JWT；guest 传 ttl 对齐 expires_at。"""
    ttl = ttl_hours if ttl_hours is not None else config.TOKEN_TTL_HOURS
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": utcnow() + timedelta(hours=ttl),
        "iat": utcnow(),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并验签。无效/过期抛 jwt.PyJWTError。"""
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
