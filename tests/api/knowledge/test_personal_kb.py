"""P0 对话记忆 + 个人知识库：数据地基测试。

覆盖：
1. 注册即建个人库（列表可见，is_personal=True、visibility="private"）
2. ensure_personal_kb 幂等（重复触发不产生第二个库）
3. 共享 guest 有个人库
4. backfill_personal_kbs 对存量无库用户生效（幂等）
5. 用户 A 的个人库对用户 B 不可见
6. purge_user_knowledge 后 kb/knowledge/chunk 零残留
"""
import uuid

from app.core.db import SessionLocal
from app.jobs.personal_kb import backfill_personal_kbs
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.models.user import User
from app.services.memory.store import (
    ensure_personal_kb,
    get_personal_kb_id,
    purge_user_knowledge,
)

PASSWORD = "Passw0rd1"


def _register(client, username: str) -> dict:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()


def _login(client, username: str) -> str:
    r = client.post("/api/auth/login", data={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _get_user(db, username: str) -> User:
    u = db.query(User).filter(User.username == username).first()
    assert u, f"用户 {username} 不存在"
    return u


def test_register_creates_personal_kb(client):
    """注册新用户后，其个人库出现在自己的知识库列表且标记正确。"""
    username = f"kbuser_{uuid.uuid4().hex[:8]}"
    _register(client, username)
    token = _login(client, username)
    r = client.get("/api/knowledge/bases", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    personal = [k for k in items if k.get("is_personal")]
    assert len(personal) == 1, f"应恰好有一个个人库：{items}"
    kb = personal[0]
    assert kb["visibility"] == "private"
    assert kb["name"] == f"{username} 的记忆库"
    # 排序：个人库应排在最后（前端默认选中第一项，个人库置底避免误选）
    assert items[-1]["id"] == kb["id"]


def test_ensure_personal_kb_idempotent(client, db_session):
    """重复调用 ensure_personal_kb 不产生第二个库。"""
    username = f"idem_{uuid.uuid4().hex[:8]}"
    _register(client, username)
    u = _get_user(db_session, username)
    kb1 = ensure_personal_kb(db_session, u)
    kb2 = ensure_personal_kb(db_session, u)
    assert kb1.id == kb2.id
    cnt = db_session.query(KnowledgeBase).filter(
        KnowledgeBase.user_id == u.id, KnowledgeBase.is_personal.is_(True)).count()
    assert cnt == 1


def test_guest_has_personal_kb(client, db_session):
    """共享 guest 账号也有个人库（记忆提炼阶段通过配置跳过访客）。"""
    g = _get_user(db_session, "guest")
    assert get_personal_kb_id(db_session, g.id) is not None


def test_backfill_personal_kbs(client, db_session):
    """backfill 对存量无库用户生效，且重复执行幂等。"""
    username = f"legacy_{uuid.uuid4().hex[:8]}"
    u = User(username=username, email=f"{username}@test.com", role="user", data_dir="")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    assert get_personal_kb_id(db_session, u.id) is None

    db2 = SessionLocal()
    try:
        n = backfill_personal_kbs(db2, limit=1000)
        assert n >= 1
    finally:
        db2.close()
    assert get_personal_kb_id(db_session, u.id) is not None

    # 幂等：所有用户都已有库，再次回填补建数应为 0
    db3 = SessionLocal()
    try:
        assert backfill_personal_kbs(db3, limit=1000) == 0
    finally:
        db3.close()


def test_personal_kb_not_visible_to_others(client, db_session):
    """用户 A 的个人库（private）对用户 B 不可见。"""
    ua, ub = f"visA_{uuid.uuid4().hex[:8]}", f"visB_{uuid.uuid4().hex[:8]}"
    _register(client, ua)
    _register(client, ub)
    kb_a = get_personal_kb_id(db_session, _get_user(db_session, ua).id)
    assert kb_a
    token_b = _login(client, ub)
    r = client.get("/api/knowledge/bases", headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 200, r.text
    ids = [k["id"] for k in r.json()["items"]]
    assert kb_a not in ids


def test_purge_user_knowledge_cleans_all(client, db_session):
    """purge_user_knowledge 后该用户 kb/knowledge/chunk 零残留。"""
    username = f"purge_{uuid.uuid4().hex[:8]}"
    _register(client, username)
    u = _get_user(db_session, username)
    kb_id = get_personal_kb_id(db_session, u.id)
    assert kb_id

    # 造一条文档 + 分块（模拟已入库的记忆/附件文档）
    doc = Knowledge(user_id=u.id, knowledge_base_id=kb_id, title="记忆片段", parse_status="ready")
    db_session.add(doc)
    db_session.flush()
    db_session.add(Chunk(user_id=u.id, knowledge_base_id=kb_id,
                         knowledge_id=doc.id, content="用户偏好中文回复"))
    db_session.commit()

    stat = purge_user_knowledge(db_session, u.id)
    assert stat["bases"] >= 1 and stat["docs"] >= 1 and stat["chunks"] >= 1
    assert db_session.query(KnowledgeBase).filter(
        KnowledgeBase.user_id == u.id).count() == 0
    assert db_session.query(Knowledge).filter(
        Knowledge.user_id == u.id).count() == 0
    assert db_session.query(Chunk).filter(
        Chunk.user_id == u.id).count() == 0
    assert get_personal_kb_id(db_session, u.id) is None
