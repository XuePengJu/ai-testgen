"""P4 记忆回注检索测试（个人库强制检索 + citations.personal 标记 + 权限隔离）。

覆盖：不传 kb_ids 仍命中个人记忆库且 citations 含 personal=true /
传业务库时个人库与业务库两类都命中 / 他人账号检索不到我的记忆。
LLM 一律 mock（monkeypatch memory_chat；对话走平台 mock 模型）。
"""
from app.core.db import SessionLocal
from app.models.conversation import Conversation
from app.services.knowledge import vectorstore

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}

# 个人记忆文档里的独特业务规则（mock 哈希向量靠字符重叠召回）
MEM_RULE = "库存上限规则：单笔订单库存上限为 500 件，超限需要审批"


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _seed_personal_memory(client, token: str) -> str:
    """走真实链路播种个人记忆：建会话 + 消息 → 打标 → process_conversation（mock LLM）。"""
    from app.jobs.chat_memory import process_conversation

    r = client.post("/api/conversations", json={"title": "库存规则会话"}, headers=HDR(token))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "user", "content": MEM_RULE}, headers=HDR(token))
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "assistant", "content": "已记录库存上限规则"},
                headers=HDR(token))
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        conv.mem_dirty = True
        db.commit()
        out = process_conversation(db, conv, manual=False)
        assert out["memory"] == "created", out
        return out["doc_id"]
    finally:
        db.close()


def _citations(text: str) -> list[dict]:
    """从 SSE 文本里解析 citations 事件的 items（无则空列表）。"""
    import json
    for block in text.split("\n\n"):
        if block.startswith("event: citations"):
            for line in block.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])["items"]
    return []


def test_personal_kb_hit_without_kb_ids(client, monkeypatch):
    """不传 kb_ids：个人记忆库强制检索，citations 命中且 personal=true。"""
    calls: list = []
    monkeypatch.setattr(
        "app.services.memory.llm.memory_chat",
        lambda p, prev_memory="": (calls.append(p),
                                   f"## 背景\n库存\n\n## 关键结论\n{MEM_RULE}\n\n"
                                   "## 业务规则\n上限 500 件\n\n## 待办与遗留\n（暂无）")[1])
    token = _register(client, "memrecall1")
    _seed_personal_memory(client, token)

    r = client.post("/api/chat/stream", json={"message": "库存上限是多少件？"},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    assert "event: error" not in r.text
    cites = _citations(r.text)
    assert cites, "不传 kb_ids 也必须命中个人记忆库（强制检索）"
    assert any(c.get("personal") is True for c in cites), cites


def test_personal_and_biz_kb_both_hit(client, monkeypatch):
    """传业务库：个人记忆与业务文档两类都命中（personal 标记区分）。"""
    calls: list = []
    monkeypatch.setattr(
        "app.services.memory.llm.memory_chat",
        lambda p, prev_memory="": (calls.append(p),
                                   f"## 背景\n库存\n\n## 关键结论\n{MEM_RULE}\n\n"
                                   "## 业务规则\n上限 500 件\n\n## 待办与遗留\n（暂无）")[1])
    token = _register(client, "memrecall2")
    _seed_personal_memory(client, token)

    # 业务库：入库一条与记忆不同的规则文档
    r = client.post("/api/knowledge/bases",
                    json={"name": "退货规则库", "visibility": "private"},
                    headers=HDR(token))
    assert r.status_code in (200, 201), r.text
    kb_id = r.json()["id"]
    r = client.post(f"/api/knowledge/bases/{kb_id}/documents",
                    files={"file": ("退货规则.md",
                                    "退货金额超过 5000 元必须财务审批".encode(),
                                    "text/markdown")},
                    headers=HDR(token))
    assert r.status_code in (200, 201), r.text
    assert r.json()["parse_status"] == "ready", r.text

    r = client.post("/api/chat/stream",
                    json={"message": "库存上限 500 件和退货审批规则分别是什么？",
                          "kb_ids": [kb_id]},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    cites = _citations(r.text)
    assert any(c.get("personal") is True for c in cites), "必须命中个人记忆"
    assert any(c.get("personal") is False for c in cites), "必须命中业务库"


def test_other_user_cannot_recall(client, monkeypatch):
    """权限隔离：他人账号检索不到我的记忆（无 citations / 无 personal=true）。"""
    calls: list = []
    monkeypatch.setattr(
        "app.services.memory.llm.memory_chat",
        lambda p, prev_memory="": (calls.append(p),
                                   f"## 背景\n库存\n\n## 关键结论\n{MEM_RULE}\n\n"
                                   "## 业务规则\n上限 500 件\n\n## 待办与遗留\n（暂无）")[1])
    token_a = _register(client, "memrecall3")
    _seed_personal_memory(client, token_a)

    token_b = _register(client, "memrecall4")
    r = client.post("/api/chat/stream", json={"message": "库存上限是多少件？"},
                    headers=HDR(token_b))
    assert r.status_code == 200, r.text
    assert "event: error" not in r.text
    cites = _citations(r.text)
    assert not any(c.get("personal") is True for c in cites), \
        "他人账号绝不能检索到我的个人记忆"
    # B 自己没有业务库勾选、记忆为空 → 整个 citations 事件都不应出现
    assert not cites, cites

    # 向量层二次验证：A 的记忆文档向量确实存在（隔离靠检索方 kb_id 过滤保证）
    doc_ids = vectorstore._collection().get(where={"user_id": _uid("memrecall3")})["ids"]
    assert doc_ids


def _uid(username: str) -> int:
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u
        return u.id
    finally:
        db.close()
