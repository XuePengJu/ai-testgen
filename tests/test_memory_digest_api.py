"""P2 手动记忆提炼 API 测试（POST /conversations/{id}/memory/digest + GET state）。

覆盖：状态流转 idle→running→done（mock LLM）/ 并发第二次 409 / 失败写
mem_error / running 超 5min 自愈回落 idle / 访客 403 / manual 幂等短路不烧 LLM。
"""
from datetime import timedelta

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.conversation import Conversation

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _mk_conv(client, token: str) -> str:
    """建会话 + 一轮消息（不自动打标，由用例按需控制状态机）。"""
    r = client.post("/api/conversations", json={"title": "回归测试会话"}, headers=HDR(token))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages",
                    json={"role": "user", "content": "订单模块要校验库存上限"},
                    headers=HDR(token))
    assert r.status_code == 201, r.text
    r = client.post(f"/api/conversations/{cid}/messages",
                    json={"role": "assistant", "content": "已记录：库存上限 500 件"},
                    headers=HDR(token))
    assert r.status_code == 201, r.text
    return cid


def _fake_llm(calls: list):
    def _fake(prompt: str, prev_memory: str = "") -> str:
        calls.append(prompt)
        return ("## 背景\n订单模块\n\n## 关键结论\n库存上限 500 件\n\n"
                "## 业务规则\n超限拦截\n\n## 待办与遗留\n（暂无）")
    return _fake


def _set_status(cid: str, status: str, ago_min: int = 0) -> None:
    """直改 DB 会话状态机（模拟并发占用 / 残留 / 自愈前置）。"""
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        conv.mem_status = status
        if ago_min:
            conv.mem_at = utcnow() - timedelta(minutes=ago_min)
        db.commit()
    finally:
        db.close()


def test_digest_flow_idle_to_done(client, monkeypatch):
    """正常流转：idle → POST 202 running（后台同步跑完）→ done + doc_id。"""
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))
    token = _register(client, "memapi1")
    cid = _mk_conv(client, token)

    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.status_code == 200 and r.json()["status"] == "idle", r.text

    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202, r.text
    assert r.json() == {"status": "running"}

    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    body = r.json()
    assert body["status"] == "done", body
    assert body["doc_id"]
    assert body["error"] is None
    assert body["msg_count"] == 2
    assert calls, "mock LLM 必须被调用"


def test_digest_conflict_409(client, monkeypatch):
    """已有实例在跑（mem_status=running）→ 第二次 POST 409，不再触发。"""
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))
    token = _register(client, "memapi2")
    cid = _mk_conv(client, token)
    _set_status(cid, "running")
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 409, r.text
    assert not calls  # 409 路径绝不触发 LLM


def test_digest_failure_writes_error(client, monkeypatch):
    """提炼失败：mem_status=failed + mem_error 落库（按钮不卡死）。"""
    def _boom(prompt, prev_memory=""):
        raise RuntimeError("上游模型超时")
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _boom)
    token = _register(client, "memapi3")
    cid = _mk_conv(client, token)

    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202, r.text  # 抢占成功，失败发生在后台
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    body = r.json()
    assert body["status"] == "failed", body
    assert "上游模型超时" in (body["error"] or "")


def test_running_stale_self_heal(client):
    """running 残留超 5 分钟（进程重启）→ GET state 自愈回落 idle。"""
    token = _register(client, "memapi4")
    cid = _mk_conv(client, token)
    _set_status(cid, "running", ago_min=6)
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.json()["status"] == "idle", r.text
    # 刚置 running（< 5min）不自愈
    _set_status(cid, "running", ago_min=1)
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.json()["status"] == "running", r.text


def test_guest_forbidden(client, fresh_guest):
    """访客：先建会话（通过归属守卫），触发 digest → 403 注册引导。"""
    gt, _ = fresh_guest
    r = client.post("/api/conversations", json={"title": "访客会话"}, headers=HDR(gt))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(gt))
    assert r.status_code == 403, r.text
    assert "注册" in r.json()["detail"]


def test_manual_idempotent_shortcut(client, monkeypatch):
    """manual 连点：水位已到最新且记忆文档有效 → skipped，不再烧 LLM。"""
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))
    token = _register(client, "memapi5")
    cid = _mk_conv(client, token)

    # 第一次：真实提炼（会话消息存在但未打标，manual=True 不看 mem_dirty）
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202 and r.json() == {"status": "running"}
    assert len(calls) == 1
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.json()["status"] == "done"

    # 连点第二次：LLM 零调用（幂等短路），状态仍 done
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202, r.text
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.json()["status"] == "done"
    assert len(calls) == 1, "manual 幂等短路必须跳过 LLM"

    # 直接调 process_conversation 验证返回 skipped（供 run_daily 统计口径）
    from app.jobs.chat_memory import process_conversation
    db = SessionLocal()
    try:
        out = process_conversation(db, db.get(Conversation, cid), manual=True)
        assert out["memory"] == "skipped"
        assert out["doc_id"]
    finally:
        db.close()
    assert len(calls) == 1
