"""演示站知识库快照播种（V9.4）测试。

覆盖：
1. 快照灌入：知识库/文档/分块 + 原始文件回灌 + file_path 指向当前实例
2. 幂等：重复播种不产生重复行、不重复计数
3. owner 重映射：快照里的 user_id 被改写为演示站账号 id
4. 缺快照：安静 no-op（不抛异常）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import UPLOAD_DIR
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.services.demo_kb_seed import seed_demo_kb

pytestmark = pytest.mark.usefixtures("client")


def _write_snapshot(root: Path, tag: str) -> Path:
    """构造最小快照：1 库 / 1 文档 / 1 分块 / 1 文件（无向量，避免依赖 Chroma）。

    tag 参与主键：同一 pytest 进程内 DB 行持久（session 级库），
    不同用例必须用互异 id，否则第二个用例会被幂等跳过。
    """
    kb_id = f"kb-snapshot-{tag}"
    doc_id = f"doc-snapshot-{tag}"
    chunk_id = f"chunk-snapshot-{tag}"
    snap = root / "snap"
    (snap / "files" / "knowledge" / kb_id).mkdir(parents=True, exist_ok=True)
    (snap / "files" / "knowledge" / kb_id / "demo.md").write_text("# 演示文档\nERP 采购订单", encoding="utf-8")
    (snap / "meta.json").write_text(json.dumps({"version": 1, "kb_ids": [kb_id]}), encoding="utf-8")
    (snap / "bases.json").write_text(json.dumps([{
        "id": kb_id, "user_id": 999, "name": "示例-快照知识库", "description": "d",
        "visibility": "global", "type": "document", "embedding_model_id": "",
        "chunking_config": "{}", "extract_config": "{}", "is_personal": False,
        "created_at": "2026-10-12 10:00:00", "updated_at": "2026-10-12 10:00:00",
        "deleted_at": None,
    }], ensure_ascii=False), encoding="utf-8")
    (snap / "docs.json").write_text(json.dumps([{
        "id": doc_id, "user_id": 999, "knowledge_base_id": kb_id, "type": "document",
        "title": "演示文档", "description": "", "file_name": "demo.md", "file_type": "md",
        "file_size": 24, "file_path": f"knowledge/{kb_id}/demo.md", "file_hash": "h",
        "parse_status": "ready", "error_message": "", "chunk_count": 1, "doc_meta": "{}",
        "custom_meta": "{}", "wiki_summary": "", "wiki_category": "", "source_key": None,
        "created_at": "2026-10-12 10:00:00", "updated_at": "2026-10-12 10:00:00",
        "deleted_at": None, "processed_at": "2026-10-12 10:00:01",
    }], ensure_ascii=False), encoding="utf-8")
    (snap / "chunks.json").write_text(json.dumps([{
        "id": chunk_id, "user_id": 999, "knowledge_base_id": kb_id,
        "knowledge_id": doc_id, "chunk_index": 0, "content": "ERP 采购订单 审批流程",
        "source_content": "ERP 采购订单 审批流程", "content_revision": 0, "context_header": "",
        "parent_chunk_id": "", "chunk_type": "text", "start_at": 0, "end_at": 10,
        "relation_chunks": "[]", "is_enabled": True, "index_status": "ready",
        "created_at": "2026-10-12 10:00:00", "updated_at": "2026-10-12 10:00:00", "deleted_at": None,
    }], ensure_ascii=False), encoding="utf-8")
    (snap / "revisions.json").write_text("[]", encoding="utf-8")
    return snap


@pytest.mark.title("演示站知识库快照灌入：行数据+文件落盘")
def test_seed_from_snapshot_creates_rows_and_files(tmp_path, db_session):
    """快照灌入：行数据入库、文件落到实例 uploads、file_path 指向当前实例。"""
    snap = _write_snapshot(tmp_path, "a")
    counts = seed_demo_kb(db_session, snap, owner_user_id=2)

    assert counts["bases"] == 1 and counts["docs"] == 1 and counts["chunks"] == 1
    assert counts["files"] == 1

    kb = db_session.get(KnowledgeBase, "kb-snapshot-a")
    assert kb is not None and kb.user_id == 2 and kb.name == "示例-快照知识库"
    doc = db_session.get(Knowledge, "doc-snapshot-a")
    assert doc is not None and doc.user_id == 2
    # file_path 被还原为当前实例的绝对路径，且文件确实存在
    assert doc.file_path.startswith(str(UPLOAD_DIR))
    assert Path(doc.file_path).is_file()
    assert Path(doc.file_path).read_text(encoding="utf-8").startswith("# 演示文档")
    assert db_session.get(Chunk, "chunk-snapshot-a") is not None


@pytest.mark.title("演示站知识库快照播种幂等：重复灌入不产生重复行")
def test_seed_is_idempotent(tmp_path, db_session):
    """重复播种：不新增重复行（主键幂等），第二次各项计数为 0。"""
    snap = _write_snapshot(tmp_path, "b")
    seed_demo_kb(db_session, snap, owner_user_id=2)
    again = seed_demo_kb(db_session, snap, owner_user_id=2)

    assert again["bases"] == 0 and again["docs"] == 0 and again["chunks"] == 0
    n_bases = db_session.execute(
        select(func.count()).select_from(KnowledgeBase)
        .where(KnowledgeBase.id == "kb-snapshot-b")
    ).scalar()
    n_docs = db_session.execute(
        select(func.count()).select_from(Knowledge)
        .where(Knowledge.knowledge_base_id == "kb-snapshot-b")
    ).scalar()
    assert n_bases == 1 and n_docs == 1


@pytest.mark.title("演示站知识库快照缺失：安静跳过不报错")
def test_seed_missing_snapshot_is_noop(tmp_path, db_session):
    """快照目录不存在：安静返回 skipped，不抛异常。"""
    result = seed_demo_kb(db_session, tmp_path / "not-exists", owner_user_id=2)
    assert result.get("skipped") is True
