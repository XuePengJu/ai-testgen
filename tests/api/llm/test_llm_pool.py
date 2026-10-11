"""模型池（V5.0 P1）单元 + API 测试。

覆盖：
1. 错误归类与冷却策略（rate_limit / quota / auth / 404 / 5xx / 网络）
2. resolve_pool 优先级：用户池 > 平台池 > 空；enabled=False 过滤
3. PoolClient 调度：限流切换、auth 切换、全失败抛错、冷却过滤、全冷却破格
4. chat_stream：未产出才切换；已吐字不切换（防两个模型文本混排）
5. build_client 三级回落：池 → 单条配置 → None
6. API：CRUD / 409 判重 / reorder / enabled / health / 权限（guest 403、非 admin 403）
7. 池优先展示口径：pool_first 跳过冷却 / 全冷却取首条；/llm/effective 与 /llm/test-default 走池
8. pool_stats 计数与归属（total 含已停用 / hit 跳过冷却 / 平台池回落 / 全冷却）与
   /llm/effective 的 pools 字段（三槽齐全、旧字段向后兼容、访客也能看到平台池条数）
"""
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.llm_pool import LLMModelPool
from app.models.user import User
from app.services import llm_pool, llm_service
from app.services.langchain_client import LLMError


# ============ 工具 ============

def _user_id(username: str = "alice") -> int:
    db = SessionLocal()
    try:
        return db.execute(select(User).where(User.username == username)).scalar_one().id
    finally:
        db.close()


def _mk_row(owner_id: int, model: str = "m1", slot: str = "text",
            key: str = "sk-test-1", **kw) -> int:
    """插入一条池记录（Key 走真实加密链路），返回 id。"""
    db = SessionLocal()
    try:
        row = LLMModelPool(
            user_id=owner_id, slot=slot, provider="custom", base_url="http://x/v1",
            model=model, key_fingerprint=llm_service.key_fingerprint(key),
            api_key_enc=llm_service.encrypt_key(key, owner_id),
            api_key_tail=key[-4:], **kw,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


@pytest.fixture()
def clean_pool():
    """清空池表（前后各一次），避免用例间互相污染。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(delete(LLMModelPool))
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


def _cand(model: str, cid=None, cooldown_until=None) -> dict:
    return {
        "id": cid, "model": model, "base_url": "http://x/v1", "api_key": "k",
        "provider": "custom", "cooldown_until": cooldown_until,
    }


def _install_fake(monkeypatch, behavior: dict, calls: list):
    """把 llm_pool 内部客户端替成假实现；behavior[model] = (动作, 值)。"""
    class Fake:
        def __init__(self, base_url, api_key, model):
            self.model = model

        def chat(self, messages, **kw):
            calls.append(self.model)
            act, val = behavior.get(self.model, ("ok", "hi"))
            if act == "raise":
                raise LLMError(val)
            return val

        def chat_stream(self, messages, **kw):
            calls.append(self.model)
            act, val = behavior.get(self.model, ("ok", "hi"))
            if act == "error":
                yield ("error", val)
                return
            if act == "partial":
                yield ("delta", "前半段")
                yield ("error", val)
                return
            yield ("delta", val)
            yield ("done", {"full": val, "clean": val})

        def describe_image(self, url, hint=""):
            calls.append(self.model)
            return "desc"

    monkeypatch.setattr(llm_pool, "_Client", Fake)


# ============ 1. 错误归类 / 冷却 ============

def test_classify_error():
    assert llm_pool.classify_error("HTTP 429：限流") == "rate_limit"
    assert llm_pool.classify_error("HTTP 402：额度已用尽") == "quota"
    assert llm_pool.classify_error("HTTP 401：invalid api key") == "auth"
    assert llm_pool.classify_error("HTTP 403：forbidden") == "auth"
    assert llm_pool.classify_error("HTTP 404：model not found") == "not_found"
    assert llm_pool.classify_error("HTTP 503：service busy") == "server"
    assert llm_pool.classify_error("网络错误：ReadTimeout") == "network"
    assert llm_pool.classify_error("HTTP 400：bad param") == "other"


def test_cooldown_policy():
    now = utcnow()
    assert llm_pool.cooldown_until_for("rate_limit", now) == now + timedelta(minutes=5)
    assert llm_pool.cooldown_until_for("server", now) == now + timedelta(minutes=1)
    assert llm_pool.cooldown_until_for("network", now) == now + timedelta(minutes=1)
    quota = llm_pool.cooldown_until_for("quota", now)
    assert quota.hour == 0 and quota.minute == 5 and quota > now
    # 未登记类型 → 不冷却（None）
    assert llm_pool.cooldown_until_for("whatever", now) is None


# ============ 2. resolve_pool 优先级 ============

def test_resolve_pool_user_wins_over_platform(clean_pool):
    uid = _user_id()
    _mk_row(0, model="platform-m", key="sk-platform")
    _mk_row(uid, model="user-m", key="sk-user")
    db = SessionLocal()
    try:
        pool = llm_service.resolve_pool(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert [c["model"] for c in pool] == ["user-m"]
    assert pool[0]["owner_id"] == uid


def test_resolve_pool_falls_back_to_platform(clean_pool):
    uid = _user_id()
    _mk_row(0, model="platform-m", key="sk-platform")
    db = SessionLocal()
    try:
        pool = llm_service.resolve_pool(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert [c["model"] for c in pool] == ["platform-m"]


def test_resolve_pool_skips_disabled_and_orders_by_priority(clean_pool):
    uid = _user_id()
    _mk_row(uid, model="slow", key="sk-1", priority=200)
    _mk_row(uid, model="fast", key="sk-2", priority=10)
    _mk_row(uid, model="off", key="sk-3", priority=1, enabled=False)
    db = SessionLocal()
    try:
        pool = llm_service.resolve_pool(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert [c["model"] for c in pool] == ["fast", "slow"]


def test_resolve_pool_empty_returns_empty(clean_pool):
    db = SessionLocal()
    try:
        assert llm_service.resolve_pool(db, db.get(User, _user_id()), "text") == []
    finally:
        db.close()


# ============ 3. PoolClient 调度 ============

def test_pool_switches_on_rate_limit(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("raise", "HTTP 429：限流"), "m2": ("ok", "pong")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    assert c.chat([{"role": "user", "content": "hi"}]) == "pong"
    assert calls == ["m1", "m2"]
    assert c.switch_log and "m2" in c.switch_log[0]["to"]


def test_pool_switches_on_auth_error(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("raise", "HTTP 401：invalid key"), "m2": ("ok", "ok2")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    assert c.chat([{"role": "user", "content": "hi"}]) == "ok2"
    assert calls == ["m1", "m2"]


def test_pool_raises_when_all_fail(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("raise", "HTTP 429：限流"), "m2": ("raise", "HTTP 503：busy")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "hi"}])
    assert "全部候选失败" in str(ei.value)
    assert calls == ["m1", "m2"]


def test_pool_same_instance_does_not_retry_failed_candidate(monkeypatch):
    """同一实例内失败过的候选不再撞第二次（多批脚本生成场景）。"""
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("raise", "HTTP 401：invalid key"), "m2": ("ok", "ok2")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    c.chat([{"role": "user", "content": "1"}])
    c.chat([{"role": "user", "content": "2"}])
    assert calls == ["m1", "m2", "m2"]


def test_pool_skips_cooling_candidate(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("ok", "a"), "m2": ("ok", "b")}, calls)
    cands = [_cand("m1", cooldown_until=utcnow() + timedelta(minutes=5)), _cand("m2")]
    c = llm_pool.PoolClient(cands, persist=False)
    assert c.chat([{"role": "user", "content": "hi"}]) == "b"
    assert calls == ["m2"]


def test_pool_breaks_glass_when_all_cooling(monkeypatch):
    """全池冷却 → 取最早到期的一条破格尝试，避免任务无限等待。"""
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("ok", "a"), "m2": ("ok", "b")}, calls)
    cands = [
        _cand("m1", cooldown_until=utcnow() + timedelta(minutes=30)),
        _cand("m2", cooldown_until=utcnow() + timedelta(minutes=5)),
    ]
    c = llm_pool.PoolClient(cands, persist=False)
    assert c.chat([{"role": "user", "content": "hi"}]) == "b"
    assert calls == ["m2"]


# ============ 4. chat_stream 切换边界 ============

def _collect(gen):
    return list(gen)


def test_stream_switches_when_no_output(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("error", "HTTP 429：限流"), "m2": ("ok", "正文")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    events = _collect(c.chat_stream([{"role": "user", "content": "hi"}]))
    assert calls == ["m1", "m2"]
    assert any(e[0] == "delta" and e[1] == "正文" for e in events)


def test_stream_does_not_switch_after_partial_output(monkeypatch):
    """已吐字再切换会出现两个模型文本混排 → 必须不切。"""
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("partial", "HTTP 503：busy"), "m2": ("ok", "不该出现")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    events = _collect(c.chat_stream([{"role": "user", "content": "hi"}]))
    assert calls == ["m1"]
    assert events[0] == ("delta", "前半段")
    done = [e for e in events if e[0] == "done"][-1]
    assert done[1]["degraded"] is True
    assert not any(e[0] == "delta" and e[1] == "不该出现" for e in events)


def test_stream_error_when_all_fail(monkeypatch):
    calls: list[str] = []
    _install_fake(monkeypatch, {"m1": ("error", "HTTP 429：限流"), "m2": ("error", "HTTP 429：限流")}, calls)
    c = llm_pool.PoolClient([_cand("m1"), _cand("m2")], persist=False)
    events = _collect(c.chat_stream([{"role": "user", "content": "hi"}]))
    assert events[-1][0] == "error"


# ============ 5. build_client 三级回落 ============

def test_build_client_prefers_pool(clean_pool):
    uid = _user_id()
    _mk_row(uid, model="pooled", key="sk-1")
    db = SessionLocal()
    try:
        c = llm_pool.build_client(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert isinstance(c, llm_pool.PoolClient)
    assert c.candidates[0]["model"] == "pooled"


def test_build_client_falls_back_to_single_config(clean_pool):
    """池空 → 回落 resolve_effective 的单条客户端（行为与上线池化前一致）。"""
    uid = _user_id()
    db = SessionLocal()
    try:
        c = llm_pool.build_client(db, db.get(User, uid), "text")
    finally:
        db.close()
    # 池空时绝不能返回 PoolClient；有单条配置则返回单条客户端，无配置则 None
    assert not isinstance(c, llm_pool.PoolClient)


def test_describe_model_pool_text(clean_pool):
    uid = _user_id()
    _mk_row(uid, model="pooled", key="sk-1")
    db = SessionLocal()
    try:
        desc = llm_pool.describe_model(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert "模型池 1 条" in desc and "pooled" in desc


# ============ 6. API ============

def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


_BODY = {"provider": "custom", "base_url": "http://api.test/v1",
         "model": "m-a", "api_key": "sk-aaaa1111"}


def test_pool_api_crud(client, accounts, clean_pool):
    h = _h(accounts["user"]["token"])
    r = client.post("/api/llm/pool/text", json=_BODY, headers=h)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["model"] == "m-a" and item["api_key_masked"] == "****1111"
    assert item["enabled"] is True and item["effective"] is True
    item_id = item["id"]

    # 重复配置 → 409
    dup = client.post("/api/llm/pool/text", json=_BODY, headers=h)
    assert dup.status_code == 409 and "重复" in dup.json()["detail"]

    # 同端点同模型换 Key → 允许（指纹不同）
    other = dict(_BODY, api_key="sk-bbbb2222")
    assert client.post("/api/llm/pool/text", json=other, headers=h).status_code == 200
    assert len(client.get("/api/llm/pool/text", headers=h).json()) == 2

    # 更新（api_key 留空 = 保留原 Key）
    r = client.put(f"/api/llm/pool/text/{item_id}",
                   json=dict(_BODY, model="m-a2", api_key=None), headers=h)
    assert r.status_code == 200 and r.json()["model"] == "m-a2"
    assert r.json()["api_key_masked"] == "****1111"

    # 启停
    r = client.patch(f"/api/llm/pool/text/{item_id}/enabled",
                     json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False

    # 健康
    hp = client.get("/api/llm/pool/text/health", headers=h).json()
    assert hp["total"] == 2 and hp["enabled"] == 1 and hp["available"] == 1

    # 排序：把第二条提到最前
    ids = [i["id"] for i in client.get("/api/llm/pool/text", headers=h).json()]
    r = client.post("/api/llm/pool/text/reorder", json={"ids": ids[::-1]}, headers=h)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == ids[::-1]

    # 删除
    assert client.delete(f"/api/llm/pool/text/{item_id}", headers=h).status_code == 204
    assert len(client.get("/api/llm/pool/text", headers=h).json()) == 1


def test_pool_api_validation(client, accounts, clean_pool):
    h = _h(accounts["user"]["token"])
    # 非法 slot
    assert client.get("/api/llm/pool/bogus", headers=h).status_code == 400
    # 未知厂商
    r = client.post("/api/llm/pool/text", json=dict(_BODY, provider="nope"), headers=h)
    assert r.status_code == 400
    # 非免费厂商不填 Key
    r = client.post("/api/llm/pool/text", json=dict(_BODY, api_key=""), headers=h)
    assert r.status_code == 400
    # model 空 → 422（schema 校验）
    r = client.post("/api/llm/pool/text", json=dict(_BODY, model=""), headers=h)
    assert r.status_code == 422
    # 不存在 id
    assert client.delete("/api/llm/pool/text/999999", headers=h).status_code == 404


def test_pool_api_isolated_between_users(client, accounts, clean_pool):
    """用户池互相隔离（各人只看自己的池）。"""
    _mk_row(0, model="platform-m", key="sk-platform")
    hu = _h(accounts["user"]["token"])
    ha = _h(accounts["admin"]["token"])
    assert client.post("/api/llm/pool/text", json=_BODY, headers=hu).status_code == 200
    assert len(client.get("/api/llm/pool/text", headers=hu).json()) == 1
    assert client.get("/api/llm/pool/text", headers=ha).json() == []
    # 平台池对普通用户只读可见，且标注 effective=False（用户池已生效）
    plat = client.get("/api/llm/platform-pool/text", headers=hu).json()
    assert len(plat) == 1 and plat[0]["effective"] is False


def test_pool_api_permission(client, accounts, fresh_guest, clean_pool):
    gt, _ = fresh_guest
    # 访客不能管理个人池
    assert client.get("/api/llm/pool/text", headers=_h(gt)).status_code == 403
    # 普通用户不能改平台池
    hu = _h(accounts["user"]["token"])
    r = client.post("/api/llm/platform-pool/text", json=_BODY, headers=hu)
    assert r.status_code == 403
    # admin 可以
    ha = _h(accounts["admin"]["token"])
    assert client.post("/api/llm/platform-pool/text", json=_BODY, headers=ha).status_code == 200


def test_pool_test_endpoint_uses_saved_key(client, accounts, clean_pool, monkeypatch):
    h = _h(accounts["user"]["token"])
    item = client.post("/api/llm/pool/text", json=_BODY, headers=h).json()

    seen = {}

    def fake_test(base_url, api_key, model, kind="chat"):
        seen.update(base_url=base_url, api_key=api_key, model=model, kind=kind)
        return {"ok": True, "latency_ms": 12, "reply": "ok"}

    monkeypatch.setattr(llm_service, "test_connectivity", fake_test)
    r = client.post(f'/api/llm/pool/text/{item["id"]}/test', headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["api_key"] == "sk-aaaa1111" and seen["model"] == "m-a"


# ============ 7. 池优先的展示口径（V5.0 P2） ============

def test_pool_first_skips_cooling(clean_pool):
    """pool_first 取第一条未冷却候选（展示/测通与实际调度口径一致）。"""
    uid = _user_id()
    _mk_row(uid, model="cooling", key="sk-c", priority=10,
            cooldown_until=utcnow() + timedelta(minutes=5))
    _mk_row(uid, model="ready", key="sk-r", priority=20)
    db = SessionLocal()
    try:
        cfg = llm_pool.pool_first(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert cfg["model"] == "ready"


def test_pool_first_returns_first_when_all_cooling(clean_pool):
    """全池冷却 → 仍返回第一条（界面显示黄灯，而不是整块空白）。"""
    uid = _user_id()
    _mk_row(uid, model="c1", key="sk-1", priority=10,
            cooldown_until=utcnow() + timedelta(minutes=30))
    _mk_row(uid, model="c2", key="sk-2", priority=20,
            cooldown_until=utcnow() + timedelta(minutes=30))
    db = SessionLocal()
    try:
        cfg = llm_pool.pool_first(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert cfg["model"] == "c1"


def test_pool_first_empty_returns_none(clean_pool):
    db = SessionLocal()
    try:
        assert llm_pool.pool_first(db, db.get(User, _user_id()), "text") is None
    finally:
        db.close()


def test_effective_prefers_pool(client, accounts, clean_pool):
    """池非空 → /llm/effective 展示池首条且 source=pool（不再显示单条配置）。"""
    uid = _user_id()
    _mk_row(uid, model="pooled-a", key="sk-a", priority=10)
    _mk_row(uid, model="pooled-b", key="sk-b", priority=20)
    d = client.get("/api/llm/effective", headers=_h(accounts["user"]["token"])).json()
    assert d["source"] == "pool"
    assert d["text"]["model"] == "pooled-a"      # 优先级更高者


def test_test_default_prefers_pool(client, accounts, clean_pool, monkeypatch):
    """一键测通走池内首条候选（服务端持 Key 解密后测试）。"""
    uid = _user_id()
    _mk_row(uid, model="pooled-x", key="sk-x", priority=10)
    seen = {}

    def fake_test(base_url, api_key, model, kind="chat"):
        seen.update(base_url=base_url, api_key=api_key, model=model)
        return {"ok": True, "latency_ms": 5}

    monkeypatch.setattr(llm_service, "test_connectivity", fake_test)
    r = client.post("/api/llm/test-default/text", headers=_h(accounts["user"]["token"]))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["model"] == "pooled-x" and seen["api_key"] == "sk-x"


# ============ 8. pool_stats 与 /llm/effective 的 pools 字段（V5.1 P1） ============

def test_pool_stats_empty(clean_pool):
    """无池 → 归属为空、计数全 0（前端显示「未启用池」）。"""
    db = SessionLocal()
    try:
        st = llm_pool.pool_stats(db, db.get(User, _user_id()), "text")
    finally:
        db.close()
    assert st == {"active": False, "owner": None, "total": 0, "enabled": 0,
                  "available": 0, "cooling": 0, "hit": 0}


def test_pool_stats_counts_and_hit(clean_pool):
    """total 含已停用（与池卡卡头同口径）；hit 跳过冷却中的那条。"""
    uid = _user_id()
    _mk_row(uid, model="s1", key="sk-s1", priority=10,
            cooldown_until=utcnow() + timedelta(minutes=30))
    _mk_row(uid, model="s2", key="sk-s2", priority=20)
    _mk_row(uid, model="s3", key="sk-s3", priority=30, enabled=False)
    db = SessionLocal()
    try:
        st = llm_pool.pool_stats(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert st["owner"] == "personal" and st["active"] is True
    assert st["total"] == 3        # 含已停用的 s3
    assert st["enabled"] == 2
    assert st["available"] == 1    # s1 冷却中
    assert st["cooling"] == 1
    assert st["hit"] == 2          # 跳过 s1，下一条会命中第 2 条


def test_pool_stats_platform_fallback(clean_pool):
    """用户池空 → 归属平台池（用户视角看到的是平台条数）。"""
    _mk_row(0, model="p1", key="sk-p1")
    db = SessionLocal()
    try:
        st = llm_pool.pool_stats(db, db.get(User, _user_id()), "text")
    finally:
        db.close()
    assert st["owner"] == "platform" and st["total"] == 1 and st["hit"] == 1


def test_pool_stats_all_cooling(clean_pool):
    """有池但全冷却 → active=False、hit=0（前端应显示「全部不可用」而非「优先 ①」）。"""
    uid = _user_id()
    _mk_row(uid, model="c1", key="sk-c1",
            cooldown_until=utcnow() + timedelta(minutes=30))
    db = SessionLocal()
    try:
        st = llm_pool.pool_stats(db, db.get(User, uid), "text")
    finally:
        db.close()
    assert st["owner"] == "personal" and st["active"] is False
    assert st["total"] == 1 and st["available"] == 0
    assert st["cooling"] == 1 and st["hit"] == 0


def test_effective_pools_field(client, accounts, clean_pool):
    """三槽池现状随 /llm/effective 下发；旧字段保持不动（向后兼容）。"""
    uid = _user_id()
    _mk_row(uid, model="e1", key="sk-e1", priority=10)
    _mk_row(uid, model="e2", key="sk-e2", priority=20, slot="vision")
    d = client.get("/api/llm/effective", headers=_h(accounts["user"]["token"])).json()

    # 旧字段原样保留
    assert d["source"] == "pool" and d["text"]["model"] == "e1"
    assert "embedding_source" in d

    pools = d["pools"]
    assert set(pools) == {"text", "vision", "embedding"}
    assert pools["text"]["owner"] == "personal" and pools["text"]["total"] == 1
    assert pools["vision"]["total"] == 1 and pools["vision"]["hit"] == 1
    # 没配池的槽 → 未启用（不再像旧版那样因 source 只反映 text 槽而误判）
    assert pools["embedding"]["owner"] is None and pools["embedding"]["total"] == 0


def test_effective_pools_visible_for_guest(client, accounts, fresh_guest, clean_pool):
    """本轮立项目标：访客无权读池列表，但仍能从 /llm/effective 看到平台池条数。"""
    gt, _ = fresh_guest
    _mk_row(0, model="plat-a", key="sk-pg1", priority=10)
    _mk_row(0, model="plat-b", key="sk-pg2", priority=20)

    # 前提先立住：访客读池列表确实 403
    assert client.get("/api/llm/pool/text", headers=_h(gt)).status_code == 403

    d = client.get("/api/llm/effective", headers=_h(gt)).json()
    assert d["pools"]["text"]["owner"] == "platform"
    assert d["pools"]["text"]["total"] == 2
    assert d["pools"]["text"]["hit"] == 1
