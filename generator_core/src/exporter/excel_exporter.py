"""Excel 导出（openpyxl，缺失时自动降级为 CSV，保证零依赖可运行）。"""
import csv
from src.models.testcase import TestCase

HEADERS = ["用例ID", "标题", "模块", "类型", "优先级", "前置条件", "步骤", "步骤预期", "预期结果", "测试数据"]


def export_excel(cases: list[TestCase], out_path: str) -> str:
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "测试用例"
        ws.append(HEADERS)
        for c in cases:
            ws.append(c.to_row())
        wb.save(out_path)
        return "xlsx"
    except ImportError:
        csv_path = str(out_path).rsplit(".", 1)[0] + ".csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(HEADERS)
            for c in cases:
                w.writerow(c.to_row())
        return "csv"
