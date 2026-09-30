"""任务管理 REST 端点（V2：登录 + 数据隔离）。"""
import json
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import task_queue
from app.core.config import UPLOAD_DIR, OUTPUT_DIR, GUEST_MAX_TASKS
from app.core.db import get_db
from app.models.conversation import Conversation, Message
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.task import TaskOut, StepLogOut, TaskPatchIn
from app.services.doc_extract import SUPPORTED_EXTS
from app.workflow.iterate import run_iterate
from src.models.testcase import ensure_case_ids, ensure_compound_titles
from src.generator.case_generator import parse_roles

router = APIRouter()

# 支持的生成类任务类型（裁剪后仅保留文档驱动的用例生成）
VALID_KINDS: tuple[str, ...] = ("api", "business")

# 接口文档（swagger）额外接受的文本格式：仅读文件、不走 doc_extract
_API_EXTRA_EXTS: tuple[str, ...] = (".json", ".yaml", ".yml")


def _allowed_exts(kind: str) -> tuple[str, ...]:
    """按任务类型返回允许上传的扩展名（与前端 accept 保持一致）。"""
    return SUPPORTED_EXTS + _API_EXTRA_EXTS if kind == "api" else SUPPORTED_EXTS


def _fmt_exts(exts: tuple[str, ...]) -> str:
    return " / ".join(e.lstrip(".") for e in exts)


def _parse_cases(cases_json: str | None) -> list[dict]:
    """cases_json 字段是 Task 模型里的 Text 列，存储原始 JSON 字符串。

    返回结构化用例列表。解析失败时兜底返回 []，不让单条脏数据把整个详情接口炸掉。

    读取侧统一补齐两件事：
    1. 缺失的用例ID按现有最大 TC-序号递增补全（存量任务兜底，前端表格/搜索/导图/会话回填一致）；
    2. 标题补齐为 `动作 -> 预期`：历史任务的 title 是纯动作形式，不补齐看不到预期信息。
    """
    if not cases_json:
        return []
    try:
        obj = json.loads(cases_json)
        if not isinstance(obj, list):
            return []
        obj = ensure_case_ids(obj)  # 读取侧补全缺失用例ID：存量任务兜底，TC-序号接续递增
        return ensure_compound_titles(obj)  # 读取侧补齐 `动作 -> 预期` 复合标题
    except (ValueError, TypeError):
        return []


def _resolve_conversation(db: Session, task: Task) -> str | None:
    """兜底解析任务所属会话 id（历史任务 conversation_id 为空时使用）。

    解析顺序：
    1. `task.conversation_id` 已有值 → 直接返回；
    2. 按 `messages.task_id == task.id` 反查所属会话 → 回填任务（覆盖「由会话创建但未回写」的历史数据）；
    3. 仍查不到 → 新建会话并绑定，保证详情页「继续优化」永远有处可跳（决策点 2 选项 A）。

    会落库（回填 / 新建会话），仅详情接口调用，列表接口不触发以避免 N+1。
    """
    if task.conversation_id:
        return task.conversation_id

    hit = db.execute(
        select(Message.conversation_id)
        .where(Message.task_id == task.id)
        .order_by(Message.id.asc())
    ).first()
    if hit and hit[0]:
        task.conversation_id = hit[0]
        db.commit()
        return hit[0]

    conv = Conversation(id=uuid.uuid4().hex[:12], user_id=task.user_id,
                        title=(task.name or "任务会话")[:255])
    db.add(conv)
    db.flush()  # 先落会话行再回填 task.conversation_id（MySQL 外键约束）
    task.conversation_id = conv.id
    db.commit()
    return conv.id


def _has_auto_script(db: Session, task: Task) -> bool:
    """M2 执行引擎：任务目录 auto/ 下是否已生成自动化脚本（存在 test_*.py）。

    列表/详情都会调用，必须轻量：只 glob 一次，不做任何 IO 之外的解析。
    """
    auto_dir = OUTPUT_DIR / task.user_data_dir(db) / task.id / "auto"
    return auto_dir.is_dir() and any(auto_dir.glob("test_*.py"))


def _to_out(db: Session, task: Task, include_cases: bool = False) -> TaskOut:
    steps = db.execute(
        select(StepLog).where(StepLog.task_id == task.id).order_by(StepLog.id)
    ).scalars().all()
    return TaskOut(
        id=task.id, name=task.name, kind=task.kind, source_type=task.source_type,
        status=task.status, review_status=task.review_status or "draft",
        cases_count=task.cases_count, duration_ms=task.duration_ms,
        formats=task.formats, roles=task.roles or '["qa"]',
        category_id=task.category_id,
        parent_task_id=task.parent_task_id,
        has_auto=_has_auto_script(db, task),
        conversation_id=task.conversation_id,
        created_at=task.created_at, finished_at=task.finished_at,
        steps=[
            StepLogOut(
                name=s.name, title=s.title, status=s.status,
                progress=s.progress, duration_ms=s.duration_ms,
                input_summary=s.input_summary,
                output_summary=s.output_summary, error=s.error,
            )
            for s in steps
        ],
        # 仅详情接口为 True：77 条用例 ≈ 几十 KB，列表页不背这个 payload
        cases=_parse_cases(task.cases_json) if include_cases else [],
    )


def _own_task(db: Session, task_id: str, user: User) -> Task:
    """非本人且非 admin → 404（不暴露存在性）。"""
    task = db.get(Task, task_id)
    if not task or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile | None = File(None),
    text: str = Form(""),
    kind: str = Form("business"),
    formats: str = Form("xlsx,json"),
    roles: str = Form("qa"),
    name: str = Form(""),
    conversation_id: str = Form(""),
):
    """提交一个测试用例生成任务。可上传规格文件或粘贴文本。"""
    # kind 校验：仅接受生成类任务类型（探索/全链路已裁剪）
    if kind not in VALID_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的任务类型 {kind}（支持：{'、'.join(VALID_KINDS)}）")

    # 访客任务上限（防滥用）
    if user.role == "guest":
        count = db.execute(
            select(func.count()).select_from(Task).where(
                Task.user_id == user.id, Task.is_sample.is_(False)
            )
        ).scalar_one()
        if count >= GUEST_MAX_TASKS:
            raise HTTPException(status_code=429, detail=f"访客最多 {GUEST_MAX_TASKS} 个任务，注册后无限制")

    task_id = uuid.uuid4().hex[:12]
    source_type = "file" if file else "text"
    input_ref = ""

    # 会话关联：传了 conversation_id → 校验存在且属于当前用户（越权一律 404，不暴露存在性）
    conv_obj = None
    if conversation_id.strip():
        conv_obj = db.get(Conversation, conversation_id.strip())
        if not conv_obj or conv_obj.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在")
        conversation_id = conv_obj.id

    # 文件落 data_dir 目录（用户隔离）
    user_dir = UPLOAD_DIR / user.data_dir
    user_dir.mkdir(parents=True, exist_ok=True)
    if file:
        ext = os.path.splitext(file.filename or "")[1].lower()
        allowed = _allowed_exts(kind)
        # 提前拦住解析不了的格式：否则会一路走到解析步骤才崩，用户拿不到有效提示
        if ext not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"暂不支持 {ext or '该'} 格式，请上传 {_fmt_exts(allowed)} 文件",
            )
        fname = f"{task_id}{ext}"
        (user_dir / fname).write_bytes(await file.read())
        input_ref = fname
    elif text.strip():
        input_ref = text
    else:
        raise HTTPException(status_code=400, detail="file 与 text 至少提供一个")

    (OUTPUT_DIR / user.data_dir).mkdir(parents=True, exist_ok=True)

    task = Task(
        id=task_id,
        name=name or f"任务-{task_id}",
        kind=kind,
        source_type=source_type,
        input_ref=input_ref,
        formats=formats,
        roles=json.dumps(parse_roles(roles), ensure_ascii=False),
        status="pending",
        user_id=user.id,
        conversation_id=conversation_id or None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 回填该会话下最后一条 assistant 消息的 task_id（聊天流回放时据此渲染节点/用例卡）。
    # e2e 自动建的会话里，占位消息 task_id=None 会在此被回填新任务 id，无需单独处理。
    if conversation_id:
        last_msg = db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id,
                   Message.role == "assistant",
                   Message.task_id.is_(None))
            .order_by(Message.id.desc())
        ).scalars().first()
        if last_msg:
            last_msg.task_id = task_id
            db.commit()

    task_queue.enqueue(task_id)
    return _to_out(db, task)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db),
               user: User = Depends(get_current_user),
               all: bool = False):
    """只返回当前用户的任务；admin 可带 ?all=true 看全部。"""
    stmt = select(Task)
    if not (user.role == "admin" and all):
        stmt = stmt.where(Task.user_id == user.id)
    tasks = db.execute(stmt.order_by(Task.created_at.desc())).scalars().all()
    return [_to_out(db, t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """任务详情：默认带上结构化用例（用于网页思维导图 + 测试用例 tab）。

    额外解析并回填 conversation_id（历史任务兜底），供详情页「继续优化」跳回会话。
    """
    task = _own_task(db, task_id, user)
    _resolve_conversation(db, task)
    return _to_out(db, task, include_cases=True)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def patch_task(task_id: str,
               payload: TaskPatchIn,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """V5.5 用例库资产化：更新用例集元数据（名称 / 评审状态）。

    - 归类（category_id）走既有 PUT /categories/move-task/{task_id}，本接口不重复提供
    - review_status 仅 draft/reviewed；生成过程状态（status）由工作流管理，不可手工改
    """
    t = _own_task(db, task_id, user)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        t.name = name[:255]
    if payload.review_status is not None:
        if payload.review_status not in ("draft", "reviewed"):
            raise HTTPException(status_code=400, detail="review_status 仅支持 draft / reviewed")
        t.review_status = payload.review_status
    db.commit()
    db.refresh(t)
    return _to_out(db, t)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """删除任务：级联删 StepLog + 导出文件 + 清除关联 assistant 消息的 task_id。
    对话本身保留（只是这条消息不再渲染节点卡/用例卡）。"""
    t = _own_task(db, task_id, user)
    deleted_files = _delete_task_cascade(db, t)
    db.delete(t)
    db.commit()
    return {"ok": True, "deleted_task_id": task_id, "deleted_files": deleted_files}


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: str,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """重试失败任务（断点续跑）：只保留已完成的 parser 步骤，删除其余步骤后重新入队。

    - 仅允许 failed 状态的任务重试
    - parser 步骤已完成 → 保留其 StepLog（含 units 数据），run_task 会自动跳过并恢复
    - 其余步骤（generator/reviewer/exporter）全部删除，重新执行
    - 复用原任务的输入、配置（kind/formats/roles）、会话关联
    """
    t = _own_task(db, task_id, user)
    if t.status != "failed":
        raise HTTPException(status_code=400, detail=f"只有失败状态的任务可以重试（当前状态: {t.status}）")

    # 只保留 parser 的 completed StepLog，删除其余所有步骤
    db.execute(
        delete(StepLog).where(
            StepLog.task_id == t.id,
            StepLog.name != "parser",
        )
    )
    # parser 步骤如果不是 completed 也删掉（重新执行）
    db.execute(
        delete(StepLog).where(
            StepLog.task_id == t.id,
            StepLog.name == "parser",
            StepLog.status != "completed",
        )
    )

    # 重置任务状态与结果
    t.status = "pending"
    t.cases_count = 0
    t.cases_json = None
    t.report_json = None
    t.duration_ms = 0
    t.finished_at = None
    t.created_at = datetime.utcnow()  # 重置创建时间，前端计时器从重试时刻开始计算
    db.commit()

    # 重新入全局任务队列
    task_queue.enqueue(t.id)
    return _to_out(db, t)


def _delete_task_cascade(db: Session, t: Task) -> int:
    """删除任务的级联副作用：StepLog + 导出文件 + 清除 messages.task_id。
    返回删除的导出文件数。调用方负责 db.commit() 和 db.delete(t)。"""
    db.execute(delete(StepLog).where(StepLog.task_id == t.id))
    data_dir = t.user_data_dir(db)
    deleted_files = 0
    for ext in ("xlsx", "json", "xmind"):
        p = OUTPUT_DIR / data_dir / f"{t.id}.{ext}"
        if p.exists():
            try:
                p.unlink()
                deleted_files += 1
            except OSError:
                pass
    # 清除关联 assistant 消息的 task_id（消息本身保留，只是解除关联）
    db.execute(
        update(Message).where(Message.task_id == t.id).values(task_id=None)
    )
    return deleted_files


@router.get("/tasks/{task_id}/download")
def download(task_id: str, fmt: str = "xlsx",
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """下载导出文件（fmt=xlsx/json/xmind）。非本人任务 → 404。"""
    task = _own_task(db, task_id, user)
    ext = fmt if fmt.startswith(".") else "." + fmt
    path = OUTPUT_DIR / task.user_data_dir(db) / f"{task_id}{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {fmt} 导出文件")
    return FileResponse(path, filename=f"{task_id}{ext}")


@router.post("/tasks/{task_id}/iterate", response_model=TaskOut, status_code=201)
async def iterate_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile | None = File(None),
    instruction: str = Form(""),
    conversation_id: str = Form(""),
):
    """对已完成任务进行迭代补充，生成新版本子任务。

    - instruction：补充要求（必填）
    - file：可选，上传本地用例文件（xmind/xlsx/json），导入后与原用例合并
    - conversation_id：可选，关联会话（默认继承原任务的会话）
    """
    parent = _own_task(db, task_id, user)
    if parent.status not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail=f"任务状态为 {parent.status}，仅 completed/failed 任务可迭代")
    if not instruction.strip() and not file:
        raise HTTPException(status_code=400, detail="补充要求（instruction）与用例文件至少提供一个")

    # 并发保护：同一原任务下不能有正在运行的子任务
    running_child = db.execute(
        select(Task).where(
            Task.parent_task_id == task_id, Task.status == "running"
        )
    ).scalar_one_or_none()
    if running_child:
        raise HTTPException(status_code=409, detail="该任务已有迭代正在进行中，请等待完成")

    # 上传文件落盘
    uploaded_path = None
    uploaded_ext = None
    if file:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in (".xmind", ".xlsx", ".json"):
            raise HTTPException(status_code=400, detail=f"不支持的用例文件格式 {ext}（支持 xmind/xlsx/json）")
        user_dir = UPLOAD_DIR / user.data_dir
        user_dir.mkdir(parents=True, exist_ok=True)
        uploaded_path = str(user_dir / f"iter_{uuid.uuid4().hex[:8]}{ext}")
        with open(uploaded_path, "wb") as f:
            f.write(await file.read())
        uploaded_ext = ext.lstrip(".")

    eff_conv = conversation_id or parent.conversation_id

    # 预创建新任务空壳（status=pending），后台 run_iterate 填充
    new_task_id = uuid.uuid4().hex[:12]
    new_task = Task(
        id=new_task_id,
        name=parent.name,
        kind=parent.kind,
        source_type="iterate",
        input_ref=instruction[:500],
        formats=parent.formats,
        roles=parent.roles or '["qa"]',
        status="pending",
        user_id=parent.user_id,
        conversation_id=eff_conv,
        parent_task_id=task_id,
        category_id=parent.category_id,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    # 会话消息落库：让迭代过程在会话中可回放。
    # user 消息记录迭代指令；assistant 占位消息（task_id=None）由 run_iterate 完成时回填新子任务 id，
    # 前端据此在该位置渲染任务卡（iterate.py 结尾的既有回填逻辑）。
    conv_obj = db.get(Conversation, eff_conv) if eff_conv else None
    if conv_obj:
        user_content = instruction.strip() or "（无文字补充要求，仅上传用例文件）"
        if file and file.filename:
            user_content = f"{user_content}\n📎 已附用例文件：{file.filename}"
        db.add(Message(conversation_id=eff_conv, role="user",
                       content=f"🔁 迭代《{parent.name}》：{user_content}"))
        db.flush()  # 保证 user 消息先于 assistant 占位消息入库
        db.add(Message(conversation_id=eff_conv, role="assistant",
                       content=f"正在基于《{parent.name}》迭代补充用例…", task_id=None))
        conv_obj.updated_at = datetime.utcnow()
        db.commit()

    background_tasks.add_task(
        run_iterate, new_task_id, task_id, instruction.strip(), uploaded_path, uploaded_ext, eff_conv)
    return _to_out(db, new_task)
