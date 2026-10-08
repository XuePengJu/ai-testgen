"""V7.3 评估体系测试（黄金集形态 + 整套跑分 + 阈值判定 + 归因映射）。

run_suite 依赖 V7.2 的 retrieve.hybrid_search（工程师并行开发中）；
hybrid_search 未落地时本文件整体 skip（skipif 探测 import），落地后自动生效。
"""
from __future__ import annotations

import json

import pytest

from app.services.memory import eval as mem_eval

# hybrid_search 未就绪时整文件 skip（V7.2 合入后自动跑）
pytest.importorskip(
    "app.services.memory.retrieve",
    reason="V7.2 retrieve.hybrid_search 尚未合入")


def test_golden_set_shape():
    """黄金集形态：30 条、5 场景各 6、必需字段齐全、期望字段合法。"""
    cases = mem_eval.GOLDEN["cases"]
    assert len(cases) == 30
    by_scen: dict[str, int] = {}
    for c in cases:
        by_scen[c["scenario"]] = by_scen.get(c["scenario"], 0) + 1
        assert c["id"] and c["query"] and c["expect"]
        for f in c.get("setup") or []:
            assert f["kind"] in ("fact", "preference", "rule", "todo", "profile")
            assert 0.0 <= f["confidence"] <= 1.0
        for role, facts in (c.get("seed") or {}).items():
            assert role in ("a", "b") and facts
        exp = c["expect"]
        assert not (exp.get("top1_contains") and exp.get("top1_absent")), \
            "top1_contains 与 top1_absent 互斥"
    assert by_scen == {"preference_change": 6, "conflict": 6,
                       "expiry": 6, "isolation": 6, "temporal_decay": 6}


def test_eval_suite_all_pass_and_thresholds():
    """整套跑分：30 用例全过且五类指标全部达标。"""
    out = mem_eval.run_suite()
    assert out["total"] == 30
    assert out["failed"] == 0, mem_eval.format_report(out)
    assert out["threshold_fail"] == [], mem_eval.format_report(out)
    m = out["metrics"]
    assert m["hit_rate_at_5"] >= mem_eval.THRESHOLDS["hit_rate_at_5"]
    assert m["false_memory_rate"] <= mem_eval.THRESHOLDS["false_memory_rate"]
    assert m["conflict_rate"] <= mem_eval.THRESHOLDS["conflict_rate"]
    assert m["inflation_rate"] <= mem_eval.THRESHOLDS["inflation_rate"]
    assert m["latency_p95_ms"] <= mem_eval.THRESHOLDS["latency_p95_ms"]


def test_conflict_and_expiry_state_real():
    """裁决/过期走的是真实链路：冲突场景留有 conflict 行、过期场景留有 expired 行。"""
    out = mem_eval.run_suite()
    by_id = {r["id"]: r for r in out["cases"]}
    # 冲突场景每个用例至少 1 条 conflict（低置信挂起的直接证据）
    for cid in ("cft-01", "cft-02", "cft-06"):
        assert by_id[cid]["passed"], by_id[cid]
    # 过期场景每个用例至少 1 条 expired（TTL 让位的直接证据）
    for cid in ("exp-01", "exp-04", "exp-06"):
        assert by_id[cid]["passed"], by_id[cid]


def test_attribute_mapping():
    """归因映射：隔离泄漏 / 抽取缺 / 排序丢单 / 场景默认类。"""
    assert mem_eval._attribute(
        {"scenario": "isolation"},
        {"failures": ["期望零召回但命中 1 条"], "recalled": 1}) == "isolation_leak"
    assert mem_eval._attribute(
        {"scenario": "conflict"},
        {"failures": ["state：conflict 条目数不足 1"], "recalled": 0}) \
        == "extraction_miss"
    assert mem_eval._attribute(
        {"scenario": "preference_change"},
        {"failures": ["零召回"], "recalled": 0}) == "rank_miss"
    assert mem_eval._attribute(
        {"scenario": "conflict"},
        {"failures": ["top1 不含「周五」"], "recalled": 2}) == "conflict_lost"
    assert mem_eval._attribute(
        {"scenario": "expiry"},
        {"failures": ["召回出现禁词「王五」"], "recalled": 2}) == "expired_wrong"


def test_threshold_direction():
    """阈值方向：hit_rate 低了不达标，其余高了不达标（方向表回归）。"""
    m_ok = {"hit_rate_at_5": 1.0, "false_memory_rate": 0.0,
            "conflict_rate": 0.0, "inflation_rate": 1.0, "latency_p95_ms": 1.0}
    fail = [
        k for k, v in mem_eval.THRESHOLDS.items()
        if (m_ok[k] < v if k == "hit_rate_at_5" else m_ok[k] > v)
    ]
    assert fail == []
    m_bad = dict(m_ok, hit_rate_at_5=0.1, latency_p95_ms=999.0)
    fail_bad = [
        k for k, v in mem_eval.THRESHOLDS.items()
        if (m_bad[k] < v if k == "hit_rate_at_5" else m_bad[k] > v)
    ]
    assert set(fail_bad) == {"hit_rate_at_5", "latency_p95_ms"}
