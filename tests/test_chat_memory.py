"""P2 对话记忆提炼核心链路测试（app/jobs/chat_memory.py + memory.store.upsert）。

覆盖：首次提炼建文档 / 二次覆盖 doc_id 不变且 chunks 不翻倍（先删向量）/
水位推进 / LLM 失败不清 mem_dirty 不动水位 / 日报幂等 / 删会话级联零残留。
LLM 一律 mock（monkeypatch memory_chat），不真调模型。
"""
from datetime import timedelta

import pytest

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.chat_file import ChatAttachment
from app.models.conversation import Conversation, Message
from app.models.knowledge import Chunk, Knowledge
from app.services.knowledge import vectorstore
from app.services.memory.store import get_personal_kb_id

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _register(client, username: str) -> str:
    """注册 + 登录，返回 token（角色为 user；admin/user 槽位已被 conftest 占用）。"""
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _uid(username: str) -> int:
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u, f"用户 {username} 不存在"
        return u.id
    finally:
        db.close()


def _mk_conv(client, token: str, uid: int, title: str = "登录模块测试会话") -> str:
    """建会话 + 一轮消息（user/assistant），并手动打上 mem_dirty 标记。"""
    r = client.post("/api/conversations", json={"title": title}, headers=HDR(token))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages",
                    json={"role": "user", "content": "登录模块要校验手机号格式"},
                    headers=HDR(token))
    assert r.status_code == 201, r.text
    r = client.post(f"/api/conversations/{cid}/messages",
                    json={"role": "assistant", "content": "已记录登录校验规则，手机号 11 位"},
                    headers=HDR(token))
    assert r.status_code == 201, r.text
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        assert conv and conv.user_id == uid
        conv.mem_dirty = True
        db.commit()
    finally:
        db.close()
    return cid


def _fake_llm(calls: list):
    """构造可计数的 memory_chat 替身（输出固定 Markdown 记忆文档）。"""
    def _fake(prompt: str, prev_memory: str = "") -> str:
        calls.append({"prompt": prompt, "prev": prev_memory})
        return ("## 背景\n登录模块联调\n\n## 关键结论\n手机号校验 11 位\n\n"
                "## 业务规则\n登录失败 5 次锁定\n\n## 待办与遗留\n（暂无）")
    return _fake


def _chunk_count(db, doc_id: str) -> int:
    return db.execute(_select_chunks(doc_id)).scalar_one()


def _select_chunks(doc_id: str):
    from sqlalchemy import select, func
    return select(func.count()).select_from(Chunk).where(Chunk.knowledge_id == doc_id)


def _vector_ids(doc_id: str) -> list[str]:
    return list(vectorstore._collection().get(where={"knowledge_id": doc_id})["ids"])


def test_first_digest_creates_doc(client, monkeypatch):
    """首次提炼：建个人库 + 记忆文档入库 + 水位推进 + mem_dirty 清零。"""
    from app.jobs.chat_memory import process_conversation

    token = _register(client, "memchain1")
    uid = _uid("memchain1")
    cid = _mk_conv(client, token, uid)
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))

    db = SessionLocal()
    try:
        out = process_conversation(db, db.get(Conversation, cid), manual=False)
        assert out["memory"] == "created"
        assert out["doc_id"]
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is False
        assert conv.mem_doc_id == out["doc_id"]
        assert conv.mem_last_msg_id is not None
        from sqlalchemy import select as _select
        latest = db.execute(
            _select(Message.id).where(Message.conversation_id == cid)
            .order_by(Message.id.desc()).limit(1)
        ).scalar()
        assert conv.mem_last_msg_id == latest  # 水位 = 最新消息 id
        assert calls and "登录模块" in calls[0]["prompt"]  # transcript 进了 prompt
        doc = db.get(Knowledge, out["doc_id"])
        assert doc.source_key == f"mem:conv:{uid}:{cid}"
        assert doc.parse_status == "ready"
        assert doc.knowledge_base_id == get_personal_kb_id(db, uid)
        assert _chunk_count(db, doc.id) > 0
    finally:
        db.close()


def test_second_digest_idempotent(client, monkeypatch):
    """二次提炼：doc_id 不变、chunks 数不翻倍、向量数与 chunks 一致（先删后建）。"""
    from app.jobs.chat_memory import process_conversation

    token = _register(client, "memchain2")
    uid = _uid("memchain2")
    cid = _mk_conv(client, token, uid)
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))

    db = SessionLocal()
    try:
        first = process_conversation(db, db.get(Conversation, cid), manual=False)
        doc_id = first["doc_id"]
        n1 = _chunk_count(db, doc_id)
        v1 = _vector_ids(doc_id)
        assert len(v1) == n1

        # 追加增量消息 → 重新打标 → 再提炼
        db.add(Message(conversation_id=cid, role="user", content="补充：登录要加验证码"))
        conv = db.get(Conversation, cid)
        conv.mem_dirty = True
        db.commit()
        second = process_conversation(db, db.get(Conversation, cid), manual=False)
        assert second["memory"] == "updated"
        assert second["doc_id"] == doc_id  # 幂等覆盖，不新建文档
        n2 = _chunk_count(db, doc_id)
        assert n2 == n1  # 先删旧分块再重建，绝不翻倍
        assert _vector_ids(doc_id) and len(_vector_ids(doc_id)) == n2  # 旧向量已清
        assert calls[1]["prev"]  # 上一版记忆进了滚动合并
    finally:
        db.close()


def test_llm_failure_keeps_watermark(client, monkeypatch):
    """LLM 失败：mem_dirty 不清、水位不动，异常向上抛（夜间任务记录后继续）。"""
    from app.jobs.chat_memory import process_conversation

    token = _register(client, "memchain3")
    uid = _uid("memchain3")
    cid = _mk_conv(client, token, uid)

    def _boom(prompt, prev_memory=""):
        raise RuntimeError("模型爆炸")

    monkeypatch.setattr("app.services.memory.llm.memory_chat", _boom)
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        before_id = conv.mem_last_msg_id
        with pytest.raises(Exception):
            process_conversation(db, conv, manual=False)
        db.expire_all()
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is True          # 不清待提炼标记
        assert conv.mem_last_msg_id == before_id  # 水位不动
        assert conv.mem_doc_id is None
    finally:
        db.close()


def test_daily_index_idempotent(client, monkeypatch):
    """记忆日报：两次生成同一 doc，chunks/向量不翻倍。"""
    from app.jobs.chat_memory import build_daily_index, process_conversation

    token = _register(client, "memchain4")
    uid = _uid("memchain4")
    cid = _mk_conv(client, token, uid)
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))

    db = SessionLocal()
    try:
        process_conversation(db, db.get(Conversation, cid), manual=False)
        # 与业务切日同口径（AITF_MEMORY_TZ，非 UTC）
        from app.jobs.chat_memory import _biz_date
        today = _biz_date()
        db.expire_all()
        doc1 = build_daily_index(db, uid, today)
        assert doc1
        n1 = _chunk_count(db, doc1)
        v1 = _vector_ids(doc1)
        doc2 = build_daily_index(db, uid, today)
        assert doc2 == doc1  # 幂等覆盖
        assert _chunk_count(db, doc2) == n1
        # 向量重建后 id 会变（chunk 随机 id），但数量必须一致（不残留不翻倍）
        assert len(_vector_ids(doc2)) == len(v1)
        doc = db.get(Knowledge, doc2)
        assert doc.source_key == f"mem:daily:{uid}:{today}"
        assert doc.title == f"记忆日报 · {today}"
    finally:
        db.close()


def test_daily_index_uses_biz_timezone(client, monkeypatch):
    """时区切日：mem_at=UTC 20:00（北京次日 04:00）必须归入北京时间的次日日报。"""
    from datetime import datetime

    from app.jobs.chat_memory import build_daily_index, process_conversation

    token = _register(client, "memchain6")
    uid = _uid("memchain6")
    cid = _mk_conv(client, token, uid)
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))

    db = SessionLocal()
    try:
        process_conversation(db, db.get(Conversation, cid), manual=False)
        # 把 mem_at 固定为 naive UTC 2026-01-14 20:00 = 北京时间 2026-01-15 04:00
        conv = db.get(Conversation, cid)
        conv.mem_at = datetime(2026, 1, 14, 20, 0, 0)
        db.commit()
        db.expire_all()
        # 北京时间的「次日」（2026-01-15）应包含该会话
        doc = build_daily_index(db, uid, "2026-01-15")
        assert doc
        row = db.get(Knowledge, doc)
        assert row.source_key == f"mem:daily:{uid}:2026-01-15"
        # UTC 口径的「当日」（2026-01-14）不含该会话 → 不生成空日报
        assert build_daily_index(db, uid, "2026-01-14") == ""
    finally:
        db.close()


def test_delete_conversation_cascade(client, monkeypatch, tmp_path):
    """删会话：记忆文档 + 附件入库文档的行/分块/向量/副本文件/附件登记行零残留。"""
    token = _register(client, "memchain5")
    uid = _uid("memchain5")
    cid = _mk_conv(client, token, uid)
    calls: list = []
    monkeypatch.setattr("app.services.memory.llm.memory_chat", _fake_llm(calls))

    from app.jobs.chat_memory import process_conversation
    db = SessionLocal()
    try:
        out = process_conversation(db, db.get(Conversation, cid), manual=False)
        mem_doc_id = out["doc_id"]
        kb_id = get_personal_kb_id(db, uid)
        # 造一个附件入库文档（行 + 分块 + 向量 + 副本文件）+ 附件登记行
        copy_file = tmp_path / "附件副本.txt"
        copy_file.write_text("attachment copy", encoding="utf-8")
        att_doc = Knowledge(user_id=uid, knowledge_base_id=kb_id, type="document",
                            title="需求附件", file_name="需求.txt", file_type="txt",
                            file_path=str(copy_file), file_hash="h1",
                            parse_status="ready")
        db.add(att_doc)
        db.commit()
        db.refresh(att_doc)
        db.add(Chunk(user_id=uid, knowledge_base_id=kb_id, knowledge_id=att_doc.id,
                     chunk_index=0, content="附件内容", source_content="附件内容"))
        db.commit()
        vectorstore.index_chunks(
            kb_id=kb_id, knowledge_id=att_doc.id, user_id=uid, visibility="private",
            chunks=[{"id": f"vec-{att_doc.id}", "content": "附件内容", "chunk_index": 0}],
            file_name="需求.txt")
        att_row = ChatAttachment(conversation_id=cid, user_id=uid, file_id="f-1",
                                 file_name="需求.txt", file_path="uploads/chat/需求.txt",
                                 knowledge_id=att_doc.id)
        db.add(att_row)
        db.commit()
        att_id = att_row.id
        # 两条向量都应在库
        assert _vector_ids(mem_doc_id) and _vector_ids(att_doc.id)
    finally:
        db.close()

    r = client.delete(f"/api/conversations/{cid}", headers=HDR(token))
    assert r.status_code == 200, r.text
    assert r.json()["deleted_docs"] >= 2

    db = SessionLocal()
    try:
        assert db.get(Conversation, cid) is None       # 会话没了
        assert db.get(Knowledge, mem_doc_id) is None   # 记忆文档行没了
        assert db.query(ChatAttachment).filter(
            ChatAttachment.id == att_id).first() is None  # 附件登记行没了
        assert db.execute(_select_chunks(mem_doc_id)).scalar_one() == 0   # 分块没了
        assert _vector_ids(mem_doc_id) == []           # 向量没了
        assert db.execute(
            Knowledge.__table__.select().where(
                Knowledge.source_key == f"mem:conv:{uid}:{cid}")).first() is None
    finally:
        db.close()
    assert not copy_file.exists()                      # 副本文件没了
