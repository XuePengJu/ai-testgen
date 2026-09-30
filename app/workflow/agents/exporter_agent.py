"""ExporterAgent：导出 xlsx / json / xmind 多格式文件。

入参：cases（用例列表）、output_path（不含扩展名）、formats（格式列表）
出参：(files, output_summary)  files: {fmt: 绝对路径}
"""
from app.services.pipeline_lib import lib_export
from src.models.testcase import TestCase, ensure_case_ids, ensure_compound_titles


def run_exporter(cases: list[TestCase], output_path: str, formats):
    # 导出前补全缺失的用例ID，保证导出文件（xlsx 用例ID 列等）完整
    cases = ensure_case_ids(cases)
    # 导出前把标题补齐为 `动作 -> 预期`（历史任务标题为纯动作形，导出文件也要一致）
    cases = ensure_compound_titles(cases)
    files = lib_export(cases, output_path, formats)
    summary = "导出：" + (", ".join(files.keys()) if files else "无")
    return files, summary, ""
