"""V2.8-fix4 复合标题测试：用例标题统一为 `动作概括 -> 预期结果`。

覆盖：预期去套话压缩、幂等拼接（不重复拼成 A->B->B）、拆分/剥离、
批量补齐（TestCase 对象与 dict 双形态）、xmind 导出标题不双重拼接。
"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from src.models.testcase import (  # noqa: E402
    TestCase,
    CaseType,
    Priority,
    compose_title,
    condense_expected,
    ensure_compound_titles,
    split_title,
    strip_title_expected,
)

BOILER = "上架状态下商品退款流程正常完成，退款单状态正确，库存与财务预占金额一致"
CONDENSED = "退款单状态正确，库存与财务预占金额一致"


# ============ 预期压缩：剥掉无信息量套话 ============

class TestCondenseExpected:
    def test_strip_leading_boilerplate(self):
        assert condense_expected(BOILER) == CONDENSED

    def test_keeps_substantive_clause(self):
        assert condense_expected("订单状态变为已支付") == "订单状态变为已支付"

    def test_single_boilerplate_clause_not_stripped_to_empty(self):
        assert condense_expected("操作成功") == "操作成功"

    def test_all_boilerplate_keeps_last_clause(self):
        assert condense_expected("操作成功，功能正常") == "功能正常"

    def test_no_abnormal_clause(self):
        assert condense_expected("无异常，单据状态为已审核") == "单据状态为已审核"

    def test_trailing_period_removed(self):
        assert condense_expected("退款单状态正确。") == "退款单状态正确"

    def test_empty(self):
        assert condense_expected("") == ""
        assert condense_expected(None) == ""


# ============ 拼接：幂等，绝不出现 A->B->B ============

class TestComposeTitle:
    def test_basic(self):
        assert compose_title("上架商品后发起退款流程", BOILER) == (
            "上架商品后发起退款流程 -> " + CONDENSED
        )

    def test_idempotent_ascii_arrow(self):
        assert compose_title("A -> B", BOILER) == "A -> B"

    def test_idempotent_fullwidth_arrow(self):
        assert compose_title("A → B", BOILER) == "A → B"

    def test_no_expected_returns_action_only(self):
        assert compose_title("只有动作", "") == "只有动作"

    def test_empty_title_falls_back(self):
        assert compose_title("", "") == "未命名用例"


# ============ 拆分：去重键取动作部分 ============

class TestSplitTitle:
    def test_split_compound(self):
        assert split_title("A -> B") == ("A", "B")

    def test_split_without_spaces(self):
        assert split_title("A->B") == ("A", "B")

    def test_split_plain_title(self):
        assert split_title("纯动作") == ("纯动作", "")

    def test_strip_expected(self):
        assert strip_title_expected("A -> B") == "A"
        assert strip_title_expected("纯动作") == "纯动作"


# ============ 批量补齐：兼容对象与 dict，只改非空标题 ============

class TestEnsureCompoundTitles:
    def test_objects(self):
        cases = [
            TestCase(title="动作X", module="M", case_type=CaseType.POSITIVE,
                     priority=Priority.P0, steps=["s"], expected="结果Y"),
            TestCase(title="动作Z -> 已拼", module="M", case_type=CaseType.POSITIVE,
                     priority=Priority.P0, steps=["s"], expected="结果Y"),
        ]
        ensure_compound_titles(cases)
        assert cases[0].title == "动作X -> 结果Y"
        assert cases[1].title == "动作Z -> 已拼"  # 幂等

    def test_dicts(self):
        cases = [{"title": "动作X", "expected": "结果Y"}]
        ensure_compound_titles(cases)
        assert cases[0]["title"] == "动作X -> 结果Y"

    def test_dict_missing_expected_untouched(self):
        cases = [{"title": "动作X"}]
        ensure_compound_titles(cases)
        assert cases[0]["title"] == "动作X"

    def test_empty_title_not_invented(self):
        cases = [{"title": "", "expected": "结果Y"}]
        ensure_compound_titles(cases)
        assert cases[0]["title"] == ""

    def test_empty_list(self):
        assert ensure_compound_titles([]) == []


# ============ xmind 导出：标题与数据层一致且不双重拼接 ============

class TestXmindExportTitle:
    NS = "{urn:xmind:xmap:xmlns:content:2.0}"

    def _case_title(self, case, tmp_path) -> str:
        """导出后取出用例节点标题（XML 会把 `>` 转义成 `&gt;`，故按节点解析而非字符串匹配）。"""
        import xml.etree.ElementTree as ET
        from src.exporter.xmind_exporter import export_xmind
        out = str(tmp_path / "t.xmind")
        export_xmind([case], out)
        with zipfile.ZipFile(out) as zf:
            raw = zf.read("content.xml").decode("utf-8")
        root_el = ET.fromstring(raw)
        for topic in root_el.iter(f"{self.NS}topic"):
            labels = [(lb.text or "") for lb in topic.findall(f"{self.NS}labels/{self.NS}label")]
            if any(x.endswith("用例") for x in labels):
                el = topic.find(f"{self.NS}title")
                return (el.text or "") if el is not None else ""
        return ""

    def test_compound_title_not_double_joined(self, tmp_path):
        c = TestCase(title="动作X -> 结果Y", module="M", case_type=CaseType.POSITIVE,
                     priority=Priority.P0, steps=["s"], expected="结果Y")
        title = self._case_title(c, tmp_path)
        assert title.endswith("动作X -> 结果Y")
        assert title.count("->") == 1  # 绝不出 A->B->B

    def test_plain_title_gets_expected_appended(self, tmp_path):
        """旧数据（纯动作标题）→ 导出时按整体预期拼接。"""
        c = TestCase(title="上架商品后发起退款流程", module="退货退款",
                     case_type=CaseType.POSITIVE, priority=Priority.P0,
                     steps=["提交退款"], expected=BOILER)
        title = self._case_title(c, tmp_path)
        assert title.endswith("上架商品后发起退款流程 -> " + CONDENSED)
