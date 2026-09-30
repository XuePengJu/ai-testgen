"""V2.7 步骤级预期结果 · 端到端验证脚本。

流程：
1. 用带逐步预期的用例集导出 xmind/xlsx/json
2. 解包 xmind content.xml，验证每个操作步骤下都有预期结果子节点
3. 反向导入 xmind，验证逐步预期还原一致
4. 将导出文件挂到本地库某任务名下，供 curl 下载验证 HTTP 层

用法：python scripts/verify_step_expected.py
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "generator_core"))

from src.models.testcase import TestCase, CaseType, Priority
from src.exporter.xmind_exporter import export_xmind
from src.exporter.excel_exporter import export_excel
from src.exporter.json_exporter import export_json
from app.workflow.agents.import_agent import import_xmind, import_cases

OUT_DIR = Path("/tmp/step_exp_verify")
OUT_DIR.mkdir(exist_ok=True)

CASES = [
    TestCase(title="超长输入字段边界测试", module="退货退款", case_type=CaseType.BOUNDARY, priority=Priority.P2,
             pre_condition="用户已登录",
             steps=["在退货原因字段输入超过最大长度限制（如500字符）", "点击提交"],
             step_expectations=["提示'退货原因长度超出限制'或被截断到允许长度", "正常提交或提示长度超出限制"],
             expected="提示'退货原因长度超出限制'或被截断后正常提交",
             test_data="退货原因:500个字符的重复文本"),
    TestCase(title="并发提交同一订单退货申请防重验证", module="退货退款", case_type=CaseType.BOUNDARY, priority=Priority.P0,
             pre_condition="准备同一用户账号在同一订单无退货记录的状态",
             steps=["使用同一账号同时发起两次退货申请请求", "观察系统返回结果"],
             step_expectations=["两个请求均被受理（或并发排队）", "仅第一个请求成功生成退货单，第二个请求被拦截提示'该订单已有退货申请'，幂等性保证"],
             expected="仅第一个请求成功生成退货单，第二个请求被拦截，幂等性保证",
             test_data="订单号:SO20240101001,并发次数:2"),
]

ok = True


def check(name: str, cond: bool, detail: str = ""):
    global ok
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        ok = False


print("=== 1. 导出 xmind / xlsx / json ===")
export_xmind(CASES, str(OUT_DIR / "step_exp.xmind"))
export_excel(CASES, str(OUT_DIR / "step_exp.xlsx"))
export_json(CASES, str(OUT_DIR / "step_exp.json"))
for f in ("step_exp.xmind", "step_exp.xlsx", "step_exp.json"):
    p = OUT_DIR / f
    check(f"{f} 已生成 ({p.stat().st_size}B)", p.exists() and p.stat().st_size > 0)

print("\n=== 2. 解包 xmind 验证每步有预期 ===")
with zipfile.ZipFile(OUT_DIR / "step_exp.xmind") as zf:
    xml = zf.read("content.xml").decode("utf-8")
# 操作步骤节点数（含编号标题）
import re
step_count = len(re.findall(r'<label>操作步骤</label>', xml))
exp_count = len(re.findall(r'<label>预期结果</label>', xml))
check(f"操作步骤节点数 = {step_count}（预期 ≥4）", step_count >= 4, f"实际 {step_count}")
check(f"预期结果节点数 = {exp_count}（预期 ≥4，每步一个）", exp_count >= 4, f"实际 {exp_count}")
check("步骤1预期内容存在", "提示'退货原因长度超出限制'" in xml)
check("步骤2预期内容存在", "正常提交或提示长度超出限制" in xml)
check("并发用例第2步预期存在", "幂等性保证" in xml)

print("\n=== 3. 反向导入 xmind 还原逐步预期 ===")
cases = import_xmind(str(OUT_DIR / "step_exp.xmind"))
check(f"导入用例数 = {len(cases)}", len(cases) == 2, f"实际 {len(cases)}")
c0 = cases[0]
check("TC1 步骤还原", c0.steps == ["在退货原因字段输入超过最大长度限制（如500字符）", "点击提交"], str(c0.steps))
check("TC1 逐步预期还原",
      c0.resolved_expectations() == ["提示'退货原因长度超出限制'或被截断到允许长度", "正常提交或提示长度超出限制"],
      str(c0.resolved_expectations()))
c1 = cases[1]
check("TC2 第2步预期还原", c1.resolved_expectations()[1].startswith("仅第一个请求成功生成退货单"), c1.resolved_expectations()[1][:30])
check("TC2 expected 兜底非空", bool(c1.expected))

print("\n=== 4. xlsx 含步骤预期列 ===")
from openpyxl import load_workbook
wb = load_workbook(OUT_DIR / "step_exp.xlsx", read_only=True)
ws = wb.active
headers = [c.value for c in next(ws.iter_rows())]
check("xlsx 含「步骤预期」列", "步骤预期" in headers, str(headers))
rows = list(ws.iter_rows(values_only=True))
check("xlsx 步骤预期单元格有内容", len(rows) > 1 and bool(rows[1][7]), f"单元格={str(rows[1][7])[:40] if len(rows) > 1 else 'N/A'}")
wb.close()

print("\n=== 5. 导出文件挂到本地任务（供 curl 下载验证）===")
sys.path.insert(0, str(ROOT))
from app.core.db import SessionLocal
from app.models.task import Task
from app.models.user import User

db = SessionLocal()
admin = db.query(User).filter(User.username == "admin").first()
tid = "verify-step-exp"
t = db.get(Task, tid)
import uuid
if not t:
    t = Task(id=tid, name="步骤预期验证(脚本)", kind="business", source_type="text",
             input_ref="脚本造数", formats="xmind,xlsx,json", status="completed",
             user_id=admin.id if admin else None, cases_count=len(CASES),
             cases_json=json.dumps([c.to_dict() for c in CASES], ensure_ascii=False),
             report_json=json.dumps({"total": len(CASES)}))
    db.add(t)
db.commit()
db.close()
check(f"任务 {tid} 已就绪（admin 可下载验证）", True)

print("\n" + ("✅ 全部通过" if ok else "❌ 存在失败项"))
sys.exit(0 if ok else 1)
