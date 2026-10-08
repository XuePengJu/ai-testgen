"""共享稀疏检索工具（V7.2 条目检索 + 新的文档级混合检索共用）。

**这是稀疏通道（BM25 / Jaccard / 名次 / 数值夹取）的唯一实现来源**——
`app/services/memory/retrieve.py`（条目级检索）与后续的文档级混合检索都从
本模块导入，**禁止在别处重复实现同一套逻辑**（避免两份 BM25 语义漂移，
那会让 RRF 融合出来的名次在两个通道间不可比）。

分词刻意不放在本模块：调用方继续用 `app/services/memory/tokenize.py: tokenize()`
产出 token 列表后传进来（本模块只消费 token 列表，不自带分词实现，也不 import
tokenize——保持「分词」与「稀疏打分」两件事解耦）。
"""
from __future__ import annotations

import math
from collections import Counter

# BM25 经典参数（Okapi）
_BM25_K1 = 1.5
_BM25_B = 0.75


def clamp01(v) -> float:
    """数值安全夹到 [0, 1]（非数/NaN 兜底 0.0）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return min(1.0, max(0.0, f))


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度；任一侧为空返回 0。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def rank(scores: dict[str, float]) -> dict[str, int]:
    """分数 → 名次（1 起；同分按 id 稳定排序，保证可复现）。"""
    order = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {did: i + 1 for i, (did, _) in enumerate(order)}


def match_tokens(tokens: list[str]) -> list[str]:
    """BM25 匹配用的 token 过滤：只留 CJK bigram + 英文/数字整词（长度 ≥2）。

    ⚠️ 单字 CJK 刻意不进 BM25 匹配空间：单字太歧义（「少」同时命中「多少」
    和「至少」），一个巧合单字重叠就能让零相关条目拿到 bm25 rank 2，而 RRF
    是名次制、各名次分差极小（1/61 vs 1/62），会被 importance 权重差反超，
    挤掉真正多 term 匹配的条目（实测 eval cft-03/04 top1 被挤掉）。
    MMR 冗余度的 Jaccard 仍用全 token（相似度场景单字信息无害）。
    """
    return [t for t in tokens if len(t) >= 2]


def bm25(query_toks: list[str], docs_toks: dict[str, list[str]]) -> dict[str, float]:
    """自实现 Okapi BM25 over 候选条目 token 列表。

    IDF = ln(1 + (N - df + 0.5) / (df + 0.5))（+/1 平滑防负权）；
    tf 饱和与长度归一：tf*(k1+1) / (tf + k1*(1-b+b*|d|/avgdl))。
    """
    n = len(docs_toks)
    if n == 0 or not query_toks:
        return {did: 0.0 for did in docs_toks}
    df: Counter = Counter()
    for toks in docs_toks.values():
        df.update(set(toks))
    avgdl = (sum(len(t) for t in docs_toks.values()) / n) or 1.0
    q = Counter(query_toks)
    out: dict[str, float] = {}
    for did, toks in docs_toks.items():
        tf = Counter(toks)
        dl = len(toks) or 1
        s = 0.0
        for term in q:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * f * (_BM25_K1 + 1) / (
                f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
        out[did] = s
    return out
