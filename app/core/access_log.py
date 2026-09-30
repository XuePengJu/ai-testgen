"""接口出入参访问日志（M10.1）：替代 uvicorn.access 裸访问行。

记录每个 /api 请求的：方法、路径、query、**请求体（入参）**、状态码、耗时、
**响应体（出参）**——加密中间件（ApiCryptoMiddleware）在本中间件**外层**，
因此本层看到的是解密后的入参与加密前的明文出参（注册顺序见 main.py）。

降噪与安全约定：
- 高频轮询 GET（/api/tasks 5s 轮询等）只记 DEBUG，不刷 INFO 文件；
- 敏感字段（password/token/secret/api_key/authorization/enc_key 等）递归打码为 ***；
- 请求/响应体只记 JSON 且 ≤64KB，超长截断到 _MAX_BODY 字符，文件/流式只记类型与大小；
- 非 /api 路径（静态资源）不记。
"""
import json
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger("api.access")

# 高频轮询 GET：只记 DEBUG（INFO 文件里不刷屏，DEBUG 级别需要时打开 LOG_LEVEL=DEBUG 看）
_DEBUG_POLL_PATHS = (
    "/api/tasks",
    "/api/conversations",
    "/api/categories",
    "/api/users/me",
)

# 出入参记录上限（字符）：超长截断，防止大响应把日志打爆
_MAX_BODY_CHARS = 1000
# 只录 JSON 且 ≤64KB 的响应体；文件下载 / SSE 流式不录内容
_MAX_CAPTURE_BYTES = 64 * 1024

# 递归打码的字段名（小写比对）
_SENSITIVE_KEYS = {"password", "passwd", "token", "secret", "api_key", "apikey", "authorization", "enc_key"}


def _mask(obj):
    """递归打码敏感字段（dict/list 原地语义返回新结构，其他原样）。"""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in _SENSITIVE_KEYS else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    return obj


def _preview(body: bytes | None, content_type: str) -> str:
    """请求/响应体预览：JSON 解析→打码→截断；非 JSON 只记类型与大小。"""
    if not body:
        return "-"
    ct = (content_type or "").lower()
    if len(body) > _MAX_CAPTURE_BYTES:
        return f"<{len(body)}B 大体积省略>"
    if "json" in ct:
        try:
            obj = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return f"<非标 JSON {len(body)}B>"
        try:
            text = json.dumps(_mask(obj), ensure_ascii=False)
        except (TypeError, ValueError):
            return f"<不可序列化 {len(body)}B>"
        if len(text) > _MAX_BODY_CHARS:
            text = text[:_MAX_BODY_CHARS] + f"…（共 {len(text)} 字符截断）"
        return text
    if ct.startswith("text/"):
        text = body.decode("utf-8", errors="replace")
        return text[:_MAX_BODY_CHARS] if len(text) <= _MAX_BODY_CHARS else text[:_MAX_BODY_CHARS] + "…"
    return f"<{ct or '未知类型'} {len(body)}B>"


class AccessLogMiddleware(BaseHTTPMiddleware):
    """记录 /api 请求的入参与出参（挂在加密中间件内层，见明文）。"""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if not path.startswith("/api"):
            return await call_next(request)  # 静态资源 / 健康检查不记

        method = request.method
        poll = method == "GET" and any(
            path == p or path.startswith(p + "/") for p in _DEBUG_POLL_PATHS
        )

        req_body = b""
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            req_body = await request.body()

        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # 只捕获小体积 JSON 响应体（出参明文）；流式/文件直接透传不缓冲
        resp_body = b""
        resp_ct = response.headers.get("content-type", "")
        if "application/json" in resp_ct.lower():
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            resp_body = b"".join(chunks)
            response = Response(
                content=resp_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json",
            )

        query = f"?{request.url.query}" if request.url.query else ""
        level = logging.DEBUG if poll else logging.INFO
        logger.log(
            level,
            "%s %s%s → %s（%dms）入参=%s 出参=%s",
            method, path, query, response.status_code, elapsed_ms,
            _preview(req_body, request.headers.get("content-type", "")),
            _preview(resp_body, "application/json") if resp_body else "-",
        )
        return response
