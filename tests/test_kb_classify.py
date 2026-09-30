"""classify 模块单元测试：M7 归类降级的解析/兜底纯函数。

覆盖：normalize_category 的清洗与回落规则、parse_llm_category 的
JSON / 围栏 / 纯文本 / 拒答 / 嵌套对象等边界。
"""
from app.services.knowledge.classify import (
    DEFAULT_CATEGORY,
    normalize_category,
    parse_llm_category,
    strip_code_fences,
)


class TestNormalizeCategory:
    """分类值清洗：空白/引号/拒答值 → 未分类；超长截断。"""

    def test_none_falls_back(self):
        assert normalize_category(None) == "未分类"

    def test_empty_string_falls_back(self):
        assert normalize_category("") == "未分类"

    def test_plain_category_kept(self):
        assert normalize_category("测试基础") == "测试基础"

    def test_whitespace_and_quotes_stripped(self):
        assert normalize_category('  "工具配置"  ') == "工具配置"
        assert normalize_category("'开发实践'") == "开发实践"

    def test_code_fence_stripped(self):
        assert normalize_category("```json\n\"网络协议\"\n```") == "网络协议"

    def test_invalid_values_fall_back(self):
        for bad in ("未分类", "无", "未知", "无法分类", "None", "null", "N/A", "其他"):
            assert normalize_category(bad) == "未分类", bad

    def test_custom_fallback(self):
        assert normalize_category("", fallback="默认主题") == "默认主题"

    def test_long_value_truncated(self):
        out = normalize_category("这是一个超过二十个字的主题分类标签会被截断处理掉尾部")
        assert len(out) == 20
        assert out.startswith("这是一个超过二十个字的主题分类")


class TestStripCodeFences:
    """代码围栏剥离。"""

    def test_json_fence(self):
        assert strip_code_fences('```json\n{"category": "a"}\n```') == '{"category": "a"}'

    def test_plain_fence(self):
        assert strip_code_fences("```\n工具配置\n```") == "工具配置"

    def test_no_fence_passthrough(self):
        assert strip_code_fences("测试基础") == "测试基础"

    def test_empty(self):
        assert strip_code_fences("") == ""


class TestParseLlmCategoryJson:
    """JSON 形状输出：取 category 字段。"""

    def test_summary_plus_category(self):
        raw = '{"summary": "覆盖登录流程", "category": "测试基础"}'
        assert parse_llm_category(raw) == "测试基础"

    def test_category_only(self):
        raw = '{"category": "工具配置"}'
        assert parse_llm_category(raw) == "工具配置"

    def test_chinese_key(self):
        raw = '{"分类": "开发实践"}'
        assert parse_llm_category(raw) == "开发实践"

    def test_wrapped_in_fence_and_prose(self):
        raw = '解析结果：```json\n{"category": "网络协议"}\n```\n以上。'
        assert parse_llm_category(raw) == "网络协议"

    def test_nested_object_not_truncated(self):
        # 旧实现 r'\{[^}]+\}' 遇嵌套会截断；配平扫描应完整解析外层
        raw = '{"category": "测试基础", "meta": {"k": 1}}'
        assert parse_llm_category(raw) == "测试基础"

    def test_dict_without_category(self):
        assert parse_llm_category('{"summary": "只有摘要"}') == "未分类"

    def test_none_and_empty(self):
        assert parse_llm_category(None) == "未分类"
        assert parse_llm_category("") == "未分类"
        assert parse_llm_category("   ") == "未分类"


class TestParseLlmCategoryPlainText:
    """纯文本输出兜底：取首个非空行，剥前缀与句尾标点。"""

    def test_plain_line(self):
        assert parse_llm_category("测试基础") == "测试基础"

    def test_labeled_line(self):
        assert parse_llm_category("分类：工具配置") == "工具配置"
        assert parse_llm_category("Category: DevOps") == "DevOps"

    def test_multiline_takes_first(self):
        assert parse_llm_category("网络协议\n这是一个网络相关的文档") == "网络协议"

    def test_trailing_punctuation_stripped(self):
        assert parse_llm_category("测试基础。") == "测试基础"
        assert parse_llm_category("测试基础——适用于入门学习") == "测试基础"

    def test_refusal_falls_back(self):
        assert parse_llm_category("无法分类") == "未分类"
        assert parse_llm_category("未知") == "未分类"

    def test_invalid_json_treated_as_text(self):
        # 伪 JSON（截断）：剥掉行首 { 后按 "label: 值" 解析出分类
        assert parse_llm_category('{"category": "测试基础') == "测试基础"

    def test_pseudo_json_text_falls_back_when_meaningless(self):
        # 截断成 summary 键残片：解析不出有效分类 → 回落
        assert parse_llm_category('{"summary": "x') == DEFAULT_CATEGORY
