"""向量通道在「演示站无 Embedding Key」时的行为（V9.4）。

背景：演示站（/demo）默认不配 Embedding Key（避免访客消耗额度），
而 ``vectorstore.search`` 原实现会在 ``embed_texts`` 抛 RuntimeError 时
把异常上抛 —— 知识库检索直接 500。演示站语义应为「稠密通道置空，
由 BM25 兜底」；正式站必须保持原样（显式报错，不静默降级）。
"""
from __future__ import annotations

import pytest

from app.core import demo_config
from app.services.knowledge import vectorstore


def _raise_no_embedding(_texts):
    raise RuntimeError("未配置可用的 Embedding 模型")


@pytest.fixture()
def _no_embedding(monkeypatch):
    monkeypatch.setattr(vectorstore, "embed_texts", _raise_no_embedding)


@pytest.mark.title("演示站无 Embedding 时稠密检索降级为空")
def test_demo_mode_search_degrades_to_empty_dense(monkeypatch, _no_embedding):
    """演示站：无 Embedding 时稠密通道返回空（调用方 BM25 兜底），不抛异常。"""
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", True)
    assert vectorstore.search("采购订单", ["kb-1"], top_k=3) == []


@pytest.mark.title("正式站无 Embedding 时检索仍显式报错")
def test_prod_mode_search_still_raises(monkeypatch, _no_embedding):
    """正式站：无 Embedding 仍显式报错（不静默降级为无向量检索）。"""
    monkeypatch.setattr(demo_config, "AITF_DEMO_ENABLED", False)
    with pytest.raises(RuntimeError):
        vectorstore.search("采购订单", ["kb-1"], top_k=3)


@pytest.mark.title("向量库支持免 Embedding 直接写入（快照回灌）")
def test_upsert_raw_vectors_writes_without_embedding(monkeypatch):
    """快照回灌：直接按给定向量写入，不调用 Embedding 服务。

    用独立 collection：Chroma 的维度由首次写入固定，同进程内其他用例
    可能已用别的维度建好主集合，混跑会因维度不符失败。
    """
    monkeypatch.setattr(vectorstore, "VECTOR_COLLECTION", "test-raw-vectors")
    vec = [0.1] * 8
    vectorstore.upsert_raw_vectors(
        ids=["chunk-raw-1"],
        embeddings=[vec],
        documents=["ERP 采购订单"],
        metadatas=[{"kb_id": "kb-raw", "knowledge_id": "doc-raw", "user_id": 2,
                    "visibility": "global", "chunk_index": 0, "file_name": "a.md"}],
    )
    got = vectorstore._collection().get(ids=["chunk-raw-1"], include=["embeddings", "metadatas"])
    assert got["ids"] == ["chunk-raw-1"]
    assert got["metadatas"][0]["kb_id"] == "kb-raw"
    assert len(got["embeddings"][0]) == 8
