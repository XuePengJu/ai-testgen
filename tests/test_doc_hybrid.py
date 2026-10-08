"""V7.4 文档级混合检索（BM25 + 向量 + RRF）测试。

用内存 SQLite 直接建 Chunk/Knowledge 行 + monkeypatch vectorstore.search，
不依赖真实 Chroma / 网络。核心契约：
- 混合路径下 vectorstore.search **仍被调用**（对外验收 spy 口径）
- 名次/软删/mock 防御/降级/开关/guest 口径/cap 截断/metadata 形状
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.core import config
from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.knowledge import Chunk, Knowledge
from app.services.knowledge import vectorstore
from app.services.knowledge.hybrid import hybrid_search_docs


# ---------- 构造工具 ----------

def _add_knowledge(db, kid: str, kb: str, *, deleted: bool = False,
                   title: str = "文档标题", file_name: str = "doc.pdf") -> None:
    db.add(Knowledge(
        id=kid, user_id=1, knowledge_base_id=kb, type="document",
        title=title, file_name=file_name,
        deleted_at=(utcnow() if deleted else None),
    ))


def _add_chunk(db, cid: str, kid: str, kb: str, content: str, *,
               header: str = "", enabled: bool = True, deleted: bool = False,
               age_days: float | None = None) -> None:
    db.add(Chunk(
        id=cid, user_id=1, knowledge_base_id=kb, knowledge_id=kid,
        chunk_index=0, content=content, context_header=header,
        is_enabled=enabled,
        deleted_at=(utcnow() if deleted else None),
        updated_at=(utcnow() - timedelta(days=age_days) if age_days is not None else utcnow()),
    ))


def _dense(*items: tuple[str, float]) -> list[dict]:
    return [{"id": cid, "score": sc, "document": f"dense-{cid}",
             "metadata": {"chunk_id": cid, "kb_id": "x"}} for cid, sc in items]


def _ids(hits: list[dict]) -> list[str]:
    return [h["id"] for h in hits]


# ---------- 契约：vectorstore.search 仍被调用 ----------

def test_hybrid_calls_vectorstore_search(monkeypatch) -> None:
    """混合路径下 vectorstore.search 必须被调用，且 kb_ids/top_k 原样透传。"""
    calls: list[tuple] = []

    def fake_search(query, kb_ids, top_k=6, **kw):
        calls.append((query, list(kb_ids), top_k))
        return []

    monkeypatch.setattr(vectorstore, "search", fake_search)
    monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: False)

    db = SessionLocal()
    try:
        res = hybrid_search_docs(db, "任意查询", ["kb_spy"], top_k=3)
    finally:
        db.close()

    assert len(calls) == 1
    assert calls[0][0] == "任意查询"
    assert calls[0][1] == ["kb_spy"]
    assert calls[0][2] == 3
    assert res == []


def test_dense_hit_outside_sql_window_passed_through(monkeypatch) -> None:
    """稠密命中不在 SQL 候选窗口内 → 原样透传，不丢。"""
    dense = _dense(("ghost-chunk", 0.77))
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: list(dense))
    monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: False)

    db = SessionLocal()
    try:
        res = hybrid_search_docs(db, "查询", ["kb_ghost"], top_k=5)
    finally:
        db.close()

    assert _ids(res) == ["ghost-chunk"]
    assert res[0] == dense[0]  # 逐字段原样透传


# ---------- 稠密通道：零重叠等价 dense ----------

def test_zero_overlap_result_equivalent_to_dense(monkeypatch) -> None:
    """query 与所有 chunk 零重叠 → BM25 无贡献，结果与 dense 同序等价。"""
    kb = "kb_zero_overlap"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_zero", kb)
        _add_chunk(db, "c_zero_a", "k_zero", kb, "天气很好")
        _add_chunk(db, "c_zero_b", "k_zero", kb, "股票下跌")
        db.commit()
        monkeypatch.setattr(vectorstore, "search",
                            lambda *a, **k: _dense(("c_zero_a", 0.9), ("c_zero_b", 0.8)))
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: False)
        res = hybrid_search_docs(db, "zzz", [kb], top_k=5)
    finally:
        db.close()

    assert _ids(res) == ["c_zero_a", "c_zero_b"]


# ---------- 稀疏通道：BM25-only 命中 ----------

def test_bm25_only_chunk_surfaces(monkeypatch) -> None:
    """query 与某 chunk 有 bigram 重叠 → 该 BM25-only chunk 出现在结果里。"""
    kb = "kb_bm25_only"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_bm", kb, file_name="规则.pdf", title="规则文档")
        _add_chunk(db, "c_bm", "k_bm", kb, "库存上限为 666 件需要审批", header="第二章")
        _add_chunk(db, "c_other", "k_bm", kb, "天气与股票无关内容")
        db.commit()
        # 稠密空 + mock（w_vec=0）→ 结果只能由 BM25 产出，证明稀疏通道独立生效
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=5)
    finally:
        db.close()

    assert "c_bm" in _ids(res)
    hit = next(h for h in res if h["id"] == "c_bm")
    assert hit["document"] == "库存上限为 666 件需要审批"
    assert hit["metadata"]["personal"] is False


# ---------- 软删 / 禁用过滤 ----------

def test_soft_deleted_and_disabled_excluded(monkeypatch) -> None:
    """is_enabled=False / Chunk.deleted_at / Knowledge.deleted_at 均不得出现。"""
    kb = "kb_soft_del"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_ok", kb)
        _add_knowledge(db, "k_del", kb, deleted=True)
        _add_chunk(db, "c_ok", "k_ok", kb, "库存上限规则说明")
        _add_chunk(db, "c_disabled", "k_ok", kb, "库存上限规则说明", enabled=False)
        _add_chunk(db, "c_chunk_deleted", "k_ok", kb, "库存上限规则说明", deleted=True)
        _add_chunk(db, "c_kb_deleted", "k_del", kb, "库存上限规则说明")
        db.commit()
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=10)
    finally:
        db.close()

    assert _ids(res) == ["c_ok"]


# ---------- 异常降级 ----------

class _BoomSession:
    """db.execute 抛错的替身，验证 BM25/融合整段被 try/except 兜住。"""

    def execute(self, *a, **k):
        raise RuntimeError("boom in sql")


def test_bm25_exception_degrades_to_dense(monkeypatch) -> None:
    """BM25 通道抛异常 → 降级返回 dense，绝不上抛。"""
    dense = _dense(("c_x", 0.5))
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: list(dense))
    res = hybrid_search_docs(_BoomSession(), "查询", ["kb_boom"], top_k=5)
    assert _ids(res) == ["c_x"]


# ---------- 开关 / guest 口径（_doc_search 分发） ----------

def test_hybrid_disabled_uses_pure_vector(monkeypatch) -> None:
    """AITF_DOC_HYBRID_ENABLED=0 → 走纯 vectorstore.search。"""
    from app.api.chat import _doc_search

    sentinel = [{"id": "v1", "score": 0.1, "document": "d", "metadata": {}}]
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: list(sentinel))
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_ENABLED", 0)

    def _must_not_call(*a, **k):
        raise AssertionError("开关关闭时不应走混合检索")

    monkeypatch.setattr("app.services.knowledge.hybrid.hybrid_search_docs", _must_not_call)

    db = SessionLocal()
    try:
        res = _doc_search(db, "q", ["kb1"], 6, is_guest=False)
    finally:
        db.close()
    assert res == sentinel


def test_guest_disabled_uses_pure_vector(monkeypatch) -> None:
    """guest 且 AITF_DOC_HYBRID_GUEST=0 → 走纯 vectorstore.search。"""
    from app.api.chat import _doc_search

    sentinel = [{"id": "v2", "score": 0.2, "document": "d", "metadata": {}}]
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: list(sentinel))
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_ENABLED", 1)
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_GUEST", 0)
    monkeypatch.setattr("app.services.knowledge.hybrid.hybrid_search_docs",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("guest 关闭时不应走混合")))

    db = SessionLocal()
    try:
        res = _doc_search(db, "q", ["kb1"], 6, is_guest=True)
    finally:
        db.close()
    assert res == sentinel


def test_guest_enabled_by_default_uses_hybrid(monkeypatch) -> None:
    """guest + AITF_DOC_HYBRID_GUEST=1（默认）→ 走混合检索。"""
    from app.api.chat import _doc_search

    monkeypatch.setattr(config, "AITF_DOC_HYBRID_ENABLED", 1)
    monkeypatch.setattr(config, "AITF_DOC_HYBRID_GUEST", 1)
    called = {"n": 0}

    def _fake_hybrid(db, query, kb_ids, top_k=6):
        called["n"] += 1
        return [{"id": "h1", "score": 0.3, "document": "d", "metadata": {"personal": False}}]

    monkeypatch.setattr("app.services.knowledge.hybrid.hybrid_search_docs", _fake_hybrid)

    db = SessionLocal()
    try:
        res = _doc_search(db, "q", ["kb1"], 6, is_guest=True)
    finally:
        db.close()
    assert called["n"] == 1
    assert _ids(res) == ["h1"]


# ---------- cap 截断 ----------

def test_bm25_candidate_cap_truncates(monkeypatch) -> None:
    """候选数超过 AITF_DOC_BM25_MAX_DOCS 时，只保留按 updated_at 最近的前 N 条。"""
    kb = "kb_cap"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_cap", kb)
        _add_chunk(db, "c_new", "k_cap", kb, "库存上限规则", age_days=0)
        _add_chunk(db, "c_mid", "k_cap", kb, "库存上限规则", age_days=1)
        _add_chunk(db, "c_old", "k_cap", kb, "库存上限规则", age_days=5)
        db.commit()
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        monkeypatch.setattr(config, "AITF_DOC_BM25_MAX_DOCS", 2)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=10)
    finally:
        db.close()

    ids = _ids(res)
    assert "c_old" not in ids, f"超 cap 的旧块不应进候选：{ids}"
    assert set(ids) == {"c_new", "c_mid"}


# ---------- metadata 契约 ----------

def test_metadata_contract(monkeypatch) -> None:
    """结果项 metadata 必须含 kb_id/knowledge_id/file_name/context_header/personal(False)。"""
    kb = "kb_meta"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_meta", kb, title="标题兜底", file_name="")
        _add_chunk(db, "c_meta", "k_meta", kb, "库存上限规则", header="父级标题")
        db.commit()
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=5)
    finally:
        db.close()

    assert len(res) == 1
    hit = res[0]
    assert set(hit) == {"id", "score", "document", "metadata"}
    meta = hit["metadata"]
    for key in ("chunk_id", "knowledge_id", "file_name", "context_header", "kb_id",
                "chunk_index", "personal"):
        assert key in meta, f"metadata 缺少 {key}"
    assert meta["kb_id"] == kb
    assert meta["knowledge_id"] == "k_meta"
    assert meta["file_name"] == "标题兜底"     # file_name 为空 → 回退 title
    assert meta["context_header"] == "父级标题"
    assert meta["personal"] is False
    assert meta["chunk_id"] == "c_meta"
    # V7.4.1：mock embedding 下不给向量相似度（哈希假向量的余弦是噪声）；
    # 本条只被 BM25 命中，故 score 为 None + 通道标 keyword
    assert hit["score"] is None
    assert meta["hit_channel"] == "keyword"


# ---------- V7.4.1：score 口径（真实向量余弦，不是 RRF 名次分）----------

def test_score_is_vector_cosine_not_rrf(monkeypatch) -> None:
    """非 mock 下 score 必须是向量余弦相似度（0~1），而非 RRF 名次分（≈0.0164）。

    背景：RRF 满分只有 1/(K+1)≈0.0164，前端 `score*100` 当百分比渲染会塌成
    1~2%，实测 0.0155 其实是理论满分的 94.5%，却被显示成 2%。
    """
    kb = "kb_score_semantics"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_s", kb)
        _add_chunk(db, "c_s1", "k_s", kb, "库存上限规则说明")
        _add_chunk(db, "c_s2", "k_s", kb, "采购入库流程说明")
        db.commit()
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: False)
        monkeypatch.setattr(vectorstore, "search",
                            lambda *a, **k: _dense(("c_s1", 0.83), ("c_s2", 0.61)))
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=5)
    finally:
        db.close()

    by_id = {h["id"]: h for h in res}
    # 对外曝露的是余弦，不是 RRF
    assert by_id["c_s1"]["score"] == 0.83
    assert by_id["c_s2"]["score"] == 0.61
    # 双通道命中；RRF 名次分只在 metadata 里（供排查），绝不外传为 score
    assert by_id["c_s1"]["metadata"]["hit_channel"] == "hybrid"
    assert by_id["c_s1"]["metadata"]["rrf_score"] < 0.02


def test_keyword_only_chunk_reports_no_score(monkeypatch) -> None:
    """向量零召回、仅靠 BM25 捞回的 chunk → score=None + hit_channel=keyword。"""
    kb = "kb_kw_only"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_kw", kb)
        _add_chunk(db, "c_kw", "k_kw", kb, "库存上限为 666 件需要审批")
        db.commit()
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: False)
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=5)
    finally:
        db.close()

    assert _ids(res) == ["c_kw"]
    assert res[0]["score"] is None
    assert res[0]["metadata"]["hit_channel"] == "keyword"


def test_mock_embedding_never_exposes_vector_score(monkeypatch) -> None:
    """mock embedding 下即便稠密通道「命中」（假向量），也不得外传相似度。"""
    kb = "kb_mock_score"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_m", kb)
        _add_chunk(db, "c_m", "k_m", kb, "库存上限规则说明")
        db.commit()
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        monkeypatch.setattr(vectorstore, "search",
                            lambda *a, **k: _dense(("c_m", 0.91)))
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=5)
    finally:
        db.close()

    assert res and res[0]["score"] is None
    assert res[0]["metadata"]["hit_channel"] == "keyword"


# ---------- V7.4.1：单文档命中限流 ----------

def test_max_per_doc_limits_and_backfills(monkeypatch) -> None:
    """同一篇最多 N 条；超限的跳过继续往下取，把名额让给别的文档。"""
    kb = "kb_max_per_doc"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_a", kb, title="文档A")
        _add_knowledge(db, "k_b", kb, title="文档B")
        for i in range(3):
            _add_chunk(db, f"a{i}", "k_a", kb, "库存上限规则说明", age_days=i)
        _add_chunk(db, "b0", "k_b", kb, "库存上限规则说明", age_days=3)
        db.commit()
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(config, "AITF_DOC_MAX_PER_DOC", 2)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=4)
    finally:
        db.close()

    ids = _ids(res)
    assert sum(1 for i in ids if i.startswith("a")) == 2, f"同文档应限 2 条：{ids}"
    assert "b0" in ids, f"被限流后应能顶上别的文档：{ids}"
    # 池子里只有 4 块且 a 有 3 块 → 限流后取到 a0/a1/b0 共 3 条
    assert len(ids) == 3, ids


def test_max_per_doc_zero_means_unlimited(monkeypatch) -> None:
    """AITF_DOC_MAX_PER_DOC=0 → 关闭限流（回滚口径）。"""
    kb = "kb_unlimited"
    db = SessionLocal()
    try:
        _add_knowledge(db, "k_u", kb)
        for i in range(4):
            _add_chunk(db, f"u{i}", "k_u", kb, "库存上限规则说明", age_days=i)
        db.commit()
        monkeypatch.setattr(vectorstore, "using_mock_embedding", lambda: True)
        monkeypatch.setattr(vectorstore, "search", lambda *a, **k: [])
        monkeypatch.setattr(config, "AITF_DOC_MAX_PER_DOC", 0)
        res = hybrid_search_docs(db, "库存上限", [kb], top_k=4)
    finally:
        db.close()

    assert len(_ids(res)) == 4
