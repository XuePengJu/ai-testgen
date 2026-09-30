"""ReviewerAgent：质量校验与门禁（增强版）。

入参：cases（GeneratorAgent 产出的用例列表）
出参：(report, output_summary)

增强：检查覆盖度，缺失类型时调用 AI 补充用例。
"""
import json
import logging
from collections import Counter

from app.services import llm_service
from src.models.testcase import TestCase, CaseType, Priority, align_step_expectations
from src.utils import jsonx

logger = logging.getLogger(__name__)


_FILL_PROMPT = """你是资深测试工程师。请为以下「测试点」补充测试用例，使其覆盖缺失的维度。

缺失维度：{missing}
已有用例数量：{total} 条
测试点描述：{description}

请补充 2~4 条用例，补齐缺失维度。输出 JSON 数组，每个元素结构：
[
  {{
    "title": "用例标题：只写「动作+对象」的动作概括，不要写预期",
    "module": "模块",
    "case_type": "正向|异常|边界值|场景组合",
    "priority": "P0|P1|P2|P3",
    "pre_condition": "前置条件",
    "steps": ["步骤1", "步骤2"],
    "step_expectations": ["步骤1的预期结果", "步骤2的预期结果"],
    "expected": "整体预期结果：只写可验证的实质结果断言，不要写「XX流程正常完成」这类套话；多个结果用「，」分隔",
    "test_data": "测试数据"
  }}
]

要求：step_expectations 必须与 steps 一一对应、数量严格一致。
expected 必须是可验证的实质结果断言（写清结果状态/数据变化）。
只输出 JSON，不要解释。"""

_MISSING_THRESHOLD = {
    "正向": 1,
    "异常": 1,
    "边界值": 1,
    "场景组合": 0,  # 可选
}


def _normalize_type(ct) -> str:
    """兼容不同大小写/中文化。

    ⚠️ 必须先取枚举 ``.value``：``CaseType`` 继承 ``(str, Enum)``，
    ``str(CaseType.NEGATIVE)`` 得到的是 ``'CaseType.NEGATIVE'`` 而**不是** ``'异常'``。
    旧实现直接 ``str(ct)`` → 归一化对枚举输入全部失效，``by_type`` 的键变成类名，
    ``by_type.get("异常")`` 恒为 0。用户可见后果有两个：
      1. 质量报告摘要里「异常/边界占比」恒显示 0%（无论用例实际覆盖多好）；
      2. 覆盖度缺失维度恒被判为「正向/异常/边界值」三项 → 每次评审都白跑一次
         AI 补充调用（花时间与额度，补出来的用例还进了一个错误的键）。
    本函数同时接受枚举与字符串输入（dict 形态的用例传来的是字符串）。
    """
    if ct is None:
        return ""
    val = getattr(ct, "value", None)      # 枚举实例 → 取字面值（"异常"）
    ct = val if isinstance(val, str) else str(ct).strip()
    mapping = {
        "正向": "正向", "positive": "正向", "正常": "正向",
        "异常": "异常", "negative": "异常", "出错": "异常",
        "边界": "边界值", "边界值": "边界值", "boundary": "边界值",
        "场景": "场景组合", "场景组合": "场景组合", "scenario": "场景组合",
    }
    return mapping.get(ct, ct)


def _build_cases_from_json(arr: list) -> list[TestCase]:
    """从 JSON 数组构建 TestCase（复用 _normalize 逻辑）。"""
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        ct = item.get("case_type") or item.get("caseType") or "正向"
        pr = item.get("priority") or item.get("优先级") or "P1"
        steps = item.get("steps", []) or []
        expected = item.get("expected", "")
        se = item.get("step_expectations") or item.get("stepExpectations") or []
        try:
            out.append(TestCase(
                title=item.get("title", "未命名用例"),
                module=item.get("module", ""),
                case_type=CaseType.POSITIVE if _normalize_type(ct) == "正向" else
                          CaseType.NEGATIVE if _normalize_type(ct) == "异常" else
                          CaseType.BOUNDARY if _normalize_type(ct) == "边界值" else
                          CaseType.SCENARIO,
                priority=Priority(pr),
                pre_condition=item.get("pre_condition", "") or item.get("preCondition", ""),
                steps=steps,
                step_expectations=align_step_expectations(steps, se, expected),
                expected=expected,
                test_data=item.get("test_data") or item.get("testData"),
            ))
        except Exception:
            continue
    return out


def run_reviewer(cases: list[TestCase], client=None) -> tuple[dict, str]:
    """质量校验 + AI 补充。client 为可选的真实模型客户端。"""
    total = len(cases)
    by_type = Counter(_normalize_type(c.case_type) for c in cases)
    by_priority = Counter((getattr(c.priority, "value", None) or str(c.priority)) for c in cases)
    abnormal = by_type.get("异常", 0) + by_type.get("边界值", 0)
    modules = Counter(c.module for c in cases if c.module)

    # 结构校验：步骤与预期缺失
    struct_issues = [c.title for c in cases if not c.steps or not c.expected]

    report = {
        "total": total,
        "by_type": dict(by_type),
        "by_priority": dict(by_priority),
        "abnormal_ratio": round(abnormal / total, 2) if total else 0,
        "modules": dict(modules),
        "struct_issues": struct_issues,
        "quality_pass": (len(struct_issues) == 0 and total > 0),
        "supplemented": 0,
    }

    # 覆盖度检查 + AI 补充
    supplemented = 0
    if client is not None and total > 0:
        missing = [k for k, v in _MISSING_THRESHOLD.items() if by_type.get(k, 0) < v]
        if missing:
            try:
                # 取一个典型测试点描述（这里用第一条用例的模块作为上下文）
                desc = cases[0].module or cases[0].title if cases else "未知"
                fill_prompt = _FILL_PROMPT.format(
                    missing="、".join(missing),
                    total=total,
                    description=desc,
                )
                raw = client.chat([{"role": "user", "content": fill_prompt}], temperature=0.5, max_tokens=2048)
                # D 修复：改用 jsonx 配对解析（旧贪婪正则在「数组后另有 [1]」「值内含 ]」时
                # 必然失败并被 except 静默吞掉，表现为「缺维度但一直没补上」且无日志）
                arr = jsonx.find_dict_list(raw or "", jsonx.CASE_KEYS)
                if not arr:
                    logger.warning(
                        "质量校验的 AI 补充用例解析无产出（缺失维度=%s）：原文 %d 字，reason=%s",
                        "、".join(missing), len(raw or ""),
                        "invalid_json" if jsonx.has_array_literal(raw or "") else "no_json",
                    )
                else:
                    new_cases = _build_cases_from_json(arr)
                    if new_cases:
                        cases.extend(new_cases)
                        # 更新 by_type
                        for nc in new_cases:
                            by_type[_normalize_type(nc.case_type)] += 1
                        supplemented = len(new_cases)
            except llm_service.LLMError:
                pass  # 补充失败不阻断，保留原有报告

    # 更新 report
    report["by_type"] = dict(by_type)
    report["supplemented"] = supplemented
    report["quality_pass"] = (len(struct_issues) == 0 and total > 0)

    pct = report["abnormal_ratio"] * 100
    if supplemented > 0:
        extra = f"；补充了 {supplemented} 条"
    else:
        extra = ""
    status_txt = "通过" if report["quality_pass"] else f"存在 {len(struct_issues)} 条结构问题"
    summary = f"质量报告：共 {len(cases)} 条，异常/边界占比 {pct:.0f}%，结构校验{status_txt}{extra}"
    return report, summary, json.dumps(report, ensure_ascii=False, default=str)
