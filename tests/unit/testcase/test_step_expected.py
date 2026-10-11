"""V2.7 步骤级预期结果测试：对齐兜底、xmind 导出/导入闭环、旧数据兼容。"""
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

import pytest  # noqa: E402
from src.models.testcase import TestCase, CaseType, Priority, align_step_expectations  # noqa: E402
from src.exporter.xmind_exporter import export_xmind  # noqa: E402
from src.generator.mock_generator import mock_generate  # noqa: E402
from src.models.testcase import RequirementUnit  # noqa: E402

from app.workflow.agents.import_agent import import_xmind, import_cases  # noqa: E402


# ============ 对齐兜底函数 ============

class TestAlignStepExpectations:
    def test_perfect_match(self):
        steps = ["输入账号", "点击登录"]
        se = ["账号输入成功", "进入首页"]
        assert align_step_expectations(steps, se, "进入首页") == se

    def test_single_step_uses_expected(self):
        steps = ["点击提交"]
        assert align_step_expectations(steps, [], "提交成功") == ["提交成功"]

    def test_multi_step_missing_uses_expected_everywhere(self):
        steps = ["步骤A", "步骤B", "步骤C"]
        out = align_step_expectations(steps, [], "整体预期")
        assert out == ["整体预期", "整体预期", "整体预期"]

    def test_length_mismatch_falls_back(self):
        steps = ["a", "b", "c"]
        se = ["x", "y"]  # 数量不齐 → 兜底
        out = align_step_expectations(steps, se, "整体")
        assert out == ["整体", "整体", "整体"]

    def test_empty_strings_in_step_exps_trigger_fallback(self):
        steps = ["a", "b"]
        se = ["", "整体"]  # 含空 → 兜底
        out = align_step_expectations(steps, se, "整体")
        assert out == ["整体", "整体"]

    def test_no_steps_returns_empty(self):
        assert align_step_expectations([], [], "") == []

    def test_resolved_expectations_old_data(self):
        # 旧数据：无 step_expectations → resolved 兜底每步挂整体预期
        c = TestCase(title="T", module="M", case_type="正向",
                     steps=["s1", "s2"], expected="整体预期")
        assert c.resolved_expectations() == ["整体预期", "整体预期"]

    def test_resolved_expectations_new_data(self):
        c = TestCase(title="T", module="M", case_type="正向",
                     steps=["s1", "s2"], step_expectations=["e1", "e2"], expected="整体")
        assert c.resolved_expectations() == ["e1", "e2"]


# ============ xmind 导出：每个步骤都有预期子节点 ============

def _read_xmind_content(xmind_path: str) -> str:
    with zipfile.ZipFile(xmind_path, "r") as zf:
        return zf.read("content.xml").decode("utf-8")


class TestXmindPerStepExpected:
    def _make_case(self, steps, se, expected):
        return TestCase(title="超长输入测试", module="退货退款", case_type=CaseType.BOUNDARY,
                        priority=Priority.P2, pre_condition="用户已登录",
                        steps=steps, step_expectations=se, expected=expected,
                        test_data="500字符文本")

    def test_each_step_has_expected_child(self, tmp_path):
        c = self._make_case(
            ["输入超长文本", "点击提交"],
            ["提示超出限制或被截断", "正常提交"],
            "提示超出限制或被截断，提交成功")
        out = str(tmp_path / "cases.xmind")
        export_xmind([c], out)
        xml = _read_xmind_content(out)
        # 两个操作步骤节点都存在
        assert xml.count('labels>操作步骤') == 2 or xml.count("操作步骤") >= 2
        # 预期结果节点出现两次（每步一个）
        assert xml.count("预期结果") >= 2
        # 逐步预期内容都在
        assert "提示超出限制或被截断" in xml
        assert "正常提交" in xml

    def test_old_data_fallback_each_step(self, tmp_path):
        # 旧数据：无 step_expectations → 每步都挂整体预期
        c = TestCase(title="T", module="M", case_type="正向",
                     steps=["a", "b"], expected="整体预期")
        out = str(tmp_path / "old.xmind")
        export_xmind([c], out)
        xml = _read_xmind_content(out)
        assert xml.count("预期结果") >= 2

    def test_export_import_roundtrip(self, tmp_path):
        """导出新格式 → 再导入 → 逐步预期还原一致。"""
        c = self._make_case(
            ["输入超长文本", "点击提交"],
            ["提示超出限制或被截断", "正常提交"],
            "提示超出限制或被截断，提交成功")
        out = str(tmp_path / "roundtrip.xmind")
        export_xmind([c], out)
        cases = import_xmind(str(out))
        assert len(cases) == 1
        got = cases[0]
        assert got.steps == ["输入超长文本", "点击提交"]
        assert got.resolved_expectations() == ["提示超出限制或被截断", "正常提交"]
        # V2.8-fix4：标题已承载整体预期（`动作 -> 预期`），导入时优先从标题还原，
        # 比旧行为（兜底取最后一步预期）更忠实于原用例（不再是「正常提交」）
        assert got.expected == "提示超出限制或被截断，提交成功"


# ============ mock 生成器逐步预期 ============

class TestMockStepExpectations:
    def test_mock_api_cases_have_per_step_expected(self):
        unit = RequirementUnit(name="订单", kind="api", description="下单接口")
        cases = mock_generate(unit)
        assert len(cases) >= 8
        for c in cases:
            assert len(c.steps) == len(c.resolved_expectations()), c.title
            assert all(e for e in c.resolved_expectations()), c.title

    def test_mock_module_cases_have_per_step_expected(self):
        unit = RequirementUnit(name="采购管理", kind="module", description="采购流程")
        cases = mock_generate(unit)
        assert len(cases) >= 6
        for c in cases:
            assert len(c.steps) == len(c.resolved_expectations()), c.title
            assert all(e for e in c.resolved_expectations()), c.title


# ============ 导入兼容 ============

class TestImportCompat:
    def test_json_with_step_expectations(self, tmp_path):
        data = [{
            "title": "登录", "module": "登录", "case_type": "正向", "priority": "P0",
            "steps": ["输入账号", "点登录"],
            "step_expectations": ["账号输入成功", "进入首页"],
            "expected": "进入首页",
        }]
        p = tmp_path / "c.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cases, _ = import_cases(str(p), "json")
        assert cases[0].resolved_expectations() == ["账号输入成功", "进入首页"]

    def test_json_old_format_fallback(self, tmp_path):
        # 旧格式：无 step_expectations → 兜底
        data = [{
            "title": "登录", "module": "登录", "case_type": "正向",
            "steps": ["a", "b"], "expected": "整体",
        }]
        p = tmp_path / "old.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cases, _ = import_cases(str(p), "json")
        assert cases[0].resolved_expectations() == ["整体", "整体"]

    def test_iterate_parse_old_cases_json(self):
        from app.workflow.iterate import _parse_cases_json
        old = json.dumps([{
            "case_id": "TC-001", "title": "T", "module": "M",
            "case_type": "正向", "priority": "P0",
            "steps": ["s1", "s2"], "expected": "整体预期",
        }], ensure_ascii=False)
        cases = _parse_cases_json(old)
        assert len(cases) == 1
        assert cases[0].resolved_expectations() == ["整体预期", "整体预期"]
