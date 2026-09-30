"""API 分级加密中间件（V2.1）。

分级策略（角色驱动，强制生效，无协商降级）：
- admin：全程明文直通（方便 Swagger 调试与运维排查）
- user / guest：JSON 响应强制加密为 {"enc": base64url(nonce||密文||tag)}；
  请求体为 {"enc": ...} 形状时透明解密后交给端点

密钥分发：
- login / register / guest/token / upgrade 是明文通道（响应里下发 enc_key）
- 其余端点按 JWT 角色决定是否加密（HKDF 派生密钥，前后端独立计算）

不加密项：文件下载（非 JSON 响应）、FormData 文件上传、密钥分发端点。
"""
import json

import jwt as pyjwt

from app.core import config, crypto, security

# 密钥/凭据分发通道：必须明文（客户端此刻还没有密钥）
_PLAINTEXT_PATHS = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/guest/token"),
    # SSE 流式响应：响应是 text/event-stream，不能加密；请求体是明文 JSON，无需解密
    ("POST", "/api/chat/stream"),
}


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return v.decode("latin-1")
    return None


class ApiCryptoMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not config.API_ENCRYPT:
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/")
        if (scope["method"], path) in _PLAINTEXT_PATHS:
            await self.app(scope, receive, send)
            return

        role, enc_key = self._resolve_identity(scope)
        if role not in ("user", "guest"):
            # admin / 未登录 / 无效 token → 明文直通（鉴权失败由端点依赖返回 401/403）
            await self.app(scope, receive, send)
            return
        # user / guest：强制加密（前端纯 JS 实现，不依赖安全上下文，无降级后门）

        # ---- 请求体：{"enc": ...} 形状则解密，否则原样透传 ----
        body = await self._read_body(receive)
        ct = (_header(scope, b"content-type") or "").lower()
        if body and ct.startswith("application/json"):
            try:
                obj = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                obj = None
            if isinstance(obj, dict) and set(obj.keys()) == {"enc"} and isinstance(obj["enc"], str):
                try:
                    plain = json.dumps(
                        crypto.decrypt_obj(obj["enc"], enc_key), ensure_ascii=False
                    ).encode("utf-8")
                except ValueError:
                    await self._send_json(send, 400, {"detail": "请求解密失败"})
                    return
                body = plain
        receive = self._fake_receive(body)
        scope = dict(scope)
        scope["headers"] = [
            (k, v) for k, v in scope["headers"] if k.lower() != b"content-length"
        ] + [(b"content-length", str(len(body)).encode())]

        # ---- 响应：JSON 加密，其余（文件下载）透传 ----
        await self.app(scope, receive, self._wrap_send(send, enc_key))

    # ================= 内部工具 =================

    def _resolve_identity(self, scope) -> tuple[str | None, str | None]:
        """只解码 JWT 判角色（不回查 DB；回查在端点依赖里做，失败照样 401）。"""
        auth = _header(scope, b"authorization") or ""
        if not auth.startswith("Bearer "):
            return None, None
        try:
            payload = security.decode_token(auth[7:])
        except pyjwt.PyJWTError:
            return None, None
        return payload.get("role"), crypto.derive_key(payload.get("sub", ""))

    @staticmethod
    async def _read_body(receive) -> bytes:
        chunks = []
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                break
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        return b"".join(chunks)

    @staticmethod
    def _fake_receive(body: bytes):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return receive

    @staticmethod
    def _wrap_send(send, enc_key: str):
        buffered_start = None
        buffered_body = bytearray()
        encrypting = False

        async def send_wrapper(message):
            nonlocal buffered_start, buffered_body, encrypting
            if message["type"] == "http.response.start":
                ct = ""
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        ct = v.decode("latin-1").lower()
                        break
                if ct.startswith("application/json"):
                    encrypting = True
                    buffered_start = message
                else:
                    await send(message)  # 文件下载等：透传
            elif message["type"] == "http.response.body" and encrypting:
                buffered_body.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return
                try:
                    obj = json.loads(buffered_body)
                    out = json.dumps(
                        {"enc": crypto.encrypt_obj(obj, enc_key)}, ensure_ascii=False
                    ).encode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    out = bytes(buffered_body)  # 解析失败原样放行，不能把接口搞挂
                headers = [
                    (k, v) for k, v in buffered_start["headers"]
                    if k.lower() not in (b"content-length",)
                ] + [(b"content-length", str(len(out)).encode())]
                await send({"type": "http.response.start",
                            "status": buffered_start["status"], "headers": headers})
                await send({"type": "http.response.body", "body": out})
            else:
                await send(message)

        return send_wrapper

    @staticmethod
    async def _send_json(send, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start", "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
