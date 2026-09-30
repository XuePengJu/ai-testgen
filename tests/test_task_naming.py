"""V2.9 任务命名测试：解析需求时顺带产出「需求摘要 + 任务名」。

背景：任务名/会话名原先由前端直接拿用户原话截断 40 字（如「写一个登录功能的测试用例」），
现改为在「解析规格」步骤由模型总结产出，无 AI 时用第一个测试点名兜底。

覆盖：任务名清洗（非法字符 / 冗余后缀 / 首尾标点 / 限长）、模型输出解析
（新对象格式 / 旧裸数组格式 / markdown 代码块包裹 / 纯垃圾文本）、兜底命名、
run_parser 的 details 是否携带 title 与 req_summary。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from app.workflow.agents.parser_agent import (  # noqa: E402
    _fallback_naming,
    _parse_ai_output,
    clean_task_name,
    run_parser,
)
from src.models.testcase import RequirementUnit  # noqa: E402


NEW_FORMAT = (
    '{"summary":"用户登录鉴权与异常处理","title":"登录功能",'
    '"units":[{"name":"账号密码登录","description":"输入正确账号密码可登录"}]}'
)
OLD_FORMAT = '[{"name":"账号密码登录","description":"输入正确账号密码可登录"}]'


class _FakeClient:
    """假模型客户端：固定返回预设文本，用于验证解析与命名链路。"""

    def __init__(self, reply: str):
        self.reply = reply

    def generate(self, prompt: str) -> str:  # noqa: ARG002
        return self.reply


# ============ 任务名清洗 ============


class TestCleanTaskName:
    def test_strips_illegal_filename_chars(self):
        assert clean_task_name("登录/功能") == "登录 功能"
        assert clean_task_name("订单:管理") == "订单 管理"
        assert clean_task_name("A|B") == "A B"

    def test_strips_redundant_suffix(self):
        assert clean_task_name("登录功能的测试用例") == "登录功能"
        assert clean_task_name("登录功能测试") == "登录功能"
        assert clean_task_name("登录功能用例") == "登录功能"

    def test_trims_punctuation_and_whitespace(self):
        assert clean_task_name("。登录功能，") == "登录功能"
        assert clean_task_name("  登录功能  ") == "登录功能"

    def test_strips_markdown_heading_marker(self):
        """兜底解析可能拿到带 ## 的整行，命名里不能出现 markdown 标记。"""
        assert clean_task_name("## 登录功能") == "登录功能"
        assert clean_task_name("#登录功能#") == "登录功能"

    def test_truncates_to_max_len(self):
        assert len(clean_task_name("测" * 50)) == 20
        assert len(clean_task_name("测" * 50, max_len=8)) == 8

    def test_empty_inputs(self):
        assert clean_task_name("") == ""
        assert clean_task_name("   ") == ""
        assert clean_task_name(None) == ""


# ============ 模型输出解析（新格式 + 兼容旧格式）============


class TestParseAiOutput:
    def test_new_object_format(self):
        units, title, req = _parse_ai_output(NEW_FORMAT)
        assert title == "登录功能"
        assert req == "用户登录鉴权与异常处理"
        assert len(units) == 1
        assert units[0].name == "账号密码登录"

    def test_old_array_format_still_works(self):
        """向后兼容：模型只吐裸数组时，units 照常解析，名字留空由兜底补。"""
        units, title, req = _parse_ai_output(OLD_FORMAT)
        assert len(units) == 1
        assert units[0].name == "账号密码登录"
        assert title == ""
        assert req == ""

    def test_markdown_fence_wrapped(self):
        units, title, _ = _parse_ai_output("```json\n" + NEW_FORMAT + "\n```")
        assert title == "登录功能"
        assert len(units) == 1

    def test_garbage_returns_empty(self):
        units, title, req = _parse_ai_output("抱歉，我无法处理这段需求。")
        assert units == []
        assert title == ""
        assert req == ""

    def test_drops_items_without_name(self):
        raw = '{"title":"登录功能","units":[{"description":"没有名字"},{"name":"验证码登录"}]}'
        units, title, _ = _parse_ai_output(raw)
        assert [u.name for u in units] == ["验证码登录"]
        assert title == "登录功能"


# ============ 兜底命名 ============


class TestFallbackNaming:
    def test_uses_first_unit_name(self):
        units = [RequirementUnit(name="账号密码登录"), RequirementUnit(name="验证码登录")]
        title, req = _fallback_naming(units)
        assert title == "账号密码登录"
        assert req == "账号密码登录（共 2 个测试点）"

    def test_single_unit_keeps_plain_summary(self):
        title, req = _fallback_naming([RequirementUnit(name="登录")])
        assert title == "登录"
        assert req == "登录"

    def test_empty_units(self):
        assert _fallback_naming([]) == ("", "")


# ============ run_parser 集成 ============


class TestRunParserNaming:
    def test_details_carry_ai_title(self, tmp_path):
        p = tmp_path / "req.md"
        p.write_text("用户需要能够使用账号密码登录系统。", encoding="utf-8")
        units, summary, details = run_parser(str(p), "business", _FakeClient(NEW_FORMAT))
        meta = json.loads(details)
        assert meta["title"] == "登录功能"
        assert meta["req_summary"] == "用户登录鉴权与异常处理"
        assert len(units) == 1
        assert "测试点" in summary

    def test_fallback_title_without_ai(self, tmp_path):
        """无 AI（访客 / mock）时必须给兜底名字，不能为空、不能报错。"""
        p = tmp_path / "req.md"
        p.write_text(
            "登录功能需求\n\n## 账号密码登录\n\n正常登录\n\n## 验证码登录\n\n短信验证码\n",
            encoding="utf-8",
        )
        units, _summary, details = run_parser(str(p), "business", None)
        meta = json.loads(details)
        assert meta["title"] == "账号密码登录"
        assert meta["req_summary"] == "账号密码登录（共 2 个测试点）"
        assert [u.name for u in units] == ["账号密码登录", "验证码登录"]

    def test_ai_failure_falls_back_to_regex_and_names(self, tmp_path):
        """模型吐垃圾 → 回退正则解析，命名仍可用（不出现空名）。"""
        p = tmp_path / "req.md"
        p.write_text("需求说明\n\n## 登录功能\n\n账号密码登录\n", encoding="utf-8")
        units, _summary, details = run_parser(str(p), "business", _FakeClient("我无法解析"))
        meta = json.loads(details)
        assert meta["title"] == "登录功能"
        assert units

    def test_details_always_have_keys(self, tmp_path):
        """details 结构稳定：三个键始终存在，前端/引擎可直接取。"""
        p = tmp_path / "req.md"
        p.write_text("需求说明\n\n## 登录功能\n\n账号密码登录\n", encoding="utf-8")
        _units, _summary, details = run_parser(str(p), "business", None)
        meta = json.loads(details)
        assert set(meta) >= {"units", "title", "req_summary"}
