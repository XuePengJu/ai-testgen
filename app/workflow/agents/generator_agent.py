"""GeneratorAgent：按策略调用模型生成测试用例（V2.4 支持注入真实模型；V3.1 多角色）。

入参：units（ParserAgent 产出的测试单元列表）、client（可选，OpenAI 兼容客户端）、
     roles（可选，参与生成的角色 pm/qa/dev，默认 qa）、
     progress_cb（可选，每个测试点完成后的实时进度回调）
出参：(cases, output_summary, details_json)
"""
import json
from collections import Counter
from app.services.pipeline_lib import lib_generate
from src.models.testcase import RequirementUnit, TestCase
from src.generator.case_generator import ROLE_LABELS, parse_roles


def run_generator(units: list[RequirementUnit], client=None, model_desc: str = "",
                  progress_cb=None, roles=None, template_loader=None):
    role_list = parse_roles(roles)
    role_note = "、".join(ROLE_LABELS.get(r, r) for r in role_list)
    meta: dict = {}
    cases: list[TestCase] = lib_generate(units, client=client, progress_cb=progress_cb,
                                         roles=role_list, out_meta=meta,
                                         template_loader=template_loader)
    model_note = model_desc or "未配置可用模型，当前为模拟生成，请到【模型设置】配置真实模型"
    # 统计每个测试点生成多少条用例
    case_count_by_unit = Counter()
    for c in cases:
        case_count_by_unit[c.module] += 1
    unit_stats = [{"name": u.name, "cases_generated": case_count_by_unit.get(u.name, 0)} for u in units]
    summary = f"AI 生成 {len(cases)} 条测试用例（{role_note}视角 · 覆盖正向 / 异常 / 边界 · 模型：{model_note}）"
    # D 修复：调用成功却解析 0 条的测试点必须显性暴露（原来静默吞掉，白耗一次调用且用户无感）
    parse_failed = meta.get("parse_failures") or []
    if parse_failed:
        names = "、".join(f["unit"] for f in parse_failed[:3])
        tail = f" 等 {len(parse_failed)} 个" if len(parse_failed) > 3 else ""
        reasons = sorted({f.get("reason", "unknown") for f in parse_failed})
        summary += (f"；⚠️ {names}{tail}测试点未返回可解析用例"
                    f"（原因：{'/'.join(reasons)}），建议降低思考强度或更换模型后重跑")
    details = {"total_cases": len(cases), "by_unit": unit_stats,
               "model": model_note, "roles": role_list,
               "parse_failed": parse_failed}
    return cases, summary, json.dumps(details, ensure_ascii=False)
