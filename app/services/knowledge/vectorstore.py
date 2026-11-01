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

from app.core.config import (
    VECTOR_DIR, VECTOR_COLLECTION, AITF_ALLOW_DEMO, AITF_EMBED_MODEL_GUARD,
)

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


# ============ V7.8 embedding 模型空间防呆 ============
# 背景：换 embedding 模型后若没重算历史向量，且两边维度恰好相同（阿里
# text-embedding-v3 与 BAAI/bge-m3 都是 1024 维），维度校验完全拦不住 ——
# 检索照常返回、也不报错，但相似度是噪声（实测同一段文本余弦 -0.03，
# 前端引用百分比掉到 1~3%）。这是典型的静默错误，比直接报错危险得多。
EMBED_MODEL_FIELD = "embedding_model"   # 存在 chunk metadata 里的建库模型名
_SPACE_CHECK_TTL = 60.0                 # 空间校验结果缓存秒数（避免每次检索都抽样）
_space_check: dict = {"at": 0.0, "model": "", "ok": True, "warned_missing": False}


def current_embed_model() -> str:
    """当前注入的 embedding 模型名；无真实配置时返回空串（无从比对）。"""
    if not _EMBED_CFG:
        return ""
    return str(_EMBED_CFG.get("model") or "")


def _check_model_space(coll) -> bool:
    """抽样比对「库中向量的建库模型」与「当前 embedding 模型」是否一致。

    返回 True = 可安全走稠密检索（一致 / 历史无标记 / 校验关闭 / 无真实配置）。
    - 无 ``embedding_model`` 标记（旧数据）：不阻断，仅首次提示建议重建索引；
    - 标记存在且与当前模型不同：``logger.error`` 点名两个模型，返回 False ——
      调用方据此关闭稠密通道（退化为 BM25），绝不拿错空间的结果冒充命中。
    - 结果按当前模型缓存 ``_SPACE_CHECK_TTL`` 秒，重建索引后最多 60s 自愈。
    """
    if not AITF_EMBED_MODEL_GUARD:
        return True
    cur = current_embed_model()
    if not cur:
        return True  # 没有真实 embedding，无从比对（后续 embed_texts 自身会报错/降级）
    import time as _time
    now = _time.monotonic()
    if _space_check["model"] == cur and (now - _space_check["at"]) < _SPACE_CHECK_TTL:
        return bool(_space_check["ok"])
    try:
        sample = coll.get(limit=1, include=["metadatas"])
    except Exception as e:  # noqa: BLE001  抽样失败不阻断检索
        logger.warning("embedding 模型空间抽样失败（本次跳过校验）：%s", e)
        return True
    metas = sample.get("metadatas") or []
    stored = str((metas[0] or {}).get(EMBED_MODEL_FIELD) or "") if len(metas) else ""
    ok = True
    if not stored:
        if not _space_check["warned_missing"]:
            logger.warning(
                "向量库缺少 %s 标记，无法校验模型空间；若曾更换 embedding 模型，"
                "请重建向量索引（当前模型=%s）", EMBED_MODEL_FIELD, cur,
            )
            _space_check["warned_missing"] = True
    elif stored != cur:
        ok = False
        logger.error(
            "⚠️ embedding 模型空间不一致：向量库由 %r 建立，当前模型为 %r。"
            "两者维度可能相同但向量空间不同，稠密检索只会返回噪声 —— "
            "已关闭稠密通道（本次检索退化纯 BM25）。请重算向量索引，"
            "或将 embedding 模型改回 %r。", stored, cur, stored,
        )
    _space_check.update({"at": now, "model": cur, "ok": ok})
    return ok


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
    # ⚠️ 必须先跑 sqlite3 兼容垫片，再 import chromadb：
    # Chroma 内部用 SQLite 文件存向量，硬要求 sqlite3>=3.35；系统版本偏老时会直接抛错。
    # 垫片只在版本不足时动作，且 chromadb 只在这里 import —— 放这里是最稳的覆盖点。
    from app.core import sqlite_compat  # noqa: F401
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
            # V7.8：记录建库模型，供检索时校验空间一致性
            EMBED_MODEL_FIELD: current_embed_model(),
        } for c in chunks],
    )


def update_chunk_vectors(chunk_id: str, content: str, metadata: dict | None = None) -> None:
    """单块向量更新（分块手动编辑后调用，同步 Chroma）。

    V7.8：强制写入 ``embedding_model``。调用方不传 metadata 时先取旧的原样
    合并（Chroma 的 update 传 metadata 会整体覆盖，直接传会丢字段）。
    """
    coll = _collection()
    vectors = embed_texts([content])
    meta: dict = dict(metadata) if metadata else {}
    if not metadata:
        try:
            got = coll.get(ids=[chunk_id], include=["metadatas"])
            old = (got.get("metadatas") or [])
            if old and old[0]:
                meta = dict(old[0])
        except Exception as e:  # noqa: BLE001  取旧值失败就用空 metadata（不阻断）
            logger.warning("取旧 chunk metadata 失败（沿用空 metadata）：%s", e)
    model = current_embed_model()
    if model:
        meta[EMBED_MODEL_FIELD] = model
    if meta:
        coll.update(ids=[chunk_id], documents=[content], embeddings=vectors,
                    metadatas=[meta])
    else:
        coll.update(ids=[chunk_id], documents=[content], embeddings=vectors)


def upsert_raw_vectors(
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict],
) -> None:
    """直接按给定向量写入（不调用 Embedding 服务）。

    专供「知识库快照回灌」（app/services/demo_kb_seed.py）：快照里已带
    源实例算好的向量，演示站无需 Embedding Key 即可恢复可检索的知识库。
    幂等（upsert）。
    """
    if not ids:
        return
    _collection().upsert(
        ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas,
    )


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
    P4 语义：传入的是「已确定的检索范围」（含权限过滤，且强制并入了个人记忆库
    kb_id），本函数不做任何权限/属主判断；调用方可分两次传入（个人库一次、
    业务库一次）后按 chunk id 去重合并（见 chat._build_rag_context）。
    返回 [{id, score, document, metadata}]。
    """
    if not visible_kb_ids:
        return []
    coll = _collection()
    if not _check_model_space(coll):
        return []  # V7.8 向量空间不一致：关闭稠密通道，由 BM25 稀疏通道兜底
    where: dict = {"kb_id": {"$in": visible_kb_ids}}
    if kb_id:
        where = {"$and": [where, {"kb_id": kb_id}]}
    if knowledge_id:
        where = {"$and": [where, {"knowledge_id": knowledge_id}]}
    try:
        qvec = embed_texts([query])[0]
    except RuntimeError as e:
        # 演示站通常不配 Embedding Key（避免访客消耗额度）：稠密通道直接置空，
        # 检索由 doc_hybrid_search 的 BM25 稀疏通道兜底（快照回灌的向量只在
        # 有 Key 时才参与排序）。正式站保持原有「显式报错」语义不动。
        from app.core import demo_config

        if demo_config.AITF_DEMO_ENABLED:
            logger.warning("演示站未配置 Embedding，跳过稠密检索（改走 BM25）：%s", e)
            return []
        raise
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


# ============ V7.2：条目级记忆向量（source=mem_item 显式标量过滤） ============
# 只新增不改：index_chunks / search 等既有函数签名与行为零变动（零回归风险）。
# Chroma where 不支持 $exists，条目向量靠显式标量 "source": "mem_item" 圈定。

def index_memory_vectors(items: list) -> int:
    """把记忆条目写入向量库（upsert 天然幂等，可重复调用/重复回填）。

    items：MemoryItem ORM 行列表。mock embedding 时**静默跳过**（绝不写
    哈希假向量——假向量会稀释真实检索结果，这是 V7.2 最关键的防御）；
    任何异常只告警不抛（向量是旁路，行数据才是权威）。返回实际写入条数。
    """
    if not items:
        return 0
    if using_mock_embedding():
        return 0  # 无真实 embedding：静默跳过，不写假向量
    try:
        coll = _collection()
        coll.upsert(
            ids=[f"memitem:{it.id}" for it in items],
            embeddings=embed_texts([(it.content or "") for it in items]),
            documents=[(it.content or "") for it in items],
            metadatas=[{
                "source": "mem_item",
                "user_id": it.user_id,
                "memory_item_id": it.id,
                "kind": it.kind,
                "subject": (it.subject or "")[:200],
                "knowledge_id": "",
                "kb_id": "",
                "personal": True,
                "file_name": f"记忆 · {it.subject}"[:200],
                # V7.8：记录建库模型，供检索时校验空间一致性
                EMBED_MODEL_FIELD: current_embed_model(),
            } for it in items],
        )
        return len(items)
    except Exception as e:  # noqa: BLE001  向量旁路失败不阻断行数据链路
        logger.warning("记忆条目向量写入失败（%d 条）：%s", len(items), e)
        return 0


def search_memory_vectors(query: str, user_id: int, top_k: int = 5) -> list[dict]:
    """条目向量检索（仅 source=mem_item + user_id 过滤；mock embedding 返回 []）。

    返回与 search() 同形状的 [{id, score, document, metadata}]。
    """
    if using_mock_embedding():
        return []
    coll = _collection()
    if not _check_model_space(coll):
        return []  # V7.8 向量空间不一致：关闭条目稠密通道
    try:
        qvec = embed_texts([query])[0]
        res = coll.query(
            query_embeddings=[qvec],
            n_results=max(1, min(top_k, 50)),
            where={"$and": [{"source": "mem_item"}, {"user_id": user_id}]},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("记忆条目向量检索失败：%s", e)
        return []
    out: list[dict] = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for i, cid in enumerate(ids):
        score = 1.0 - float(dists[i]) if i < len(dists) else 0.0
        out.append({
            "id": cid,
            "score": round(max(0.0, min(1.0, score)), 4),
            "document": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
        })
    return out


def delete_item_vectors(item_id: str) -> None:
    """删除单个记忆条目的全部向量（hard_delete / 宽限清理 / 删会话级联时调用）。"""
    try:
        _collection().delete(where={"memory_item_id": item_id})
    except Exception as e:  # noqa: BLE001  删除失败不阻断主流程
        logger.warning("删除记忆条目向量失败 %s: %s", item_id, e)


def delete_user_item_vectors(user_id: int) -> None:
    """删除某用户全部记忆条目向量（删用户级联清理时调用）。"""
    try:
        _collection().delete(where={"$and": [{"source": "mem_item"},
                                             {"user_id": user_id}]})
    except Exception as e:  # noqa: BLE001
        logger.warning("删除用户记忆条目向量失败 %s: %s", user_id, e)
