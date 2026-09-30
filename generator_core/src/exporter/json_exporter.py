"""JSON 导出（供后续测试平台/CI 消费）。"""
import json
from src.models.testcase import TestCase


def export_json(cases: list[TestCase], out_path: str) -> None:
    data = [c.to_dict() for c in cases]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
