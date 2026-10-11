"""删用户的级联完整性回归（V7.4）。

被修的 bug
----------
``app/api/users.py: delete_user`` 原来的级联只覆盖 step_logs / tasks / 知识库(含记忆
条目+审计+Chroma 向量) / 数据目录，**漏了** conversations / messages / categories /
chat_attachments / prompt_overrides / llm_configs / llm_model_pool / llm_usage /
memory_retrieval_logs。而 ``conversations.user_id`` **没有外键约束**（全库唯一 FK 是
``messages.conversation_id → conversations.id``），数据库不会替你级联
→ 用管理界面删用户会留下**永久悬空、查不到也删不掉**的孤儿会话。

修法：级联逻辑收敛到 ``app/services/user_purge.py: purge_user_data``，
``delete_user`` 与 ``guest_cleaner`` 共用。

本文件的两层防护
----------------
1. ``test_purge_covers_every_user_scoped_table``：**动态**从 ``Base.metadata`` 枚举所有
   带 ``user_id`` 的表，断言都已被 ``user_purge.PURGED_TABLES`` 收录。以后任何人新增
   带 user_id 的表却没同步清理逻辑，这条直接红——不靠人工记忆。
2. ``test_delete_user_leaves_no_orphans``：真实走 HTTP 删用户，逐表核对无残留。

注意：``/api/auth/register`` 会调 ``sample_seeder`` 播种示例数据
（3 个分类 × 2 条任务，每条任务 1 会话 + 2 消息），所以新用户**天生就有**若干
conversations/categories/tasks。断言必须用「相对基线」而不是绝对值。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.core.db import Base, SessionLocal
from app.models.chat_file import ChatAttachment
from app.models.conversation import Conversation, Message
from app.models.user import User


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _user_scoped_tables() -> set[str]:
    """Base.metadata 里所有含 user_id 列的表名（动态，自动涵盖新增表）。"""
    return {t.name for t in Base.metadata.tables.values() if "user_id" in t.columns}


def _rows_for_uid(db, table_name: str, uid: int) -> int:
    tbl = Base.metadata.tables[table_name]
    return int(db.execute(
        select(func.count()).select_from(tbl).where(tbl.c.user_id == uid)
    ).scalar() or 0)


def _snapshot(db, uid: int) -> dict[str, int]:
    return {t: _rows_for_uid(db, t, uid) for t in sorted(_user_scoped_tables())}


# ---------------- 1) 动态兜底：清理覆盖面 ----------------

def test_purge_covers_every_user_scoped_table(client):
    """每个带 user_id 的表都必须被 purge_user_data 覆盖，否则删用户会留孤儿。

    client fixture 会触发 lifespan → init_db()，届时全部模型已注册进 Base.metadata。
    """
    from app.services.user_purge import PURGED_TABLES

    missing = _user_scoped_tables() - set(PURGED_TABLES)
    assert not missing, (
        f"以下表带 user_id 但未被 user_purge 覆盖，删用户会留下孤儿数据：{sorted(missing)}。"
        "请在 app/services/user_purge.py 的 _USER_SCOPED_TABLES 与 PURGED_TABLES 中同步登记，"
        "并在 purge_user_data 里补上对应删除。"
    )


def test_purged_tables_are_real_tables(client):
    """反向护栏：PURGED_TABLES 里不许出现拼错/已删除的表名（否则兜底断言形同虚设）。"""
    from app.services.user_purge import PURGED_TABLES

    bogus = set(PURGED_TABLES) - set(Base.metadata.tables)
    assert not bogus, f"PURGED_TABLES 含不存在的表名（拼写错误？）：{sorted(bogus)}"


# ---------------- 2) 真实链路：删用户后逐表无残留 ----------------

@pytest.fixture()
def doomed_user(client):
    """注册一个专用用户（不造数据；示例播种由 register 自己产生）。"""
    uname = f"purge_{uuid.uuid4().hex[:6]}"
    r = client.post("/api/auth/register", json={
        # 注意：邮箱域名不能用 .local/.test 等保留名，email-validator 会判 422
        "username": uname, "email": f"{uname}@example.com", "password": "Purge1234"})
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "username": uname}


def test_delete_user_leaves_no_orphans(client, accounts, doomed_user):
    """造齐脏数据 → admin 删用户 → 逐表核对 0 残留（本 bug 的核心回归）。"""
    uid = doomed_user["id"]

    # ---- 前置 1：register 会播种示例数据，基线本身不为 0 ----
    db = SessionLocal()
    try:
        base = _snapshot(db, uid)
    finally:
        db.close()
    assert base["conversations"] >= 1, "示例播种未生效，测试前提不成立"
    assert base["categories"] >= 1, "示例播种未生效，测试前提不成立"

    # ---- 前置 2：补造「历史上会被漏掉」的那几类数据 ----
    conv_id = uuid.uuid4().hex[:16]
    db = SessionLocal()
    try:
        db.add(Conversation(id=conv_id, user_id=uid, title="待删会话"))
        db.add(Message(conversation_id=conv_id, role="user", content="你好"))
        db.add(ChatAttachment(
            id=uuid.uuid4().hex[:16], user_id=uid,
            file_id=uuid.uuid4().hex[:16], file_name="gone.txt",
            # 指向不存在的相对路径：清理时 is_file() 为假会安全跳过，不影响断言
            file_path=f"chat/{uuid.uuid4().hex}.txt",
            sha256="0" * 64, size=1,
        ))
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        mid = _snapshot(db, uid)
        assert mid["conversations"] == base["conversations"] + 1
        assert mid["chat_attachments"] == base["chat_attachments"] + 1
        # 记录全部会话 id（含示例播种的），删除后要连带其消息一起消失
        all_conv_ids = [
            r[0] for r in db.execute(
                select(Conversation.id).where(Conversation.user_id == uid)).all()
        ]
    finally:
        db.close()

    # ---- 执行：admin 删用户 ----
    r = client.delete(f"/api/users/{uid}", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted_user_id"] == uid
    assert "deleted_tasks" in body, "返回体需保留 deleted_tasks 键（兼容既有调用方）"

    # ---- 校验：账号消失 + 逐表 0 残留 ----
    db = SessionLocal()
    try:
        assert db.get(User, uid) is None, "用户行应被删除"

        leftovers = {t: n for t, n in _snapshot(db, uid).items() if n > 0}
        assert not leftovers, f"删用户后仍有残留数据（孤儿行）：{leftovers}"

        orphan_msgs = int(db.execute(
            select(func.count()).select_from(Message)
            .where(Message.conversation_id.in_(all_conv_ids))
        ).scalar() or 0)
        assert orphan_msgs == 0, "会话被删但消息未级联"
    finally:
        db.close()


def test_delete_user_requires_admin(client, accounts, doomed_user):
    """越权护栏：普通用户不能删别人（顺带确认这批新逻辑没放宽权限）。"""
    r = client.delete(f"/api/users/{doomed_user['id']}",
                      headers=_hdr(accounts["user"]["token"]))
    assert r.status_code == 403


def test_shared_guest_cannot_be_deleted(client, accounts):
    """共享 guest 账号受保护（历史上全靠这个分支拦住，回归护栏）。"""
    db = SessionLocal()
    try:
        g = db.execute(select(User).where(User.username == "guest")).scalar_one_or_none()
        assert g is not None, "conftest 应已播种共享 guest"
        gid = g.id
    finally:
        db.close()
    r = client.delete(f"/api/users/{gid}", headers=_hdr(accounts["admin"]["token"]))
    assert r.status_code == 400
