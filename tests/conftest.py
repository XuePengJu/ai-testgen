"""测试夹具：临时数据根目录 + TestClient + 基础账号。

关键：AITF_ROOT_DIR / GUEST_* 等环境变量必须在 import app 之前设置，
config 在模块导入时读取一次。
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path

# ---- 必须在 import app 之前 ----
_TMP_ROOT = tempfile.mkdtemp(prefix="aitf_test_")
os.environ["AITF_ROOT_DIR"] = _TMP_ROOT          # 数据落临时目录，不污染真实 app.db
os.environ["DB_TYPE"] = "sqlite"                 # 测试强制本地 SQLite，绝不连真实 MySQL
os.environ["DATABASE_URL"] = ""                  # 清掉任何直填 URL（.env 可能配了远程库）
os.environ["JWT_SECRET"] = "test-secret-for-pytest"
os.environ["ENV"] = "dev"
os.environ["DASHSCOPE_API_KEY"] = ""             # 无真实模型配置
os.environ["AITF_ALLOW_DEMO"] = "1"              # 存量用例覆盖「演示模式」路径（新默认=0 不静默兜底）
os.environ["API_ENCRYPT"] = "0"                  # 存量用例走明文；加密场景由 test_crypto 用 monkeypatch 开启

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402  此时 config 已锁定到临时目录
from app.core.db import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402

# 以下 4 个是「脚本式 e2e」（自带独立 DB + TestClient，作者注明 pytest 不收集，用
# `python tests/xxx.py` 手动跑）。文件名虽是 test_*.py，但 pytest 会在**导入期**执行
# 其模块级断言，并把 app.dependency_overrides[get_current_user] 全局替换成 SimpleNamespace
# 且不还原 → 全量跑时污染其余所有用例（约 40 个连锁失败）。这里显式排除。
collect_ignore = [
    "test_chat_rag.py",
    "test_embedding_config.py",
    "test_knowledge_api.py",
    "test_v41_kb_qa.py",
]


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
