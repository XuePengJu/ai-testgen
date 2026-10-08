"""知识库文档「多选批量删除」回归（V7.6）。

新增接口：``POST /api/knowledge/documents/batch-delete``，入参 ``{ids: [...]}``。

与单删接口同一套语义（``_can_manage`` = 创建者或 admin），关键差异是**不因个别文档
越权/不存在而整批失败** —— 不可删的进 ``skipped``，删除出错的进 ``failed``，其余照删。
本文件锁住这些语义，并守住「越权不能借批量接口绕过」这条底线。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.models.user import User
from app.services.memory.store import get_personal_kb_id


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _me(client, token) -> dict:
    return client.get("/api/auth/me", headers=_hdr(token)).json()


def _mk_doc(db, uid: int, kb_id: str, title: str, n_chunks: int = 1) -> str:
    """直插文档 + 分块（绕开 ingest 管线，测试只关心删除语义）。"""
    did = uuid.uuid4().hex[:16]
    db.add(Knowledge(id=did, user_id=uid, knowledge_base_id=kb_id,
                     title=title, file_name=f"{title}.md", file_type="md",
                     file_size=10, parse_status="ready", chunk_count=n_chunks))
    for i in range(n_chunks):
        db.add(Chunk(id=uuid.uuid4().hex[:16], user_id=uid, knowledge_base_id=kb_id,
                     knowledge_id=did, chunk_index=i, content=f"{title} 的第 {i} 段"))
    db.commit()
    return did


def _alive(db, doc_id: str) -> bool:
    return db.execute(
        select(Knowledge).where(Knowledge.id == doc_id, Knowledge.deleted_at.is_(None))
    ).scalar_one_or_none() is not None


def _chunk_count(db, doc_id: str) -> int:
    return int(db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.knowledge_id == doc_id)
    ).scalar() or 0)


@pytest.fixture()
def admin_kb(client, accounts):
    """admin 的个人知识库 id（注册时会自动建）。"""
    uid = _me(client, accounts["admin"]["token"])["id"]
    db = SessionLocal()
    try:
        kb_id = get_personal_kb_id(db, uid)
    finally:
        db.close()
    assert kb_id, "admin 应有个人知识库"
    return {"uid": uid, "kb_id": kb_id}


def test_batch_delete_removes_docs_and_chunks(client, accounts, admin_kb):
    """核心路径：一次删多篇 → 文档软删、分块被清。"""
    db = SessionLocal()
    try:
        ids = [_mk_doc(db, admin_kb["uid"], admin_kb["kb_id"], f"待删-{i}", n_chunks=2)
               for i in range(3)]
        assert all(_alive(db, i) for i in ids)
    finally:
        db.close()

    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["admin"]["token"]), json={"ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] == 3, body
    assert body["skipped"] == [] and body["failed"] == [], body
    assert body["total"] == 3

    db = SessionLocal()
    try:
        for i in ids:
            assert not _alive(db, i), "文档应被软删除"
            assert _chunk_count(db, i) == 0, "分块应被物理清除"
    finally:
        db.close()


def test_nonexistent_ids_are_skipped_not_failed(client, accounts, admin_kb):
    """不存在的 id 进 skipped，其余照删 —— 不整批失败。"""
    db = SessionLocal()
    try:
        real = _mk_doc(db, admin_kb["uid"], admin_kb["kb_id"], "存在的一篇")
    finally:
        db.close()
    ghost = "0" * 16

    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["admin"]["token"]), json={"ids": [real, ghost]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["deleted"] == 1
    assert ghost in b["skipped"], b
    assert b["failed"] == []

    db = SessionLocal()
    try:
        assert not _alive(db, real)
    finally:
        db.close()


def test_cannot_delete_others_docs_via_batch(client, accounts, admin_kb):
    """越权护栏：普通用户借批量接口删别人的文档 → 进 skipped，且文档仍在。"""
    db = SessionLocal()
    try:
        victim = _mk_doc(db, admin_kb["uid"], admin_kb["kb_id"], "admin 的文档")
    finally:
        db.close()

    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["user"]["token"]), json={"ids": [victim]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["deleted"] == 0, "普通用户不该删得掉 admin 的文档"
    assert victim in b["skipped"], b

    db = SessionLocal()
    try:
        assert _alive(db, victim), "越权请求不得真的删掉文档"
    finally:
        db.close()


def test_ids_are_deduped(client, accounts, admin_kb):
    """重复 id 去重：同一篇传三次只算一次删除。"""
    db = SessionLocal()
    try:
        did = _mk_doc(db, admin_kb["uid"], admin_kb["kb_id"], "重复提交")
    finally:
        db.close()

    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["admin"]["token"]),
                    json={"ids": [did, did, did]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["deleted"] == 1, b
    assert b["total"] == 1, "total 应是去重后的条数"


def test_empty_ids_is_noop(client, accounts):
    """空数组是合法 no-op，不报错。"""
    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["admin"]["token"]), json={"ids": []})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["deleted"] == 0 and b["total"] == 0


def test_over_limit_rejected(client, accounts):
    """上限护栏：>500 条直接 400（防误传整库）。"""
    r = client.post("/api/knowledge/documents/batch-delete",
                    headers=_hdr(accounts["admin"]["token"]),
                    json={"ids": [uuid.uuid4().hex[:16] for _ in range(501)]})
    assert r.status_code == 400, r.text
    assert "500" in str(r.json().get("detail", ""))


def test_requires_auth(client):
    """未登录直接 401。"""
    r = client.post("/api/knowledge/documents/batch-delete", json={"ids": ["x"]})
    assert r.status_code == 401
