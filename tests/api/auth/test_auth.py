"""认证测试：注册 / 登录 / Token / 改密 / 限速。对应方案 9.7 注册、登录、Token 三张表。"""
import jwt
import pytest

from app.core import config
from app.core import security


# ---------------- 注册 ----------------

@pytest.mark.parametrize("username,email,password,expected", [
    ("ok_user", "ok@test.com", "Abcd1234", 201),      # 正常注册
    ("alice", "dup@test.com", "Abcd1234", 409),       # 用户名重复
    ("fresh_user", "alice@test.com", "Abcd1234", 409),  # 邮箱重复
    ("weak1", "w1@test.com", "123", 422),             # 太短
    ("weak2", "w2@test.com", "abcdefgh", 422),        # 无数字
    ("weak3", "w3@test.com", "12345678", 422),        # 无字母
    ("weak4", "w4@test.com", "Abcd123", 422),         # 长度不足 8
])
def test_register_validation(client, accounts, username, email, password, expected):
    r = client.post("/api/auth/register",
                    json={"username": username, "email": email, "password": password})
    assert r.status_code == expected, r.text


def test_register_invalid_email(client, accounts):
    r = client.post("/api/auth/register",
                    json={"username": "bademail", "email": "not-an-email", "password": "Abcd1234"})
    assert r.status_code == 422


def test_register_too_long_username(client, accounts):
    r = client.post("/api/auth/register",
                    json={"username": "x" * 100, "email": "long@test.com", "password": "Abcd1234"})
    assert r.status_code == 422


def test_first_register_is_admin(client, accounts):
    """首个注册用户自动成为 admin（accounts 已建，这里断言其角色）。"""
    assert accounts["admin"]["token"]
    r = client.get("/api/auth/me",
                   headers={"Authorization": f"Bearer {accounts['admin']['token']}"})
    assert r.json()["role"] == "admin"


def test_register_not_leak_hash(client, accounts):
    r = client.post("/api/auth/register",
                    json={"username": "leak_check", "email": "leak@test.com", "password": "Abcd1234"})
    assert "password" not in r.json() and "hash" not in r.json()


# ---------------- 登录 ----------------

def test_login_ok(client, accounts):
    r = client.post("/api/auth/login",
                    data={"username": "alice", "password": "Alice1234"})
    assert r.status_code == 200
    assert "access_token" in r.json()


@pytest.mark.parametrize("username,password", [
    ("alice", "WrongPass9"),   # 密码错误
    ("no_such_user_x", "Abcd1234"),  # 用户不存在
])
def test_login_fail_same_message(client, accounts, username, password):
    """用户不存在与密码错误必须同文案（防枚举）。"""
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 401
    assert "用户名或密码错误" in r.text


def test_login_lockout(client, accounts):
    """连续失败 5 次锁定（429）。用唯一用户名避免串扰。"""
    uname = "lockme_user"
    client.post("/api/auth/register",
                json={"username": uname, "email": "lock@test.com", "password": "Abcd1234"})
    for _ in range(5):
        r = client.post("/api/auth/login", data={"username": uname, "password": "WrongPass9"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", data={"username": uname, "password": "Abcd1234"})
    assert r.status_code == 429, "锁定后即使密码正确也应拒绝"


def test_guest_cannot_login(client, accounts, fresh_guest):
    r = client.post("/api/auth/login", data={"username": "guest_x", "password": "anything"})
    assert r.status_code in (401, 403)


# ---------------- Token ----------------

def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def test_token_missing(client, accounts):
    r = client.get("/api/tasks")
    assert r.status_code == 401


def test_token_malformed(client, accounts):
    r = client.get("/api/tasks", headers=_hdr("not-a-jwt"))
    assert r.status_code == 401


def test_token_expired(client, accounts):
    """伪造 exp 已过期的 token → 401。"""
    from datetime import timedelta
    from app.core.utils import utcnow
    payload = {
        "sub": "2", "username": "alice", "role": "user",
        "exp": utcnow() - timedelta(hours=1),
        "iat": utcnow() - timedelta(hours=2),
    }
    expired = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    r = client.get("/api/tasks", headers=_hdr(expired))
    assert r.status_code == 401


def test_token_tampered(client, accounts):
    """篡改签名 → 401。"""
    t = accounts["user"]["token"]
    r = client.get("/api/tasks", headers=_hdr(t[:-4] + "abcd"))
    assert r.status_code == 401


def test_token_wrong_secret(client, accounts):
    """用别的 secret 签发 → 401。"""
    from datetime import timedelta
    from app.core.utils import utcnow
    payload = {"sub": "2", "username": "alice", "role": "user",
               "exp": utcnow() + timedelta(hours=1),
               "iat": utcnow()}
    forged = jwt.encode(payload, "totally-different-secret", algorithm=config.JWT_ALGORITHM)
    r = client.get("/api/tasks", headers=_hdr(forged))
    assert r.status_code == 401


# ---------------- 改密 ----------------

def test_change_password(client, accounts):
    uname = "chpw_user"
    client.post("/api/auth/register",
                json={"username": uname, "email": "chpw@test.com", "password": "Abcd1234"})
    r = client.post("/api/auth/login", data={"username": uname, "password": "Abcd1234"})
    token = r.json()["access_token"]

    r = client.post("/api/auth/change-password", headers=_hdr(token),
                    json={"old_password": "Abcd1234", "new_password": "Newpwd99"})
    assert r.status_code == 200

    # 旧密码失效
    r = client.post("/api/auth/login", data={"username": uname, "password": "Abcd1234"})
    assert r.status_code == 401
    # 新密码生效
    r = client.post("/api/auth/login", data={"username": uname, "password": "Newpwd99"})
    assert r.status_code == 200


def test_change_password_wrong_old(client, accounts):
    r = client.post("/api/auth/change-password",
                    headers=_hdr(accounts["user"]["token"]),
                    json={"old_password": "WrongOld9", "new_password": "Newpwd99"})
    assert r.status_code == 401


# ---------------- 并发 ----------------

def test_concurrent_login(client, accounts):
    """同一账号并发多端登录，JWT 无状态互不影响。"""
    import concurrent.futures

    def _login():
        r = client.post("/api/auth/login", data={"username": "alice", "password": "Alice1234"})
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(lambda _: _login(), range(8)))
    assert all(c == 200 for c in codes), codes
