"""解析 Swagger/OpenAPI JSON → 测试单元列表（DBERP 订单对接 API 逆向规格）。"""
import json
from pathlib import Path
from src.models.testcase import RequirementUnit


def parse_swagger(path: str | Path) -> list[RequirementUnit]:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)

    units = []
    paths = spec.get("paths", {})
    for p, methods in paths.items():
        for method, op in methods.items():
            if method.lower() != "post":
                continue
            params = []
            try:
                props = (op["requestBody"]["content"]
                         ["application/x-www-form-urlencoded"]["schema"]["properties"])
                for pn, pv in props.items():
                    if "enum" in pv:
                        params.append(f"{pn} ∈ {pv['enum']}")
                    else:
                        params.append(pn)
            except Exception:
                pass
            units.append(RequirementUnit(
                name=op.get("summary", p),
                kind="api",
                path=p,
                description=op.get("description", ""),
                params=params,
            ))
    return units
