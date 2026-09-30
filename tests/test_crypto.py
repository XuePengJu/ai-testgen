"""API 分级加密测试（V2.1）：admin 明文 / user+guest AES-256-GCM 强制加密。

存量 55 条用例在 API_ENCRYPT=0 下走明文；本文件按需开启加密验证分级行为。
请求体加密用 change-password（真 JSON 端点）验证，/api/tasks 是 FormData 不参与。
"""
import pytest

from app.core import config, crypto, security


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture()
def enc_on(monkeypatch):
    """按用例开启加密（中间件运行时读 config.API_ENCRYPT）。"""
    monkeypatch.setattr(config, "API_ENCRYPT", True)


def _user_key(client, username, password):
    """登录（明文通道）拿 token + 下发的 enc_key。"""
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "enc_key" in d
    return d["access_token"], d["enc_key"]


def _register(client, username, email, password):
    r = client.post("/api/auth/register",
                    json={"username": username, "email": email, "password": password})
    assert r.status_code == 201, r.text


# ---------------- 分级策略 ----------------

def test_admin_plaintext_even_when_encrypted(client, accounts, enc_on):
    """admin 响应永远明文，方便 Swagger 调试。"""
    t = accounts["admin"]["token"]
    r = client.get("/api/tasks", headers=_hdr(t), params={"all": True})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list), "admin 应为明文数组"


def test_user_response_encrypted(client, accounts, enc_on):
    """user 的 JSON 响应被加密成 {"enc": ...}。"""
    t, _ = _user_key(client, "alice", "Alice1234")
    r = client.get("/api/tasks", headers=_hdr(t))
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, dict) and set(d.keys()) == {"enc"}, f"应为密文包装，实为 {str(d)[:80]}"


def test_guest_response_encrypted(client, fresh_guest, enc_on):
    token, _ = fresh_guest
    r = client.get("/api/auth/me", headers=_hdr(token))
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, dict) and set(d.keys()) == {"enc"}


def test_guest_decrypt_roundtrip(client, fresh_guest, enc_on):
    """密文用派生密钥还原出正确业务数据。"""
    token, _ = fresh_guest
    key = crypto.derive_key(security.decode_token(token)["sub"])
    r = client.get("/api/auth/me", headers=_hdr(token))
    obj = crypto.decrypt_obj(r.json()["enc"], key)
    assert obj["role"] == "guest"
    assert obj["username"] == "guest", "共享访客账号名固定为 guest"
    assert obj.get("remaining_hours") is None, "共享访客不过期，不应有倒计时"


# ---------------- 请求体加密 ----------------

def test_encrypted_request_body_accepted(client, accounts, enc_on):
    """user 用下发密钥加密 JSON 请求体 → 中间件透明解密，端点正常处理。"""
    _register(client, "enc_req", "enc_req@test.com", "Encreq12")
    t, key = _user_key(client, "enc_req", "Encreq12")
    payload = {"old_password": "Encreq12", "new_password": "Newenc99"}
    r = client.post(
        "/api/auth/change-password",
        headers={**_hdr(t), "Content-Type": "application/json"},
        json={"enc": crypto.encrypt_obj(payload, key)},
    )
    assert r.status_code == 200, r.text


def test_wrong_key_request_rejected(client, accounts, enc_on):
    """错误密钥加密的请求体 → 400（解密失败）。"""
    _register(client, "enc_wrong", "enc_wrong@test.com", "Encwrong12")
    t, _ = _user_key(client, "enc_wrong", "Encwrong12")
    wrong_key = crypto.derive_key(999999)
    payload = {"old_password": "Encwrong12", "new_password": "Newpwd99"}
    r = client.post(
        "/api/auth/change-password",
        headers={**_hdr(t), "Content-Type": "application/json"},
        json={"enc": crypto.encrypt_obj(payload, wrong_key)},
    )
    assert r.status_code == 400


def test_tampered_ciphertext_rejected(client, accounts, enc_on):
    """密文被篡改 → GCM tag 校验失败。"""
    _, key = _user_key(client, "alice", "Alice1234")
    enc = crypto.encrypt_obj({"a": 1}, key)
    tampered = enc[:20] + ("A" if enc[20] != "A" else "B") + enc[21:]
    with pytest.raises(ValueError):
        crypto.decrypt_obj(tampered, key)


# ---------------- 明文通道（密钥分发） ----------------

@pytest.mark.parametrize("make", [
    lambda c: c.post("/api/auth/login", data={"username": "alice", "password": "Alice1234"}),
    lambda c: c.post("/api/auth/register",
                     json={"username": "enc_reg", "email": "enc_reg@test.com", "password": "Encpwd12"}),
    lambda c: c.post("/api/guest/token"),
])
def test_key_distribution_channels_plaintext(client, accounts, enc_on, make):
    """密钥分发端点永远明文且下发 enc_key。"""
    r = make(client)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    assert isinstance(d, dict) and "enc" not in d, "密钥分发通道应明文"
    assert "enc_key" in d, "应下发 enc_key"


def test_enc_key_matches_derived(client, accounts, enc_on):
    """下发 enc_key == HKDF 派生值（前后端可独立计算）。"""
    t, enc_key = _user_key(client, "alice", "Alice1234")
    payload = security.decode_token(t)
    assert enc_key == crypto.derive_key(payload["sub"])


def test_plaintext_when_flag_off(client, accounts):
    """API_ENCRYPT=0 时全部明文（存量行为不回归）。"""
    t, _ = _user_key(client, "alice", "Alice1234")
    r = client.get("/api/tasks", headers=_hdr(t))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------- 密钥派生稳定性 ----------------

def test_shared_guest_key_is_stable(client, enc_on):
    """共享 guest 是固定账号：多次取 token 的 user_id 与派生密钥恒定（所有人共用同一身份）。"""
    r1 = client.post("/api/guest/token")
    r2 = client.post("/api/guest/token")
    assert r1.status_code == 200 and r2.status_code == 200, r1.text
    assert r1.json()["username"] == "guest"
    gid = security.decode_token(r1.json()["access_token"])["sub"]
    gid2 = security.decode_token(r2.json()["access_token"])["sub"]
    assert gid == gid2, "共享 guest 必须始终是同一个账号"
    assert r1.json()["enc_key"] == crypto.derive_key(gid)
