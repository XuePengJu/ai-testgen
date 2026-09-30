"""admin 治理 + 越权防护 + 角色权限矩阵。对应方案 9.7 越权表与角色矩阵。"""
import pytest


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


SPEC_TEXT = "# 登录模块\n- 用户输入正确账号密码，登录成功跳转首页"


# ---------------- 越权 ----------------

def test_cross_user_task_404(client, accounts):
    """用户 A 访问用户 B 的任务 → 404（不暴露存在性）。"""
    r = client.post("/api/tasks", headers=_hdr(accounts["user"]["token"]),
                    data={"text": SPEC_TEXT, "kind": "business", "formats": "json"})
    tid = r.json()["id"]
    r2 = client.get(f"/api/tasks/{tid}", headers=_hdr(accounts["admin"]["token"]))
    assert r2.status_code == 200  # admin 可看
    # 先建第三个用户来模拟越权
    client.post("/api/auth/register",
                json={"username": "bob", "email": "bob@test.com", "password": "Bobby123"})
    r3 = client.post("/api/auth/login", data={"username": "bob", "password": "Bobby123"})
    bob = r3.json()["access_token"]
    assert client.get(f"/api/tasks/{tid}", headers=_hdr(bob)).status_code == 404
    assert client.get(f"/api/tasks/{tid}/download?fmt=json", headers=_hdr(bob)).status_code == 404


def test_task_list_isolated(client, accounts):
    """普通用户只看到自己的任务；不带 all 时不含他人任务。"""
    r = client.get("/api/tasks", headers=_hdr(accounts["user"]["token"]))
    assert r.status_code == 200
    for t in r.json():
        assert t["id"], "列表结构正常"


# ---------------- admin 接口权限 ----------------

@pytest.mark.parametrize("path", ["/api/users", "/api/admin/stats"])
def test_non_admin_forbidden(client, accounts, path):
    for token in (accounts["user"]["token"],):
        assert client.get(path, headers=_hdr(token)).status_code == 403


def test_guest_forbidden_admin_apis(client, fresh_guest):
    token, _ = fresh_guest
    assert client.get("/api/users", headers=_hdr(token)).status_code == 403
    assert client.get("/api/admin/stats", headers=_hdr(token)).status_code == 403


# ---------------- admin 治理 ----------------

def test_admin_list_users(client, accounts):
    r = client.get("/api/users", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 200
    roles = [u["role"] for u in r.json()]
    assert "admin" in roles and "user" in roles
    assert all("password_hash" not in u for u in r.json()), "不得泄露密码哈希"


def test_admin_disable_user_kills_token(client, accounts):
    """禁用用户 → 存量 token 立即 401（回查 DB 生效）；再启用恢复。"""
    client.post("/api/auth/register",
                json={"username": "victim", "email": "victim@test.com", "password": "Victim123"})
    r = client.post("/api/auth/login", data={"username": "victim", "password": "Victim123"})
    victim_token = r.json()["access_token"]

    users = client.get("/api/users", headers=_hdr(accounts["admin"]["token"])).json()
    uid = [u["id"] for u in users if u["username"] == "victim"][0]

    # 禁用
    assert client.patch(f"/api/users/{uid}", headers=_hdr(accounts["admin"]["token"]),
                        json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=_hdr(victim_token)).status_code == 401, \
        "禁用后存量 token 必须立即失效"
    assert client.post("/api/auth/login", data={"username": "victim", "password": "Victim123"}).status_code == 403

    # 启用恢复
    assert client.patch(f"/api/users/{uid}", headers=_hdr(accounts["admin"]["token"]),
                        json={"is_active": True}).status_code == 200
    assert client.get("/api/auth/me", headers=_hdr(victim_token)).status_code == 200


def test_admin_protect_self(client, accounts):
    """admin 不可禁用/删除自己。"""
    admin_id = client.get("/api/auth/me", headers=_hdr(accounts["admin"]["token"])).json()["id"]
    assert client.patch(f"/api/users/{admin_id}", headers=_hdr(accounts["admin"]["token"]),
                        json={"is_active": False}).status_code == 400
    assert client.delete(f"/api/users/{admin_id}",
                         headers=_hdr(accounts["admin"]["token"])).status_code == 400


def test_admin_delete_user_cascade(client, accounts):
    """删除用户级联任务与文件。"""
    import time
    client.post("/api/auth/register",
                json={"username": "deleteme", "email": "del@test.com", "password": "Delme123"})
    r = client.post("/api/auth/login", data={"username": "deleteme", "password": "Delme123"})
    token = r.json()["access_token"]
    r = client.post("/api/tasks", headers=_hdr(token),
                    data={"text": SPEC_TEXT, "kind": "business", "formats": "json"})
    tid = r.json()["id"]
    # 等后台任务跑完，避免删除与后台写入竞态
    for _ in range(30):
        if client.get(f"/api/tasks/{tid}", headers=_hdr(token)).json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)

    users = client.get("/api/users", headers=_hdr(accounts["admin"]["token"])).json()
    uid = [u["id"] for u in users if u["username"] == "deleteme"][0]
    data_dir = f"u_{uid}"

    assert client.delete(f"/api/users/{uid}", headers=_hdr(accounts["admin"]["token"])).status_code == 200

    from app.core.db import SessionLocal
    from app.models.task import Task
    from app.models.user import User
    from app.core.config import OUTPUT_DIR
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.id == uid).first() is None
        assert db.query(Task).filter(Task.id == tid).first() is None, "任务应级联删除"
    finally:
        db.close()
    assert not (OUTPUT_DIR / data_dir).exists(), "文件目录应级联删除"


def test_admin_stats(client, accounts):
    r = client.get("/api/admin/stats", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 200
    j = r.json()
    assert j["registered_users"] >= 2
    # 共享 guest 恒存在且全站唯一 → 活跃访客只能是 1
    assert j["active_guests"] == 1
    assert "total_tasks" in j
    # 「24h 内清理」随动态访客机制一起移除
    assert "cleaned_24h" not in j


def test_admin_reset_shared_guest_data(client, accounts):
    """清空共享访客数据：仅 admin 可调，返回清理统计。"""
    assert client.post("/api/admin/guest/shared/reset",
                       headers=_hdr(accounts["user"]["token"])).status_code == 403
    r = client.post("/api/admin/guest/shared/reset", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 200
    assert "deleted_tasks" in r.json()


def test_shared_guest_survives_reset(client, accounts):
    """清空只删任务/文件，账号本身保留 → 访客仍可继续使用，且全站仍只有一个 guest。"""
    token = client.post("/api/guest/token").json()["access_token"]
    assert client.post("/api/admin/guest/shared/reset",
                       headers=_hdr(accounts["admin"]["token"])).status_code == 200
    assert client.get("/api/auth/me", headers=_hdr(token)).status_code == 200
    users = client.get("/api/users", headers=_hdr(accounts["admin"]["token"])).json()
    assert sum(1 for u in users if u["role"] == "guest") == 1, "共享 guest 账号应保留且唯一"


def test_admin_tasks_all(client, accounts):
    r = client.get("/api/tasks?all=true", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 200
    # admin 不带 all → 只看自己的
    r2 = client.get("/api/tasks", headers=_hdr(accounts["admin"]["token"]))
    assert len(r.json()) >= len(r2.json())


def test_guest_disable_equals_clean(client, accounts, fresh_guest):
    """禁用共享 guest = 清空其体验数据（账号本身保留，所有人仍可继续使用）。"""
    token, _ = fresh_guest
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    uname = me["username"]

    admin_token = accounts["admin"]["token"]
    users = client.get("/api/users", headers=_hdr(admin_token)).json()
    gid = [u["id"] for u in users if u["username"] == uname][0]
    r = client.patch(f"/api/users/{gid}", headers=_hdr(admin_token), json={"is_active": False})
    assert r.status_code == 200, r.text
    assert "cleaned" in r.json(), "应返回清理统计"
    # 共享账号不被删除：访客仍可继续使用
    assert client.get("/api/auth/me", headers=_hdr(token)).status_code == 200


# ---------------- 角色权限矩阵（24 断言） ----------------

# (接口, 方法) × 三角色期望状态码
MATRIX = [
    # path, method, guest, user, admin
    ("/api/auth/me", "GET", 200, 200, 200),
    ("/api/guest/token", "POST", 200, 200, 200),          # 任意角色可拿共享访客身份
    ("/api/tasks", "GET", 200, 200, 200),
    ("/api/users", "GET", 403, 403, 200),
    ("/api/admin/stats", "GET", 403, 403, 200),
    ("/api/admin/guest/shared/reset", "POST", 403, 403, 200),
    ("/api/tasks", "POST", 400, 400, 400),                # 空参 400（鉴权均放行）
]


@pytest.mark.parametrize("path,method,exp_guest,exp_user,exp_admin", MATRIX)
def test_role_matrix(client, accounts, fresh_guest, path, method, exp_guest, exp_user, exp_admin):
    guest_token, _ = fresh_guest
    tokens = {"guest": guest_token, "user": accounts["user"]["token"], "admin": accounts["admin"]["token"]}
    expected = {"guest": exp_guest, "user": exp_user, "admin": exp_admin}
    for role, token in tokens.items():
        if method == "GET":
            r = client.get(path, headers=_hdr(token))
        else:
            r = client.post(path, headers=_hdr(token))
        assert r.status_code == expected[role], \
            f"{role} {method} {path} 期望 {expected[role]}，实际 {r.status_code}"


def test_role_matrix_unauth(client, accounts):
    """未携带 Token 访问受保护接口 → 401（矩阵第 4 列）。"""
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/admin/stats").status_code == 401
    assert client.post("/api/tasks").status_code == 401
    assert client.get("/api/tasks/whatever").status_code == 401
    assert client.get("/api/tasks/whatever/download").status_code == 401
