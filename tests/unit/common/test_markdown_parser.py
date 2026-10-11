"""Markdown 需求解析测试。

重点回归：**文件第一个字符就是 `##` 标题** 时首个小节不能被吞掉。
旧实现 `re.split(r"\n#{2,}\s+", text)` 依赖标题前必须有换行，
导致 `## A\n...\n## B\n...` 只切出 B，A 连同标题被并进 parts[0] 丢弃。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from src.parser.markdown_parser import parse_markdown  # noqa: E402


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "req.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestHeadingAtFileStart:
    def test_first_heading_not_swallowed(self, tmp_path):
        """文件以 ## 开头：首个小节必须被解析出来（本次修复的核心场景）。"""
        path = _write(tmp_path, "## 账号密码登录\n\n输入正确账号密码可登录\n\n## 验证码登录\n\n短信验证码登录\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["账号密码登录", "验证码登录"]
        assert units[0].description == "输入正确账号密码可登录"

    def test_single_heading_at_start(self, tmp_path):
        """整个文件只有一个小节且以 ## 开头 —— 旧实现会退化成兜底（名字带 ##）。"""
        path = _write(tmp_path, "## 登录功能\n\n账号密码登录\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["登录功能"]
        assert not units[0].name.startswith("#")

    def test_leading_blank_lines_before_heading(self, tmp_path):
        path = _write(tmp_path, "\n\n## 登录功能\n\n账号密码登录\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["登录功能"]


class TestGeneralSplitting:
    def test_heading_after_preamble(self, tmp_path):
        path = _write(tmp_path, "登录功能需求\n\n## 账号密码登录\n\n正常登录\n\n## 验证码登录\n\n短信验证码\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["账号密码登录", "验证码登录"]

    def test_third_level_heading_also_splits(self, tmp_path):
        path = _write(tmp_path, "## 模块A\n\n正文A\n\n### 子场景B\n\n正文B\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["模块A", "子场景B"]

    def test_title_parenthesis_stripped(self, tmp_path):
        path = _write(tmp_path, "## 登录功能（含验证码）\n\n正文\n")
        units = parse_markdown(path)
        assert [u.name for u in units] == ["登录功能"]

    def test_no_heading_falls_back_to_whole_text(self, tmp_path):
        path = _write(tmp_path, "这是一个没有任何标题的需求描述。\n第二行补充说明。\n")
        units = parse_markdown(path)
        assert len(units) == 1
        assert units[0].name == "这是一个没有任何标题的需求描述。"

    def test_empty_file_yields_nothing(self, tmp_path):
        path = _write(tmp_path, "   \n\n")
        assert parse_markdown(path) == []
