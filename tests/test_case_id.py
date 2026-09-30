"""用例ID补全测试：ensure_case_ids 保证每条用例都有 TC 编号。"""
import sys
sys.path.insert(0, "generator_core/src")

from models.testcase import TestCase, ensure_case_ids


def _mk(cid=""):
    return TestCase(case_id=cid, title="t", module="m", case_type="正向",
                    priority="P1", steps=["s1"], expected="e")


def test_all_missing_starts_from_001():
    cases = [_mk(""), _mk(""), _mk("")]
    ensure_case_ids(cases)
    assert [c.case_id for c in cases] == ["TC-001", "TC-002", "TC-003"]


def test_increments_after_existing():
    cases = [_mk("TC-001"), _mk("TC-002"), _mk("TC-010"), _mk(""), _mk("")]
    ensure_case_ids(cases)
    assert [c.case_id for c in cases] == ["TC-001", "TC-002", "TC-010", "TC-011", "TC-012"]


def test_keeps_existing_ids_untouched():
    cases = [_mk("TC-007"), _mk("CASE-A"), _mk("")]
    ensure_case_ids(cases)
    assert [c.case_id for c in cases] == ["TC-007", "CASE-A", "TC-008"]


def test_supports_tc_without_padding_and_tc_prefix():
    cases = [_mk("TC1"), _mk("TC-2"), _mk("")]
    ensure_case_ids(cases)
    assert [c.case_id for c in cases] == ["TC1", "TC-2", "TC-003"]


def test_supports_dict_input():
    cases = [{"case_id": "", "title": "a"}, {"case_id": "TC-005", "title": "b"}, {"case_id": None, "title": "c"}]
    ensure_case_ids(cases)
    assert [c["case_id"] for c in cases] == ["TC-006", "TC-005", "TC-007"]


def test_empty_list_ok():
    assert ensure_case_ids([]) == []
