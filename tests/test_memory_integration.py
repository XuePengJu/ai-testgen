"""对话记忆 + 个人知识库 全链路集成测试（全程 mock LLM）。

链路：注册（自动建个人库）→ 建会话 → 上传附件（/api/files 带 conversation_id，
登记 ChatAttachment + 个人库副本 + 后台入库）→ 追加消息 → POST memory/digest
（202 running）→ 轮询 GET memory/state 到 done → 断言：
  - 个人库出现「会话记忆」文档（source_key=mem:conv:*）与附件文档（file:*）
  - chat_attachments.knowledge_id 回写、附件 parse_status=ready
  - vectorstore.search 命中记忆分块；对话 citations 带 personal=true
  - DELETE 会话 → 记忆文档/附件文档/向量/副本文件/登记行零残留

LLM 全部替换：memory_chat 返回固定 Markdown（monkeypatch）；
对话走平台 mock 模型（conftest 未配真实 Key，source=mock）。
"""
import json

import pytest

from app.core.db import SessionLocal
from app.models.chat_file import ChatAttachment
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.services.knowledge import vectorstore

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}

# 固定记忆文档（含独特业务规则，供 mock 哈希向量召回）
MEM_MD = ("## 背景\n库存模块联调\n\n## 关键结论\n单笔订单库存上限为 666 件，"
          "超限必须财务审批\n\n## 业务规则\n上限 666 件\n\n## 待办与遗留\n（暂无）")
MEM_KEY = "库存上限为 666 件"


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _personal_kb(db, user_id: int) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.user_id == user_id, KnowledgeBase.is_personal.is_(True),
        KnowledgeBase.deleted_at.is_(None)).one()
    return kb


def _uid(username: str) -> int:
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u
        return u.id
    finally:
        db.close()


def _citations(resp_text: str) -> list[dict]:
    for block in resp_text.split("\n\n"):
        if "event: citations" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])["items"]
    return []


def _state(client, token: str, cid: str) -> dict:
    r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_full_memory_lifecycle(client, monkeypatch):
    """上传附件 → 消息 → 手动提炼 → 状态 done → 检索命中 → 删除零残留。"""
    # 0) 注册（P0：自动创建个人库）
    token = _register(client, "memint1")
    uid = _uid("memint1")
    db = SessionLocal()
    try:
        kb = _personal_kb(db, uid)
        assert kb.is_personal and kb.visibility == "private"
    finally:
        db.close()

    # 1) 建会话 + 上传附件（带 conversation_id）
    r = client.post("/api/conversations", json={"title": "集成链路会话"}, headers=HDR(token))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    att_text = "# 附件规则\n库存模块的特殊约定：附件标记规则 ZZ-ATT-42 只在集成测试出现。"
    r = client.post("/api/files",
                    files={"file": ("附件规则.md", att_text.encode(), "text/markdown")},
                    data={"conversation_id": cid},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["knowledge_id"], "登录用户附件必须登记个人库并返回 knowledge_id"
    att_doc_id = up["knowledge_id"]

    # 2) 追加一轮消息（对话内容供提炼）
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "user", "content": MEM_KEY + "，请记住这个规则"},
                headers=HDR(token))
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "assistant", "content": "已记录库存上限规则"},
                headers=HDR(token))

    # 附件后台入库已在 TestClient 同步执行完：登记行 + 副本 + ready
    db = SessionLocal()
    try:
        att_rows = db.query(ChatAttachment).filter(
            ChatAttachment.conversation_id == cid).all()
        assert len(att_rows) == 1, "附件必须登记 chat_attachments"
        assert att_rows[0].knowledge_id == att_doc_id, "knowledge_id 必须回写"
        att_doc = db.get(Knowledge, att_doc_id)
        assert att_doc is not None and att_doc.parse_status == "ready", \
            "附件上传后台入库必须完成（parse_status=ready）"
        assert att_doc.knowledge_base_id == kb.id, "附件副本必须进个人库"
        assert att_doc.file_path, "持久副本路径必须落库"
        import os
        assert os.path.exists(att_doc.file_path), "副本文件必须真实存在"
        att_copy_path = att_doc.file_path
    finally:
        db.close()

    # 3) 手动提炼：POST digest（mock memory_chat）→ 202 running → 轮询 state 到 done
    monkeypatch.setattr("app.services.memory.llm.memory_chat",
                        lambda p, prev_memory="": MEM_MD)
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202, r.text
    assert r.json() == {"status": "running"}
    # 轮询（TestClient 下后台任务同步跑完，这里仍按前端契约轮询状态机）
    state = {}
    for _ in range(10):
        state = _state(client, token, cid)
        if state["status"] == "done":
            break
    assert state["status"] == "done", state
    assert state["doc_id"], state
    assert state["error"] is None, state
    assert state["msg_count"] == 2, state
    assert state["attachments"] == {"total": 1, "done": 1}, state

    # 4) 个人库内容断言：会话记忆文档 + 附件文档都在
    db = SessionLocal()
    try:
        mem_doc = db.query(Knowledge).filter(
            Knowledge.source_key == f"mem:conv:{uid}:{cid}").one()
        assert mem_doc.knowledge_base_id == kb.id
        assert mem_doc.title.startswith("会话记忆"), mem_doc.title
        assert db.get(Knowledge, state["doc_id"]) is not None
        # 记忆正文确实存在 chunks 里且含关键结论
        text = "".join(ch.content for ch in db.query(Chunk).filter(
            Chunk.knowledge_id == mem_doc.id).order_by(Chunk.chunk_index))
        assert MEM_KEY in text
    finally:
        db.close()

    # 5) 向量层：个人库检索能命中记忆分块（真实 chunk + mock 向量）
    mem_hits = vectorstore.search(MEM_KEY, [kb.id], top_k=3)
    assert mem_hits, "个人库必须能检索到会话记忆分块"
    assert any(h["metadata"].get("knowledge_id") == mem_doc.id for h in mem_hits)

    # 6) 对话检索回注：不选库 → citations 命中个人库且 personal=true
    r = client.post("/api/chat/stream", json={"message": MEM_KEY},
                    headers=HDR(token))
    assert r.status_code == 200 and "event: error" not in r.text
    cites = _citations(r.text)
    assert cites, "个人库有记忆时必须回注 citations"
    assert any(x.get("personal") is True for x in cites), cites

    # 7) 删除会话 → 记忆文档/附件文档/向量/副本文件/登记行零残留
    r = client.delete(f"/api/conversations/{cid}", headers=HDR(token))
    assert r.status_code == 200, r.text
    assert r.json()["deleted_docs"] >= 2, r.text  # 记忆文档 + 附件文档
    db = SessionLocal()
    try:
        assert db.get(Knowledge, mem_doc.id) is None, "会话记忆文档必须物理删除"
        assert db.get(Knowledge, att_doc_id) is None, "附件入库文档必须物理删除"
        assert db.query(Chunk).filter(Chunk.knowledge_id == mem_doc.id).count() == 0
        assert db.query(Chunk).filter(Chunk.knowledge_id == att_doc_id).count() == 0
        assert db.query(ChatAttachment).filter(
            ChatAttachment.conversation_id == cid).count() == 0, "附件登记行必须随会话删除"
    finally:
        db.close()
    assert vectorstore._collection().get(
        where={"knowledge_id": mem_doc.id})["ids"] == [], "记忆向量必须零残留"
    assert vectorstore._collection().get(
        where={"knowledge_id": att_doc_id})["ids"] == [], "附件向量必须零残留"
    import os
    assert not os.path.exists(att_copy_path), "附件副本文件必须被清理"


def test_attachment_fallback_accepts_conversation(client):
    """契约口径：兜底补跑入参是 conversation_id 字符串，返回 {total, ingested, skipped}。

    Bug#1 已修复：chat_memory._ingest_pending_attachments 改传 conv.id，
    返回键与 file_ingest.ingest_pending_attachments 对齐为
    {"total", "ingested", "skipped"}。
    """
    from app.jobs.chat_memory import _ingest_pending_attachments
    from app.models.conversation import Conversation

    token = _register(client, "memint2")
    uid = _uid("memint2")
    r = client.post("/api/conversations", json={"title": "兜底会话"}, headers=HDR(token))
    cid = r.json()["id"]
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        out = _ingest_pending_attachments(db, conv)   # 与 chat_memory.py:104 同款调用
        assert set(out.keys()) == {"total", "ingested", "skipped"}, out
    finally:
        db.close()
