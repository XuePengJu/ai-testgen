"""M10.1 接口出入参访问日志单测：AccessLogMiddleware 的记录、打码与降噪。

覆盖点：
1. POST /api JSON：入参出参都记录，敏感字段（password/token/api_key）递归打码为 ***；
2. 响应体捕获后正确透传给客户端（内容不丢、content-type 保持 JSON）；
3. 高频轮询 GET（/api/tasks）只记 DEBUG（INFO 级别不产生记录）；
4. 非 /api 路径不记；
5. 非 JSON 响应（流式/文件）不缓冲内容，出参记 "-"；
6. 超长 JSON 截断到 _MAX_BODY_CHARS。

注意：断言一律按 logger 名 == "api.access" 过滤——TestClient 自身的 httpx
"HTTP Request: ..." 行也会进 caplog，不能混入。
"""
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.testclient import TestClient

from app.core.access_log import AccessLogMiddleware, _mask, _preview


@pytest.fixture()
def log_capture(caplog):
    """捕获 api.access 的 DEBUG 及以上日志（DEBUG 用于轮询降噪用例断言）。"""
    caplog.set_level("DEBUG", logger="api.access")
    return caplog


def _own(caplog):
    """只取本中间件产生的日志记录（排除 TestClient 的 httpx 访问行）。"""
    return [r for r in caplog.records if r.name == "api.access"]


def test_post_body_and_response_logged_with_masking(log_capture):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.post("/api/auth/login")
    def login():
        return {"ok": True, "token": "jwt-abc", "user": {"name": "peng", "password": "plain"}}

    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "peng", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["token"] == "jwt-abc", "响应体捕获后应原样透传给客户端"

    own = _own(log_capture)
    assert len(own) == 1, "一次请求应恰好产生一条访问日志"
    line = own[0].getMessage()
    assert "POST /api/auth/login" in line
    assert '"password": "***"' in line, "入参敏感字段应打码"
    assert "secret123" not in line, "入参明文密码不得出现在日志"
    assert '"token": "***"' in line, "出参敏感字段应打码"
    assert "jwt-abc" not in line, "出参 token 不得出现在日志"
    assert "→ 200" in line and "入参=" in line and "出参=" in line


def test_polling_get_is_debug_only(log_capture):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/api/tasks")
    def list_tasks():
        return {"tasks": []}

    client = TestClient(app)
    client.get("/api/tasks")
    own = _own(log_capture)
    assert not [r for r in own if r.levelno == logging.INFO], "轮询 GET 不应产生 INFO 日志"
    debug_lines = [r.getMessage() for r in own if r.levelno == logging.DEBUG]
    assert any("GET /api/tasks" in m for m in debug_lines), "轮询 GET 应记 DEBUG"


def test_non_api_path_skipped(log_capture):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/assets/app.js")
    def asset():
        return {"static": True}

    client = TestClient(app)
    client.get("/assets/app.js")
    assert not _own(log_capture), "非 /api 路径不应记录"


def test_streaming_response_not_buffered(log_capture):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.post("/api/chat/stream")
    def stream():
        def gen():
            yield b"data: hello\n\n"
            yield b"data: world\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    client = TestClient(app)
    r = client.post("/api/chat/stream", json={"message": "你好"})
    assert r.status_code == 200
    assert b"hello" in r.content, "流式响应体应正常透传"
    own = _own(log_capture)
    assert len(own) == 1
    assert "出参=-" in own[0].getMessage(), "非 JSON 响应不录内容"


def test_long_json_truncated(log_capture):
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/api/big")
    def big():
        return {"data": "x" * 50000}

    client = TestClient(app)
    client.get("/api/big")
    own = _own(log_capture)
    assert len(own) == 1
    line = own[0].getMessage()
    assert "截断" in line, "超长出参应截断"
    assert len(line) < 50000, "日志行不应塞进完整大响应"


def test_mask_helper_nested():
    obj = {"a": 1, "password": "x", "list": [{"api_key": "k", "name": "n"}]}
    masked = _mask(obj)
    assert masked["password"] == "***"
    assert masked["list"][0]["api_key"] == "***"
    assert masked["list"][0]["name"] == "n"
    assert obj["password"] == "x", "打码应返回新结构，不改原对象"


def test_preview_non_json_body():
    assert _preview(b"\x00\x01\x02", "application/octet-stream").startswith("<")
    assert _preview(None, "application/json") == "-"
    text = _preview(json.dumps({"hello": "世界"}).encode(), "application/json")
    assert "世界" in text
