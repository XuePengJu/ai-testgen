"""对话流式端点（SSE，前端打字机体验）。

- POST /api/chat/stream
  入参：{ message, history?, attached_text? }
  响应：text/event-stream，逐 event 输出原始 delta / 完成 / 错误

SSE 协议：
  event: delta
  data: {"content": "..."}

  event: think
  data: {"content": "..."}         思考增量（reasoning_content 类字段），直接进思考面板

  event: notice
  data: {"message": "..."}         非终态提示（降级/中断），流会继续

  event: citations
  data: {"items": [...], "kb_id": "...", "top_k": n}   V4.1 引用溯源：正文前发送，
                                                       items 为命中的知识库分块元数据

  event: done
  data: {"full": "...", "source": "user|platform|env|mock", "thinking": "..."}

  event: error
  data: {"message": "..."}

思考有两个来源：① 上游独立字段 → 走 think 事件；② 老模型混在正文里的
<think>/<thinking> 标签 或 mock 的 思考...思考 → 前端自行切分。

流式响应 Content-Type 是 text/event-stream，不会被 ApiCryptoMiddleware 加密
（中间件只加密 application/json）。
"""
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.files import load_chat_file
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.knowledge import Knowledge
from app.models.task import Task
from app.models.user import User
from app.schemas.llm_config import ChatIn
from app.services import llm_service

router = APIRouter()

# M10 运维日志：/chat/stream 调用记录（user_id / 模型 / 耗时 / 成功失败；
# 只记元信息，不落用户消息全文与 prompt，避免敏感内容进运维日志）
logger = logging.getLogger("api.chat")

# V4.1 会话模式：workflow=首页工作流对话；kb_qa=知识库问答
_VALID_MODES = ("workflow", "kb_qa")


def _sse(event: str, data: dict) -> bytes:
    """构造一个 SSE 事件（UTF-8 JSON）。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _split_think(full: str) -> tuple[str, str]:
    """切分思考与回复，返回 (thinking, reply)。与前端 splitThink 保持一致。

    支持两种上游格式：
    - 真实模型：<think>...</think> 或 <thinking>...</thinking>
    - mock： 思考 ... 思考 分隔符
    """
    if not full:
        return "", ""
    m = re.search(r"<think(?:ing)?>([\s\S]*?)</think(?:ing)?>", full)
    if m:
        thinking = m.group(1).strip()
        reply = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", full).strip()
        return thinking, reply
    m = re.search(r" 思考([\s\S]*?)思考", full)
    if m:
        thinking = m.group(1).strip()
        reply = (full[:m.start()] + full[m.end():]).strip()
        return thinking, reply
    return "", full.strip()


def _ensure_conversation(db: Session, user: User | None, body: ChatIn) -> None:
    """确保 conversation_id 有效；无/无效/不属于当前用户时自动创建会话。

    修复「新会话发送消息不落库」：前端新会话 conversation_id 为空，原先直接跳过
    持久化，切换会话/刷新后聊天记录丢失。此处统一由后端兜底创建会话，
    并在 SSE done 事件把 conversation_id 回传给前端保存。

    V4.1：创建会话时落库 mode / kb_id，用于会话列表按模式隔离。
    """
    if user is None:
        return  # 游客（未登录）保持原行为：不落库
    if body.conversation_id:
        conv = db.get(Conversation, body.conversation_id)
        if conv and conv.user_id == user.id:
            return
    conv = Conversation(
        id=uuid.uuid4().hex[:12],
        user_id=user.id,
        title=(body.message or "新会话")[:40],
        mode=body.mode if body.mode in _VALID_MODES else "workflow",
        kb_id=(body.kb_id or None),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    body.conversation_id = conv.id


def _persist_chat(db: Session, user: User | None, body: ChatIn, full_text: str,
                  think_text: str = "", citations: list[dict] | None = None) -> None:
    """流式结束后把 user + assistant 消息落库到会话（对话记录持久化）。

    think_text 是上游独立字段（reasoning_content）累积的思考内容；
    为空时回落到从正文里切 <think> 标签（老模型形态）。思考必须单独存，
    否则历史消息的思考面板会空。

    V4.5.2：citations 一并持久化（JSON 文本），切换会话/刷新后引用不丢。
    """
    if not user or not body.conversation_id:
        return
    conv = db.get(Conversation, body.conversation_id)
    if not conv or conv.user_id != user.id:
        return
    thinking, reply = _split_think(full_text)
    if think_text:
        thinking = f"{think_text.strip()}\n\n{thinking}".strip() if thinking else think_text.strip()
    cites_json = json.dumps(citations, ensure_ascii=False) if citations else None
    db.add(Message(conversation_id=conv.id, role="user", content=body.message))
    db.add(Message(conversation_id=conv.id, role="assistant",
                   content=reply, thinking=thinking, citations=cites_json))
    conv.updated_at = utcnow()
    db.commit()


def _build_task_summary(db: Session, task_id: str, user: User | None) -> str:
    """迭代补充模式：构建任务用例的压缩摘要，附在对话上下文里。"""
    if not task_id:
        return ""
    task = db.get(Task, task_id)
    if not task or (user and task.user_id != user.id and user.role != "admin"):
        return ""
    if not task.cases_json:
        return f"【任务上下文】任务「{task.name}」暂无已生成用例"
    try:
        cases = json.loads(task.cases_json)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(cases, list) or not cases:
        return ""
    from collections import Counter
    by_module = Counter(c.get("module") or "未分类" for c in cases)
    by_type = Counter(c.get("case_type") or "正向" for c in cases)
    lines = [f"【任务上下文】正在迭代任务「{task.name}」，已有 {len(cases)} 条用例："]
    lines.append(f"类型分布：{dict(by_type)}")
    for mod, cnt in by_module.items():
        titles = [c.get("title", "")[:30] for c in cases if (c.get("module") or "未分类") == mod][:8]
        lines.append(f"- 模块「{mod}」（{cnt} 条）：{'、'.join(titles)}")
    return "\n".join(lines)[:2000]


def _build_attachment_context(loaded: tuple[str, str] | None) -> str:
    """把上传附件的抽取文本包装成注入上下文。

    loaded = (文件名, 抽取文本)，由 load_chat_file 读回（None 表示没有附件 /
    缓存已过期）。抽不出文字（扫描件 pdf / 空文档）时给一句说明，避免模型凭空猜测。
    """
    if not loaded:
        return ""
    name, text = loaded
    if not text.strip():
        return f"【附件】用户上传了文档《{name}》，但未能提取到文字内容（可能是扫描件或空文档）"
    return f"【附件《{name}》内容】\n{text}"


def _build_rag_context(db: Session, user: User | None, body: ChatIn) -> tuple[str, list[dict]]:
    """V4.0 RAG：按用户可见知识库检索问题相关分块，拼成参考上下文。

    V4.1 变更：额外返回引用溯源元数据列表（citations），供 SSE citations
    事件回传前端；返回值从 str 改为 (context, citations) 元组——调用点仅
    _run 一处，无其他波及。

    限长 4000 字符（attached_text 总上限 6000，给任务摘要/附件留空间）；
    mock 向量时检索质量差，真实 Embedding Key 配置后自然增强。
    """
    if not user:
        return "", []
    from app.services import llm_service
    from app.services.knowledge import vectorstore
    from app.services.knowledge.ingest import visible_kb_ids

    # 注入 embedding 配置（当前用户个人配置 > 平台 > env > mock；与入库同模型才可匹配）
    vectorstore.configure_embedding(llm_service.resolve_embedding(db, user.id).get("cfg"))

    vids = visible_kb_ids(db, user.id, admin=user.role == "admin")
    if not vids:
        return "", []
    # V5.8 检索语义：不选知识库 = 不检索（旧行为"全库搜"已废除，避免无关库噪音挤占 top_k）；
    # kb_ids 多选 = 所选库联合检索；kb_id 单库字段为 kb_qa 遗留，兼容读取。
    # 选中但无权限的库静默剔除（不报错，检索范围仅限有权可见的库）。
    picked = [k for k in (body.kb_ids or ([] if not body.kb_id else [body.kb_id])) if k in vids]
    if not picked:
        return "", []
    hits = vectorstore.search(
        body.message, picked,
        top_k=6 if not body.file_id else 4,  # 有附件时少检索几块，给附件正文留空间
    )
    if not hits:
        return "", []
    parts, cites = [], []
    for h in hits:
        meta = h.get("metadata") or {}
        header = (meta.get("context_header") or "").strip()
        src = (meta.get("file_name") or "").strip()
        loc = f"{src} | {header}" if header else src
        parts.append(f"【{loc}】\n{h.get('document', '')}")
        cites.append({
            "chunk_id": meta.get("chunk_id") or h.get("id") or "",
            "knowledge_id": (meta.get("knowledge_id") or "").strip(),
            "doc_title": src,
            "context_header": header,
            "snippet": (h.get("document") or "")[:120],
            "score": h.get("score"),
        })
    # 批量补 Wiki/文档条目标题（引用跳转定位用），一次查询避免 N+1
    kids = {c["knowledge_id"] for c in cites if c["knowledge_id"]}
    if kids:
        rows = db.execute(
            select(Knowledge.id, Knowledge.title).where(Knowledge.id.in_(kids))
        ).all()
        titles = dict(rows)
        for c in cites:
            t = titles.get(c["knowledge_id"])
            if t:
                c["doc_title"] = t
    out = "【知识库检索参考（用于回答，未命中业务规则时如实说明）】\n" + "\n\n---\n\n".join(parts)
    return out[:4000], cites


async def _run(db: Session, user: User | None, body: ChatIn, source: str,
               model: str = ""):
    """异步 SSE 事件流 generator。

    上游 llm_service.chat_stream 输出 delta / think / notice / done / error，
    本函数做翻译转发（think 单独走 think 事件，与正文分开，避免思考混进正文）。
    流式结束后（finally）把 user + assistant 消息落库到会话，实现对话持久化。

    V4.1：正文开始前若 RAG 命中知识库，先发 citations 事件（引用溯源）。
    M10：finally 处记录本次调用的成功/失败与耗时（毫秒）到运维日志。
    """
    full_text = ""
    think_text = ""
    had_error = False       # 本次流是否出现错误（error 事件或流式异常）
    t0 = time.perf_counter()  # 请求耗时统计起点
    task_summary = _build_task_summary(db, body.task_id, user)
    # 附件文本由 POST /api/files 预先抽取落盘，这里按 file_id 读回
    attached = load_chat_file(body.file_id) if body.file_id else None
    attach_name = attached[0] if attached else ""
    # 任务摘要 + 上传附件 + 知识库检索统一走 attached_text 注入
    rag_ctx, citations = _build_rag_context(db, user, body)
    context = "\n\n".join(
        x for x in (task_summary, rag_ctx, _build_attachment_context(attached)) if x
    )
    # V4.1 引用溯源：正文 delta 之前发送，前端可先显示「引用 N 篇」。
    # 对所有对话生效（首页/知识库页），是否渲染由前端 showCitations 决定。
    if citations:
        yield _sse("citations", {
            "items": citations,
            "kb_ids": body.kb_ids or ([] if not body.kb_id else [body.kb_id]),
            "top_k": len(citations),
        })
    try:
        async for ev, payload in llm_service.chat_stream(
            db, user, body.message, body.history, context, attach_name, body.thinking,
            roles=body.roles,
            kb_mode=(body.mode == "kb_qa"),  # V4.2.2：知识库问答走中性 RAG 人设
        ):
            if ev == "delta":
                full_text += payload
                yield _sse("delta", {"content": payload})
            elif ev == "think":
                # 上游独立字段（reasoning_content）的思考增量，前端直接进思考面板
                think_text += payload
                yield _sse("think", {"content": payload})
            elif ev == "done":
                full = (payload or {}).get("full") if isinstance(payload, dict) else ""
                if full and not full_text:
                    full_text = full
                up_think = (payload or {}).get("thinking") if isinstance(payload, dict) else ""
                if up_think and not think_text:
                    think_text = up_think
                yield _sse("done", {"full": full, "source": source,
                                    "thinking": think_text,
                                    "conversation_id": body.conversation_id})
            elif ev == "notice":
                # 降级/中断提示：非终态错误，前端以黄色提示条展示，流会继续
                yield _sse("notice", {"message": str(payload)[:300]})
            elif ev == "error":
                had_error = True
                msg = payload if isinstance(payload, str) else str(payload)
                yield _sse("error", {"message": msg[:300]})
                yield _sse("done", {"full": "", "source": source, "had_error": True,
                                    "conversation_id": body.conversation_id})
    except Exception as e:  # noqa: BLE001
        had_error = True
        try:
            yield _sse("error", {"message": f"流式中断：{e.__class__.__name__}: {str(e)[:120]}"})
            yield _sse("done", {"full": "", "source": source, "had_error": True})
        except Exception:
            pass
    finally:
        # M10 调用记录：只记元信息（user_id / 模型 / 耗时 / 成功失败），不记消息内容
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("chat/stream %s user_id=%s model=%s elapsed_ms=%d",
                    "failed" if had_error else "success",
                    user.id if user else "guest",
                    model or "-", elapsed_ms)
        _persist_chat(db, user, body, full_text, think_text, citations)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """对话流式端点（SSE 协议）。"""
    # V3.1.1：新会话（conversation_id 为空）自动创建会话并回传 id，保证消息落库不丢
    _ensure_conversation(db, user, body)
    eff = llm_service.resolve_effective(db, user)
    source = eff.get("source", "mock")
    # M10：生效模型标识（个人/平台池配置或 env 兜底的模型名，mock 时为 "-"）
    model = (eff.get("text") or {}).get("model") or "-"
    logger.info("chat/stream 开始 user_id=%s model=%s source=%s",
                user.id if user else "guest", model, source)
    return StreamingResponse(
        _run(db, user, body, source, model=model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # Nginx / Cloudflare 反代时不缓冲流式响应
        },
    )
