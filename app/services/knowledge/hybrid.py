"""文档级混合检索（V7.4：稀疏 BM25 + 稠密向量 + RRF 融合）。

把文档检索从「纯向量」升级为「BM25 + 向量 + RRF」——**叠加**一路稀疏通道再融合，
**不替换**原向量检索：`vectorstore.search()` 仍被无条件先调用（对外验收脚本
`tests/test_chat_rag.py` / `tests/test_v41_kb_qa.py` 用 spy 监视其入参，业务库/
个人库 id 必须照传，该契约不可破坏）。

设计要点（与条目级 `memory/retrieve.py` **完全同口径**，共用
`memory/sparse.py` + `memory/tokenize.py`）：
- 稠密通道：`vectorstore.search(query, kb_ids, top_k)` —— 无条件先调用
- 稀疏通道：SQL 取 chunks（`is_enabled` + Chunk/Knowledge 双软删过滤，按
  `updated_at` 取最近 `AITF_DOC_BM25_MAX_DOCS` 条），索引文本 = content +
  context_header，`tokenize` → `match_tokens` → `bm25`
- 名次表铁律：**只有 bm25>0 才进 `rank()`**（零相关项会靠 tie-break（id 序）
  拿到靠前名次，再被权重放大挤掉真命中——`retrieve.py` 已记过此坑，不重蹈）
- mock 防御：`using_mock_embedding()` 为真时 `w_vec=0`（哈希假向量的余弦是
  噪声，会稀释 BM25 的正确结果，必须整关稠密名次）
- RRF：`w_vec/(K+rank_vec) + (1-w_vec)/(K+rank_bm25)`，`K=AITF_DOC_RRF_K`
- 稠密命中若不在 SQL 候选窗口内（如超出 cap 的旧块），**原样透传不丢**
- 异常降级：BM25/融合整段 try/except，失败 `logger.warning` 后**直接返回
  dense**（纯向量），绝不允许异常上抛到 `/chat/stream` 主链路

返回伪装 vectorstore hit 的 `[{id, score, document, metadata}]`，与
`vectorstore.search` 同形状，`chat._build_rag_context` 的既有三路去重 /
citations 构造逻辑零改动。

**score 语义（V7.4.1 修正，重要）**：对外 score 一律是**真实向量余弦相似度**
（0~1，与纯向量路径同口径），**不是**用于排序的 RRF 名次分——RRF 满分只有
`1/(K+1)≈0.0164`，直接当百分比渲染会塌成 1~2% 造成「没匹配上」的误判。
仅靠 BM25 捞回（向量未命中）或 mock embedding（假向量不可信）时 score 为
`None`，`metadata.hit_channel` 给 `hybrid|vector|keyword` 供前端标注。
排序仍由 RRF 决定，其分值另存 `metadata.rrf_score`。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import config
from app.models.knowledge import Chunk, Knowledge
from app.services.knowledge import vectorstore
from app.services.memory.sparse import bm25, match_tokens, rank
from app.services.memory.tokenize import tokenize

logger = logging.getLogger("knowledge.hybrid")


def _chunk_index_text(content: str | None, context_header: str | None) -> str:
    """chunk 的 BM25 索引文本 = 正文 + 上下文标题（父级路径）。

    与条目通道一致：subject 类短标题常是自然语言 query 的命中入口，正文未必含 query 词。
    """
    return f"{content or ''} {context_header or ''}"


def hybrid_search_docs(db: Session, query: str, kb_ids: list[str],
                       top_k: int = 6) -> list[dict]:
    """文档级混合检索（BM25 + 向量 + RRF）。

    - db：SQLAlchemy Session（取 BM25 候选 chunk 用）
    - kb_ids：已确定的检索范围（含权限过滤，与 vectorstore.search 的入参同义）
    - top_k：融合后返回条数上限

    返回伪装 vectorstore hit 的 `[{id, score, document, metadata}]`。
    ``vectorstore.search`` 始终被调用（契约）；内部任何异常都降级为纯向量结果。
    """
    # ---- 稠密通道：无条件先调用（对外验收 spy 契约；自身异常已内部返回 []）----
    dense = vectorstore.search(query, kb_ids, top_k=top_k)
    if not kb_ids:
        return dense

    try:
        # ---- 稀疏通道：SQL 取候选（双软删过滤，按 updated_at 取最近 N 条）----
        rows = db.execute(
            select(Chunk, Knowledge)
            .join(Knowledge, Chunk.knowledge_id == Knowledge.id)
            .where(
                Chunk.knowledge_base_id.in_(kb_ids),
                Chunk.is_enabled.is_(True),
                Chunk.deleted_at.is_(None),        # 软删块剔除
                Knowledge.deleted_at.is_(None),    # 软删父文档剔除
            )
            .order_by(Chunk.updated_at.desc())
            .limit(config.AITF_DOC_BM25_MAX_DOCS)
        ).all()
        chunk_rows: dict[str, tuple[Chunk, Knowledge]] = {c.id: (c, k) for c, k in rows}

        # 与条目通道完全同口径：tokenize → match_tokens → bm25
        docs_match = {
            cid: match_tokens(tokenize(_chunk_index_text(c.content, c.context_header)))
            for cid, (c, _k) in chunk_rows.items()
        }
        bm25_scores = bm25(match_tokens(tokenize(query or "")), docs_match)
        # 名次表铁律：只有 bm25 > 0 的候选才进 rank()
        bm25_ranked = {cid: s for cid, s in bm25_scores.items() if s > 0.0}

        # ---- mock 防御 + RRF 融合 ----
        w_vec = config.AITF_DOC_W_VEC
        if vectorstore.using_mock_embedding():
            w_vec = 0.0  # 关键防御：假向量余弦是噪声，整关稠密名次
        k = config.AITF_DOC_RRF_K

        dense_scores = {h["id"]: float(h.get("score") or 0.0)
                        for h in dense if h.get("id")}
        dense_by_id = {h["id"]: h for h in dense if h.get("id")}
        r_vec = rank(dense_scores)
        r_bm25 = rank(bm25_ranked)

        fused: list[tuple[float, str]] = []
        for cid in set(dense_scores) | set(bm25_ranked):
            rrf = 0.0
            rv = r_vec.get(cid)
            if rv is not None:
                rrf += w_vec / (k + rv)
            rb = r_bm25.get(cid)
            if rb is not None:
                rrf += (1.0 - w_vec) / (k + rb)
            if rrf <= 0.0:
                continue  # 两通道都零贡献：直接出局
            fused.append((rrf, cid))

        # 同分按 id 稳定排序，保证可复现
        fused.sort(key=lambda t: (-t[0], t[1]))

        # ---- V7.4.1 显示口径修复 ----
        # RRF 是**名次融合分**（理论满分仅 1/(K+1)≈0.0164），被前端 `score*100`
        # 当百分比渲染会塌成 1~2%，让人误以为「没匹配上」（实测 0.0155 其实是
        # 理论满分的 94.5%）。故对外 score 一律给**真实向量余弦相似度**
        # （0~1，与纯向量路径同口径，前端渲染逻辑不用改）；仅靠 BM25 捞回、
        # 向量未命中的置 None，前端改显「关键词」——顺带让用户看见稀疏通道
        # 到底有没有出力。RRF 名次分保留在 metadata.rrf_score 供排查。
        _mock = vectorstore.using_mock_embedding()

        # ---- V7.4.1 多样性：单文档命中上限（0=不限）----
        # 条目通道有 MMR 去冗余，文档通道原先没有 —— 实测 6 条引用里 3 条来自
        # 同一篇文档，白占 top_k 名额。超限的**跳过继续往下取**而非直接截断，
        # 保证仍能凑满 top_k。
        max_per_doc = config.AITF_DOC_MAX_PER_DOC
        per_doc: dict[str, int] = {}

        def _take(k_id: str) -> bool:
            """该文档是否还有名额；有则计数 +1。max_per_doc<=0 或不限文档时不限制。"""
            if max_per_doc <= 0 or not k_id:
                return True
            if per_doc.get(k_id, 0) >= max_per_doc:
                return False
            per_doc[k_id] = per_doc.get(k_id, 0) + 1
            return True

        out: list[dict] = []
        for rrf, cid in fused:
            if len(out) >= max(0, top_k):
                break
            # mock 下哈希假向量的余弦是噪声，不算向量命中
            vec_hit = (not _mock) and (cid in dense_scores)
            pair = chunk_rows.get(cid)
            if pair is None:
                # 稠密命中不在 SQL 候选窗口内：原样透传，绝不丢。
                # （dense 原始 dict 的 score 本就是余弦相似度，口径天然一致）
                dm = dense_by_id[cid].get("metadata") or {}
                if not _take(dm.get("knowledge_id") or ""):
                    continue
                out.append(dense_by_id[cid])
                continue
            c, krow = pair
            k_id = c.knowledge_id or ""
            if not _take(k_id):
                continue
            dense_score = dense_by_id.get(cid, {}).get("score")
            out.append({
                "id": c.id,
                "score": (round(float(dense_score), 4)
                          if vec_hit and dense_score is not None else None),
                "document": c.content or "",
                "metadata": {
                    "chunk_id": c.id,
                    "knowledge_id": k_id,
                    "file_name": (krow.file_name or krow.title or "")[:200],
                    "context_header": c.context_header or "",
                    "kb_id": c.knowledge_base_id,
                    "chunk_index": c.chunk_index,
                    "personal": False,
                    # V7.4.1：命中通道，供前端标注「仅关键词命中」
                    "hit_channel": ("hybrid" if vec_hit and cid in bm25_ranked
                                    else "vector" if vec_hit else "keyword"),
                    # 排序依据（RRF 名次分），前端不展示、仅供排查
                    "rrf_score": round(rrf, 6),
                },
            })
        return out
    except Exception as e:  # noqa: BLE001  稀疏/融合失败一律降级纯向量，绝不上抛
        logger.warning("文档混合检索失败，降级纯向量：%s", e)
        return dense
