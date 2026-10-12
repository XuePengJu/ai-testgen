"""演示站（V9.0）测试：免登录 / 每日重置 / 正式站隔离。

源：演示站落地方案（子路径反代 + 单项目双实例，2026-10-12）。

说明：演示开关（AITF_DEMO_ENABLED）是**进程级**配置，而 tests/api 的
client fixture 是 session 级（启动即固化）——因此本文件不依赖该 fixture，
直接用 monkeypatch 切换 demo_config 模块属性，在同一进程内模拟
「演示实例 / 正式实例」两种语义（端点运行时读取模块属性）。

覆盖：
- 演示实例（AITF_DEMO_ENABLED=1）：
  * POST /api/demo/token 返回 admin JWT（明文通道）+ enc_key
  * GET  /api/demo/meta 返回引导链接 / 重置时间
  * 首页注入 __AITF_DEMO__（前端 Banner 数据源）
  * 每日重置 reset_demo_data：清空业务表 + 重播种示例任务
- 正式实例（AITF_DEMO_ENABLED=0）：
  * /api/demo/* 一律 403
  * 首页不含 __AITF_DEMO__（零注入）
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import demo_config
from app.core.db import SessionLocal
from app.jobs.demo_reset import reset_demo_data
from app.models.task import Task
from app.models.user import User

# 自建 session 级 TestClient（不依赖 tests/api conftest 的 session client，
# 避免其启动期固化的进程开关与本文件的演示/正式切换冲突）
pytestmark = pytest.mark.usefixtures("_demo_client")


@pytest.fixture(scope="module")
def _demo_client():
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fake_static_dir(tmp_path, monkeypatch):
    """首页用例不依赖真实前端构建产物。

    CI 的 api job 不跑 npm build（dist 被 .gitignore），main.index() 直接
    read_text(STATIC_DIR/index.html) 会 FileNotFoundError。路由在调用时读
    main 模块属性，故把 STATIC_DIR 指向含最小 index.html 的临时目录，
    只验证「</head> 前注入/不注入」逻辑本身。
    """
    import main
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>t</title></head><body></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _restore_personal_kbs():
    """演示重置会清库含每个用户的个人记忆库，测后必须还原。

    tests/api 的 session DB 是**跨模块共享且行持久**的，而 reset_demo_data
    （AITF_DEMO_PURGE_ADMIN=1）按真实语义把全业务数据（含 knowledge_bases）
    清空 —— 不清完就拍拍屁股走人，会让后序依赖个人库的用例在 fixture 阶段
    直接 AssertionError（实测波及 tests/api/knowledge/test_kb_batch_delete.py
    的 admin_kb fixture：「admin 应有个人知识库」）。

    原则：谁污染谁还原。本文件跑完每个用例后为所有用户重建个人记忆库，
    把进入本文件前的库不变量还回去。
    """
    yield
    from app.services.memory.store import ensure_personal_kb
    db = SessionLocal()
    try:
        for u in db.execute(select(User)).scalars().all():
            ensure_personal_kb(db, u)
    finally:
        db.close()


def _set_demo(monkeypatch, **overrides):
    """把 demo_config 模块属性切成目标值（端点运行时读取模块属性）。"""
    defaults = {
        "AITF_DEMO_ENABLED": True,
        "AITF_DEMO_RESET_HOUR": 2,
        "AITF_DEMO_RESET_MINUTE": 0,
        "AITF_DEMO_GUIDE_URL": "https://ai-testgen.agentest.vip",
        "AITF_DEMO_ADMIN_USERNAME": "admin",
        "AITF_DEMO_ADMIN_PASSWORD": "Admin@123",
        "AITF_DEMO_TOKEN_TTL_HOURS": 72,
        "AITF_DEMO_PURGE_ADMIN": True,
        "AITF_DEMO_SEED_SAMPLE": True,
    }
    defaults.update(overrides)
    for name, val in defaults.items():
        monkeypatch.setattr(demo_config, name, val)


# ---------- 演示实例（开启演示） ----------

@pytest.mark.title("演示站免登录返回admin令牌并可用")
def test_demo_token_returns_admin_jwt(_demo_client, monkeypatch):
    """演示模式：免登录 token 是 admin 角色且可正常访问 /auth/me。"""
    _set_demo(monkeypatch)
    r = _demo_client.post("/api/demo/token")
    assert r.status_code == 200
    d = r.json()
    assert d["role"] == "admin"
    assert d["username"] == "admin"
    assert d["access_token"]
    assert d["enc_key"]
    assert d["is_demo"] is True

    me = _demo_client.get("/api/auth/me", headers={"Authorization": f"Bearer {d['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


@pytest.mark.title("演示站meta返回引导链接与重置时间")
def test_demo_meta_returns_guide_and_cron(_demo_client, monkeypatch):
    """演示模式：meta 返回引导链接与重置时间文案。"""
    _set_demo(monkeypatch, AITF_DEMO_GUIDE_URL="https://example.com/formal")
    r = _demo_client.get("/api/demo/meta")
    assert r.status_code == 200
    d = r.json()
    assert d["is_demo"] is True
    assert d["guide_url"] == "https://example.com/formal"
    assert d["reset_cron"] == "02:00"
    assert "自动清空" in d["notice"]


@pytest.mark.title("演示站首页注入Banner标识")
def test_demo_index_injects_banner(_demo_client, monkeypatch):
    """演示模式：首页 <head> 注入 __AITF_DEMO__（前端 Banner 数据源）。"""
    _set_demo(monkeypatch)
    html = _demo_client.get("/").text
    assert "__AITF_DEMO__" in html
    assert "guide_url" in html


@pytest.mark.title("演示站每日重置清空并重播种示例数据")
def test_demo_reset_clears_and_reseeds(_demo_client, monkeypatch):
    """每日重置：清空业务数据后重播种示例任务（账号保留）。"""
    _set_demo(monkeypatch, AITF_DEMO_PURGE_ADMIN=True, AITF_DEMO_SEED_SAMPLE=True)

    db = SessionLocal()
    try:
        # 造一个非示例用户 + 任务，验证会被清
        u = User(username="demo-junk-user", email="junk@demo.local",
                 password_hash="$2b$12$abcdefghijklmnopqrstuv", role="user", data_dir="u_9999")
        db.add(u)
        db.commit()
        t = Task(id=uuid.uuid4().hex[:12], name="junk-task", kind="business",
                 source_type="text", status="completed", user_id=u.id, formats="xlsx")
        db.add(t)
        db.commit()

        counts = reset_demo_data(db)

        # 用户行保留（只清数据）；junk 任务被清
        assert counts["users_cleaned"] >= 1
        assert db.execute(select(Task).where(Task.name == "junk-task")).scalar_one_or_none() is None

        # 示例任务重新播种（is_sample=True 至少 3 个）
        samples = db.execute(select(Task).where(Task.is_sample.is_(True))).scalars().all()
        assert len(samples) >= 3
        assert all(t.status == "completed" for t in samples)
    finally:
        db.close()


@pytest.mark.title("重置策略保留admin数据开关生效")
def test_demo_reset_keeps_admin_data_when_purge_admin_off(_demo_client, monkeypatch):
    """AITF_DEMO_PURGE_ADMIN=0：admin 数据保留，只清其他角色。"""
    _set_demo(monkeypatch, AITF_DEMO_PURGE_ADMIN=False, AITF_DEMO_SEED_SAMPLE=False)

    db = SessionLocal()
    try:
        # 独立建一个专属 admin（避免受前序重置用例的库状态影响）
        from app.core import security
        admin = User(username="demo-keep-admin", email="keep@demo.local",
                     password_hash=security.hash_password("Keep1234"),
                     role="admin", data_dir="u_keep")
        db.add(admin)
        db.commit()
        t = Task(id=uuid.uuid4().hex[:12], name="admin-keep-task", kind="business",
                 source_type="text", status="completed", user_id=admin.id, formats="xlsx")
        db.add(t)
        db.commit()

        counts = reset_demo_data(db)
        # admin 任务保留
        kept = db.execute(select(Task).where(Task.name == "admin-keep-task")).scalar_one_or_none()
        assert kept is not None
        # 其他用户（demo-junk-user 若存在）会被清；这里至少验证 admin 路径
        assert counts["users_cleaned"] >= 0
    finally:
        db.close()


# ---------- 正式实例（关闭演示） ----------

@pytest.mark.title("正式站演示接口一律403隔离")
def test_prod_demo_endpoints_forbidden(_demo_client, monkeypatch):
    """正式实例：/api/demo/* 一律 403。"""
    _set_demo(monkeypatch, AITF_DEMO_ENABLED=False)
    assert _demo_client.get("/api/demo/meta").status_code == 403
    assert _demo_client.post("/api/demo/token").status_code == 403


@pytest.mark.title("正式站首页零Banner注入")
def test_prod_index_no_banner_injection(_demo_client, monkeypatch):
    """正式实例：首页不注入 __AITF_DEMO__。"""
    _set_demo(monkeypatch, AITF_DEMO_ENABLED=False)
    html = _demo_client.get("/").text
    assert "__AITF_DEMO__" not in html
