"""访客测试（V2 简化后）：全站唯一固定共享 guest 账号。

旧版「按 IP 动态建访客 / 24h TTL 过期 / 懒清理 / 单 IP 限频 / 访客转正」已整体移除，
对应旧用例（生命周期、防滥用、转正、同 IP 复用）不再适用，此处按新模型重写：

- 只有一个固定账号（username='guest'，data_dir='guest_shared'），所有人共用、不过期
- 启动/取 token 时会清掉历史遗留的动态 guest_* 账号
- 管理员可清空共享访客的数据，但账号本身保留
"""
import time

SPEC_TEXT = "# 登录模块\n- 用户输入正确账号密码，点击登录，登录成功跳转首页"


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _mk_task(client, token):
    return client.post("/api/tasks", headers=_hdr(token),
                       data={"text": SPEC_TEXT, "kind": "business", "formats": "json"})


def _wait_done(client, token, tid, timeout=30):
    for _ in range(timeout * 2):
        r = client.get(f"/api/tasks/{tid}", headers=_hdr(token))
        if r.status_code == 200 and r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"任务 {tid} 未在 {timeout}s 内完成")


# ---------------- 固定共享账号 ----------------

def test_guest_token_returns_fixed_account(client):
    r = client.post("/api/guest/token")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "guest"
    assert r.json()["username"] == "guest"


def test_guest_token_is_shared_not_per_ip(client):
    """多次取 token 都应是同一个固定账号，绝不按来源另建身份。"""
    r1 = client.post("/api/guest/token")
    r2 = client.post("/api/guest/token")
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = client.get("/api/auth/me", headers=_hdr(r1.json()["access_token"])).json()["id"]
    id2 = client.get("/api/auth/me", headers=_hdr(r2.json()["access_token"])).json()["id"]
    assert id1 == id2, "所有人必须共用同一个访客账号"


def test_shared_guest_never_expires(client):
    """共享 guest 不过期：不应有 remaining_hours 倒计时。"""
    token = client.post("/api/guest/token").json()["access_token"]
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    assert me["role"] == "guest"
    assert me.get("remaining_hours") is None, "共享访客不应有过期倒计时"


def test_shared_guest_files_in_own_dir(client):
    token = client.post("/api/guest/token").json()["access_token"]
    r = _mk_task(client, token)
    assert r.status_code == 201, r.text
    from app.core.config import OUTPUT_DIR
    assert (OUTPUT_DIR / "guest_shared").exists(), "共享访客文件应在 guest_shared 目录"


# ---------------- 已移除的能力 ----------------

def test_upgrade_endpoint_removed(client):
    """转正接口随动态访客一起删除 → 404。"""
    token = client.post("/api/guest/token").json()["access_token"]
    r = client.post("/api/guest/upgrade", headers=_hdr(token),
                    json={"username": "up_u", "email": "u@t.com", "password": "Upwd1234"})
    assert r.status_code == 404, "访客转正接口应已删除"


def test_legacy_dynamic_guests_purged(client):
    """历史遗留的动态 guest_* 账号会在 ensure_shared_guest 时被清掉，只留一个共享 guest。"""
    from app.core.db import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        legacy = User(username="guest_deadbeef_0", role="guest", data_dir="guest_deadbeef_0")
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    finally:
        db.close()

    client.post("/api/guest/token")  # 触发 ensure_shared_guest → purge_legacy_guests

    db = SessionLocal()
    try:
        assert db.get(User, legacy_id) is None, "旧动态 guest 账号应被清除"
        assert db.query(User).filter(User.role == "guest").count() == 1, "只应剩一个共享 guest"
    finally:
        db.close()


# ---------------- 管理员清空共享访客数据 ----------------

def test_admin_reset_shared_guest_data(client, accounts):
    """清空共享访客数据：任务被清掉，但账号保留、仍可继续使用。"""
    token = client.post("/api/guest/token").json()["access_token"]
    r = _mk_task(client, token)
    assert r.status_code == 201, r.text
    _wait_done(client, token, r.json()["id"])

    admin = accounts["admin"]["token"]
    rr = client.post("/api/admin/guest/shared/reset", headers=_hdr(admin))
    assert rr.status_code == 200, rr.text
    assert rr.json().get("deleted_tasks", 0) >= 1

    tasks = client.get("/api/tasks", headers=_hdr(token)).json()
    assert tasks == [], "共享访客的任务应被清空"

    # 账号本身保留，仍可继续使用
    assert client.post("/api/guest/token").status_code == 200
    assert client.get("/api/auth/me", headers=_hdr(token)).json()["role"] == "guest"
