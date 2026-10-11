"""共享稀疏检索工具（app/services/memory/sparse.py）单元测试。

这些工具是 V7.2 条目检索与 V7.4 文档级混合检索共用的稀疏通道唯一实现来源，
需保证语义稳定：BM25 打分、名次可复现、数值兜底、Jaccard 边界、单字剔除。
"""
from __future__ import annotations

import math

import pytest

from app.services.memory import sparse
from app.services.memory.sparse import (
    bm25,
    clamp01,
    jaccard,
    match_tokens,
    rank,
)


# ---------- match_tokens：刻意剔除单字 ----------

def test_match_tokens_drops_single_chars() -> None:
    """单字（含单字 CJK / 单字母）不进 BM25 匹配空间。"""
    assert match_tokens(["我", "我们", "a", "ab"]) == ["我们", "ab"]


def test_match_tokens_keeps_bigrams_and_words() -> None:
    """长度 ≥2 的 token 一律保留，顺序不变。"""
    assert match_tokens(["库存", "上限", "api", "x"]) == ["库存", "上限", "api"]


def test_match_tokens_empty() -> None:
    assert match_tokens([]) == []


# ---------- bm25：打分语义 ----------

def test_bm25_zero_overlap_scores_zero() -> None:
    """查询词与文档零重叠 → 该文档 0 分。"""
    scores = bm25(["库存"], {"d1": ["天气", "预报"]})
    assert scores == {"d1": 0.0}


def test_bm25_overlap_positive() -> None:
    """有重叠 → 正分。"""
    scores = bm25(["库存"], {"d1": ["库存", "上限"]})
    assert scores["d1"] > 0.0


def test_bm25_empty_docs_no_error() -> None:
    """空 docs → 不抛异常，返回空 dict。"""
    assert bm25(["库存"], {}) == {}


def test_bm25_empty_query_all_zero() -> None:
    """空 query → 不抛异常，全部文档返回 0 分。"""
    scores = bm25([], {"d1": ["库存"], "d2": ["天气"]})
    assert scores == {"d1": 0.0, "d2": 0.0}


def test_bm25_higher_tf_scores_higher() -> None:
    """同长度文档里，命中词频更高者得分更高（tf 饱和但单调递增）。"""
    scores = bm25(
        ["库存"],
        {
            "high": ["库存", "库存", "库存"],
            "low": ["库存", "天气", "预报"],
        },
    )
    assert scores["high"] > scores["low"]


def test_bm25_shorter_doc_scores_higher() -> None:
    """词频相同时，文档更短者得分更高（长度归一）。"""
    scores = bm25(
        ["库存"],
        {
            "short": ["库存", "上限"],
            "long": ["库存", "上限", "天气", "预报", "温度", "湿度"],
        },
    )
    assert scores["short"] > scores["long"]


def test_bm25_idf_formula_matches_reference() -> None:
    """IDF/饱和/长度归一的公式与参考实现逐字一致（回归护栏）。

    手工复算单个 term：N=2、df=1、dl=len(doc)、avgdl=平均长度。
    """
    docs = {"d1": ["库存", "上限"], "d2": ["天气", "预报"]}
    scores = bm25(["库存"], docs)
    n = 2
    df = 1
    k1, b = 1.5, 0.75
    avgdl = (len(docs["d1"]) + len(docs["d2"])) / n
    dl = len(docs["d1"])
    f = 1
    idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
    expected = idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
    assert scores["d1"] == pytest.approx(expected)


# ---------- rank：同分稳定排序 ----------

def test_rank_basic() -> None:
    """名次从 1 起，分数降序。"""
    assert rank({"a": 0.9, "b": 0.5, "c": 0.1}) == {"a": 1, "b": 2, "c": 3}


def test_rank_ties_stable_by_id() -> None:
    """同分按 id 升序稳定排序，保证可复现。"""
    r1 = rank({"z": 1.0, "a": 1.0, "m": 1.0})
    r2 = rank({"m": 1.0, "a": 1.0, "z": 1.0})
    assert r1 == r2 == {"a": 1, "m": 2, "z": 3}


def test_rank_empty() -> None:
    assert rank({}) == {}


# ---------- clamp01：数值兜底 ----------

@pytest.mark.parametrize("value,expected", [
    (0.5, 0.5),
    (1.5, 1.0),
    (-1.0, 0.0),
    (0.0, 0.0),
    (1.0, 1.0),
    (float("nan"), 0.0),
    (float("inf"), 1.0),
    (None, 0.0),
    ("abc", 0.0),
    ("0.7", 0.7),
    ("", 0.0),
])
def test_clamp01_safe_fallback(value, expected) -> None:
    """NaN / None / 字符串 / 超界值全部安全兜底到 [0,1]。"""
    assert clamp01(value) == expected


# ---------- jaccard：空集边界 ----------

def test_jaccard_basic() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_jaccard_empty_side_returns_zero() -> None:
    """任一侧为空 → 0.0（不抛 ZeroDivisionError）。"""
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard(set(), set()) == 0.0


# ---------- 回归护栏：retrieve.py 的别名导入仍生效 ----------

def test_retrieve_private_aliases_still_work() -> None:
    """retrieve.py 以私有别名从 sparse 导入，原有调用点必须零改动可用。"""
    from app.services.memory import retrieve

    # 别名指向 sparse.py 中的同一函数对象
    assert retrieve._bm25 is sparse.bm25
    assert retrieve._clamp01 is sparse.clamp01
    assert retrieve._jaccard is sparse.jaccard
    assert retrieve._match_tokens is sparse.match_tokens
    assert retrieve._rank is sparse.rank

    # 且仍可正常调用
    assert retrieve._bm25(["库存"], {"d1": ["库存"]})["d1"] > 0.0
    assert retrieve._match_tokens(["我", "我们"]) == ["我们"]
    assert retrieve._rank({"a": 0.2, "b": 0.9}) == {"b": 1, "a": 2}
    assert retrieve._clamp01(float("nan")) == 0.0
    assert retrieve._jaccard({"a"}, {"a"}) == 1.0
