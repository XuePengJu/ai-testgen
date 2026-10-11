"""P1 聊天附件入库统一测试。

覆盖：登录用户上传附件 → ChatAttachment 落库 / 持久副本存在 / Knowledge 行
source_key 正确 / 后台入库完成；同文件重传幂等（复用 Knowledge 行）；
访客不落库（AITF_FILE_INGEST_GUEST=0）；带 conversation_id 登记；
supported-formats 唯一真源路由。
"""
import hashlib
from pathlib import Path

from app.core.db import SessionLocal
from app.models.chat_file import ChatAttachment
from app.models.knowledge import Knowledge
from app.models.user import User
from app.services.memory.store import get_personal_kb_id


def _upload(client, token, filename, content, conversation_id=None):
    data = {"conversation_id": conversation_id} if conversation_id else {}
    return client.post(
        "/api/files",
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
        headers={"Authorization": "Bearer " + token},
    )


def test_upload_registers_attachment_and_kb_doc(client, accounts, db_session):
    """登录用户上传：附件登记 + 副本落盘 + Knowledge 行 source_key 正确 + 后台入库完成。"""
    tok = accounts["user"]["token"]
    content = "登录模块需求：支持账号密码登录，连续失败 5 次锁定 10 分钟".encode("utf-8")
    r = _upload(client, tok, "需求.txt", content, conversation_id="conv-p1-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_id"] and body["chars"] > 0
    assert body["knowledge_id"], "登录用户附件应同步登记进个人记忆库"

    att = db_session.query(ChatAttachment).filter(
        ChatAttachment.file_id == body["file_id"]).first()
    assert att is not None
    assert att.conversation_id == "conv-p1-1"
    assert att.sha256 == hashlib.sha256(content).hexdigest()
    assert att.size == len(content)
    assert att.knowledge_id == body["knowledge_id"]

    doc = db_session.get(Knowledge, body["knowledge_id"])
    assert doc is not None
    assert doc.source_key == f"file:{att.user_id}:{att.sha256[:32]}"
    assert doc.file_name == "需求.txt"
    assert doc.file_type == "txt"
    assert doc.file_size == len(content)
    # TestClient 会在请求内同步执行后台任务 → 入库应已完成
    assert doc.parse_status == "ready"
    assert (doc.chunk_count or 0) >= 1

    # 持久副本存在且内容一致（不受 24h 清扫影响）
    copy = Path(doc.file_path)
    assert copy.exists(), f"副本不存在：{doc.file_path}"
    assert copy.name == f"{doc.id}.txt"
    assert copy.parent.parent.name == "knowledge"
    assert copy.read_bytes() == content


def test_reupload_same_file_reuses_knowledge(client, accounts, db_session):
    """同文件重传：source_key 幂等复用同一 Knowledge 行，附件记录各一条。"""
    tok = accounts["user"]["token"]
    content = "同一份需求文档内容".encode("utf-8")
    r1 = _upload(client, tok, "same.txt", content)
    r2 = _upload(client, tok, "same.txt", content)
    assert r1.status_code == 200 and r2.status_code == 200, r1.text + r2.text
    k1, k2 = r1.json()["knowledge_id"], r2.json()["knowledge_id"]
    assert k1 and k2 and k1 == k2

    u = db_session.query(User).filter(
        User.username == accounts["user"]["username"]).first()
    sha = hashlib.sha256(content).hexdigest()
    rows = db_session.query(Knowledge).filter(
        Knowledge.source_key == f"file:{u.id}:{sha[:32]}",
        Knowledge.deleted_at.is_(None)).all()
    assert len(rows) == 1, f"同 source_key 应只有一行，实际 {len(rows)}"
    # 两次上传各生成一条附件记录（file_id 不同），都指向同一文档
    assert r1.json()["file_id"] != r2.json()["file_id"]
    assert db_session.query(ChatAttachment).filter(
        ChatAttachment.knowledge_id == k1).count() == 2


def test_guest_upload_skips_ingest(client, fresh_guest, db_session):
    """访客（AITF_FILE_INGEST_GUEST=0）：只做对话缓存，不登记附件不入库。"""
    token, _ = fresh_guest
    r = _upload(client, token, "访客附件.txt", "访客上传的内容".encode("utf-8"))
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_id"] is None

    g = db_session.query(User).filter(User.username == "guest").first()
    assert db_session.query(ChatAttachment).filter(
        ChatAttachment.user_id == g.id).count() == 0
    kb_id = get_personal_kb_id(db_session, g.id)
    assert kb_id is not None
    assert db_session.query(Knowledge).filter(
        Knowledge.knowledge_base_id == kb_id).count() == 0


def test_supported_formats_endpoint(client):
    """/api/knowledge/supported-formats：后端唯一真源。"""
    r = client.get("/api/knowledge/supported-formats")
    assert r.status_code == 200, r.text
    data = r.json()
    assert ".docx" in data["exts"] and ".doc" in data["exts"] and ".csv" in data["exts"]
    assert ".png" in data["images"] and ".bmp" in data["images"]
    assert data["hint"]
    # 两组不重叠，合集等于 doc_extract.SUPPORTED_EXTS
    from app.services.doc_extract import SUPPORTED_EXTS
    assert sorted(data["exts"] + data["images"]) == sorted(SUPPORTED_EXTS)
