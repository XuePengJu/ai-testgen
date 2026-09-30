"""extract_summary_text 单元测试：摘要入库/存量修复共用的剥壳纯函数。

覆盖：合法 JSON / 非法 JSON / 嵌套（双重编码、summary 套 dict）/ 空串 /
非 summary 形状 / 代码围栏 / 标量值等边界。
"""
from app.services.knowledge.ingest import extract_summary_text


class TestPlainPassthrough:
    """非 JSON 形状输入：原样返回（只做 trim）。"""

    def test_plain_text(self):
        assert extract_summary_text("这是一个登录功能的测试文档摘要。") == \
            "这是一个登录功能的测试文档摘要。"

    def test_surrounding_whitespace_trimmed(self):
        assert extract_summary_text("  摘要内容  ") == "摘要内容"

    def test_text_with_braces_inside(self):
        # 中间含花括号但不是 JSON 形状 → 原文返回
        s = "配置示例 {a: 1} 如上"
        assert extract_summary_text(s) == s

    def test_text_starting_with_brace_but_invalid_json(self):
        s = "{不是json的文本"
        assert extract_summary_text(s) == s


class TestValidSummaryJson:
    """合法 {"summary": ...} 形状：剥壳取 summary 字段。"""

    def test_basic_summary_json(self):
        raw = '{"summary": "覆盖登录、注销与权限校验。"}'
        assert extract_summary_text(raw) == "覆盖登录、注销与权限校验。"

    def test_summary_with_category(self):
        raw = '{"summary": "支付流程摘要", "category": "支付"}'
        assert extract_summary_text(raw) == "支付流程摘要"

    def test_summary_value_empty_string(self):
        assert extract_summary_text('{"summary": ""}') == ""

    def test_summary_value_null(self):
        assert extract_summary_text('{"summary": null}') == ""

    def test_summary_value_number(self):
        assert extract_summary_text('{"summary": 42}') == "42"

    def test_fenced_json(self):
        raw = '```json\n{"summary": "围栏包裹的摘要"}\n```'
        assert extract_summary_text(raw) == "围栏包裹的摘要"

    def test_summary_containing_json_text_but_not_summary_shape(self):
        # summary 值里含 JSON 文本但不是 {"summary":...} 形状 → 保留该字符串
        raw = '{"summary": "{\\"a\\": 1}"}'
        assert extract_summary_text(raw) == '{"a": 1}'


class TestInvalidJson:
    """非法 JSON：summary 形状走截断修复，其他形状兜底原文，不抛异常、不丢内容。"""

    def test_truncated_summary_shape_repaired(self):
        # {"summary": 开头的截断 JSON → 修复剥壳（见 TestTruncatedRepair）
        assert extract_summary_text('{"summary": "没写完的') == "没写完的"

    def test_truncated_non_summary_shape_fallback(self):
        s = '{"content": "没写完的'
        assert extract_summary_text(s) == s

    def test_single_quotes_not_json(self):
        s = "{'summary': '单引号不是合法JSON'}"
        assert extract_summary_text(s) == s

    def test_empty_object(self):
        s = "{}"
        assert extract_summary_text(s) == s


class TestNotSummaryShape:
    """合法 JSON 但没有 summary 字段：兜底原文。"""

    def test_other_shape(self):
        s = '{"title": "文档标题", "category": "其他"}'
        assert extract_summary_text(s) == s

    def test_json_array(self):
        s = '["not", "a", "summary"]'
        assert extract_summary_text(s) == s


class TestNested:
    """嵌套/双重编码：递归剥壳。"""

    def test_double_encoded_summary(self):
        # 外层值本身又是一个 {"summary":...} JSON 字符串
        raw = '{"summary": "{\\"summary\\": \\"真正摘要\\"}"}'
        assert extract_summary_text(raw) == "真正摘要"

    def test_summary_value_is_summary_dict(self):
        raw = '{"summary": {"summary": "字典里套的摘要"}}'
        assert extract_summary_text(raw) == "字典里套的摘要"

    def test_summary_value_is_other_dict(self):
        raw = '{"summary": {"k": "v", "n": 1}}'
        assert extract_summary_text(raw) == '{"k": "v", "n": 1}'

    def test_summary_value_is_list(self):
        raw = '{"summary": ["要点一", "要点二"]}'
        assert extract_summary_text(raw) == '["要点一", "要点二"]'

    def test_triple_encoded(self):
        raw = '{"summary": "{\\"summary\\": \\"{\\\\\\"summary\\\\\\": \\\\\\\"三层\\\\\\\"}\\"}"}'
        assert extract_summary_text(raw) == "三层"


class TestTruncatedRepair:
    """截断的 {"summary": " 形状（LLM 输出被截断的存量脏数据）：修复剥壳。"""

    def test_truncated_no_closing(self):
        assert extract_summary_text('{"summary":"DBERP') == "DBERP"

    def test_truncated_with_space_after_colon(self):
        assert extract_summary_text('{"summary": "本文档明确测试范围') == "本文档明确测试范围"

    def test_truncated_complete_quote_no_brace(self):
        assert extract_summary_text('{"summary": "完整引号结尾') == "完整引号结尾"

    def test_truncated_escaped_quote_inside(self):
        raw = '{"summary": "他说\\"你好\\"然后就'
        assert extract_summary_text(raw) == '他说"你好"然后就'

    def test_truncated_with_trailing_brace(self):
        assert extract_summary_text('{"summary": "内容"}') == "内容"

    def test_truncated_prefix_only(self):
        # 剥完为空 → 兜底原文
        s = '{"summary": "'
        assert extract_summary_text(s) == s

    def test_truncated_shape_but_not_summary_key(self):
        # {"title": 开头截断 → 不修，兜底原文
        s = '{"title": "某文档'
        assert extract_summary_text(s) == s


class TestEmptyInput:
    """空值边界。"""

    def test_empty_string(self):
        assert extract_summary_text("") == ""

    def test_whitespace_only(self):
        assert extract_summary_text("   \n\t ") == ""

    def test_none(self):
        assert extract_summary_text(None) == ""
