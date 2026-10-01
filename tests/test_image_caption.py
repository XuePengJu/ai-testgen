"""图片描述模块测试（mock llm_pool，不发真实请求）。

覆盖：正常描述生成（多模态消息形态校验）、_to_data_uri 压缩、
未配置模型返回 ""、模型异常返回 ""（绝不阻断上传）。
"""
import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from app.models.knowledge import Knowledge
from app.services import image_caption


class _FakeClient:
    """替身客户端：记录 chat 入参，返回固定描述。"""

    def __init__(self, reply="登录页面截图：包含用户名、密码输入框和登录按钮"):
        self.reply = reply
        self.calls: list = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


class _BrokenClient:
    def chat(self, messages, **kwargs):
        raise RuntimeError("模型超时")


def _make_image(tmp_path: Path, size=(32, 32)) -> Path:
    p = tmp_path / f"img_{size[0]}.png"
    Image.new("RGB", size, "blue").save(p, format="PNG")
    return p


def test_image_to_text_success(db_session, tmp_path, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(
        "app.services.llm_pool.build_client", lambda db, user, slot="text": fake)
    out = image_caption.image_to_text(db_session, None, _make_image(tmp_path))
    assert out == "登录页面截图：包含用户名、密码输入框和登录按钮"
    # 多模态消息形态：text 提示词 + image_url（base64 data URI）
    content = fake.calls[0][0]["content"]
    assert content[0]["type"] == "text"
    assert "图片内容" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_to_data_uri_resizes_long_side(tmp_path):
    """最长边超过 1024 时压缩；输出可被 PIL 重新解码。"""
    p = _make_image(tmp_path, size=(2000, 1000))
    uri = image_caption._to_data_uri(p)
    assert uri.startswith("data:image/png;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    img = Image.open(io.BytesIO(raw))
    assert max(img.size) <= 1024


def test_image_to_text_no_model_returns_empty(db_session, tmp_path, monkeypatch):
    """vision/text 槽均无可用模型 → 返回 ""，不抛异常。"""
    monkeypatch.setattr(
        "app.services.llm_pool.build_client", lambda db, user, slot="text": None)
    out = image_caption.image_to_text(db_session, None, _make_image(tmp_path))
    assert out == ""


def test_image_to_text_exception_returns_empty(db_session, tmp_path, monkeypatch):
    """模型调用异常 → 返回 ""，绝不抛出阻断上传。"""
    monkeypatch.setattr(
        "app.services.llm_pool.build_client",
        lambda db, user, slot="text": _BrokenClient())
    out = image_caption.image_to_text(db_session, None, _make_image(tmp_path))
    assert out == ""


def test_image_upload_end_to_end_with_caption(client, accounts, db_session, monkeypatch):
    """端到端：图片上传 → 描述写进缓存（对话立即可用）→ 作为文档正文入库。"""
    fake = _FakeClient("购物车页面截图，包含商品列表与结算按钮")
    monkeypatch.setattr(
        "app.services.llm_pool.build_client", lambda db, user, slot="text": fake)

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, format="PNG")
    r = client.post(
        "/api/files",
        files={"file": ("截图.png", buf.getvalue(), "image/png")},
        headers={"Authorization": "Bearer " + accounts["user"]["token"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["knowledge_id"]

    # 缓存 txt 描述同步可读（load_chat_file 对话注入路径）
    from app.api.files import load_chat_file
    name, text = load_chat_file(body["file_id"])
    assert name == "截图.png"
    assert "购物车页面截图" in text

    # 后台入库（TestClient 同步执行）→ 描述成为文档正文
    doc = db_session.get(Knowledge, body["knowledge_id"])
    assert doc.parse_status == "ready"
    assert (doc.chunk_count or 0) >= 1
