"""回归：`_build_rag_context` 的 citations `personal` 标记口径（V7.4 修复）。

缺陷（V7.2 遗留）：原表达式 `bool(personal_kb_id) and (kb_id==personal_kb_id or
metadata.personal is True)` 把「用户是否已有个人知识库」当成了前置与条件——
当用户**没有**个人库（`personal_kb_id is None`）却命中**条目记忆**
（`metadata["personal"] is True`）时，整体被短路成 `False`，条目记忆被误标为非个人来源。
修法：条目记忆命中恒为 True；个人库文档仍按 `kb_id` 匹配判定。

这里直接驱动 `_build_rag_context`（monkeypatch 其外部依赖），断言产出的 cites。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core import config
from app.core.db import SessionLocal
from app.api.chat import _build_rag_context
from app.services.knowledge import vectorstore


def _item_hit(item_id: str) -> dict:
    return {
        "id": f"memitem:{item_id}",
        "score": 0.3,
        "document": "库存上限 666 件，需要财务审批",
        "metadata": {
            "chunk_id": f"memitem:{item_id}",
            "knowledge_id": "",
            "personal": True,
            "memory_item_id": item_id,
            "file_name": f"记忆 · 库存上限",
        },
    }


def _biz_hit(cid: str, kb_id: str) -> dict:
    return {
        "id": cid,
        "score": 0.4,
        "document": "业务库文档正文",
        "metadata": {"chunk_id": cid, "knowledge_id": "", "kb_id": kb_id,
                     "file_name": "biz.pdf"},
    }


def _patch_rag(monkeypatch, *, personal_kb_id, visible, search_hits, item_hits):
    """把 _build_rag_context 的外部依赖全部替身化（不碰真实 Chroma / LLM）。"""
    monkeypatch.setattr("app.services.memory.store.get_personal_kb_id",
                        lambda *a, **k: personal_kb_id)
    monkeypatch.setattr("app.services.knowledge.ingest.visible_kb_ids",
                        lambda *a, **k: list(visible))
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: list(search_hits))
    monkeypatch.setattr("app.services.memory.retrieve.hybrid_search",
                        lambda *a, **k: list(item_hits))
    monkeypatch.setattr("app.services.llm_service.resolve_embedding",
                        lambda *a, **k: {"cfg": None})
    # 关文档混合开关 → 业务路径直返 vectorstore.search，保我喂的命中不被 RRF 过滤
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_ENABLED", 0)
    monkeypatch.setattr(config, "AITF_MEMORY_HYBRID_ENABLED", 1)


def test_item_memory_personal_true_without_personal_kb(monkeypatch) -> None:
    """无个人库（personal_kb_id=None）但命中条目记忆 → personal 必须为 True。"""
    _patch_rag(
        monkeypatch,
        personal_kb_id=None,
        visible=["kb_biz"],
        search_hits=[_biz_hit("cd_1", "kb_biz")],
        item_hits=[_item_hit("abc")],
    )
    user = SimpleNamespace(id=1, role="user")
    body = SimpleNamespace(message="库存上限是多少件？", kb_ids=["kb_biz"],
                           kb_id=None, file_id=None)
    db = SessionLocal()
    try:
        _ctx, cites = _build_rag_context(db, user, body)
    finally:
        db.close()

    by_chunk = {c["chunk_id"]: c for c in cites}
    assert "memitem:abc" in by_chunk, f"条目记忆应出现在 cites：{cites}"
    assert by_chunk["memitem:abc"]["personal"] is True, "条目记忆命中必须恒为「来自记忆」"
    assert by_chunk["cd_1"]["personal"] is False, "无个人库时业务库文档仍应为 False"


def test_personal_kb_doc_still_true(monkeypatch) -> None:
    """有个人库且命中其文档（kb_id 匹配）→ 仍为 True（证明原有通路没改坏）。"""
    _patch_rag(
        monkeypatch,
        personal_kb_id="kb_personal",
        visible=[],
        search_hits=[_biz_hit("cd_p", "kb_personal")],
        item_hits=[],
    )
    user = SimpleNamespace(id=1, role="user")
    body = SimpleNamespace(message="库存上限是多少件？", kb_ids=[],
                           kb_id=None, file_id=None)
    db = SessionLocal()
    try:
        _ctx, cites = _build_rag_context(db, user, body)
    finally:
        db.close()

    assert len(cites) == 1
    assert cites[0]["chunk_id"] == "cd_p"
    assert cites[0]["personal"] is True


def test_plain_biz_doc_false_when_no_personal_kb(monkeypatch) -> None:
    """反向护栏：无个人库时普通业务库文档必须为 False（防止开关被改成恒 True）。"""
    _patch_rag(
        monkeypatch,
        personal_kb_id=None,
        visible=["kb_biz"],
        search_hits=[_biz_hit("cd_2", "kb_biz")],
        item_hits=[],
    )
    user = SimpleNamespace(id=1, role="user")
    body = SimpleNamespace(message="库存上限是多少件？", kb_ids=["kb_biz"],
                           kb_id=None, file_id=None)
    db = SessionLocal()
    try:
        _ctx, cites = _build_rag_context(db, user, body)
    finally:
        db.close()

    assert len(cites) == 1
    assert cites[0]["personal"] is False
