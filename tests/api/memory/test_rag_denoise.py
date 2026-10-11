"""回归：_build_rag_context 引用去噪（内容级去重 + 记忆类文档限量）。

背景（2026-10-11 现场）：生成用例的引用 6 条全是个人记忆库提炼稿——
- 记忆日报聚合了各会话记忆的「关键结论」原文，同一内容在向量库存两份，
  chunk_id 去重拦不住 → 成对出现白占名额（B：内容级去重管这个）；
- 提炼稿正文薄、余弦虚高，且三路汇总恒排业务库前面 → 业务库真命中
  整个被挤出引用（C：mem:conv:/mem:daily: 文档 distinct 上限管这个）。

这里直接驱动 _build_rag_context（monkeypatch 外部依赖），断言产出的 cites。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import config
from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.api.chat import _build_rag_context
from app.models.knowledge import Knowledge
from app.services.knowledge import vectorstore

# DB 集成测试：直调函数但触及 SessionLocal / ORM / vectorstore 真实资源
pytestmark = pytest.mark.integration


def _mem_doc_hit(cid: str, kid: str, kb_id: str, text: str) -> dict:
    """个人库提炼稿文档命中（chunk 形状与 vectorstore.search 出参一致）。"""
    return {
        "id": cid,
        "score": 0.7,
        "document": text,
        "metadata": {"chunk_id": cid, "knowledge_id": kid, "kb_id": kb_id,
                     "file_name": "会话记忆：测试会话"},
    }


def _biz_hit(cid: str, kid: str, kb_id: str, text: str = "业务库文档正文") -> dict:
    return {
        "id": cid,
        "score": 0.6,
        "document": text,
        "metadata": {"chunk_id": cid, "knowledge_id": kid, "kb_id": kb_id,
                     "file_name": "biz.pdf"},
    }


def _add_knowledge(db, kid: str, kb_id: str, *, source_key: str = "",
                   title: str = "文档标题") -> None:
    db.add(Knowledge(
        id=kid, user_id=1, knowledge_base_id=kb_id, type="document",
        title=title, file_name="doc.txt", source_key=source_key,
    ))


def _patch_rag(monkeypatch, *, personal_kb_id, visible, search_hits, item_hits=()):
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
    # 关文档混合开关 → 文档路径直返 vectorstore.search，保我喂的命中不被 RRF 过滤
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_ENABLED", 0)
    monkeypatch.setattr(config, "AITF_MEMORY_HYBRID_ENABLED", 1)


def _run(user_id: int = 1):
    user = SimpleNamespace(id=user_id, role="user")
    body = SimpleNamespace(message="请为此 ERP 系统的采购管理模块设计测试用例",
                           kb_ids=["kb_biz"], kb_id=None, file_id=None)
    db = SessionLocal()
    try:
        return _build_rag_context(db, user, body)
    finally:
        db.close()


# ---------- B：内容级去重 ----------

def test_duplicate_content_dedup_across_chunk_ids(monkeypatch) -> None:
    """同一正文的两份拷贝（chunk_id/knowledge_id 不同）→ 只保留排前的记忆命中。"""
    same_text = "## 背景\n- 迭代对象：《ERP采购管理模块功能验证》\n- 补充库存不足异常用例"
    _patch_rag(
        monkeypatch,
        personal_kb_id="kb_personal",
        visible=["kb_biz"],
        search_hits=[
            # 个人库命中排前（三路汇总 mem 优先），业务库同正文拷贝排后
            _mem_doc_hit("cd_mem", "k_mem", "kb_personal", same_text),
            _biz_hit("cd_biz_dup", "k_biz_dup", "kb_biz", same_text),
            _biz_hit("cd_biz_uniq", "k_biz_uniq", "kb_biz", "完全不同的业务正文"),
        ],
    )
    _ctx, cites = _run()
    chunk_ids = [c["chunk_id"] for c in cites]
    assert chunk_ids == ["cd_mem", "cd_biz_uniq"], \
        f"重复正文只应保留记忆优先的那份：{chunk_ids}"


def test_whitespace_normalized_content_dedup(monkeypatch) -> None:
    """正文仅空白差异（换行/连续空格）也视为重复。"""
    _patch_rag(
        monkeypatch,
        personal_kb_id="kb_personal",
        visible=[],
        search_hits=[
            _mem_doc_hit("cd_a", "k_a", "kb_personal", "关键结论: 补充库存用例"),
            _mem_doc_hit("cd_b", "k_b", "kb_personal", "关键结论:\r\n  补充库存用例"),
        ],
    )
    _ctx, cites = _run()
    assert [c["chunk_id"] for c in cites] == ["cd_a"]


# ---------- C：记忆类文档 distinct 上限 ----------

def test_mem_docs_capped_by_distinct_limit(monkeypatch) -> None:
    """3 篇 mem 提炼稿 + 1 篇业务文档，上限 2 → 最多 2 篇记忆文档，业务文档保留。"""
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_m1", "kb_personal", source_key="mem:conv:1:c1",
                       title="会话记忆：ERP采购管理模块功能验证")
        _add_knowledge(db, "k_m2", "kb_personal", source_key="mem:conv:1:c2",
                       title="会话记忆：V592点空白确认")
        _add_knowledge(db, "k_m3", "kb_personal", source_key="mem:daily:1:2026-10-11",
                       title="记忆日报 · 2026-10-11")
        _add_knowledge(db, "k_biz", "kb_biz", title="ERP需求文档")
        db.commit()
        _patch_rag(
            monkeypatch,
            personal_kb_id="kb_personal",
            visible=["kb_biz"],
            search_hits=[
                _mem_doc_hit("cd_m1", "k_m1", "kb_personal", "记忆正文一"),
                _mem_doc_hit("cd_m2", "k_m2", "kb_personal", "记忆正文二"),
                _mem_doc_hit("cd_m3", "k_m3", "kb_personal", "记忆正文三"),
                _biz_hit("cd_biz", "k_biz", "kb_biz"),
            ],
        )
        monkeypatch.setattr(config, "AITF_RAG_MAX_MEM_DOCS", 2)
        _ctx, cites = _run()
    finally:
        db.close()

    mem_kids = {c["knowledge_id"] for c in cites
                if c["knowledge_id"].startswith("k_m")}
    assert len(mem_kids) <= 2, f"记忆文档 distinct 应被限到 2：{mem_kids}"
    assert "cd_biz" in [c["chunk_id"] for c in cites], \
        f"业务库真命中必须保留：{cites}"


def test_mem_docs_cap_zero_means_unlimited(monkeypatch) -> None:
    """AITF_RAG_MAX_MEM_DOCS=0 → 关闭限量（回滚口径），3 篇全保留。"""
    db = SessionLocal()
    try:
        # 注意：同 session 内 DB 行持久（conftest 每进程一次建表，不逐用例清库），
        # source_key 有唯一索引——各用例的 source_key 必须互不相同
        _add_knowledge(db, "k_z1", "kb_personal", source_key="mem:conv:1:z1")
        _add_knowledge(db, "k_z2", "kb_personal", source_key="mem:conv:1:z2")
        _add_knowledge(db, "k_z3", "kb_personal", source_key="mem:daily:1:z3")
        db.commit()
        _patch_rag(
            monkeypatch,
            personal_kb_id="kb_personal",
            visible=[],
            search_hits=[
                _mem_doc_hit("cd_z1", "k_z1", "kb_personal", "记忆正文一"),
                _mem_doc_hit("cd_z2", "k_z2", "kb_personal", "记忆正文二"),
                _mem_doc_hit("cd_z3", "k_z3", "kb_personal", "记忆正文三"),
            ],
        )
        monkeypatch.setattr(config, "AITF_RAG_MAX_MEM_DOCS", 0)
        _ctx, cites = _run()
    finally:
        db.close()

    assert len(cites) == 3, f"上限 0=不限，3 篇应全保留：{cites}"


def test_mem_cap_counts_distinct_docs_not_chunks(monkeypatch) -> None:
    """同一记忆文档的多个 chunk 不重复计数：2 chunk 同文档 + 上限 1 → 都保留。"""
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_d1", "kb_personal", source_key="mem:daily:1:d1",
                       title="记忆日报 · 2026-10-11")
        db.commit()
        _patch_rag(
            monkeypatch,
            personal_kb_id="kb_personal",
            visible=[],
            search_hits=[
                _mem_doc_hit("cd_d1_p0", "k_d1", "kb_personal", "日报片段一"),
                _mem_doc_hit("cd_d1_p1", "k_d1", "kb_personal", "日报片段二"),
            ],
        )
        monkeypatch.setattr(config, "AITF_RAG_MAX_MEM_DOCS", 1)
        _ctx, cites = _run()
    finally:
        db.close()

    assert len(cites) == 2, f"同文档多 chunk 应共用一个文档名额：{cites}"
