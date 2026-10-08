"""记忆提炼 LLM 调用（P2）。

只负责「transcript + 上一版记忆 → 记忆文档（Markdown）」这一件事：
- 走 llm_pool.build_client（池 > 单条 > env > mock 的既有调度，不改 llm_pool）
- temperature=0.2、enable_thinking=False（思考 token 计入 max_tokens 会截断正文，
  项目已两次踩坑）、max_tokens=1200
- 输出 Markdown 而非 JSON（记忆文档要人可读、可直接入库分块）
- 调用失败（含疑似输出被截断）→ 输入截半 + max_tokens 翻倍重试一次
"""
from __future__ import annotations

import json
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


# ============ V7.0：事实条目抽取（extract_facts）============

# 事实 vs 推断判据（prompt 与 Python 硬夹双保险）：
#   explicit   用户显式声明「记住/我偏好/我们约定」  conf 0.90~0.95，硬夹 ≤0.95
#   assert     用户明确断言的数值/字段/接口/规则    conf 0.80~0.90，硬夹 ≤0.90
#   assistant  AI 自己给出的结论                    conf 0.60~0.70，硬夹 ≤0.70
#   infer      LLM 推断的意图/习惯                  conf 0.50~0.70，硬夹 ≤0.70
# 置信度硬夹在 services/memory/items.py 执行（Python 侧不信任 LLM 自报）。
_FACT_SYSTEM_PROMPT = (
    "你是测试平台的对话事实抽取助手。从对话记录中抽取值得长期记住的事实条目，"
    "输出 JSON 数组。\n"
    "每条对象格式：{\"kind\": \"fact|preference|rule|todo|profile\", "
    "\"subject\": \"主题短语(不超过20字)\", \"content\": \"事实内容(不超过200字)\", "
    "\"confidence\": 0.0到1.0的小数, \"prov\": \"explicit|assert|assistant|infer\", "
    "\"msg_ref\": 该事实来源消息在对话中的序号(从0开始的整数)}\n"
    "prov 判据：explicit=用户显式说要记住/我偏好/我们约定；assert=用户明确断言的"
    "数值/字段/接口/规则；assistant=AI 自己给出的结论；infer=你推断的用户意图或习惯。\n"
    "confidence 参考档位：explicit 0.90~0.95 / assert 0.80~0.90 / "
    "assistant 0.60~0.70 / infer 0.50~0.70。\n"
    "要求：\n"
    "1. 只抽有长期价值的事实，跳过寒暄、客套与无信息量的过程性内容；\n"
    "2. subject 用简短名词短语且尽量与「已有记忆主题」保持一致，避免同义改写漂移；\n"
    "3. 保留具体名词（模块名/字段名/数量/接口名/双方约定），不要泛化；\n"
    "4. 只输出 JSON 数组本身，不要任何解释、前言或代码围栏。"
)


def _strip_fences(text: str) -> str:
    """剥掉模型偶尔裹的 markdown 代码围栏（```json ... ```）。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        # 去掉开头的语言标记（json / javascript 等）
        first_nl = t.find("\n")
        if first_nl > 0 and not t[:first_nl].strip().startswith(("{", "[", '"')):
            t = t[first_nl + 1:]
    return t.strip()


def _parse_fact_json(raw: str) -> list[dict]:
    """四层防御解析抽取输出（丢坏条目而非整批失败）。

    1. 剥代码围栏；2. json.loads 失败时按「首个 [ 到最后一个完整 }」截断修复；
    3. 只收 dict 条目；4. 逐条校验（kind 枚举、confidence clamp[0,1]、
    msg_ref 非法置 0、content 截 200 字、subject 截 120 字）。
    返回字段：kind/subject/content/confidence/prov/msg_ref（越界上界由调用方按
    消息数再夹一次）。
    """
    text = _strip_fences(raw)
    if not text:
        return []
    data = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 截断修复：LLM 输出被 max_tokens 截断时，取首个 [ 到最后一个完整 }
        start = text.find("[")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1] + "]")
            except (json.JSONDecodeError, ValueError):
                data = None
        # 再兜底：单对象输出（忘了裹数组）
        if data is None:
            s2, e2 = text.find("{"), text.rfind("}")
            if s2 >= 0 and e2 > s2:
                try:
                    obj = json.loads(text[s2:e2 + 1])
                    if isinstance(obj, dict):
                        data = [obj]
                except (json.JSONDecodeError, ValueError):
                    data = None
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in ("fact", "preference", "rule", "todo", "profile"):
            continue  # kind 越界 → 丢条目
        subject = str(it.get("subject") or "").strip()[:120]
        content = str(it.get("content") or "").strip()[:200]
        if not subject or not content:
            continue
        try:
            conf = float(it.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.0
        if conf != conf:  # NaN
            conf = 0.0
        conf = min(1.0, max(0.0, conf))
        prov = it.get("prov") if it.get("prov") in (
            "explicit", "assert", "assistant", "infer") else "infer"
        msg_ref = it.get("msg_ref")
        if not isinstance(msg_ref, int) or msg_ref < 0:
            msg_ref = 0  # 越界/非法置 0（上界由调用方按消息数夹）
        out.append({"kind": kind, "subject": subject, "content": content,
                    "confidence": conf, "prov": prov, "msg_ref": msg_ref})
    return out


def extract_facts(transcript: str,
                  known_subjects: list[str] | None = None) -> list[dict]:
    """从对话 transcript 抽取事实条目，返回条目 dict 列表（可能为空）。

    与 memory_chat 刻意不同（两者并存互不影响）：
    - temperature=0.1（抽取要稳）、max_tokens=AITF_MEMORY_EXTRACT_MAX_TOKENS(1500)
      ——memory_chat 的 1200 已两次踩截断坑，再塞 JSON 必然第三次踩
    - **失败不重试不抛异常返回 []**：条目链路是文档链路的旁路双写，
      抽取失败最多损失增量条目，绝不能拖垮主链路
    返回条目字段：kind/subject/content/confidence/prov/msg_ref（置信度硬夹
    在 items.py 落库前执行）。
    """
    from app.core.db import SessionLocal
    from app.services import llm_pool
    from app.services.langchain_client import LLMError

    text = (transcript or "").strip()
    if not text:
        return []
    db = SessionLocal()
    try:
        client = llm_pool.build_client(db, None, "text")
    finally:
        db.close()
    if client is None:
        return []  # 未配置模型 → 静默放弃（不抛，旁路链路不阻断主链路）

    user_content = f"对话记录：\n{text[:_TRANSCRIPT_LIMIT]}"
    if known_subjects:
        # 塞入已有 subject 词表：让模型复用既有主题，防同义漂移成两条
        user_content += ("\n\n该用户已有的记忆主题（新条目 subject 尽量复用其中"
                         "相同主题，避免同义改写）：\n" + "；".join(known_subjects[:40]))
    messages = [
        {"role": "system", "content": _FACT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        from app.core.config import AITF_MEMORY_EXTRACT_MAX_TOKENS
        out = client.chat(messages, temperature=0.1,
                          max_tokens=AITF_MEMORY_EXTRACT_MAX_TOKENS,
                          enable_thinking=False)
    except LLMError as e:
        # 刻意不重试：条目链路下轮 dirty 会话还会再抽，重试只会放大故障
        logger.warning("事实抽取调用失败（不重试，返回空）：%s", str(e)[:200])
        return []
    except Exception as e:  # noqa: BLE001  任何客户端异常都不许冒泡
        logger.warning("事实抽取异常（不重试，返回空）：%s: %s",
                       type(e).__name__, str(e)[:200])
        return []
    facts = _parse_fact_json(out or "")
    if not facts:
        logger.info("事实抽取无有效条目（输出 %d 字）", len(out or ""))
    return facts
