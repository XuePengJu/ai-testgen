"""LLM 输出 JSON 提取公共工具（src.utils.jsonx）与三处调用点的回归测试。

背景（D 修复续，2026-09-24）：项目里曾有 **5 处** 用贪婪/截断正则解析 LLM 输出：

  1. ``generator_core/src/generator/case_generator.py``（生成用例）→ 上一轮已修
  2. ``app/workflow/agents/supplement_agent.py``（增量补充用例）
  3. ``app/workflow/agents/parser_agent.py``（需求拆测试点）
  4. ``app/workflow/agents/reviewer_agent.py``（质量门禁 AI 补维度）
  5. ``app/api/knowledge.py``（Wiki 摘要）

本文件锁住 2~5 的行为：每个用例都给出「旧写法必然失败」的输入形态，
确保替换到 jsonx 后能正确解析，且不再静默丢结果。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from src.models.testcase import CaseType, Priority, TestCase  # noqa: E402
from src.utils import jsonx  # noqa: E402

from app.workflow.agents.parser_agent import _parse_ai_output  # noqa: E402
from app.workflow.agents.reviewer_agent import run_reviewer  # noqa: E402
from app.workflow.agents.supplement_agent import _parse_llm as _supplement_parse  # noqa: E402


# ============ jsonx 原语 ============

class TestBalancedSlices:
    """括号配对切片：字符串 / 转义感知（贪婪正则的三类硬伤都源于此）。"""

    def test_string_value_containing_bracket_not_truncated(self):
        text = '[{"title":"含 ] 括号","steps":["点 ] 击"]}]'
        chunks = jsonx.balanced_slices(text, "[", "]")
        assert chunks == [text]          # 整体作为一个顶层片段，未被值内的 ] 提前截断

    def test_escaped_quote_inside_string(self):
        text = r'[{"title":"含 \" 转义引号","steps":["s"]}]'
        assert jsonx.balanced_slices(text, "[", "]") == [text]

    def test_only_top_level_slices_returned(self):
        """嵌套片段不单独返回，只返回最外层配对片段。"""
        text = '{"a":[1,2],"b":{"c":3}}'
        assert jsonx.balanced_slices(text, "{", "}") == [text]

    def test_unclosed_tail_ignored(self):
        assert jsonx.balanced_slices('前 [{"a":1}', "[", "]") == []

    def test_multiple_blocks_listed_in_order(self):
        text = '[1]\n[2]\n[3]'
        assert jsonx.balanced_slices(text, "[", "]") == ["[1]", "[2]", "[3]"]


class TestFindDictList:
    """列表提取主入口。"""

    def test_plain_array(self):
        assert jsonx.find_dict_list('[{"title":"A"}]', jsonx.CASE_KEYS) == [{"title": "A"}]

    def test_fenced_json(self):
        text = '这是用例：\n```json\n[{"title":"B"}]\n```\n以上。'
        assert jsonx.find_dict_list(text, jsonx.CASE_KEYS) == [{"title": "B"}]

    def test_trailing_citation_marker(self):
        """旧贪婪正则的经典失效：数组后紧跟 [1] 引用标记。"""
        text = '[{"title":"C"}]\n\n参考 [1] 与 [2] 文档。'
        assert jsonx.find_dict_list(text, jsonx.CASE_KEYS) == [{"title": "C"}]

    def test_trailing_comma_repaired(self):
        assert jsonx.find_dict_list('[{"title":"D"},]', jsonx.CASE_KEYS) == [{"title": "D"}]

    def test_object_wrapped_array(self):
        assert jsonx.find_dict_list('{"cases":[{"title":"E"}]}', jsonx.CASE_KEYS) == [{"title": "E"}]

    def test_prose_bracket_not_mistaken_for_cases(self):
        """正文里的 [1] 不是用例（元素非 dict / 无特征键）。"""
        assert jsonx.find_dict_list("请参考 [1] 文档。", jsonx.CASE_KEYS) is None

    def test_empty_array_returns_none(self):
        assert jsonx.find_dict_list("[]", jsonx.CASE_KEYS) is None

    def test_plain_text_returns_none(self):
        assert jsonx.find_dict_list("我无法生成用例。", jsonx.CASE_KEYS) is None


class TestFindDict:
    """对象提取：重点覆盖「嵌套对象」——旧 ``r'\\{[^}]+\\}'`` 遇嵌套必截断。"""

    def test_nested_object_not_truncated(self):
        text = '{"summary":"s","meta":{"level":2,"tags":["a"]}}'
        obj = jsonx.find_dict(text)
        assert obj == {"summary": "s", "meta": {"level": 2, "tags": ["a"]}}

    def test_need_keys_filter(self):
        text = '{"noise":1}\n{"summary":"s","units":[{"name":"n"}]}'
        assert jsonx.find_dict(text, need_keys=("units",))["summary"] == "s"

    def test_need_keys_missing_returns_none(self):
        assert jsonx.find_dict('{"a":1}', need_keys=("units",)) is None

    def test_object_followed_by_example_object(self):
        """旧贪婪 ``\\{.*\\}`` 会一路吞到最后一个 } 拼成非法 JSON。"""
        text = '{"summary":"s","units":[{"name":"n"}]}\n\n示例：{"foo":"bar"}'
        assert jsonx.find_dict(text, need_keys=("units",))["summary"] == "s"

    def test_braces_inside_string_value(self):
        text = '{"summary":"摘要含 { 花括号 } 示例"}'
        assert jsonx.find_dict(text)["summary"] == "摘要含 { 花括号 } 示例"


class TestDiagnostics:
    def test_literal_probes(self):
        assert jsonx.has_array_literal('x [1] y') is True
        assert jsonx.has_array_literal("全是文字") is False
        assert jsonx.has_object_literal('x {"a":1} y') is True

    def test_repair_only_touches_trailing_comma(self):
        assert jsonx.repair_json('{"a":1,}') == '{"a":1}'
        assert jsonx.repair_json('[1,2,]') == '[1,2]'
        assert jsonx.repair_json('{"a":1, "b":2}') == '{"a":1, "b":2}'


# ============ 调用点 2：supplement_agent ============

class TestSupplementParse:
    """补充用例解析。"""

    def test_parses_array_with_trailing_citation(self):
        """旧 ``re.search(r"\\[.*\\]", ...)`` 命中 [{...}] 与 [1] 之间的脏串 → 静默 0 条。"""
        raw = ('[{"title":"补充-空值处理","module":"M","case_type":"异常","priority":"P1",'
               '"steps":["清空必填字段","提交"],"expected":"系统提示必填项不能为空"}]\n\n参考 [1]。')
        cases = _supplement_parse(raw)
        assert len(cases) == 1
        assert cases[0].title == "补充-空值处理"
        assert cases[0].case_type == CaseType.NEGATIVE

    def test_fenced_output_parsed(self):
        raw = '```json\n[{"title":"T","module":"M","steps":["s"],"expected":"e"}]\n```'
        assert len(_supplement_parse(raw)) == 1

    def test_plain_text_returns_empty(self):
        assert _supplement_parse("抱歉，信息不足，无法补充用例。") == []


# ============ 调用点 3：parser_agent ============

class TestParserAiOutput:
    """需求拆测试点解析（对象优先，裸数组回退）。"""

    def test_object_format_with_trailing_text(self):
        raw = ('{"summary":"登录需求","title":"登录功能",'
               '"units":[{"name":"登录成功","description":"手机号+密码"}]}\n\n希望有帮助！')
        units, title, req_summary = _parse_ai_output(raw)
        assert title == "登录功能"
        assert req_summary == "登录需求"
        assert [u.name for u in units] == ["登录成功"]

    def test_object_then_example_object(self):
        """旧贪婪 ``\\{.*\\}`` 会吞到最后一个 } → JSON 非法 → 回退正则 → 测试点全丢。"""
        raw = ('{"summary":"s","title":"t","units":[{"name":"n1","description":"d"}]}\n\n'
               '示例：{"name":"示例","description":"仅演示"}')
        units, _, _ = _parse_ai_output(raw)
        assert [u.name for u in units] == ["n1"]

    def test_nested_object_in_units(self):
        """对象内含嵌套结构时不应截断。"""
        raw = '{"summary":"s","title":"t","units":[{"name":"n","description":"d","meta":{"k":1}}]}'
        units, _, _ = _parse_ai_output(raw)
        assert [u.name for u in units] == ["n"]

    def test_legacy_bare_array_fallback(self):
        raw = '[{"name":"登录","description":"d"}]\n\n请确认。'
        units, _, _ = _parse_ai_output(raw)
        assert [u.name for u in units] == ["登录"]

    def test_unparsable_returns_empty(self):
        units, title, req_summary = _parse_ai_output("我无法拆解这段需求。")
        assert units == [] and title == "" and req_summary == ""


# ============ 调用点 4：reviewer_agent ============

class _ChatStub:
    """最小 chat() 客户端：只回固定文本，记录调用。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


def _case(title: str, case_type) -> TestCase:
    return TestCase(title=title, module="M", case_type=case_type,
                    priority=Priority.P1, steps=["s"], expected="e")


class TestReviewerSupplement:
    """质量门禁的 AI 补维度（旧写法失败时完全静默：无日志、report.supplemented 恒 0）。"""

    def test_supplements_when_json_followed_by_citation(self):
        cases = [_case("登录成功", CaseType.POSITIVE)]      # 缺异常/边界值 → 触发补充
        reply = ('[{"title":"登录失败提示","module":"M","case_type":"异常","priority":"P1",'
                 '"steps":["输入错误密码","提交"],"expected":"提示账号或密码错误"}]\n\n参考 [1]。')
        report, _, _ = run_reviewer(cases, client=_ChatStub(reply))
        assert report["supplemented"] == 1
        assert report["by_type"].get("异常") == 1

    def test_no_supplement_when_model_gives_prose(self):
        cases = [_case("登录成功", CaseType.POSITIVE)]
        report, summary, _ = run_reviewer(cases, client=_ChatStub("抱歉，无法补充。"))
        assert report["supplemented"] == 0
        assert "质量报告" in summary          # 补充失败不阻断主报告

    def test_no_supplement_when_client_absent(self):
        cases = [_case("登录成功", CaseType.POSITIVE)]
        report, _, _ = run_reviewer(cases, client=None)
        assert report["supplemented"] == 0
