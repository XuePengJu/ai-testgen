"""SupplementAgent：增量生成测试用例（迭代补充）。

入参：existing_cases（已有用例列表）、instruction（补充要求）、client（可选 LLM 客户端）
出参：(new_cases, summary, details_json)

设计要点：
- 上下文只传压缩摘要（模块+标题+类型统计），不传 steps/expected 全文，防 token 超限
- 明确要求 LLM 不重复已有用例，只补充指令涉及范围
- 演示模式（AITF_ALLOW_DEMO=1）才允许 mock 兜底；默认未注入模型 / 调用失败 → 直接抛错
"""
import json
import logging
from collections import Counter

from app.core.config import AITF_ALLOW_DEMO

from src.models.testcase import (
    TestCase,
    CaseType,
    Priority,
    align_step_expectations,
    strip_title_expected,
)
from src.utils import jsonx

logger = logging.getLogger(__name__)


_SUPPLEMENT_PROMPT = """你是资深测试工程师。请根据补充要求，为已有测试用例集**增量补充**用例。

【已有用例摘要】
{existing_summary}

【补充要求】
{instruction}

【输出要求】
1. 只生成补充要求涉及的用例，**绝对不要重复已有用例的标题和场景**
2. 如果补充要求涉及新模块，module 字段填新模块名
3. 输出 JSON 数组，每个元素结构：
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
4. step_expectations 必须与 steps 一一对应、数量严格一致，每个步骤都要有明确的预期结果
5. expected 必须是可验证的实质结果断言（写清结果状态/数据变化）
6. 只输出 JSON，不要解释、不要 markdown 代码块包裹"""


def build_existing_summary(cases: list[TestCase], max_per_module: int = 15) -> str:
    """构建已有用例的压缩摘要：模块分组 + 标题 + 类型，不传全文。"""
    if not cases:
        return "（无已有用例）"
    by_module: dict[str, list[TestCase]] = {}
    for c in cases:
        by_module.setdefault(c.module or "未分类", []).append(c)
    lines = []
    total = len(cases)
    type_dist = Counter(str(c.case_type.value) for c in cases)
    lines.append(f"共 {total} 条，类型分布：{dict(type_dist)}")
    for mod, cs in by_module.items():
        lines.append(f"\n模块「{mod}」（{len(cs)} 条）：")
        for c in cs[:max_per_module]:
            title = c.title[:40] + ("…" if len(c.title) > 40 else "")
            lines.append(f"  - {title}（{c.case_type.value}）")
        if len(cs) > max_per_module:
            lines.append(f"  …（另有 {len(cs) - max_per_module} 条未列出）")
    return "\n".join(lines)


def _normalize(raw: dict) -> dict:
    """兼容 LLM 返回的字段名大小写/中英差异，并对齐逐步预期。"""
    ct = raw.get("case_type") or raw.get("caseType") or "正向"
    pr = raw.get("priority") or raw.get("优先级") or "P1"
    steps = raw.get("steps", []) or []
    expected = raw.get("expected", "")
    se = raw.get("step_expectations") or raw.get("stepExpectations") or []
    return {
        "title": raw.get("title", "未命名用例"),
        "module": raw.get("module", ""),
        "case_type": ct,
        "priority": pr,
        "pre_condition": raw.get("pre_condition", "") or raw.get("preCondition", ""),
        "steps": steps,
        "step_expectations": align_step_expectations(steps, se, expected),
        "expected": expected,
        "test_data": raw.get("test_data") or raw.get("testData"),
    }


def _parse_llm(text: str) -> list[TestCase]:
    """从 LLM 回复中解析 JSON 用例数组。

    D 修复（2026-09-24）：不再用贪婪正则 ``re.search(r"\\[.*\\]", text, re.S)``——
    该写法在「数组后另有 ``[1]`` 引用」「字段值内含 ``]``」「数组不在文本末尾」时
    必然整体失败，且旧实现静默 ``return []``，表现为「补充任务跑完一条没加」且无日志。
    现改用 ``src.utils.jsonx`` 配对切片，并把失败显性写日志。
    """
    raw_text = text or ""
    arr = jsonx.find_dict_list(raw_text, jsonx.CASE_KEYS)
    if not arr:
        logger.warning(
            "补充用例解析无产出：原文 %d 字，reason=%s",
            len(raw_text),
            "invalid_json" if jsonx.has_array_literal(raw_text) else "no_json",
        )
        return []
    out = []
    skipped = 0
    for item in arr:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            n = _normalize(item)
            out.append(TestCase(
                title=n["title"],
                module=n["module"],
                case_type=CaseType(n["case_type"]) if n["case_type"] in ("正向", "异常", "边界值", "场景组合") else CaseType.POSITIVE,
                priority=Priority(n["priority"]) if n["priority"] in ("P0", "P1", "P2", "P3") else Priority.P1,
                pre_condition=n["pre_condition"],
                steps=n["steps"],
                step_expectations=n["step_expectations"],
                expected=n["expected"],
                test_data=n["test_data"],
            ))
        except Exception:  # noqa: BLE001  单条结构不合法只丢该条
            skipped += 1
    if skipped:
        logger.warning("补充用例部分丢弃：%d/%d 条结构不合法", skipped, len(arr))
    return out


def _mock_supplement(instruction: str, existing_cases: list[TestCase]) -> list[TestCase]:
    """mock 兜底：根据补充指令关键词生成模板化用例。"""
    existing_modules = {c.module for c in existing_cases if c.module}
    target_module = ""
    for mod in existing_modules:
        if mod and mod in instruction:
            target_module = mod
            break
    if not target_module:
        target_module = instruction[:10] or "补充模块"
    templates = [
        ("补充-异常输入校验", "异常", "P1", "输入非法数据",
         ["输入非法格式数据", "提交"],
         ["非法数据可正常录入", "系统提示错误并拒绝提交"],
         "系统提示错误并拒绝提交"),
        ("补充-边界值验证", "边界值", "P2", "输入边界值",
         ["输入最小边界值", "输入最大边界值", "提交"],
         ["最小边界值被正常接受", "最大边界值被正常接受", "系统正确处理边界值"],
         "系统正确处理边界值"),
        ("补充-空值处理", "异常", "P1", "必填项为空",
         ["清空必填字段", "提交"],
         ["必填字段已清空", "系统提示必填项不能为空"],
         "系统提示必填项不能为空"),
    ]
    cases = []
    for title, ct, pr, pre, steps, se, exp in templates:
        cases.append(TestCase(
            title=title,
            module=target_module,
            case_type=CaseType(ct),
            priority=Priority(pr),
            pre_condition=pre,
            steps=steps,
            step_expectations=se,
            expected=exp,
        ))
    return cases


def run_supplement(
    existing_cases: list[TestCase],
    instruction: str,
    client=None,
    model_desc: str = "",
) -> tuple[list[TestCase], str, str]:
    """增量生成用例。

    Args:
        existing_cases: 已有用例列表（用于去重上下文）
        instruction: 用户补充要求
        client: 可选 LLM 客户端（None → 演示模式才 mock 兜底，否则抛错）

    Raises:
        RuntimeError: 未注入客户端或真实调用失败，且未开启演示模式时。
        model_desc: 模型描述（用于 summary）

    Returns:
        (new_cases, summary, details_json)
    """
    if client is not None:
        summary_text = build_existing_summary(existing_cases)
        prompt = _SUPPLEMENT_PROMPT.format(
            existing_summary=summary_text,
            instruction=instruction or "补充更多测试用例",
        )
        try:
            raw = client.generate(prompt)
            new_cases = _parse_llm(raw)
        except Exception:  # noqa: BLE001
            if AITF_ALLOW_DEMO:
                new_cases = _mock_supplement(instruction, existing_cases)
                model_note = "模型调用失败，已降级为模拟生成"
            else:
                # 默认不静默兜底：真实失败必须暴露，不能用模板假用例覆盖
                raise
        else:
            model_note = model_desc or "真实模型"
    else:
        if AITF_ALLOW_DEMO:
            new_cases = _mock_supplement(instruction, existing_cases)
            model_note = "演示模式模拟生成，请到【模型设置】配置真实模型"
        else:
            raise RuntimeError(
                "未配置可用的文本模型，无法生成补充用例。"
                "请先在「设置 → 模型配置」配置模型；"
                "如需本地演示可设环境变量 AITF_ALLOW_DEMO=1。"
            )

    # 过滤掉与已有用例标题完全重复的（已有用例标题已是 `动作 -> 预期` 复合形式，比较时剥掉预期片段）
    existing_titles = {
        (strip_title_expected(c.title), c.module, c.case_type.value) for c in existing_cases
    }
    deduped = [
        c for c in new_cases
        if (strip_title_expected(c.title), c.module, c.case_type.value) not in existing_titles
    ]

    by_module = Counter(c.module or "未分类" for c in deduped)
    summary = f"增量生成 {len(deduped)} 条用例（模型：{model_note}）"
    details = {
        "generated": len(new_cases),
        "after_dedup": len(deduped),
        "by_module": dict(by_module),
        "instruction": instruction,
        "model": model_note,
    }
    return deduped, summary, json.dumps(details, ensure_ascii=False)
