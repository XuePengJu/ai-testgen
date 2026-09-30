"""Embedding + Chroma 向量存储（V4.0）。

- Embedding：由 llm_service.resolve_embedding 解析（平台配置 > 环境变量 > mock），
  调用方（入库/检索）先 configure_embedding 注入，保证全局同一个向量空间
- 默认（AITF_ALLOW_DEMO=0）：无 Embedding Key 或调用失败 → 抛 RuntimeError，
  绝不静默写入哈希假向量（假向量会让检索结果毫无意义却不报错，属于典型的静默兜底）
- 演示模式（AITF_ALLOW_DEMO=1）才降级为确定性哈希向量，仅供本地/现场演示
- 单 collection + metadata 过滤（kb_id / knowledge_id / user_id），
  多知识库与多租户隔离都靠 where 过滤；迁移 Qdrant 时换连接即可
"""
from __future__ import annotations

import hashlib
import logging
import re

from app.core.config import VECTOR_DIR, VECTOR_COLLECTION, AITF_ALLOW_DEMO

logger = logging.getLogger("knowledge.vectorstore")

MOCK_DIM = 256  # 演示用向量维度（真实 embedding 维度由模型决定，互不通用）
EMBED_BATCH = 10  # 阿里百炼 embedding 单次批量上限：v1/v2=25、v3/v4=10，取 10 全兼容

# 全局生效的 embedding 配置 {provider, base_url, model, api_key}；由调用方在
# 每次请求前用 llm_service.resolve_embedding(db) 的结果刷新（单进程内赋值安全）
_EMBED_CFG: dict | None = None

_NO_EMBED_HINT = (
    "未配置可用的 Embedding 模型，无法构建或检索知识库。"
    "请先在「设置 → 模型配置」配置 Embedding；"
    "如需本地演示可设环境变量 AITF_ALLOW_DEMO=1。"
)


def configure_embedding(cfg: dict | None) -> None:
    """注入全局 embedding 配置（None 或缺 Key = 无真实 Embedding）。"""
    global _EMBED_CFG
    _EMBED_CFG = cfg if (cfg and cfg.get("api_key")) else None


def using_mock_embedding() -> bool:
    """是否已注入真实 Embedding（False 表示当前没有可用的真实 embedding）。"""
    return _EMBED_CFG is None


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。

    默认（AITF_ALLOW_DEMO=0）：无 Key / 调用失败 → 抛 RuntimeError，不返回假向量。
    演示模式（=1）：降级为确定性哈希向量。
    """
    if _EMBED_CFG is not None:
        try:
            from langchain_openai import OpenAIEmbeddings
            emb = OpenAIEmbeddings(
                model=_EMBED_CFG["model"],
                api_key=_EMBED_CFG["api_key"],
                base_url=_EMBED_CFG["base_url"],
                check_embedding_ctx_length=False,
            )
            cleaned = [t or " " for t in texts]
            out: list[list[float]] = []
            for i in range(0, len(cleaned), EMBED_BATCH):
                out.extend(emb.embed_documents(cleaned[i : i + EMBED_BATCH]))
            return out
        except Exception as e:  # noqa: BLE001
            if AITF_ALLOW_DEMO:
                logger.warning("embedding 调用失败，演示模式下降级 mock：%s", e)
            else:
                raise RuntimeError(f"Embedding 调用失败：{e}") from e
    if AITF_ALLOW_DEMO:
        return [_mock_vector(t) for t in texts]
    raise RuntimeError(_NO_EMBED_HINT)


def _mock_vector(text: str) -> list[float]:
    """演示用确定性哈希向量：16 个字符窗口各取一个 float，归一化到 [-1,1]。"""
    vec = [0.0] * MOCK_DIM
    clean = re.sub(r"\s+", "", text or "")
    for i, ch in enumerate(clean):
        h = int(hashlib.md5(f"{i % 16}:{ch}".encode("utf-8")).hexdigest()[:8], 16)
        vec[i % MOCK_DIM] += (h % 2001 - 1000) / 1000.0
    norm = (sum(v * v for v in vec) ** 0.5) or 1.0
    return [round(v / norm, 6) for v in vec]


def _collection():
    """惰性获取持久化 Chroma collection（单例由 chromadb 内部管理）。"""
    import chromadb
    client = chromadb.PersistentClient(path=str(VECTOR_DIR))
    return client.get_or_create_collection(
        VECTOR_COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def index_chunks(
    kb_id: str, knowledge_id: str, user_id: int, visibility: str,
    chunks: list[dict], file_name: str,
) -> None:
    """把分块（含 id）写入向量库。chunks 元素须含 id/content。

    无真实 Embedding 时抛 RuntimeError（由调用方转成明确错误响应）。
    """
    if not chunks:
        return
    coll = _collection()
    ids = [c["id"] for c in chunks]
    docs = [c["content"] for c in chunks]
    vectors = embed_texts(docs)
    coll.upsert(
        ids=ids,
        embeddings=vectors,
        documents=docs,
        metadatas=[{
            "kb_id": kb_id,
            "knowledge_id": knowledge_id,
            "user_id": user_id,
            "visibility": visibility,
            "chunk_index": c.get("chunk_index", 0),
            "file_name": file_name[:200],
        } for c in chunks],
    )


def update_chunk_vectors(chunk_id: str, content: str, metadata: dict | None = None) -> None:
    """单块向量更新（分块手动编辑后调用，同步 Chroma）。"""
    coll = _collection()
    vectors = embed_texts([content])
    if metadata:
        coll.update(ids=[chunk_id], documents=[content], embeddings=vectors,
                    metadatas=[metadata])
    else:
        coll.update(ids=[chunk_id], documents=[content], embeddings=vectors)


def delete_knowledge_vectors(knowledge_id: str) -> None:
    """删除某文档的全部向量（重建索引 / 删除文档时调用）。"""
    try:
        _collection().delete(where={"knowledge_id": knowledge_id})
    except Exception as e:  # noqa: BLE001 - 删除失败不阻断主流程
        logger.warning("删除知识库向量失败 %s: %s", knowledge_id, e)


def delete_kb_vectors(kb_id: str) -> None:
    """删除整个知识库的向量。"""
    try:
        _collection().delete(where={"kb_id": kb_id})
    except Exception as e:  # noqa: BLE001
        logger.warning("删除知识库向量失败 %s: %s", kb_id, e)


def search(
    query: str,
    visible_kb_ids: list[str],
    top_k: int = 6,
    kb_id: str | None = None,
    knowledge_id: str | None = None,
) -> list[dict]:
    """向量检索。

    权限由调用方先查 MySQL 得出 ``visible_kb_ids``（visibility='global' OR user_id=me），
    这里用 kb_id IN 过滤——权限切换即时生效，避免向量 metadata 同步问题。
    返回 [{id, score, document, metadata}]。
    """
    if not visible_kb_ids:
        return []
    coll = _collection()
    where: dict = {"kb_id": {"$in": visible_kb_ids}}
    if kb_id:
        where = {"$and": [where, {"kb_id": kb_id}]}
    if knowledge_id:
        where = {"$and": [where, {"knowledge_id": knowledge_id}]}
    qvec = embed_texts([query])[0]
    try:
        res = coll.query(
            query_embeddings=[qvec],
            n_results=max(1, min(top_k, 50)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("向量检索失败：%s", e)
        return []
    out: list[dict] = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for i, cid in enumerate(ids):
        score = 1.0 - float(dists[i]) if i < len(dists) else 0.0  # 余弦距离 → 相似度
        out.append({
            "id": cid,
            "score": round(max(0.0, min(1.0, score)), 4),
            "document": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
        })
    return out
