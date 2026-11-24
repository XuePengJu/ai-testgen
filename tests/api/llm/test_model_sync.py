"""模型配置同步（V9.6）：正式站平台池 → 演示站。

覆盖：
- 导出：含明文 Key、保留 disabled、无可用行时带 env 兜底
- 导入：重新加密可解、按槽位替换、运行时字段清零、去重、embedding 变更标记
- 内部接口：令牌未配置/缺失/错误 403，正确令牌 200；演示站才接受 import
- 一键同步端点：仅 admin、仅正式站；push 参数正确
"""
import pytest
from sqlalchemy import delete, select

from app.core import config, demo_config
from app.core.db import SessionLocal
from app.models.llm_pool import LLMModelPool
from app.services import llm_service, model_sync


# ============ 工具 ============

def _mk_platform_row(slot: str, model: str, key: str, *, enabled: bool = True,
                     priority: int = 10, base_url: str = "http://x/v1") -> int:
    db = SessionLocal()
    try:
        row = LLMModelPool(
            user_id=0, slot=slot, provider="custom", base_url=base_url, model=model,
            api_key_enc=llm_service.encrypt_key(key, 0) if key else "",
            api_key_tail=key[-4:] if key else "",
            key_fingerprint=llm_service.key_fingerprint(key),
            priority=priority, enabled=enabled,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _rows(slot: str) -> list[LLMModelPool]:
    db = SessionLocal()
    try:
        return list(db.execute(
            select(LLMModelPool).where(
                LLMModelPool.user_id == 0, LLMModelPool.slot == slot,
            ).order_by(LLMModelPool.priority.asc())
        ).scalars().all())
    finally:
        db.close()


@pytest.fixture()
def clean_platform():
    """只清平台池（user_id=0），避免影响其他用例的个人池。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(delete(LLMModelPool).where(LLMModelPool.user_id == 0))
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


def _payload(slot: str, items: list[dict]) -> dict:
    return {"version": 1, "source": "main", "slots": {slot: items}}


def _item(**kw) -> dict:
    base = {
        "provider": "custom", "base_url": "http://x/v1", "model": "m1",
        "api_key": "sk-test", "priority": 10, "enabled": True, "paid": False, "note": "",
    }
    base.update(kw)
    return base


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============ 导出 ============

def test_export_includes_plaintext_disabled_and_runtime_clean(clean_platform):
    _mk_platform_row("text", "m1", "sk-abc", priority=10)
    _mk_platform_row("text", "m2", "sk-def", enabled=False, priority=20)
    _mk_platform_row("embedding", "emb1", "sk-emb", priority=10)

    db = SessionLocal()
    try:
        payload = model_sync.build_export_payload(db)
    finally:
        db.close()

    text = payload["slots"]["text"]
    assert [i["model"] for i in text] == ["m1", "m2"]
    assert text[0]["api_key"] == "sk-abc"
    assert text[1]["enabled"] is False
    # 运行态字段不跨实例搬运
    assert "cooldown_until" not in text[0] and "success_count" not in text[0]
    assert payload["slots"]["embedding"][0]["api_key"] == "sk-emb"


def test_export_env_fallback_when_no_usable_pool(clean_platform, monkeypatch):
    _mk_platform_row("text", "m1", "sk-abc", enabled=False)  # 只有停用行 → 视为不可用
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "env-key")
    monkeypatch.setattr(config, "MODEL_NAME", "qwen-env")

    db = SessionLocal()
    try:
        payload = model_sync.build_export_payload(db)
    finally:
        db.close()

    text = payload["slots"]["text"]
    assert len(text) == 2
    env = [i for i in text if i["provider"] == "bailian"][0]
    assert env["api_key"] == "env-key"
    assert env["model"] == "qwen-env"
    assert env["note"] == "env 兜底"


# ============ 导入 ============

def test_apply_reencrypts_and_replaces_pool(clean_platform):
    _mk_platform_row("text", "old", "sk-old", priority=5)
    _mk_platform_row("vision", "keep", "sk-keep", priority=5)

    db = SessionLocal()
    try:
        counts = model_sync.apply_payload(
            db, _payload("text", [_item(model="new", api_key="sk-new", priority=7)]),
        )
    finally:
        db.close()

    assert counts["counts"]["text"] == 1
    text_rows = _rows("text")
    assert len(text_rows) == 1
    assert text_rows[0].model == "new"
    assert llm_service.decrypt_key(text_rows[0].api_key_enc, 0) == "sk-new"
    assert text_rows[0].cooldown_until is None and text_rows[0].fail_count == 0
    # 未出现在 payload 的槽位保持不动
    assert [r.model for r in _rows("vision")] == ["keep"]


def test_apply_dedups_identical_items(clean_platform):
    item = _item(model="dup", api_key="sk-dup")
    db = SessionLocal()
    try:
        res = model_sync.apply_payload(db, _payload("text", [item, dict(item)]))
    finally:
        db.close()

    assert res["counts"]["text"] == 1
    assert res["skipped"] == 1
    assert len(_rows("text")) == 1


def test_apply_detects_embedding_model_change(clean_platform):
    _mk_platform_row("embedding", "old-emb", "sk-old", priority=5)

    db = SessionLocal()
    try:
        res = model_sync.apply_payload(
            db, _payload("embedding", [_item(model="new-emb", api_key="sk-new")]),
        )
    finally:
        db.close()

    assert res["embedding_changed"] is True
    assert res["embedding_before"]["model"] == "old-emb"
    assert res["embedding_after"]["model"] == "new-emb"


# ============ 内部接口 ============

def test_internal_export_requires_token(client, monkeypatch):
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "")
    assert client.get("/api/internal/model-pool/export").status_code == 403

    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    assert client.get("/api/internal/model-pool/export").status_code == 403
    assert client.get(
        "/api/internal/model-pool/export",
        headers={"X-AITF-Sync-Token": "wrong"},
    ).status_code == 403

    r = client.get(
        "/api/internal/model-pool/export",
        headers={"X-AITF-Sync-Token": "tok"},
    )
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    assert set(r.json()["slots"]) == {"text", "vision", "embedding", "rerank"}


def test_internal_export_rejects_forwarded_request(client, monkeypatch):
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    r = client.get(
        "/api/internal/model-pool/export",
        headers={"X-AITF-Sync-Token": "tok", "X-Forwarded-For": "203.0.113.1"},
    )
    assert r.status_code == 403


def test_internal_import_only_demo(client, monkeypatch, clean_platform):
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    body = _payload("text", [_item(model="demo-m", api_key="sk-demo")])

    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", False)
    r = client.post(
        "/api/internal/model-pool/import", json=body,
        headers={"X-AITF-Sync-Token": "tok"},
    )
    assert r.status_code == 403

    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", True)
    monkeypatch.setattr(demo_config, "AITF_DEMO_ADMIN_USERNAME", "no-such-admin")
    r = client.post(
        "/api/internal/model-pool/import", json=body,
        headers={"X-AITF-Sync-Token": "tok"},
    )
    assert r.status_code == 200
    assert r.json()["counts"]["text"] == 1
    assert llm_service.decrypt_key(_rows("text")[0].api_key_enc, 0) == "sk-demo"


# ============ 一键同步端点 ============

def test_sync_demo_endpoint_admin_only(client, accounts, monkeypatch, clean_platform):
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", False)
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    _mk_platform_row("text", "m1", "sk-abc")

    called: dict = {}

    def fake_push(payload):
        called["payload"] = payload
        return {"ok": True, "counts": {"text": 1}, "cleared_personal": 0,
                "embedding_changed": False}

    monkeypatch.setattr(model_sync, "push_payload", fake_push)

    # 非 admin 403
    assert client.post(
        "/api/llm/platform-pool/sync-demo", headers=_hdr(accounts["user"]["token"]),
    ).status_code == 403

    r = client.post(
        "/api/llm/platform-pool/sync-demo", headers=_hdr(accounts["admin"]["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["text"] == 1
    assert called["payload"]["slots"]["text"][0]["api_key"] == "sk-abc"


def test_sync_demo_endpoint_forbidden_on_demo_instance(client, accounts, monkeypatch):
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", True)
    r = client.post(
        "/api/llm/platform-pool/sync-demo", headers=_hdr(accounts["admin"]["token"]),
    )
    assert r.status_code == 403


def test_sync_demo_endpoint_502_when_push_fails(client, accounts, monkeypatch, clean_platform):
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", False)
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    _mk_platform_row("text", "m1", "sk-abc")

    def boom(_payload):
        raise RuntimeError("演示站不可达")

    monkeypatch.setattr(model_sync, "push_payload", boom)
    r = client.post(
        "/api/llm/platform-pool/sync-demo", headers=_hdr(accounts["admin"]["token"]),
    )
    assert r.status_code == 502
    assert "演示站不可达" in r.json()["detail"]


# ============ 跨实例 HTTP 调用的 URL / 令牌 ============

class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    calls: list[dict] = []

    def __init__(self, **_kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None, headers=None):
        _FakeClient.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResp({"ok": True, "counts": {"text": 1}})

    def get(self, url, headers=None):
        _FakeClient.calls.append({"url": url, "headers": headers})
        return _FakeResp(_payload("text", [_item(model="pulled", api_key="sk-p")]))


def test_push_uses_internal_import_url_and_token(monkeypatch):
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    monkeypatch.setattr(demo_config, "AITF_DEMO_MODEL_SYNC_TARGET_URL", "http://127.0.0.1:8006")
    monkeypatch.setattr(model_sync.httpx, "Client", _FakeClient)
    _FakeClient.calls.clear()

    out = model_sync.push_payload(_payload("text", [_item()]))
    assert out["ok"] is True
    call = _FakeClient.calls[0]
    assert call["url"] == "http://127.0.0.1:8006/api/internal/model-pool/import"
    assert call["headers"]["X-AITF-Sync-Token"] == "tok"


def test_pull_uses_internal_export_url_and_token(monkeypatch):
    monkeypatch.setattr(config, "MODEL_SYNC_TOKEN", "tok")
    monkeypatch.setattr(demo_config, "AITF_DEMO_MODEL_SYNC_MAIN_URL", "http://127.0.0.1:8005")
    monkeypatch.setattr(model_sync.httpx, "Client", _FakeClient)
    _FakeClient.calls.clear()
    captured: dict = {}
    monkeypatch.setattr(
        model_sync, "apply_payload",
        lambda db, payload, **kw: captured.update(payload=payload, kw=kw) or {"counts": {}},
    )

    model_sync.pull_and_apply(db=None)
    call = _FakeClient.calls[0]
    assert call["url"] == "http://127.0.0.1:8005/api/internal/model-pool/export"
    assert call["headers"]["X-AITF-Sync-Token"] == "tok"
    assert captured["payload"]["slots"]["text"][0]["model"] == "pulled"
    assert captured["kw"] == {"clear_admin_personal": True}