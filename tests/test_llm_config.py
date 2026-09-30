"""V2.4 FR-I → V5.4：模型接入（厂商预设 / 模型池 Key 加密 / 生效优先级 / 连通测试 / 管线注入）。

V5.4 起单条配置（/llm/config、/llm/platform-config）已下线，配置统一走模型池
（/llm/pool、/llm/platform-pool）；本文件所有"配置"用例均经池端点建立。
外部 LLM 调用统一 mock `LangChainClient.chat`（V3 迁移后适配层），离线可跑。
"""
import time

import pytest

from app.core import config
from app.core.db import SessionLocal
from app.models.llm_pool import LLMModelPool
from app.models.user import User
from app.services import llm_service
from app.services.langchain_client import LangChainClient

_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _alice(db):
    return db.query(User).filter(User.username == "alice").first()


def _add_pool(client, tok, slot="text", **over):
    """往个人池加一条（默认百炼 qwen-plus），返回响应。"""
    body = {"provider": "bailian", "base_url": _URL,
            "model": "qwen-plus", "api_key": "sk-test-1234", **over}
    return client.post(f"/api/llm/pool/{slot}", headers=_h(tok), json=body)


# ---------- 厂商预设 ----------

def test_providers_require_auth(client):
    assert client.get("/api/llm/providers").status_code == 401


def test_providers_listed(client, accounts):
    r = client.get("/api/llm/providers", headers=_h(accounts["user"]["token"]))
    assert r.status_code == 200
    data = r.json()
    # 三家重点厂商 + 智谱 Coding Plan 专用端点 + 自定义兜底
    for k in ("bailian", "zhipu", "zhipu_coding", "hunyuan", "deepseek", "kimi", "doubao", "custom"):
        assert k in data, f"缺厂商预设 {k}"
    assert data["zhipu_coding"]["base_url"].endswith("/api/coding/paas/v4")
    # 百炼有视觉模型标注
    assert any(m["vision"] for m in data["bailian"]["models"])
    # deepseek 全系不支持图像
    assert not any(m["vision"] for m in data["deepseek"]["models"])


# ---------- 访客限制 ----------

def test_guest_cannot_config(client, fresh_guest):
    token, _ = fresh_guest
    body = {"provider": "bailian", "base_url": _URL,
            "model": "qwen-plus", "api_key": "sk-x"}
    assert client.get("/api/llm/pool/text", headers=_h(token)).status_code == 403
    assert client.post("/api/llm/pool/text", headers=_h(token), json=body).status_code == 403


# ---------- 个人池：加密落库 + 脱敏回显 ----------

def _clear_pools(db, *user_ids):
    """清空指定归属（含平台池 0）的池条目，制造干净的初始状态。"""
    db.query(LLMModelPool).filter(LLMModelPool.user_id.in_(user_ids)).delete()
    db.commit()


def test_user_config_roundtrip(client, accounts, db_session):
    tok = accounts["user"]["token"]
    r = _add_pool(client, tok)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["api_key_masked"] == "****1234"
    assert "api_key" not in out          # 永不回显明文

    # 落库为密文
    u = _alice(db_session)
    row = (db_session.query(LLMModelPool)
           .filter(LLMModelPool.user_id == u.id, LLMModelPool.slot == "text").first())
    assert row and row.api_key_enc
    assert "sk-test-1234" not in row.api_key_enc
    assert llm_service.decrypt_key(row.api_key_enc, u.id) == "sk-test-1234"


def test_put_without_key_keeps_old(client, accounts, db_session):
    """池条目更新时未提供 api_key → 旧 Key 保留。"""
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    _clear_pools(db_session, u.id, 0)   # 清个人池 + 平台池，避免上一用例残留干扰
    item = _add_pool(client, tok).json()
    r = client.put(f"/api/llm/pool/text/{item['id']}", headers=_h(tok), json={
        "provider": "bailian", "base_url": _URL, "model": "qwen-max"})
    assert r.status_code == 200 and r.json()["model"] == "qwen-max"
    u = _alice(db_session)
    row = (db_session.query(LLMModelPool)
           .filter(LLMModelPool.user_id == u.id, LLMModelPool.slot == "text").first())
    # 未提供 api_key → 旧 Key 保留
    assert llm_service.decrypt_key(row.api_key_enc, u.id) == "sk-test-1234"


def test_text_slot_requires_key(client, accounts, db_session):
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    # 先清掉已有池条目，制造"无 Key"状态
    db_session.query(LLMModelPool).filter(LLMModelPool.user_id == u.id).delete()
    db_session.commit()
    r = client.post("/api/llm/pool/text", headers=_h(tok), json={
        "provider": "bailian", "base_url": _URL, "model": "qwen-plus"})
    assert r.status_code == 400 and "API Key" in r.json()["detail"]


def test_free_provider_no_key_allowed(client, accounts, db_session, monkeypatch):
    """免费厂商(魔搭)允许不填 API Key，保存后 effective 解析出服务端 Key 可用。"""
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "ms-test-dummy")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    db_session.query(LLMModelPool).filter(LLMModelPool.user_id == u.id).delete()
    db_session.commit()
    # 个人池选魔搭免费模型，不传 api_key → 应放行（200）
    r = client.post("/api/llm/pool/text", headers=_h(tok), json={
        "provider": "modelscope",
        "base_url": config.MODELSCOPE_BASE_URL, "model": config.MODELSCOPE_MODEL})
    assert r.status_code == 200, r.text
    eff = client.get("/api/llm/effective", headers=_h(tok)).json()
    assert eff["source"] == "pool"                    # 池存在时统一标 pool
    assert eff["pools"]["text"]["owner"] == "personal"
    assert eff["text"]["provider"] == "modelscope"
    assert eff["text"]["model"] == config.MODELSCOPE_MODEL
    assert "api_key" not in eff["text"]      # 对外视图无 Key


def test_free_provider_no_server_key_falls_mock(client, accounts, db_session, monkeypatch):
    """免费厂商但服务端未配 Key：条目可入池，但解析时无可用 Key → 回落 mock（不虚报可用）。"""
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    db_session.query(LLMModelPool).filter(LLMModelPool.user_id == u.id).delete()
    db_session.commit()
    r = client.post("/api/llm/pool/text", headers=_h(tok), json={
        "provider": "modelscope",
        "base_url": config.MODELSCOPE_BASE_URL, "model": config.MODELSCOPE_MODEL})
    assert r.status_code == 200, r.text
    eff = client.get("/api/llm/effective", headers=_h(tok)).json()
    assert eff["source"] == "mock"
    assert eff["text"] is None


def test_invalid_slot_rejected(client, accounts):
    r = client.post("/api/llm/pool/foo", headers=_h(accounts["user"]["token"]), json={
        "provider": "bailian", "base_url": _URL, "model": "m", "api_key": "sk-x"})
    assert r.status_code == 400


def test_dup_entry_rejected(client, accounts):
    """同端点 + 同模型 + 同 Key 的池条目 → 409 判重。"""
    tok = accounts["user"]["token"]
    assert _add_pool(client, tok).status_code == 200
    r2 = _add_pool(client, tok)
    assert r2.status_code == 409


# ---------- 生效优先级：个人池 > 平台池 > mock ----------

def test_effective_priority(client, accounts, monkeypatch, db_session):
    # 隔离服务器 env 兜底 key，确保下面只验证个人池 / 平台池两层
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    _clear_pools(db_session, u.id, 0)     # 清个人池 + 平台池，密封起始状态

    # alice 个人池配 text
    assert _add_pool(client, tok).status_code == 200
    eff = client.get("/api/llm/effective", headers=_h(tok)).json()
    assert eff["source"] == "pool"
    assert eff["pools"]["text"]["owner"] == "personal"
    assert eff["text"]["model"] == "qwen-plus"
    assert "api_key" not in eff["text"]      # 对外视图无 Key
    assert eff["vision"] is None

    # admin 配平台池（text + vision）
    atok = accounts["admin"]["token"]
    r = client.post("/api/llm/platform-pool/text", headers=_h(atok), json={
        "provider": "zhipu", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.5", "api_key": "sk-platform-key"})
    assert r.status_code == 200
    zhipu_id = r.json()["id"]
    client.post("/api/llm/platform-pool/vision", headers=_h(atok), json={
        "provider": "bailian", "base_url": _URL,
        "model": "qwen-vl-plus", "api_key": "sk-platform-vl"})

    # alice 仍优先自己的池（personal > platform），vision 个人池为空 → 用平台池
    eff2 = client.get("/api/llm/effective", headers=_h(tok)).json()
    assert eff2["pools"]["text"]["owner"] == "personal"
    assert eff2["text"]["model"] == "qwen-plus"
    assert eff2["vision"] and eff2["vision"]["model"] == "qwen-vl-plus"

    # 新用户 carol：无个人池 → 走平台池
    client.post("/api/auth/register", json={
        "username": "carol-llm", "email": "carol-llm@test.com", "password": "Carol12345"})
    r = client.post("/api/auth/login", data={"username": "carol-llm", "password": "Carol12345"})
    btok = r.json()["access_token"]
    eff3 = client.get("/api/llm/effective", headers=_h(btok)).json()
    assert eff3["pools"]["text"]["owner"] == "platform"
    assert eff3["text"]["model"] == "glm-4.5"
    assert eff3["vision"]["model"] == "qwen-vl-plus"

    # 平台池内切换：停用 zhipu 条目 → 魔搭条目顶上（免费厂商服务端 Key 兜底）
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "ms-test-dummy")
    r = client.post("/api/llm/platform-pool/text", headers=_h(atok), json={
        "provider": "modelscope",
        "base_url": config.MODELSCOPE_BASE_URL, "model": config.MODELSCOPE_MODEL})
    assert r.status_code == 200
    r = client.patch(f"/api/llm/platform-pool/text/{zhipu_id}/enabled",
                     headers=_h(atok), json={"enabled": False})
    assert r.status_code == 200
    eff4 = client.get("/api/llm/effective", headers=_h(btok)).json()
    assert eff4["pools"]["text"]["owner"] == "platform"
    assert eff4["text"]["provider"] == "modelscope"
    assert eff4["text"]["model"] == config.MODELSCOPE_MODEL


def test_platform_pool_read_open_write_admin(client, accounts):
    """平台池 GET 开放给所有登录用户（摘要/回显用）；写操作仍仅 admin。"""
    # 普通用户可读
    r = client.get("/api/llm/platform-pool/text", headers=_h(accounts["user"]["token"]))
    assert r.status_code == 200 and isinstance(r.json(), list)
    # admin 可读
    r = client.get("/api/llm/platform-pool/text", headers=_h(accounts["admin"]["token"]))
    assert r.status_code == 200 and isinstance(r.json(), list)
    # 普通用户写入仍 403
    assert client.post("/api/llm/platform-pool/text", headers=_h(accounts["user"]["token"]),
                       json={"provider": "bailian", "base_url": _URL,
                             "model": "qwen-plus", "api_key": "sk-x"}).status_code in (401, 403)


def test_delete_slot(client, accounts, db_session):
    tok = accounts["user"]["token"]
    item = _add_pool(client, tok, slot="vision", model="qwen-vl-plus",
                     api_key="sk-vl-5678").json()
    r = client.delete(f"/api/llm/pool/vision/{item['id']}", headers=_h(tok))
    assert r.status_code == 204
    u = _alice(db_session)
    assert (db_session.query(LLMModelPool)
            .filter(LLMModelPool.user_id == u.id, LLMModelPool.slot == "vision").count()) == 0


# ---------- 连通测试 ----------

def test_connectivity_ok_and_fail(client, accounts, monkeypatch):
    tok = accounts["user"]["token"]
    seen = {}

    def fake_chat(self, messages, temperature=0.3, max_tokens=8192, timeout=180,
                  enable_thinking=None):
        seen["thinking"] = enable_thinking
        assert self.api_key == "sk-test-1234"
        assert self.model == "qwen-plus"
        return "ok"

    monkeypatch.setattr(LangChainClient, "chat", fake_chat)
    r = client.post("/api/llm/test", headers=_h(tok), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": "sk-test-1234"})
    d = r.json()
    assert d["ok"] is True and d["reply"] == "ok" and d["latency_ms"] >= 0
    # V5.4：连通测试强制关思考（只验 Key/端点/模型，不浪费时间推理）
    assert seen["thinking"] is False

    def bad_chat(self, messages, temperature=0.3, max_tokens=8192, timeout=180,
                 enable_thinking=None):
        raise llm_service.LLMError("HTTP 401：无效的 API Key")

    monkeypatch.setattr(LangChainClient, "chat", bad_chat)
    r2 = client.post("/api/llm/test", headers=_h(tok), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": "sk-bad"})
    assert r2.json()["ok"] is False and "401" in r2.json()["error"]


def test_connectivity_reuses_pool_key(client, accounts, monkeypatch):
    """api_key 留空 → 从池里复用同厂商已存 Key（个人池优先）。"""
    tok = accounts["user"]["token"]
    seen = {}

    def fake_chat(self, messages, temperature=0.3, max_tokens=8192, timeout=180,
                  enable_thinking=None):
        seen["key"] = self.api_key
        return "ok"

    monkeypatch.setattr(LangChainClient, "chat", fake_chat)
    r = client.post("/api/llm/test", headers=_h(tok), json={
        "provider": "bailian", "base_url": _URL, "model": "qwen-plus", "api_key": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["key"] == "sk-test-1234"


def test_connectivity_requires_key(client, accounts):
    """无同厂商池条目且未填 Key → 400 明确报错。"""
    db = SessionLocal()
    db.query(LLMModelPool).filter(LLMModelPool.user_id == 0).delete()
    db.commit()
    db.close()
    r = client.post("/api/llm/test", headers=_h(accounts["admin"]["token"]), json={
        "provider": "custom", "base_url": _URL, "model": "qwen-plus", "api_key": ""})
    assert r.status_code == 400


# ---------- 生成管线：注入真实 client ----------

def test_generate_with_injected_client():
    """CaseGenerator 优先使用平台注入的 client（OpenAI 兼容）。"""
    from app.services.pipeline_lib import lib_generate
    from src.models.testcase import RequirementUnit

    class FakeClient:
        def generate(self, prompt):
            return ('[{"title":"AI注入生成用例","module":"登录","case_type":"正向",'
                    '"priority":"P1","steps":["输入用户名","输入密码","点击登录"],'
                    '"expected":"登录成功"}]')

    units = [RequirementUnit(name="登录", kind="action", description="用户登录功能")]
    cases = lib_generate(units, client=FakeClient())
    assert any(c.title == "AI注入生成用例" for c in cases)


def test_full_task_with_real_llm_path(client, accounts, monkeypatch, db_session):
    """端到端：池里配好 Key 的用户提交任务 → 引擎走真实调用路径（LangChain mock）。"""
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    _clear_pools(db_session, u.id, 0)     # 密封：只留本用例的池条目
    assert _add_pool(client, tok).status_code == 200

    def fake_chat(self, messages, temperature=0.3, max_tokens=8192, timeout=180,
                  enable_thinking=None):
        assert self.base_url == _URL and self.api_key == "sk-test-1234"
        return ('[{"title":"真实模型用例","module":"采购","case_type":"正向","priority":"P1",'
                '"steps":["填写采购单","提交"],"expected":"创建成功"}]')

    monkeypatch.setattr(LangChainClient, "chat", fake_chat)
    r = client.post("/api/tasks", headers=_h(tok), data={
        "text": "采购管理 - 采购订单创建。功能点：新增、编辑、提交审批。",
        "kind": "business", "formats": "json", "name": "LLM路径任务"})
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]

    # 异步队列：轮询等待终态
    deadline = time.time() + 15
    t = {"status": "pending"}
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}", headers=_h(tok)).json()
        if t["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert t["status"] == "completed", t["steps"]
    gen = [s for s in t["steps"] if s["name"] == "generator"][0]
    # 摘要里标注真实模型（非 mock）
    assert "qwen-plus" in gen["output_summary"]
    assert "mock" not in gen["output_summary"]


# ---------- 两段式视觉理解 ----------

class _FakeVision:
    def describe_image(self, url, hint=""):
        return "登录页面截图：包含用户名、密码输入框和登录按钮"


def test_extract_image_refs():
    text = "说明文字 ![登录页](https://x.com/login.png) 以及 data:image/png;base64,AAAA"
    refs = llm_service.extract_image_refs(text)
    assert len(refs) == 2
    assert refs[0] == "https://x.com/login.png"


def test_vision_enrich_replaces_images():
    text = "登录功能\n![登录页](https://x.com/login.png)\n"
    out, n = llm_service.vision_enrich(text, _FakeVision())
    assert n == 1
    assert "截图解读" in out
    assert "https://x.com/login.png" not in out


def test_vision_note_without_vision_model(client, accounts, monkeypatch):
    """未配置视觉模型时，含图片输入不报错，解析步骤标注'已忽略图片'。"""
    tok = accounts["user"]["token"]
    r = client.post("/api/tasks", headers=_h(tok), data={
        "text": "登录功能\n![登录页](https://x.com/login.png)\n功能点：登录、找回密码。",
        "kind": "business", "formats": "json", "name": "含图任务"})
    assert r.status_code == 201
    deadline = time.time() + 15
    t = {"status": "pending"}
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{r.json()['id']}", headers=_h(tok)).json()
        if t["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert t["status"] == "completed"
    parser = [s for s in t["steps"] if s["name"] == "parser"][0]
    assert "图片" in parser["output_summary"] and "忽略" in parser["output_summary"]
