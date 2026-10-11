"""工作流引擎：任务状态机 + 四 Agent 顺序编排 + 步骤日志。

在后台线程运行（FastAPI BackgroundTasks），前端轮询任务状态即可看到实时进度。
"""
import json
import logging
import threading
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
from app.services import prompt_service
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


def _commit_progress(db, step: StepLog, task: Task, accumulated: list, *,
                     cur: int, total: int, label: str, cases_so_far: int,
                     snapshot_ok: bool) -> bool:
    """提交一条生成进度（顺带「尽力而为」地刷新过程态快照）；返回更新后的 snapshot_ok。

    V1.3-fix 不变式：无论快照落库是否成功，本函数**绝不抛出**，且**绝不**把 Session 留在
    PendingRollback 状态 —— 过程态快照只是 UI 预览，没有杀死整个任务的权力。

    两条分支：
      ① 写「已产出用例」快照 + commit（仅 snapshot_ok 且 accumulated 非空时）；失败即降级：
         rollback、停用快照、清掉脏属性（否则下次 commit 会再次 flush 触发同一错误）；
      ② 纯提交 step.progress —— 即使快照已停用也必须执行，UI 实时进度不能因快照失败而消失。
    """
    progress_text = (f"正在为第 {cur}/{total} 个子任务生成用例"
                     f"（{label}）· 已生成 {cases_so_far} 条")
    step.progress = progress_text

    if snapshot_ok and accumulated:
        try:
            task.partial_cases_json = cases_to_json(accumulated)
            db.commit()
            return snapshot_ok
        except Exception:  # noqa: BLE001  快照是尽力而为，任何失败都不许外泄
            snapshot_ok = False
            logger.warning("过程态快照落库失败，已停用快照（不影响生成）", exc_info=True)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            # rollback 使上面的内存赋值失效；脏属性还会在下次 commit 再次触发 flush → 一并清掉并重挂进度
            task.partial_cases_json = None
            step.progress = progress_text

    try:
        db.commit()
    except Exception:  # noqa: BLE001  纯进度提交自身失败也必须回滚，绝不留 PendingRollback
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    return snapshot_ok


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

        # ---- V1.3-fix：生成模板覆盖必须在**主线程预取** ----
        # 生成模板的加载发生在 generator 的 ThreadPoolExecutor 工作线程里
        # （case_generator._load_template → _build_prompt）；若在工作线程里查询**共享的
        # 请求级 Session**，多个线程会同时发查询 → 抛
        # `InvalidRequestError: This session is provisioning a new connection; concurrent
        # operations are not permitted`（_progress 的 db.commit() 归还连接时窗口最大）。
        # 故在进入并发之前（此处仍是单线程、单 Session），一次性取出该用户全部 gen 模板
        # 覆盖，工作线程只读内存 dict、零 DB 访问。
        tpl_cache = prompt_service.get_gen_overrides(db, task.user_id)

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
                    # V1.3：子任务已并发生成，多个线程会同时回进度。共用一个请求级 Session，
                    # 必须把「写 StepLog 进度 + 累积用例 + commit」整体串行化，否则并发 commit 出错。
                    progress_lock = threading.Lock()
                    accumulated: list = []
                    # V1.3-fix：过程态快照是「尽力而为」的 UI 预览，没有杀死任务的权力。
                    # 首次落库失败（如 TEXT 溢出 DataError）即停用快照，并保证 Session 一定被回滚到
                    # 可用态（progress_cb 的异常会被 generator 的 _emit 吞掉，没人替我们 rollback）。
                    snapshot_ok = True

                    def _progress(cur: int, total: int, unit_name: str,
                                  cases_so_far: int, new_cases=None) -> None:
                        """每个子任务完成 → 更新进度（+ 尽力而为地刷新「已产出用例」快照）。"""
                        nonlocal snapshot_ok
                        with progress_lock:
                            if new_cases:
                                accumulated.extend(new_cases)
                            snapshot_ok = _commit_progress(
                                db, step, task, accumulated,
                                cur=cur, total=total, label=unit_name,
                                cases_so_far=cases_so_far, snapshot_ok=snapshot_ok)

                    # V5.10：任务属主在「提示词」弹窗自定义的生成模板 → 注入优先于内置。
                    # V1.3-fix：本函数运行在 generator 的**工作线程**里，**绝不允许**触碰
                    # 共享 Session —— 只读主线程预取的 `tpl_cache`（key→content），零 DB 访问。
                    # kind 归一化与 prompt_service.get_gen_override 完全一致：
                    # api/action → api，其余 → req。
                    def _template_loader(kind: str, role: str) -> str | None:
                        norm = "api" if kind in ("api", "action") else "req"
                        return tpl_cache.get(prompt_service.gen_key(norm, role))

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
                # 失败可见性（V1.3-fix）：此前这里一句日志都没有 → 排查全靠 DB 反查（「黑盒」主因之一）。
                # logger.exception 落在 except 上下文内，自动带上完整堆栈。
                logger.exception("工作流步骤失败 task=%s step=%s", task_id, name)
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
                    # 失败也是终态：落 finished_at / duration_ms，否则界面显示 0.0s / 空白
                    task.finished_at = utcnow()
                    task.duration_ms = round((time.time() - t0) * 1000, 1)
                    db.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("失败状态落库失败 task=%s step=%s", task_id, name)
                return

        cases = data["cases"]
        cases = ensure_case_ids(cases)  # 补全缺失的用例ID（TC-xxx 序号）
        cases = ensure_compound_titles(cases)  # 标题补齐为 `动作 -> 预期` 复合形式
        task.status = "completed"
        task.cases_count = len(cases)
        task.cases_json = cases_to_json(cases)
        # V1.3：过程态快照功成身退（前端改用正式 cases_json，避免两处数据源并存）
        task.partial_cases_json = None
        task.report_json = json.dumps(data["report"], ensure_ascii=False)
        task.finished_at = utcnow()
        task.duration_ms = round((time.time() - t0) * 1000, 1)
        db.commit()
    finally:
        db.close()
