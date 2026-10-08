"""条目级混合检索（V7.2：稀疏 BM25 + 稠密向量 + RRF 融合 + 时间衰减 + MMR）。

公式（需求本体，勿改语义）：
- 候选 C = 该 user 的 active 条目（结构化过滤 status/kind/expires_at 在
  SQL where 里；上限 AITF_MEMORY_BM25_MAX_DOCS，按 updated_at 取最近）
- 通道A 稠密 vec_score = 1 - cosine；**using_mock_embedding() 为真时整条
  关闭（w_vec=0、不查向量）**——哈希假向量的余弦是噪声，会稀释正确结果，
  这是本方案最关键的防御（测试环境恒 mock，BM25 恒可用）
- 通道B 稀疏 BM25：k1=1.5, b=0.75, IDF=ln(1+(N-df+0.5)/(df+0.5))，over
  候选条目 content（分词见 tokenize.py）
- RRF = w_vec/(K+rank_vec) + (1-w_vec)/(K+rank_bm25)，K=AITF_MEMORY_RRF_K
- decay = 0.5 ** (age_days / max(1, half_life_days))，age 按 updated_at
- imp_w = 0.5 + 0.5*clamp01(importance)；conf_w = 0.35 + 0.65*clamp01(confidence)
  （不抹零：低重要性/低置信度条目仍有被召回的机会）
- base = RRF * decay * imp_w * conf_w；cutoff = max(AITF_MEMORY_MIN_SCORE,
  best_base * AITF_MEMORY_MIN_SCORE_RATIO)，base < cutoff 丢弃（V7.4 相对阈值：
  绝对地板默认 0=关闭，靠相对系数砍长尾；榜首恒 >= cutoff，top1 永不误杀）
- MMR λ=AITF_MEMORY_MMR_LAMBDA：贪心 argmax[λ*norm(base) -
  (1-λ)*max Jaccard(tokens)]；norm=base/max_base。冗余度刻意用 token
  Jaccard 而非向量余弦——mock embedding 下同样有效
- 最终取 AITF_MEMORY_ITEM_TOPK 条，对选中条目调 bump_importance_on_hit

返回伪装成 vectorstore hit 形状的 dict（{id, score, document, metadata}），
chat._build_rag_context 的既有拼装逻辑零改动。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    AITF_MEMORY_BM25_MAX_DOCS,
    AITF_MEMORY_ITEM_TOPK,
    AITF_MEMORY_MIN_SCORE,
    AITF_MEMORY_MIN_SCORE_RATIO,
    AITF_MEMORY_MMR_LAMBDA,
    AITF_MEMORY_RRF_K,
    AITF_MEMORY_W_VEC,
)
from app.core.utils import utcnow
from app.models.memory import MemoryItem
from app.services.memory import tokenize as _tok
from app.services.memory.forget import bump_importance_on_hit, kind_half_life

# 稀疏通道工具的唯一实现来源是 app/services/memory/sparse.py（V7.2 条目检索
# 与新的文档级检索共用）。此处以私有别名导入，保持文件内既有调用点零改动。
from app.services.memory.sparse import (
    bm25 as _bm25,
    clamp01 as _clamp01,
    jaccard as _jaccard,
    match_tokens as _match_tokens,
    rank as _rank,
)

logger = logging.getLogger("memory.retrieve")


def _mmr_select(cands: list[dict], top_k: int, lam: float) -> list[dict]:
    """MMR 去冗余：贪心 argmax[λ*norm(base) - (1-λ)*max Jaccard(已选)]。

    冗余度用 token Jaccard（分词来自 tokenize.py），不依赖 embedding——
    mock 向量下同样有效。cands 元素需含 {id, base, tokens}。
    """
    if not cands:
        return []
    max_base = max(c["base"] for c in cands) or 1.0
    for c in cands:
        c["norm"] = c["base"] / max_base
    selected: list[dict] = []
    remaining = sorted(cands, key=lambda c: (-c["base"], c["id"]))
    while remaining and len(selected) < top_k:
        best, best_score = None, None
        for c in remaining:
            redundancy = 0.0
            for s in selected:
                j = _jaccard(c["tokens"], s["tokens"])
                if j > redundancy:
                    redundancy = j
            score = lam * c["norm"] - (1.0 - lam) * redundancy
            if best_score is None or score > best_score:
                best, best_score = c, score
        selected.append(best)
        remaining.remove(best)
    return selected


def hybrid_search(db: Session, user, query: str,
                  top_k: int | None = None) -> list[dict]:
    """条目级混合检索主入口，返回伪装 vectorstore hit 的 dict 列表。

    - user：已登录的非 guest 用户 ORM 行（隔离靠 user_id 硬过滤）
    - 命中强化：对最终选中的条目调 bump_importance_on_hit（失败只告警）
    - 任何内部异常都不上抛到文档检索链路（调用方另有 try/except 兜底）
    """
    q = (query or "").strip()
    if not q:
        return []
    top_k = top_k or AITF_MEMORY_ITEM_TOPK
    from app.services.knowledge import vectorstore  # 延迟 import 便于测试替身

    # ---- 候选：active 条目（结构化过滤，按 updated_at 取最近 N 条）----
    cands = db.execute(
        select(MemoryItem).where(
            MemoryItem.user_id == user.id,
            MemoryItem.status == "active",
        ).order_by(MemoryItem.updated_at.desc()).limit(AITF_MEMORY_BM25_MAX_DOCS)
    ).scalars().all()
    if not cands:
        return []

    # ---- 通道A 稠密（mock embedding 时整条关闭）----
    w_vec = AITF_MEMORY_W_VEC
    vec_scores: dict[str, float] = {}
    if vectorstore.using_mock_embedding():
        w_vec = 0.0  # 关键防御：假向量余弦是噪声，直接关闭稠密通道
    else:
        try:
            vec_hits = vectorstore.search_memory_vectors(q, user.id, top_k=len(cands))
            for h in vec_hits:
                mid = (h.get("metadata") or {}).get("memory_item_id")
                if mid:
                    vec_scores[mid] = float(h.get("score") or 0.0)
        except Exception as e:  # noqa: BLE001  稠密失败降级纯 BM25
            logger.warning("条目稠密检索失败，降级纯 BM25：%s", e)

    # ---- 通道B 稀疏 BM25 + RRF 融合 ----
    # 索引文本 = subject + content：subject 是语义槽位名（如「汇报频率」），
    # 用户的自然语言 query 经常只命中 subject 而不命中 content 正文。
    # BM25 匹配空间用过滤后的 token（bigram+整词）；doc 长度归一同样基于
    # 过滤后的词表（可索引词数才是真正的文档长度）。
    docs_toks = {c.id: _tok.tokenize(f"{c.subject or ''} {c.content or ''}")
                 for c in cands}
    docs_match = {cid: _match_tokens(toks) for cid, toks in docs_toks.items()}
    bm25_scores = _bm25(_match_tokens(_tok.tokenize(q)), docs_match)
    # ⚠️ 只对 bm25 > 0 的条目进名次表：RRF 是名次制，若零相关条目也参与
    # 排名，会按 tie-break（id 序）拿到靠前名次，靠 importance/confidence
    # 权重差反超真正匹配的条目（实测：零重叠的偏好条目挤掉 top1 匹配）。
    # 零分 = 该通道无贡献，交由 MIN_SCORE 兜底过滤。
    bm25_ranked = {did: s for did, s in bm25_scores.items() if s > 0.0}
    r_vec, r_bm25 = _rank(vec_scores), _rank(bm25_ranked)

    now = utcnow()
    scored: list[dict] = []
    for c in cands:
        rrf = 0.0
        rv = r_vec.get(c.id)
        if rv is not None:
            rrf += w_vec / (AITF_MEMORY_RRF_K + rv)
        rb = r_bm25.get(c.id)
        if rb is not None:
            rrf += (1.0 - w_vec) / (AITF_MEMORY_RRF_K + rb)
        if rrf <= 0.0:
            continue  # 两通道都零相关：直接出局（MIN_SCORE 之前的快速短路）
        age_days = 0.0
        if c.updated_at:
            age_days = max(0.0, (now - c.updated_at).total_seconds() / 86400.0)
        hl = int(c.half_life_days or 0) or kind_half_life(c.kind)
        decay = 0.5 ** (age_days / max(1, hl))
        imp_w = 0.5 + 0.5 * _clamp01(c.importance)
        conf_w = 0.35 + 0.65 * _clamp01(c.confidence)
        base = rrf * decay * imp_w * conf_w
        scored.append({"item": c, "id": c.id, "base": base,
                       "tokens": set(docs_toks.get(c.id) or set())})
    if not scored:
        return []
    # V7.4 相对阈值：旧的绝对阈值 0.01 会把所有老条目误杀 —— RRF 满分仅
    # 1/(K+1)=0.0164，叠加时间衰减后 1 年以上的条目 base 必低于 0.01，无论
    # 排第几都召回不到。改取 max(绝对地板, best_base * ratio)：best_base 即
    # 榜首分，恒 >= cutoff（ratio<=1 时），从根上保证 top1 永不被误杀；长尾
    # 噪声由 best*ratio 砍掉，零相关项已由「bm25>0 才进名次表 + rrf<=0 短路」
    # 双层挡在门外。
    best_base = max(s["base"] for s in scored)
    cutoff = max(AITF_MEMORY_MIN_SCORE, best_base * AITF_MEMORY_MIN_SCORE_RATIO)
    scored = [s for s in scored if s["base"] >= cutoff]
    if not scored:
        return []

    # ---- MMR 去冗余 + 命中强化 + 伪装 vectorstore hit 出参 ----
    picked = _mmr_select(scored, top_k, AITF_MEMORY_MMR_LAMBDA)
    hits: list[dict] = []
    for c in picked:
        item = c["item"]
        try:
            bump_importance_on_hit(db, item)
        except Exception as e:  # noqa: BLE001  强化失败不影响检索结果
            logger.warning("条目 %s 命中强化失败：%s", item.id, e)
        hits.append({
            "id": f"memitem:{item.id}",
            "score": round(c["base"], 4),
            "document": item.content or "",
            "metadata": {
                "chunk_id": f"memitem:{item.id}",
                "knowledge_id": "",   # 判空 → 前端 citations 不跳转
                "personal": True,     # chat.py personal 判定的第二通路
                "memory_item_id": item.id,
                "file_name": f"记忆 · {item.subject}"[:200],
                "context_header": "",
                "kb_id": "",
                "source": "mem_item",
                "user_id": item.user_id,
                "kind": item.kind,
                "subject": item.subject,
            },
        })
    return hits
