"""迭代补充功能测试：导入解析、合并去重、增量生成、iterate API。"""
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

import pytest  # noqa: E402
from src.models.testcase import TestCase, CaseType, Priority  # noqa: E402

from app.workflow.agents.import_agent import (  # noqa: E402
    import_cases, import_json, import_xmind, ImportError, _norm_type,
)
from app.workflow.agents.supplement_agent import (  # noqa: E402
    build_existing_summary, run_supplement,
)
from app.workflow.iterate import _parse_cases_json, _merge_dedup  # noqa: E402


# ============ 导入解析器单元测试 ============

class TestImportJson:
    def test_valid_json(self, tmp_path):
        data = [
            {"title": "登录成功", "module": "登录", "case_type": "正向", "priority": "P0",
             "steps": ["输入正确账号", "点击登录"], "expected": "进入首页"},
            {"title": "密码错误", "module": "登录", "case_type": "异常", "priority": "P1",
             "steps": ["输入错误密码"], "expected": "提示错误"},
        ]
        p = tmp_path / "cases.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cases, summary = import_cases(str(p), "json")
        assert len(cases) == 2
        # V2.8-fix4：导入后标题统一补齐为 `动作 -> 预期` 复合形式
        assert cases[0].title == "登录成功 -> 进入首页"
        assert cases[0].case_type == CaseType.POSITIVE
        assert summary["total"] == 2
        assert summary["modules"]["登录"] == 2

    def test_json_not_array(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"not": "array"}), encoding="utf-8")
        with pytest.raises(ImportError, match="顶层必须是用例数组"):
            import_json(str(p))

    def test_json_empty(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(ImportError, match="未解析到有效用例"):
            import_json(str(p))


class TestImportXmind:
    def _make_xmind_xml(self, tmp_path) -> str:
        """构造一个平台导出格式的 xmind（XML）。"""
        content_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" version="2.0">
  <sheet id="s1" timestamp="1">
    <topic id="r1" timestamp="1" structure-class="org.xmind.ui.logic.right">
      <title>测试用例</title>
      <children><topics type="attached">
        <topic id="m1" timestamp="1">
          <title>登录模块</title>
          <children><topics type="attached">
            <topic id="c1" timestamp="1">
              <title>1. 输入正确账号登录->进入首页</title>
              <labels><label>正向用例</label><label>P0</label></labels>
              <children><topics type="attached">
                <topic id="s1" timestamp="1"><title>前置条件</title><labels><label>前置条件</label></labels></topic>
                <topic id="s2" timestamp="1"><title>1. 输入正确账号</title><labels><label>操作步骤</label></labels>
                  <children><topics type="attached">
                    <topic id="e1" timestamp="1"><title>进入首页</title><labels><label>预期结果</label></labels></topic>
                  </topics></children>
                </topic>
              </topics></children>
            </topic>
          </topics></children>
        </topic>
      </topics></children>
    </topic>
    <title>测试用例</title>
  </sheet>
</xmap-content>'''
        p = tmp_path / "test.xmind"
        with zipfile.ZipFile(str(p), "w") as z:
            z.writestr("content.xml", content_xml)
        return str(p)

    def test_xmind_xml_import(self, tmp_path):
        path = self._make_xmind_xml(tmp_path)
        cases, summary = import_cases(path, "xmind")
        assert len(cases) == 1
        assert cases[0].module == "登录模块"
        assert "输入正确账号登录" in cases[0].title
        assert cases[0].case_type == CaseType.POSITIVE
        assert cases[0].priority == Priority.P0
        assert len(cases[0].steps) == 1
        assert cases[0].expected == "进入首页"

    def test_xmind_bad_zip(self, tmp_path):
        p = tmp_path / "bad.xmind"
        p.write_text("not a zip", encoding="utf-8")
        with pytest.raises(ImportError, match="损坏"):
            import_xmind(str(p))


class TestImportXlsx:
    def test_xlsx_import(self, tmp_path):
        pytest.importorskip("openpyxl")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["用例ID", "标题", "模块", "类型", "优先级", "前置条件", "步骤", "预期结果", "测试数据"])
        ws.append(["TC-001", "创建采购单", "采购", "正向", "P0", "已登录", "填写表单\n提交", "创建成功", "金额=100"])
        p = tmp_path / "cases.xlsx"
        wb.save(str(p))
        cases, summary = import_cases(str(p), "xlsx")
        assert len(cases) == 1
        # V2.8-fix4：导入后标题统一补齐为 `动作 -> 预期` 复合形式
        assert cases[0].title == "创建采购单 -> 创建成功"
        assert cases[0].module == "采购"
        assert cases[0].steps == ["填写表单", "提交"]
        assert cases[0].test_data == "金额=100"

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello", encoding="utf-8")
        with pytest.raises(ImportError, match="不支持"):
            import_cases(str(p), "txt")


# ============ 合并去重单元测试 ============

class TestMergeDedup:
    def test_no_duplicate(self):
        base = [TestCase(title="A", module="M", case_type="正向", steps=["s"], expected="e")]
        extra = [TestCase(title="B", module="M", case_type="正向", steps=["s"], expected="e")]
        merged = _merge_dedup(base, extra)
        assert len(merged) == 2

    def test_duplicate_removed(self):
        base = [TestCase(title="A", module="M", case_type="正向", steps=["s"], expected="e")]
        extra = [TestCase(title="A", module="M", case_type="正向", steps=["s2"], expected="e")]
        merged = _merge_dedup(base, extra)
        assert len(merged) == 1

    def test_same_title_diff_expected_kept(self):
        base = [TestCase(title="A", module="M", case_type="正向", steps=["s"], expected="结果一")]
        extra = [TestCase(title="A", module="M", case_type="正向", steps=["s"], expected="结果二完全不同")]
        merged = _merge_dedup(base, extra)
        assert len(merged) == 2

    def test_renumber(self):
        base = [TestCase(title="A", module="M", case_type="正向", steps=["s"], expected="e")]
        extra = [TestCase(title="B", module="M", case_type="正向", steps=["s"], expected="e")]
        merged = _merge_dedup(base, extra)
        assert merged[0].case_id == "TC-001"
        assert merged[1].case_id == "TC-002"


# ============ 增量生成单元测试 ============

class TestSupplement:
    def test_mock_supplement(self):
        existing = [TestCase(title="已有用例", module="登录", case_type="正向", steps=["s"], expected="e")]
        new_cases, summary, details = run_supplement(existing, "补充登录模块的异常场景", client=None)
        assert len(new_cases) > 0
        assert all(c.module == "登录" for c in new_cases)
        # 不应包含与已有完全重复的
        assert not any(c.title == "已有用例" for c in new_cases)

    def test_build_summary(self):
        cases = [
            TestCase(title="用例A", module="登录", case_type="正向", steps=["s"], expected="e"),
            TestCase(title="用例B", module="登录", case_type="异常", steps=["s"], expected="e"),
        ]
        summary = build_existing_summary(cases)
        assert "共 2 条" in summary
        assert "登录" in summary
        assert "用例A" in summary


# ============ iterate API 集成测试 ============

def _create_completed_task(client, token, name="测试任务"):
    """创建一个任务并等待完成（mock 模式），返回 task_id。"""
    r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
        "text": "登录模块测试需求", "kind": "business", "formats": "json", "name": name,
    })
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    # V3.3 任务队列异步化（Worker 池）：轮询等待终态，不再假设 TestClient 同步执行
    deadline = time.time() + 15
    t = {"status": "pending"}
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{tid}", headers={"Authorization": f"Bearer {token}"}).json()
        if t["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert t["status"] == "completed", t.get("steps")
    return tid


class TestIterateAPI:
    def test_iterate_success(self, client, accounts):
        token = accounts["user"]["token"]
        parent_id = _create_completed_task(client, token, "原始任务")
        # 迭代
        r = client.post(f"/api/tasks/{parent_id}/iterate",
                        headers={"Authorization": f"Bearer {token}"},
                        data={"instruction": "补充异常场景"})
        assert r.status_code == 201, r.text
        new_task = r.json()
        assert new_task["parent_task_id"] == parent_id
        assert new_task["status"] in ("pending", "running", "completed")
        # 等待后台 Worker 完成后查详情（异步队列轮询）
        deadline = time.time() + 15
        t = {"status": "pending"}
        while time.time() < deadline:
            t = client.get(f"/api/tasks/{new_task['id']}",
                           headers={"Authorization": f"Bearer {token}"}).json()
            if t["status"] in ("completed", "failed"):
                break
            time.sleep(0.2)
        assert t["status"] == "completed", t.get("steps")
        assert t["cases_count"] > 0
        assert t["parent_task_id"] == parent_id
        # 迭代步骤包含 supplement / merge
        step_names = [s["name"] for s in t["steps"]]
        assert "supplement" in step_names
        assert "merge" in step_names

    def test_iterate_not_owner_404(self, client, accounts):
        admin_token = accounts["admin"]["token"]
        user_token = accounts["user"]["token"]
        parent_id = _create_completed_task(client, admin_token, "admin的任务")
        # 普通用户迭代 admin 的任务 → 404
        r = client.post(f"/api/tasks/{parent_id}/iterate",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data={"instruction": "补充"})
        assert r.status_code == 404

    def test_iterate_running_task_rejected(self, client, accounts):
        token = accounts["user"]["token"]
        # 直接在 DB 里造一个 running 任务
        from app.core.db import SessionLocal
        from app.models.task import Task
        db = SessionLocal()
        try:
            t = Task(id="runtest001", name="运行中任务", kind="business",
                     source_type="text", input_ref="x", status="running",
                     user_id=db.query(__import__("app.models.user", fromlist=["User"]).User).filter_by(username="alice").first().id)
            db.add(t)
            db.commit()
        finally:
            db.close()
        r = client.post("/api/tasks/runtest001/iterate",
                        headers={"Authorization": f"Bearer {token}"},
                        data={"instruction": "补充"})
        assert r.status_code == 400
        assert "仅 completed/failed" in r.json()["detail"]

    def test_iterate_no_instruction_and_file(self, client, accounts):
        token = accounts["user"]["token"]
        parent_id = _create_completed_task(client, token)
        r = client.post(f"/api/tasks/{parent_id}/iterate",
                        headers={"Authorization": f"Bearer {token}"},
                        data={"instruction": ""})
        assert r.status_code == 400

    def test_iterate_version_naming(self, client, accounts):
        token = accounts["user"]["token"]
        parent_id = _create_completed_task(client, token, "版本测试")
        # 第一次迭代
        r1 = client.post(f"/api/tasks/{parent_id}/iterate",
                         headers={"Authorization": f"Bearer {token}"},
                         data={"instruction": "第一次补充"})
        t1 = client.get(f"/api/tasks/{r1.json()['id']}",
                        headers={"Authorization": f"Bearer {token}"}).json()
        assert "(v2)" in t1["name"]
        # 第二次迭代（基于 v2）
        r2 = client.post(f"/api/tasks/{t1['id']}/iterate",
                         headers={"Authorization": f"Bearer {token}"},
                         data={"instruction": "第二次补充"})
        t2 = client.get(f"/api/tasks/{r2.json()['id']}",
                        headers={"Authorization": f"Bearer {token}"}).json()
        assert "(v3)" in t2["name"]
        assert t2["parent_task_id"] == t1["id"]
