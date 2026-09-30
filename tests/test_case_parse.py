"""D 修复回归测试：LLM 输出解析健壮化 + 解析失败显性上报。

背景（2026-09-24 实测）：原 `re.search(r"\\[.*\\]")` 贪婪匹配导致 3/7 个测试点
静默产出 0 条用例，用户无任何感知。本文件锁死两类行为：
1. 解析层能正确处理围栏/夹带文字/数组后跟引用标记/字符串含 `]`/尾随逗号/对象包裹；
2. 「调用成功但 0 条」必须被记录并经 generator_agent 冒泡到步骤详情与摘要。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from src.generator.case_generator import (  # noqa: E402
    CaseGenerator,
    parse_cases_detailed,
)
from src.models.testcase import RequirementUnit  # noqa: E402
from app.workflow.agents.generator_agent import run_generator  # noqa: E402

ONE_CASE = '{"title":"用例A","steps":["步骤1"],"expected":"预期1"}'


class _FakeClient:
    """最小 LLM 客户端替身：记录调用次数，返回预设文本。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self.reply


def _unit(name: str = "测试点A") -> RequirementUnit:
    return RequirementUnit(name=name, description="描述", kind="business")


# ============ 1. 解析层：原贪婪正则会失败的输入 ============

class TestParseRobustness:
    def test_plain_array(self):
        cases, diag = parse_cases_detailed(f"[{ONE_CASE}]")
        assert len(cases) == 1
        assert diag["reason"] == "ok"

    def test_markdown_fenced_json(self):
        text = f"以下是生成的用例：\n```json\n[{ONE_CASE}]\n```\n请查收。"
        cases, diag = parse_cases_detailed(text)
        assert len(cases) == 1 and diag["reason"] == "ok"

    def test_array_followed_by_reference_marks(self):
        """回归点：数组后面还有 `[1]` 这类普通括号 → 贪婪正则会一路吞到最后一个 `]`。"""
        text = f"[{ONE_CASE}]\n\n参考 [1] 与 [2] 的规范。"
        cases, _ = parse_cases_detailed(text)
        assert len(cases) == 1

    def test_bracket_inside_string_value(self):
        """回归点：字段值里含 `]` → 贪婪正则提前截断。"""
        text = '[{"title":"含]括号","steps":["点击 ] 按钮"],"expected":"预期"}]'
        cases, _ = parse_cases_detailed(text)
        assert len(cases) == 1
        assert cases[0].title == "含]括号"

    def test_trailing_comma_repaired(self):
        cases, diag = parse_cases_detailed(f"[{ONE_CASE},]")
        assert len(cases) == 1 and diag["reason"] == "ok"

    def test_object_wrapping_cases_key(self):
        cases, _ = parse_cases_detailed('{"cases": [%s]}' % ONE_CASE)
        assert len(cases) == 1

    def test_single_object_without_array(self):
        cases, _ = parse_cases_detailed(ONE_CASE)
        assert len(cases) == 1

    def test_prose_only_marks_no_json(self):
        cases, diag = parse_cases_detailed("抱歉，我无法生成测试用例。")
        assert cases == [] and diag["reason"] == "no_json"

    def test_empty_array_marks_empty_array(self):
        cases, diag = parse_cases_detailed("[]")
        assert cases == [] and diag["reason"] == "empty_array"

    def test_broken_json_marks_invalid_json(self):
        cases, diag = parse_cases_detailed('[{"title": "缺右括号"')
        assert cases == [] and diag["reason"] in ("invalid_json", "no_json")

    def test_partial_invalid_items_are_counted(self):
        """结构不合法的单条不再无声消失：保留合法的，非法的记 skipped。"""
        text = f'[{ONE_CASE},{{"title":null,"steps":"不是列表","expected":123}}]'
        cases, diag = parse_cases_detailed(text)
        assert len(cases) == 1
        assert diag["raw_items"] == 2 and diag["skipped"] == 1

    def test_prose_bracket_not_mistaken_for_cases(self):
        """正文里的 `[1]` 是合法 JSON 但非用例条目 → 0 条，reason=empty_array。"""
        cases, diag = parse_cases_detailed("请参考 [1] 文档说明。")
        assert cases == []
        assert diag["reason"] == "empty_array"

    def test_parse_llm_compat_entry_keeps_signature(self):
        assert len(CaseGenerator._parse_llm(f"[{ONE_CASE}]")) == 1
        assert CaseGenerator._parse_llm("无内容") == []


# ============ 2. 失败上报：从 CaseGenerator 冒泡到步骤详情 ============

class TestParseFailureReporting:
    def test_generator_records_failure(self):
        gen = CaseGenerator(client=_FakeClient("我无法生成用例。"))
        cases = gen.generate_for_unit(_unit("商品管理"))
        assert cases == []
        assert len(gen.parse_failures) == 1
        assert gen.parse_failures[0]["unit"] == "商品管理"
        assert gen.parse_failures[0]["reason"] == "no_json"

    def test_generator_records_nothing_on_success(self):
        gen = CaseGenerator(client=_FakeClient(f"[{ONE_CASE}]"))
        assert len(gen.generate_for_unit(_unit())) == 1
        assert gen.parse_failures == []

    def test_run_generator_surfaces_parse_failed_in_details_and_summary(self):
        client = _FakeClient("抱歉，无法生成。")
        cases, summary, details = run_generator(
            [_unit("商品管理与批量导入")], client=client, model_desc="fake-model")
        d = json.loads(details)
        assert cases == []
        assert client.calls == 1
        assert d["parse_failed"][0]["unit"] == "商品管理与批量导入"
        assert d["parse_failed"][0]["reason"] == "no_json"
        assert "未返回可解析用例" in summary
        assert "⚠️" in summary

    def test_run_generator_no_warning_on_success(self):
        cases, summary, details = run_generator(
            [_unit("正常测试点")], client=_FakeClient(f"[{ONE_CASE}]"), model_desc="fake-model")
        d = json.loads(details)
        assert len(cases) == 1
        assert d["parse_failed"] == []
        assert "⚠️" not in summary
