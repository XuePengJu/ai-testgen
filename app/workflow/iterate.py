"""迭代流水线：对已完成任务进行补充/完善，生成新版本子任务。

流程：
1. 读取原任务用例（cases_json）作为基础
2. 若上传了用例文件 → 导入解析，与基础用例合并
3. 增量生成（supplement_agent）：基于补充指令生成新用例
4. 合并去重：基础用例 + 增量用例，按 (标题, 模块, 类型, 预期前20字) 去重，重编号
5. 质量校验（复用 reviewer_agent）
6. 导出文件（复用 exporter_agent）
7. 新建子任务（parent_task_id 指向原任务），保存结果

与 run_task 的区别：
- 不经过 parser（不需要解析需求规格）
- 新建子任务而非更新原任务
- 步骤日志记录迭代专属步骤
"""
import json
import os
import time

from sqlalchemy import select

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.conversation import Message
from app.models.task import Task, StepLog
from app.models.user import User
from app.services import llm_service
from app.services import llm_pool
from app.services.pipeline_lib import cases_to_json
from app.workflow.agents.import_agent import import_cases, ImportError
from app.workflow.agents.reviewer_agent import run_reviewer
from app.workflow.agents.supplement_agent import run_supplement
from app.workflow.agents.exporter_agent import run_exporter
from src.models.testcase import (
    TestCase,
    align_step_expectations,
    ensure_case_ids,
    ensure_compound_titles,
    strip_title_expected,
)


def _parse_cases_json(cases_json: str | None) -> list[TestCase]:
    """从 Task.cases_json 反序列化为 TestCase 列表。

    兼容旧数据：库中无 step_expectations 字段时自动对齐兜底（每步挂整体预期）。
    """
    if not cases_json:
        return []
    try:
        arr = json.loads(cases_json)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(arr, list):
        return []
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            steps = item.get("steps") or []
            expected = str(item.get("expected") or "")
            se = item.get("step_expectations") or []
            out.append(TestCase(
                case_id=str(item.get("case_id") or ""),
                title=str(item.get("title") or "未命名用例"),
                module=str(item.get("module") or ""),
                case_type=item.get("case_type") or "正向",
                priority=item.get("priority") or "P1",
                pre_condition=str(item.get("pre_condition") or ""),
                steps=steps,
                step_expectations=align_step_expectations(steps, se, expected),
                expected=expected,
                test_data=item.get("test_data"),
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _merge_dedup(base: list[TestCase], extra: list[TestCase]) -> list[TestCase]:
    """合并两组用例并去重，去重键 = (动作标题, 模块, 类型, 预期前20字)。

    标题已复合为 `动作 -> 预期`，去重时剥掉 `-> 预期` 片段，避免预期改一个字
    就被当成全新用例（旧用例来自库、新增用例来自模型，两侧形式可能不一致）。
    """
    seen: set[tuple] = set()
    merged: list[TestCase] = []
    for c in base + extra:
        key = (
            strip_title_expected(c.title).strip(),
            c.module.strip(),
            c.case_type.value,
            (c.expected or "")[:20],
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)
    # 重编号
    for i, c in enumerate(merged, 1):
        c.case_id = f"TC-{i:03d}"
    return merged


def _add_step(db, task_id: str, name: str, title: str) -> StepLog:
    step = StepLog(task_id=task_id, name=name, title=title, status="running", started_at=utcnow())
    db.add(step)
    db.commit()
    return step


def _finish_step(db, step: StepLog, status: str, summary: str = "", details: str = "", error: str = "", duration: float = 0):
    step.status = status
    step.output_summary = summary
    step.input_summary = details
    step.error = error
    step.finished_at = utcnow()
    step.duration_ms = round(duration, 1)
    db.commit()


def run_iterate(
    new_task_id: str,
    parent_task_id: str,
    instruction: str,
    uploaded_path: str | None = None,
    uploaded_ext: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """执行迭代流水线，填充已预创建的新任务。在后台线程运行。

    新任务由 API 层预创建（status=pending），本函数负责执行流程并更新结果。
    """
    db = SessionLocal()
    t0 = time.time()
    try:
        new_task = db.get(Task, new_task_id)
        parent = db.get(Task, parent_task_id)
        if not new_task or not parent:
            return

        new_task.status = "running"
        db.commit()

        # ---- 解析模型配置 ----
        owner = db.get(User, parent.user_id) if parent.user_id else None
        eff = llm_service.resolve_effective(db, owner)
        text_cfg = eff["text"]
        # V5.0 P1：模型池优先（多条候选，撞限流自动切换）；池空回落单条生效配置
        llm_client = llm_pool.build_client(db, owner, "text")
        model_desc = llm_pool.describe_model(db, owner, "text") or "未配置可用模型（模拟生成）"

        # ---- 准备输出目录 + 更新版本号 ----
        data_dir = parent.user_data_dir(db)
        out_dir = OUTPUT_DIR / data_dir if data_dir else OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        # 计算版本号：找 root parent，统计整个迭代链上的任务总数 + 1
        root = parent
        guard = 0
        while root.parent_task_id and guard < 20:
            root = db.get(Task, root.parent_task_id) or root
            guard += 1
        # BFS 统计 root 链上所有任务数（排除当前新任务自己）
        chain_count = 1  # root 本身
        to_check = [root.id]
        while to_check:
            pid = to_check.pop()
            children = db.execute(
                select(Task).where(Task.parent_task_id == pid, Task.id != new_task_id)
            ).scalars().all()
            chain_count += len(children)
            to_check.extend([c.id for c in children])
        version = chain_count + 1  # 新任务是第 chain_count+1 个版本
        new_task.name = f"{root.name} (v{version})"
        db.commit()

        # ---- 步骤1：基础用例准备 ----
        step1 = _add_step(db, new_task_id, "base_load", "加载基础用例")
        s0 = time.time()
        base_cases = _parse_cases_json(parent.cases_json)
        imported_summary = ""
        if uploaded_path and uploaded_ext:
            try:
                imported, imp_summary = import_cases(uploaded_path, uploaded_ext)
                base_cases = _merge_dedup(base_cases, imported)
                imported_summary = f"；导入文件 {imp_summary['total']} 条"
            except ImportError as e:
                _finish_step(db, step1, "failed", error=str(e), duration=time.time() - s0)
                new_task.status = "failed"
                db.commit()
                return
        _finish_step(db, step1, "completed",
                     summary=f"基础用例 {len(base_cases)} 条{imported_summary}",
                     details=json.dumps({"base_count": len(base_cases), "imported": imported_summary}, ensure_ascii=False),
                     duration=time.time() - s0)

        # ---- 步骤2：增量生成 ----
        step2 = _add_step(db, new_task_id, "supplement", "增量生成用例")
        s0 = time.time()
        try:
            new_cases, sup_summary, sup_details = run_supplement(
                base_cases, instruction, client=llm_client, model_desc=model_desc)
            _finish_step(db, step2, "completed", summary=sup_summary, details=sup_details,
                         duration=time.time() - s0)
        except Exception as e:  # noqa: BLE001
            _finish_step(db, step2, "failed", error=str(e), duration=time.time() - s0)
            new_task.status = "failed"
            db.commit()
            return

        # ---- 步骤3：合并去重 ----
        step3 = _add_step(db, new_task_id, "merge", "合并去重")
        s0 = time.time()
        merged = _merge_dedup(base_cases, new_cases)
        merged = ensure_compound_titles(merged)  # 标题补齐为 `动作 -> 预期`（供评审/导出/落库统一使用）
        added = len(merged) - len(base_cases)
        _finish_step(db, step3, "completed",
                     summary=f"合并后共 {len(merged)} 条（新增 {added} 条）",
                     details=json.dumps({"before": len(base_cases), "after": len(merged), "added": added}, ensure_ascii=False),
                     duration=time.time() - s0)

        # ---- 步骤4：质量校验 ----
        step4 = _add_step(db, new_task_id, "reviewer", "质量校验")
        s0 = time.time()
        try:
            report, rev_summary, rev_details = run_reviewer(merged, client=llm_client)
            _finish_step(db, step4, "completed", summary=rev_summary, details=rev_details,
                         duration=time.time() - s0)
        except Exception as e:  # noqa: BLE001
            report = {"total": len(merged), "quality_pass": False, "error": str(e)}
            _finish_step(db, step4, "completed", summary=f"质量校验跳过（{e}）",
                         duration=time.time() - s0)

        # ---- 步骤5：导出文件 ----
        step5 = _add_step(db, new_task_id, "exporter", "导出文件")
        s0 = time.time()
        fmts = [f.strip() for f in (parent.formats or "xlsx,json").split(",") if f.strip()]
        try:
            files, exp_summary, _ = run_exporter(merged, str(out_dir / new_task_id), fmts)
            _finish_step(db, step5, "completed", summary=exp_summary, duration=time.time() - s0)
        except Exception as e:  # noqa: BLE001
            _finish_step(db, step5, "failed", error=str(e), duration=time.time() - s0)
            new_task.status = "failed"
            db.commit()
            return

        # ---- 落库 ----
        new_task.status = "completed"
        new_task.cases_count = len(merged)
        merged = ensure_case_ids(merged)  # 补全缺失的用例ID（TC-xxx 序号）
        new_task.cases_json = cases_to_json(merged)
        new_task.report_json = json.dumps(report, ensure_ascii=False, default=str)
        new_task.finished_at = utcnow()
        new_task.duration_ms = round((time.time() - t0) * 1000, 1)
        db.commit()

        # 回填会话最后一条 assistant 消息的 task_id（聊天流回放时渲染节点卡）
        if new_task.conversation_id:
            last_msg = db.execute(
            select(Message)
            .where(Message.conversation_id == new_task.conversation_id,
                   Message.role == "assistant",
                   Message.task_id.is_(None))
            .order_by(Message.id.desc())
            .limit(1)
        ).scalar_one_or_none()
            if last_msg:
                last_msg.task_id = new_task_id
                db.commit()

        return
    finally:
        db.close()
