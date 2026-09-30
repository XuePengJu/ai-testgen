"""工作流引擎：任务状态机 + 四 Agent 顺序编排 + 步骤日志。

在后台线程运行（FastAPI BackgroundTasks），前端轮询任务状态即可看到实时进度。
"""
import json
import logging
import time
from pathlib import Path

from sqlalchemy import select, func

from app.core.utils import utcnow

logger = logging.getLogger("workflow.engine")

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.conversation import Conversation
from app.models.task import Task, StepLog
from app.models.user import User
from app.services import llm_service
from app.services import llm_pool
from app.services.pipeline_lib import cases_to_json
from src.models.testcase import ensure_case_ids, ensure_compound_titles, RequirementUnit
from app.workflow.agents import (
    parser_agent, generator_agent, reviewer_agent, exporter_agent,
)

# 步骤编排：(name, title, 函数, 流转数据key)
STEPS = [
    ("parser", "解析规格", parser_agent.run_parser, "units"),
    ("generator", "AI 生成用例", generator_agent.run_generator, "cases"),
    ("reviewer", "质量校验", reviewer_agent.run_reviewer, "report"),
    ("exporter", "导出文件", exporter_agent.run_exporter, "files"),
]


def _prepare_input(task: Task, data_dir: str) -> str:
    """返回供 ParserAgent 读取的文件路径；text 类型落盘为 .md。"""
    base = UPLOAD_DIR / data_dir if data_dir else UPLOAD_DIR
    base.mkdir(parents=True, exist_ok=True)
    if task.source_type == "text":
        p = base / f"{task.id}.md"
        p.write_text(task.input_ref, encoding="utf-8")
        return str(p)
    return str(base / task.input_ref)


def _apply_requirement_naming(db, task: Task, details: str) -> None:
    """解析步骤产出「需求摘要 + 任务名」→ 回写任务名 / 需求摘要 / 会话名。

    - 任务名：前端提交的只是用户原话截断，这里用模型总结的名字覆盖
    - 需求摘要：落到 task.input_summary（供详情页展示）
    - 会话名：仅当该会话下还没有其它任务时改名，避免多任务会话互相覆盖

    全流程防御式：任何异常都吞掉，绝不因为命名失败把任务打成 failed。
    """
    try:
        meta = json.loads(details or "{}")
        if not isinstance(meta, dict):
            return
        title = str(meta.get("title") or "").strip()
        req_summary = str(meta.get("req_summary") or "").strip()
        if not title and not req_summary:
            return

        if req_summary:
            task.input_summary = req_summary
        if title:
            task.name = title
        db.commit()

        conv_id = task.conversation_id
        if title and conv_id:
            siblings = db.execute(
                select(func.count()).select_from(Task).where(
                    Task.conversation_id == conv_id, Task.id != task.id)
            ).scalar_one()
            if siblings == 0:
                conv = db.get(Conversation, conv_id)
                if conv and conv.title != title:
                    conv.title = title
                    db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def run_task(task_id: str) -> None:
    """执行整个工作流。异常时把任务标记为 failed 并记录错误步骤。"""
    db = SessionLocal()
    t0 = time.time()
    try:
        task = db.get(Task, task_id)
        if not task:
            return
        task.status = "running"
        db.commit()

        data_dir = task.user_data_dir(db)
        out_dir = OUTPUT_DIR / data_dir if data_dir else OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        input_path = _prepare_input(task, data_dir)

        # ---- V2.4：模型解析（用户配置 > 平台默认 > 服务器 .env > mock 兜底） ----
        owner = db.get(User, task.user_id) if task.user_id else None
        eff = llm_service.resolve_effective(db, owner)
        text_cfg, vision_cfg = eff["text"], eff["vision"]
        # V5.0 P1：模型池优先（多条候选，撞限流自动切换）；池空回落单条生效配置
        llm_client = llm_pool.build_client(db, owner, "text")
        model_desc = llm_pool.describe_model(db, owner, "text") or "未配置可用模型（模拟生成）"

        # ---- V2.4：两段式视觉理解（business 输入里的图片引用） ----
        vision_note = ""
        if task.kind == "business":
            try:
                raw = Path(input_path).read_text(encoding="utf-8")
                refs = llm_service.extract_image_refs(raw)
                if refs:
                    # V5.0 P1：视觉槽同样走模型池（池空回落单条生效配置）
                    vclient = llm_pool.build_client(db, owner, "vision")
                    if vclient is not None:
                        try:
                            new_text, n = llm_service.vision_enrich(raw, vclient)
                            Path(input_path).write_text(new_text, encoding="utf-8")
                            vision_note = f"；视觉模型解析 {n} 张截图"
                        except llm_service.LLMError as e:
                            vision_note = f"；截图解析失败（{e}），已忽略图片"
                    else:
                        vision_note = f"；检测到 {len(refs)} 张图片，未配置图像识别模型已忽略"
            except (OSError, UnicodeDecodeError):
                pass    # 输入不是文本文件（理论不达），跳过

        data: dict = {}

        # 断点续跑：retry 时只保留 parser 的 completed StepLog，从中恢复 units 后跳过
        completed_steps = {
            s.name: s for s in db.execute(
                select(StepLog).where(StepLog.task_id == task_id, StepLog.status == "completed")
            ).scalars().all()
        }

        for name, title, fn, key in STEPS:
            # 断点续跑：parser 已完成 → 从 StepLog.input_summary 恢复 units，跳过执行
            if name == "parser" and name in completed_steps:
                try:
                    meta = json.loads(completed_steps[name].input_summary or "{}")
                    raw_units = meta.get("units", [])
                    # JSON 反序列化后是 dict 列表，需转回 RequirementUnit 对象
                    data["units"] = [
                        RequirementUnit(**u) if isinstance(u, dict) else u
                        for u in raw_units
                    ]
                    logger.info("断点续跑：跳过 parser，恢复 %d 个测试点", len(data.get("units", [])))
                    continue
                except (json.JSONDecodeError, ValueError, TypeError):
                    logger.warning("断点续跑：parser input_summary 解析失败，重新执行 parser")

            step = StepLog(
                task_id=task_id, name=name, title=title,
                status="running", started_at=utcnow(),
            )
            db.add(step)
            db.commit()
            s0 = time.time()
            try:
                if name == "parser":
                    out, summary, details = fn(input_path, task.kind, llm_client)
                    if vision_note:
                        summary = f"{summary}{vision_note}"
                elif name == "generator":
                    def _progress(cur: int, total: int, unit_name: str, cases_so_far: int) -> None:
                        """每个测试点完成 → 更新 StepLog.progress（前端轮询实时可见）。"""
                        step.progress = (
                            f"正在为第 {cur}/{total} 个子任务生成用例"
                            f"（{unit_name}）· 已生成 {cases_so_far} 条"
                        )
                        db.commit()

                    # V5.10：任务属主在「提示词」弹窗自定义的生成模板 → 注入优先于内置
                    def _template_loader(kind: str, role: str) -> str | None:
                        from app.services import prompt_service
                        return prompt_service.get_gen_override(db, task.user_id, kind, role)

                    out, summary, details = fn(
                        data["units"], llm_client, model_desc, _progress,
                        getattr(task, "roles", None), template_loader=_template_loader)
                elif name == "reviewer":
                    out, summary, details = fn(data["cases"], llm_client)
                else:  # exporter
                    fmts = [f.strip() for f in task.formats.split(",") if f.strip()]
                    out, summary, details = fn(data["cases"], str(out_dir / task.id), fmts)
                data[key] = out
                step.status = "completed"
                step.output_summary = summary
                step.input_summary = details or ""
                step.finished_at = utcnow()
                step.duration_ms = round((time.time() - s0) * 1000, 1)
                db.commit()
                # 解析步骤顺带回写任务名 / 需求摘要 / 会话名（失败不影响主流程）
                if name == "parser":
                    _apply_requirement_naming(db, task, details)
            except Exception as e:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                step.status = "failed"
                step.error = str(e)
                step.finished_at = utcnow()
                step.duration_ms = round((time.time() - s0) * 1000, 1)
                try:
                    db.commit()
                    task.status = "failed"
                    db.commit()
                except Exception:  # noqa: BLE001
                    pass
                return

        cases = data["cases"]
        cases = ensure_case_ids(cases)  # 补全缺失的用例ID（TC-xxx 序号）
        cases = ensure_compound_titles(cases)  # 标题补齐为 `动作 -> 预期` 复合形式
        task.status = "completed"
        task.cases_count = len(cases)
        task.cases_json = cases_to_json(cases)
        task.report_json = json.dumps(data["report"], ensure_ascii=False)
        task.finished_at = utcnow()
        task.duration_ms = round((time.time() - t0) * 1000, 1)
        db.commit()
    finally:
        db.close()
