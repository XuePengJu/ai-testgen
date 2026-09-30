"""会话 API：建会话 / 列表 / 详情 / 追加消息（对话记录持久化）。

- 每个用户独立会话（user_id 隔离）
- 会话详情按 task_id 回填关联任务的节点步骤（StepLog）与用例（cases_json）
- V4.1：会话带 mode（workflow/kb_qa）与 kb_id，列表支持 ?mode= 过滤，
  知识库问答会话与首页工作流会话互不污染
- V5.9：PATCH 手动重命名 + POST ai-title（AI 总结生成标题，覆盖不准的首条截断标题）
"""
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.tasks import _parse_cases, _delete_task_cascade
from app.core.config import OUTPUT_DIR
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.conversation import ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["会话"])


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


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """删除整个会话：连带删该会话下所有任务（StepLog + 导出文件）+ 所有消息 + 会话本身。
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
    # 3. 删会话本身
    db.delete(c)
    db.commit()
    return {"ok": True, "deleted_tasks": len(tasks), "deleted_files": deleted_files}
