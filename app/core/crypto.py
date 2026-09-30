"""API 响应体加密原语：AES-256-GCM。

格式：base64url( nonce(12B) || 密文 || tag(16B) )
与前端 Web Crypto `AES-GCM` 的 {iv, tag 内嵌} 约定一致，可直接互通。

分级策略在中间件实现：admin 明文，user/guest 加密。
"""
import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core import config

_NONCE_LEN = 12


def derive_key(user_id: str | int) -> str:
    """会话密钥派生：HKDF(JWT_SECRET, info="atgen:enc:<user_id>")。

    不落库：密钥由 JWT_SECRET + 用户 ID 确定性派生，好处——
    ① 免迁移免存储 ② 访客转正 user_id 不变，密钥无缝延续 ③ 多设备同账号密钥一致。
    """
    raw = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=b"atgen-api-encrypt-v1",
        info=f"atgen:enc:{user_id}".encode(),
    ).derive(config.JWT_SECRET.encode("utf-8"))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_session_key() -> str:
    """生成 256 位会话密钥（base64url），登录时下发并落库。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def encrypt_obj(obj: Any, key_b64: str) -> str:
    """把任意可 JSON 序列化的对象加密为 base64url 字符串。"""
    key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def decrypt_obj(payload_b64: str, key_b64: str) -> Any:
    """解密 base64url 密文并还原为 Python 对象。失败抛 ValueError。"""
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        pt = AESGCM(key).decrypt(raw[:_NONCE_LEN], raw[_NONCE_LEN:], None)
        return json.loads(pt.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("解密失败") from exc
