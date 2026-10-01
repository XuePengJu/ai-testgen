"""记忆提炼 LLM 调用（P2）。

只负责「transcript + 上一版记忆 → 记忆文档（Markdown）」这一件事：
- 走 llm_pool.build_client（池 > 单条 > env > mock 的既有调度，不改 llm_pool）
- temperature=0.2、enable_thinking=False（思考 token 计入 max_tokens 会截断正文，
  项目已两次踩坑）、max_tokens=1200
- 输出 Markdown 而非 JSON（记忆文档要人可读、可直接入库分块）
- 调用失败（含疑似输出被截断）→ 输入截半 + max_tokens 翻倍重试一次
"""
from __future__ import annotations

import logging

logger = logging.getLogger("memory.llm")

# 记忆文档固定小节；滚动摘要时上一版也会被合并进这些小节
_MEMORY_SYSTEM_PROMPT = (
    "你是测试平台的对话记忆提炼助手。把用户给出的对话记录（transcript）提炼成一份"
    "结构化记忆文档，输出 Markdown。\n"
    "要求：\n"
    "1. 固定使用以下小节（一级标题用 ##）：背景 / 关键结论 / 业务规则 / 待办与遗留；\n"
    "2. 保留具体名词：模块名、字段名、数量、接口名、双方约定，不要泛化改写；\n"
    "3. 删掉寒暄、重复与无信息量的过程性内容；\n"
    "4. 某小节无内容时写「（暂无）」，不要编造；\n"
    "5. 只输出记忆文档本身，不要任何解释、前言或代码围栏。"
)

# 输入上限：transcript 6000 字 / 上一版记忆 3000 字（调用方一般已截，这里兜底）
_TRANSCRIPT_LIMIT = 6000
_PREV_MEMORY_LIMIT = 3000
_MAX_TOKENS = 1200


def _build_user_content(prompt: str, prev_memory: str) -> str:
    """组装 user 消息：transcript（必选）+ 上一版记忆（可选，滚动摘要合并）。"""
    parts = [f"对话记录：\n{(prompt or '').strip()[:_TRANSCRIPT_LIMIT]}"]
    if prev_memory and prev_memory.strip():
        parts.append(
            "上一版记忆（请合并去重后输出完整的新版记忆，不要重复罗列）：\n"
            f"{prev_memory.strip()[:_PREV_MEMORY_LIMIT]}"
        )
    return "\n\n".join(parts)


def memory_chat(prompt: str, prev_memory: str = "") -> str:
    """把对话 transcript 提炼为记忆文档（Markdown），失败抛 LLMError。

    与业务对话解耦：后台任务/手动按钮都可能调用，模型解析用独立短会话
    （不绑定请求级 db），user 传 None → 平台池 > env 兜底，避免个人未配模型
    时夜间提炼全部失败。
    """
    from app.core.db import SessionLocal
    from app.services import llm_pool
    from app.services.langchain_client import LLMError

    db = SessionLocal()
    try:
        client = llm_pool.build_client(db, None, "text")
    finally:
        db.close()
    if client is None:
        raise LLMError("未配置可用的文本模型，无法提炼记忆")

    user_content = _build_user_content(prompt, prev_memory)
    messages = [
        {"role": "system", "content": _MEMORY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        out = client.chat(messages, temperature=0.2, max_tokens=_MAX_TOKENS,
                          enable_thinking=False)
    except LLMError as e:
        # 超长被截/调用失败：输入截半 + max_tokens 翻倍重试一次（仍失败则向上抛）
        logger.warning("记忆提炼首次调用失败，截半重试：%s", str(e)[:200])
        retry_content = _build_user_content(
            (prompt or "")[:max(0, _TRANSCRIPT_LIMIT // 2)],
            prev_memory[:max(0, _PREV_MEMORY_LIMIT // 2)],
        )
        messages = [
            {"role": "system", "content": _MEMORY_SYSTEM_PROMPT},
            {"role": "user", "content": retry_content},
        ]
        out = client.chat(messages, temperature=0.2, max_tokens=_MAX_TOKENS * 2,
                          enable_thinking=False)
    text = (out or "").strip()
    # 剥掉模型偶尔裹的代码围栏（入库分块前保持纯 Markdown）
    if text.startswith("```"):
        text = text.strip("`").lstrip("markdown").strip()
    if not text:
        raise LLMError("模型返回空内容，记忆提炼失败")
    return text
