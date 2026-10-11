"""接口层测试夹具：TestClient + 基础账号（自 tests/conftest.py 迁入 tests/api/，V1.4 §5.2）。

分层迁移说明：
- 原文件位于 tests/ 根，其 ``autouse=True`` 的 session 级 ``accounts`` 夹具会波及 ``unit/``、``ui/``；
  移入 ``tests/api/`` 后，夹具作用域收敛到 ``tests/api/**``，``unit/`` 与 ``ui/`` 零继承、零污染。
- 顶部「env 隔离」与 ``sys.path.insert`` 已上提到**仓库根 conftest.py**（必须在 ``import app`` 之前生效）；
  本文件只保留夹具。
- 原 ``collect_ignore``（4 个脚本式 e2e）已无需保留：它们已迁到 ``tests/manual/**`` 并去掉 ``test_`` 前缀，
  天然不被 pytest 收集。
"""
import pytest
from fastapi.testclient import TestClient

from main import app                      # 此处由根 conftest 先行设好 env，导入顺序安全
from app.core.db import SessionLocal
from app.models.user import User


@pytest.fixture(scope="session")
def client():
    """with 语法触发 lifespan（init_db + 共享 guest 播种 + worker 池）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def accounts(client):
    """首个注册用户 = admin（引导规则）；第二个 = 普通用户。

    autouse 保证在任何其他注册发生之前完成，角色判定不被打乱。
    """
    r = client.post("/api/auth/register", json={
        "username": "boss_admin", "email": "admin@test.com", "password": "Admin1234"})
    assert r.status_code == 201 and r.json()["role"] == "admin", r.text

    r = client.post("/api/auth/register", json={
        "username": "alice", "email": "alice@test.com", "password": "Alice1234"})
    assert r.status_code == 201 and r.json()["role"] == "user", r.text

    def _login(u, p):
        r = client.post("/api/auth/login", data={"username": u, "password": p})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    return {
        "admin": {"username": "boss_admin", "password": "Admin1234", "token": _login("boss_admin", "Admin1234")},
        "user": {"username": "alice", "password": "Alice1234", "token": _login("alice", "Alice1234")},
    }


@pytest.fixture()
def fresh_guest(client):
    """共享访客 token（V2 简化后全站只有一个固定 guest 账号，无需按 IP 隔离）。

    返回 (token, None)：第二位是历史遗留的 ip 占位，仅为兼容存量用例的解包写法。
    """
    r = client.post("/api/guest/token")
    assert r.status_code == 200, r.text
    return r.json()["access_token"], None


@pytest.fixture()
def expire_guest():
    """把指定 username 的 guest 置为过期（直改 DB 模拟 TTL 到期）。"""
    from datetime import timedelta
    from app.core.utils import utcnow

    def _expire(username: str):
        db = SessionLocal()
        try:
            g = db.query(User).filter(User.username == username).first()
            assert g, f"guest {username} 不存在"
            g.expires_at = utcnow() - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
    return _expire


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def guest_by_username(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def delete_guest_record(username: str):
    """物理删除 guest 记录（模拟清理后再测同 IP 重建计数）。"""
    db = SessionLocal()
    try:
        g = db.query(User).filter(User.username == username).first()
        if g:
            db.delete(g)
            db.commit()
    finally:
        db.close()
