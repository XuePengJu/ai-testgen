"""会话 API：建会话 / 列表 / 详情 / 追加消息（对话记录持久化）。

- 每个用户独立会话（user_id 隔离）
- 会话详情按 task_id 回填关联任务的节点步骤（StepLog）与用例（cases_json）
- V4.1：会话带 mode（workflow/kb_qa）与 kb_id，列表支持 ?mode= 过滤，
  知识库问答会话与首页工作流会话互不污染
- V5.9：PATCH 手动重命名 + POST ai-title（AI 总结生成标题，覆盖不准的首条截断标题）
- P2：POST memory/digest（手动触发记忆提炼）+ GET memory/state（提炼状态机查询）；
  DELETE 级联补齐该会话记忆文档/附件入库文档的向量与行数据清理
"""
import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.tasks import _parse_cases, _delete_task_cascade
from app.core import config
from app.core.config import OUTPUT_DIR, AITF_MEMORY_DELETE_CONV_ITEMS
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.chat_file import ChatAttachment
from app.models.conversation import Conversation, Message
from app.models.knowledge import Knowledge
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.conversation import ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["会话"])

logger = logging.getLogger("api.conversations")


class ConversationIn(BaseModel):
    title: str = Field(default="新会话", max_length=80)
    mode: str = Field(default="workflow", pattern="^(workflow|kb_qa)$")  # V4.1 会话模式
    kb_id: str | None = None                                             # V4.1 知识库归属


class MessageIn(BaseModel):
    role: str = Field(default="user", pattern="^(user|assistant)$")
    content: str = ""
    thinking: str = ""
    # V4.5.2：可选引用溯源（JSON 数组，原样存文本）
    citations: list[dict] | None = None
    task_id: str | None = None


class ConversationRenameIn(BaseModel):
    """V5.9 手动重命名（长度在端点内 strip + 截断，超长不报 422 直接截）。"""
    title: str = Field(min_length=1)


def _own_conversation(db: Session, user: User, conv_id: str) -> Conversation:
    c = db.get(Conversation, conv_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return c


def _task_brief(db: Session, task_id: str | None) -> dict | None:
    """任务摘要（含节点步骤 steps + 用例 cases），供会话详情回填 message.task。"""
    if not task_id:
        return None
    t = db.get(Task, task_id)
    if not t:
        return None
    steps = db.execute(
        select(StepLog).where(StepLog.task_id == task_id).order_by(StepLog.id)
    ).scalars().all()
    return {
        "id": t.id,
        "name": t.name,
        "status": t.status,
        "cases_count": t.cases_count,
        "duration_ms": t.duration_ms,
        "created_at": t.created_at,
        "steps": [
            {"name": s.name, "title": s.title, "status": s.status,
             "duration_ms": s.duration_ms, "output_summary": s.output_summary,
             "input_summary": s.input_summary,
             "error": s.error}
            for s in steps
        ],
        "cases": _parse_cases(t.cases_json),
    }


def _parse_citations(raw: str | None) -> list[dict] | None:
    """V4.5.2：messages.citations JSON 文本 → 列表；空/坏数据返回 None。"""
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) and v else None
    except (json.JSONDecodeError, ValueError):
        return None


def _to_out(db: Session, c: Conversation, include_messages: bool = False) -> ConversationOut:
    count = db.execute(
        select(func.count()).select_from(Message).where(Message.conversation_id == c.id)
    ).scalar_one()
    task_count = db.execute(
        select(func.count()).select_from(Task).where(Task.conversation_id == c.id)
    ).scalar_one()
    out = ConversationOut(
        id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at,
        message_count=count, task_count=task_count, messages=[],
        mode=c.mode or "workflow", kb_id=c.kb_id,
    )
    if include_messages:
        msgs = db.execute(
            select(Message).where(Message.conversation_id == c.id).order_by(Message.id)
        ).scalars().all()
        out.messages = [
            MessageOut(
                id=m.id, role=m.role, content=m.content, thinking=m.thinking,
                citations=_parse_citations(getattr(m, "citations", None)),
                task_id=m.task_id, created_at=m.created_at,
                task=_task_brief(db, m.task_id),
            )
            for m in msgs
        ]
    return out


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    c = Conversation(id=uuid.uuid4().hex[:12], user_id=user.id,
                     title=body.title.strip() or "新会话",
                     mode=body.mode, kb_id=body.kb_id or None)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_out(db, c)


@router.get("", response_model=list[ConversationOut])
def list_conversations(mode: str | None = None,
                       kb_id: str | None = None,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    stmt = select(Conversation).where(Conversation.user_id == user.id)
    if mode in ("workflow", "kb_qa"):  # V4.1：按模式过滤，非法值返回全部（兼容老前端）
        stmt = stmt.where(Conversation.mode == mode)
    # V4.5.2：知识库问答页按库过滤会话，避免切库后看到别的库的问答历史
    # kb_id 为空的老问答会话不回落——它们本就无法按正确库检索，严格隔离
    if kb_id:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    rows = db.execute(stmt.order_by(Conversation.updated_at.desc())).scalars().all()
    return [_to_out(db, c) for c in rows]


@router.get("/{conv_id}", response_model=ConversationOut)
def get_conversation(conv_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    c = _own_conversation(db, user, conv_id)
    return _to_out(db, c, include_messages=True)


@router.patch("/{conv_id}", response_model=ConversationOut)
def rename_conversation(conv_id: str, body: ConversationRenameIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """V5.9 手动重命名会话（侧栏行内编辑入口）。"""
    c = _own_conversation(db, user, conv_id)
    t = body.title.strip()[:80]
    if not t:
        raise HTTPException(status_code=400, detail="标题不能为空")
    c.title = t
    c.updated_at = utcnow()
    db.commit()
    db.refresh(c)
    return _to_out(db, c)


# ---- V5.9 AI 总结标题 ----

# 标题清洗：去掉包裹引号 / 换行 / 句号结尾；截断到 30 字防侧栏溢出
_TITLE_JUNK_RE = re.compile(r"[「」『』\"'“”‘’`*_#\n\r\t]")
_TITLE_TRAIL_RE = re.compile(r"[。．.！!？?，,；;：:\s]+$")
_TITLE_MAX = 30


def _clean_title(raw: str) -> str:
    t = _TITLE_JUNK_RE.sub("", raw or "").strip()
    t = _TITLE_TRAIL_RE.sub("", t)
    return t[:_TITLE_MAX]


def _build_title_prompt(msgs: list[Message]) -> str:
    """把前几轮消息压成紧凑 transcript（每条截 300 字），控制 token 花销。"""
    lines = []
    for m in msgs[:8]:
        content = re.sub(r"\s+", " ", (m.content or "")).strip()[:300]
        if not content:
            continue
        role = "用户" if m.role == "user" else "AI"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


@router.post("/{conv_id}/ai-title")
def ai_title_conversation(conv_id: str,
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """V5.9 AI 总结会话内容生成标题并直接覆盖（标题不准的根治入口）。

    - 取该会话前 8 条消息（每条截 300 字）→ LLM 生成 ≤15 字中文标题
    - enable_thinking=False：结构化短输出，思考只会拖慢且退化正文（项目实测结论）
    - 未配模型 / mock → 400 提示；无消息 → 400 提示
    """
    c = _own_conversation(db, user, conv_id)
    msgs = db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
    ).scalars().all()
    if not msgs:
        raise HTTPException(status_code=400, detail="会话还没有内容，先发条消息再生成标题")

    from app.services.llm_service import resolve_effective
    from app.services.langchain_client import LangChainClient, LLMError

    eff = resolve_effective(db, user)
    text_cfg = eff.get("text")
    if not text_cfg or not text_cfg.get("api_key"):
        raise HTTPException(status_code=400, detail="未配置可用模型，请先到【模型配置】设置")

    prompt = (
        "根据以下测试平台的会话对话内容，总结一个简洁准确的中文标题。\n"
        "要求：不超过15个字；不加引号、句号或任何前后缀；"
        "概括用户要做的事情（如「采购入库单库存校验测试」）。\n\n"
        f"对话内容：\n{_build_title_prompt(msgs)}\n\n只输出标题本身，不要任何解释。"
    )
    try:
        client = LangChainClient(text_cfg["base_url"], text_cfg["api_key"], text_cfg["model"])
        raw = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=64, timeout=30, enable_thinking=False,
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{e}")

    title = _clean_title(raw) or c.title or "新会话"
    c.title = title
    c.updated_at = utcnow()
    db.commit()
    return {"ok": True, "title": title}


# ---- P2 手动记忆提炼 ----

def _bg_manual_digest(conv_id: str) -> None:
    """后台执行手动记忆提炼（BackgroundTasks 回调，独立 DB 会话）。

    状态机铁律（R12）：finally 必写回 mem_status 并 commit——任何异常路径
    都不允许把会话留在 running，否则前端按钮永久禁用。失败写 mem_status=failed
    + mem_error（截 500 字，与列宽一致）。
    """
    from app.core.db import SessionLocal
    from app.jobs.chat_memory import process_conversation

    db = SessionLocal()
    try:
        try:
            conv = db.get(Conversation, conv_id)
            if conv is None:  # 会话已被并发删除：无需状态回写
                return
            process_conversation(db, conv, manual=True)
            conv.mem_status = "done"
        except Exception as e:  # noqa: BLE001
            db.rollback()
            conv = db.get(Conversation, conv_id)  # rollback 后重取再写失败态
            if conv is not None:
                conv.mem_status = "failed"
                conv.mem_error = str(e)[:500]
                logger.warning("手动记忆提炼失败 conv=%s：%s", conv_id, str(e)[:200])
        finally:
            db.commit()
    finally:
        db.close()


@router.post("/{conv_id}/memory/digest", status_code=202)
def digest_conversation(conv_id: str,
                        background_tasks: BackgroundTasks,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """P2 手动触发会话记忆提炼（前端「整理记忆」按钮）。

    - guest → 403（注册后即可使用记忆库）；开关未开 → 403 带说明
    - 条件 UPDATE 抢占：mem_status != running 才置 running，撞上 = 409 正在整理
    - 抢占成功后交给 BackgroundTasks 异步执行，立即返回 202 running
    """
    c = _own_conversation(db, user, conv_id)
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="注册后即可使用记忆库")
    if not config.AITF_MEMORY_ENABLED or not config.AITF_MEMORY_DIGEST_MANUAL:
        raise HTTPException(status_code=403, detail="对话记忆整理功能未开启，请联系管理员")
    # 条件 UPDATE 抢占（跨方言一致；rowcount=0 说明已被占，防并发双跑烧 LLM）
    res = db.execute(
        update(Conversation)
        .where(Conversation.id == conv_id, Conversation.mem_status != "running")
        .values(mem_status="running", mem_at=utcnow(), mem_error=None)
    )
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=409, detail="正在整理中，请稍候")
    background_tasks.add_task(_bg_manual_digest, conv_id)
    return {"status": "running"}


@router.get("/{conv_id}/memory/state")
def memory_state(conv_id: str,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """P2 会话记忆状态机查询（前端按钮可用性/进度展示）。

    自愈：running 超 5 分钟视为进程重启残留 → 回落 idle 并落库（防按钮永久禁用）。
    返回提炼状态 + 水位时间 + 记忆文档 id + 最近失败原因 + 消息/附件计数。
    """
    c = _own_conversation(db, user, conv_id)
    now = utcnow()
    if (c.mem_status == "running" and c.mem_at
            and (now - c.mem_at).total_seconds() > 300):
        c.mem_status = "idle"
        db.commit()
        db.refresh(c)
    msg_count = db.execute(
        select(func.count()).select_from(Message)
        .where(Message.conversation_id == conv_id)
    ).scalar_one()
    att_total = db.execute(
        select(func.count()).select_from(ChatAttachment)
        .where(ChatAttachment.conversation_id == conv_id)
    ).scalar_one()
    att_done = db.execute(
        select(func.count()).select_from(ChatAttachment)
        .where(ChatAttachment.conversation_id == conv_id,
               ChatAttachment.knowledge_id.isnot(None))
    ).scalar_one()
    return {
        "status": c.mem_status or "idle",
        "mem_at": c.mem_at.isoformat() if c.mem_at else None,
        "doc_id": c.mem_doc_id,
        "error": c.mem_error,
        "msg_count": msg_count,
        "attachments": {"total": att_total, "done": att_done},
    }


@router.post("/{conv_id}/messages", response_model=MessageOut, status_code=201)
def add_message(conv_id: str, body: MessageIn,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    c = _own_conversation(db, user, conv_id)
    m = Message(conversation_id=c.id, role=body.role, content=body.content,
                thinking=body.thinking, task_id=body.task_id,
                citations=(json.dumps(body.citations, ensure_ascii=False)
                           if body.citations else None))
    db.add(m)
    c.updated_at = utcnow()
    db.commit()
    db.refresh(m)
    return MessageOut(
        id=m.id, role=m.role, content=m.content, thinking=m.thinking,
        citations=_parse_citations(getattr(m, "citations", None)),
        task_id=m.task_id, created_at=m.created_at,
        task=_task_brief(db, m.task_id),
    )


def _delete_conversation_knowledge(db: Session, conv: Conversation) -> int:
    """P2 级联清理：删该会话的记忆文档 + 附件入库文档（向量 + 行 + 副本文件）。

    覆盖三类来源：conv.mem_doc_id 冗余指针、source_key=mem:conv:* 兜底、
    附件登记行回填的 knowledge_id。物理删除 Knowledge 行（个人库数据随会话
    走，不留软删孤儿），向量与 chunks 走 delete_document_vectors。
    """
    from app.services.knowledge.ingest import delete_document_vectors

    doc_ids: set[str] = set()
    if conv.mem_doc_id:
        doc_ids.add(conv.mem_doc_id)
    # 兜底：mem_doc_id 冗余可能缺失/不同步，按 source_key 再查一次会话记忆文档
    sk_doc = db.execute(
        select(Knowledge).where(
            Knowledge.source_key == f"mem:conv:{conv.user_id}:{conv.id}")
    ).scalars().first()
    if sk_doc:
        doc_ids.add(sk_doc.id)
    atts = db.execute(
        select(ChatAttachment).where(ChatAttachment.conversation_id == conv.id)
    ).scalars().all()
    for a in atts:
        if a.knowledge_id:
            doc_ids.add(a.knowledge_id)

    deleted_docs = 0
    for doc_id in doc_ids:
        doc = db.get(Knowledge, doc_id)
        if not doc:
            continue
        delete_document_vectors(db, doc_id)  # Chroma 向量 + chunks 行（内部 commit）
        # 副本文件（uploads/knowledge/{kb_id}/ 下，P1 上传统一登记的物理副本）
        if doc.file_path:
            try:
                Path(doc.file_path).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("副本文件删除失败 %s：%s", doc.file_path, e)
        db.delete(doc)
        deleted_docs += 1
    # 附件登记行随会话删除（uploads/chat 原件保留，属用户上传资产不在此清）
    for a in atts:
        db.delete(a)
    # V7.0 级联清理：会话级记忆条目 + 关联审计（AITF_MEMORY_DELETE_CONV_ITEMS
    # 默认开；条目含本会话的隐私内容，会话删了不能留条目孤儿）
    deleted_items = 0
    if AITF_MEMORY_DELETE_CONV_ITEMS:
        from app.models.memory import MemoryAudit, MemoryItem
        item_rows = db.execute(
            select(MemoryItem).where(MemoryItem.conversation_id == conv.id)
        ).scalars().all()
        item_ids = [i.id for i in item_rows]
        if item_ids:
            # V7.2：行删除前先清条目向量（ Chroma where 按 memory_item_id，
            # 删除失败只告警不阻断行清理）
            from app.services.knowledge.vectorstore import delete_item_vectors
            for it in item_rows:
                delete_item_vectors(it.id)
            db.execute(delete(MemoryAudit).where(MemoryAudit.item_id.in_(item_ids)))
            db.execute(delete(MemoryItem).where(MemoryItem.id.in_(item_ids)))
            deleted_items = len(item_ids)
    db.commit()
    if deleted_items:
        logger.info("会话 %s 级联删除记忆条目 %d 条", conv.id, deleted_items)
    return deleted_docs


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """删除整个会话：连带删该会话下所有任务（StepLog + 导出文件）+ 所有消息
    + 会话记忆/附件入库文档（向量 + 行 + 副本文件）+ 附件登记行 + 会话本身。
    对话一旦删除不可恢复——前端应弹确认窗。"""
    c = _own_conversation(db, user, conv_id)
    # 1. 连带删除该会话下所有任务
    tasks = db.execute(
        select(Task).where(Task.conversation_id == conv_id)
    ).scalars().all()
    deleted_files = 0
    for t in tasks:
        deleted_files += _delete_task_cascade(db, t)
        db.delete(t)
    # 2. 删该会话的所有消息
    db.execute(delete(Message).where(Message.conversation_id == conv_id))
    # 3. P2 级联：记忆文档 + 附件入库文档（向量/行/副本文件）+ 附件登记行
    deleted_docs = _delete_conversation_knowledge(db, c)
    # 4. 删会话本身
    db.delete(c)
    db.commit()
    return {"ok": True, "deleted_tasks": len(tasks), "deleted_files": deleted_files,
            "deleted_docs": deleted_docs}
