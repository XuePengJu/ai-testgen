"""LLM 服务层（V2.4 FR-I，V3 迁移 LangChain）。

- OpenAICompatClient：客户端实现迁移至 app.services.langchain_client（V3 阶段 2）
  - 默认 LangChainClient（init_chat_model + stream() 收集/逐段两用）
  - 应急回退 _HttpxCompatClient（AITF_LLM_BACKEND=httpx）
- resolve_effective：模型解析优先级 = 模型池（个人 > 平台） > 服务器环境变量 > mock 兜底
  （V5.4 起单条配置 llm_configs 已下线，模型池是唯一配置入口）
- Key 落库加密：复用 crypto 的 AES-256-GCM 原语，密钥 HKDF(JWT_SECRET, info=llm-at-rest:<owner>)
- 两段式视觉理解：图片（data: URI / http URL）先交视觉模型转文字描述，再进文本模型
- chat_stream：流式输出（前端打字机体验），yield 内容片段（prompt 强制 <think>…</think> 切分）
"""
import asyncio
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import config, crypto
from app.core.providers import provider_label, FREE_PROVIDERS
from app.models.user import User
from app.services.langchain_client import (
    LangChainClient,
    _HttpxCompatClient,
    LLMError,
    _NO_THINKING_PARAM,
)
from app.services import llm_pool  # V5.0 P1 多模型池（llm_pool 内部延迟 import 本模块，无循环）
from app.services import prompt_service  # V5.10 提示词自定义（prompt_service 引用本模块的默认提示词，仅函数内调用，无循环）

# 服务器环境变量兜底（兼容老部署：.env 里的 DASHSCOPE_API_KEY）
_BAILIAN_COMPAT = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_VISION_TIMEOUT = 180    # 视觉模型看图慢一些

# LLM 调用后端开关：langchain（默认）/ httpx（旧直连，应急回退）
_LLM_BACKEND = getattr(config, "AITF_LLM_BACKEND", "langchain")
OpenAICompatClient = LangChainClient if _LLM_BACKEND == "langchain" else _HttpxCompatClient


# ============ Key 落库加密 ============

def _at_rest_key(owner_id: int) -> str:
    """静态加密密钥：HKDF(JWT_SECRET, info=llm-at-rest:<owner_id>)，与传输加密密钥相互独立。"""
    return crypto.derive_key(f"llm-at-rest:{owner_id}")


def encrypt_key(api_key: str, owner_id: int) -> str:
    return crypto.encrypt_obj({"k": api_key}, _at_rest_key(owner_id))


def decrypt_key(api_key_enc: str, owner_id: int) -> str:
    obj = crypto.decrypt_obj(api_key_enc, _at_rest_key(owner_id))
    return obj["k"]


def mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    tail = api_key[-4:] if len(api_key) > 4 else "****"
    return f"****{tail}"


def _server_key(provider: str) -> str:
    """免费厂商(modelscope / zhipu)的服务端兜底 Key。

    平台默认或个人配置选择免费模型且未填 Key 时，由平台环境变量提供 Key，
    界面无需暴露密钥。
    """
    if provider == "modelscope":
        return config.MODELSCOPE_API_KEY
    if provider in ("zhipu", "zhipu_coding"):
        return config.ZHIPU_API_KEY
    return ""


# ============ 生效配置解析 ============

def _row_to_cfg(row, owner_id: int) -> dict:
    try:
        api_key = decrypt_key(row.api_key_enc, owner_id) if row.api_key_enc else ""
    except ValueError:
        api_key = ""   # JWT_SECRET 变更等导致解不开 → 视为无 Key
    # 免费厂商且未存 Key → 由服务端环境变量兜底（界面不暴露密钥）
    if not api_key and row.provider in FREE_PROVIDERS:
        api_key = _server_key(row.provider)
    return {
        "provider": row.provider,
        "provider_label": provider_label(row.provider),
        "base_url": row.base_url,
        "model": row.model,
        "api_key": api_key,
    }


def resolve_effective(db: Session, user: User | None) -> dict:
    """返回 {"source", "text", "vision"}。

    source: user（个人池生效） / platform（平台池生效） / env（百炼 .env 兜底） / mock
    text / vision: None 表示该槽位不可用（text 为 None → mock 生成）。

    生效优先级（V5.4 起单条配置已下线，模型池是唯一配置入口）：
        模型池（个人池 > 平台池） > 百炼 env > mock
    免费厂商由服务端 Key 兜底（resolve_pool_ex 内已处理）。
    """
    def pick(slot: str) -> tuple[dict | None, str | None]:
        pool, owner = resolve_pool_ex(db, user, slot)
        if pool:
            cand = next((c for c in pool if not llm_pool.is_cooling(c)), pool[0])
            return cand, ("user" if owner == "personal" else "platform")
        return None, None

    # 文本槽优先级：模型池 > 百炼 env 兜底
    text_cfg, src = pick("text")
    if text_cfg is None and config.DASHSCOPE_API_KEY:
        text_cfg = {
            "provider": "bailian",
            "provider_label": provider_label("bailian"),
            "base_url": _BAILIAN_COMPAT,
            "model": config.MODEL_NAME or "qwen-plus",
            "api_key": config.DASHSCOPE_API_KEY,
        }
        src = "env"

    if text_cfg is None or not text_cfg.get("api_key"):
        return {"source": "mock", "text": None, "vision": None}

    vision_cfg, _ = pick("vision")

    return {
        "source": src or "platform",
        "text": text_cfg,
        "vision": vision_cfg if (vision_cfg and vision_cfg.get("api_key")) else None,
    }


def resolve_embedding(db: Session, user_id: int | None = None) -> dict:
    """解析生效的 Embedding 配置（V4.0 RAG）。

    返回 {"source", "cfg"}：
        source: personal（个人池生效）/ platform（平台池生效）
              / env（环境变量 EMBEDDING_API_KEY） / mock
    优先级（V5.4 起单条配置已下线）：模型池（个人 > 平台） > 环境变量 > mock。
    注意：向量维度由模型决定，不同用户配置不同模型时跨用户检索（global 库）
    可能失效；同一用户自己的库入库/检索同模型，正常匹配。
    """
    if db is not None:
        from app.models.llm_pool import LLMModelPool

        def _first(owner_id: int) -> dict | None:
            rows = db.execute(
                select(LLMModelPool).where(
                    LLMModelPool.user_id == owner_id,
                    LLMModelPool.slot == "embedding",
                    LLMModelPool.enabled.is_(True),
                ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
            ).scalars().all()
            for r in rows:
                cfg = _pool_row_to_cfg(r, owner_id)
                if cfg.get("api_key"):
                    return cfg
            return None

        cfg = _first(user_id) if user_id is not None else None
        if cfg is None:
            cfg = _first(0)
        if cfg:
            src = "personal" if cfg.get("owner_id") == user_id and user_id is not None else "platform"
            return {"source": src, "cfg": cfg}
    if getattr(config, "EMBEDDING_API_KEY", ""):
        return {
            "source": "env",
            "cfg": {
                "provider": "bailian",
                "provider_label": provider_label("bailian"),
                "base_url": getattr(config, "EMBEDDING_BASE_URL", _BAILIAN_COMPAT),
                "model": getattr(config, "EMBEDDING_MODEL", "text-embedding-v3"),
                "api_key": config.EMBEDDING_API_KEY,
            },
        }
    return {"source": "mock", "cfg": None}


def public_view(cfg: dict | None) -> dict | None:
    """对外展示形态（不含 Key）。"""
    if not cfg:
        return None
    return {k: cfg[k] for k in ("provider", "provider_label", "base_url", "model")}


# ============ 多模型池解析（V5.0 P1） ============

def key_fingerprint(api_key: str) -> str:
    """Key 的确定性指纹（sha256 前 16 位），模型池判重专用。

    不能用 api_key_enc 判重：crypto.encrypt_obj 每次用随机 nonce 加密，
    同一个 Key 加密两次结果不同（实测 a == b → False），唯一约束会形同虚设。
    """
    import hashlib
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]


def _pool_row_to_cfg(row, owner_id: int) -> dict:
    """池行 → 候选 dict（复用 _row_to_cfg：解密失败置空 + 免费厂商服务端 Key 兜底）。"""
    cfg = _row_to_cfg(row, owner_id)
    cfg.update({
        "id": row.id,
        "owner_id": owner_id,
        "priority": row.priority,
        "paid": bool(row.paid),
        "note": row.note or "",
        "cooldown_until": row.cooldown_until,
    })
    return cfg


def resolve_pool_ex(db: Session, user: User | None,
                    slot: str = "text") -> tuple[list[dict], str | None]:
    """解析该槽位的候选池，并给出「归属」：(候选池, "personal" | "platform" | None)。

    优先级：用户池（user_id=user.id）非空 → 用它；否则平台池（user_id=0）非空 → 用它；
    两边都空 → ([], None)（调用方回落 resolve_effective 的单条逻辑，行为与上线池化前完全一致）。

    过滤：enabled=False 不进候选；Key 解不开且免费厂商也无服务端兜底 Key 的也会被剔除
    （避免把"必然 401"的候选塞进池子，白撞一次）。

    ⚠️ owner 是**归属**（哪一侧的池在生效），不等于"一定有可用候选"：池里条目存在但
    Key 全部解不开时，owner 仍返回该侧、候选列表却为空。这样 /llm/effective 才能显示
    「N 条 · 全部不可用」，而不是误报「未启用池」。

    db 为 None（无会话上下文，如单元测试直接调用 chat_stream）→ 视为池为空，回落单条。
    """
    if db is None:
        return [], None
    from app.models.llm_pool import LLMModelPool

    def _rows(owner_id: int) -> list:
        return list(db.execute(
            select(LLMModelPool).where(
                LLMModelPool.user_id == owner_id,
                LLMModelPool.slot == slot,
                LLMModelPool.enabled.is_(True),
            ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
        ).scalars().all())

    owner: str | None = None
    rows = _rows(user.id) if user is not None else []
    if rows:
        owner = "personal"
    else:
        rows = _rows(0)
        if rows:
            owner = "platform"

    out: list[dict] = []
    for r in rows:
        cfg = _pool_row_to_cfg(r, r.user_id)
        if cfg.get("api_key"):
            out.append(cfg)
    return out, owner


def resolve_pool(db: Session, user: User | None, slot: str = "text") -> list[dict]:
    """该槽位的可用候选池（resolve_pool_ex 的候选部分；签名与语义保持不变）。"""
    return resolve_pool_ex(db, user, slot)[0]


# ============ 对话流式 chat_stream（前端打字机体验） ============

# 角色身份映射：决定 AI 回复时的专业视角
_ROLE_IDENTITY = {
    "qa": "资深软件测试工程师，专精测试用例设计",
    "pm": "资深产品经理，专精需求分析与产品设计",
    "dev": "资深开发工程师，专精技术方案与代码实现",
    "kb": "知识库问答助手，严格基于提供的知识库检索内容回答问题",  # V4.2.2：RAG 问答专用中性人设
}

def _system_prompt_for(role: str, want_thinking: bool) -> str:
    """根据角色返回对应的系统提示词。role 非法或空时默认 qa（测试工程师）；kb 走 RAG 问答专用模板（V4.2.2）。"""
    if role == "kb":
        if want_thinking:
            return """你是试飞员（TestPilot），知识库问答助手。严格基于用户消息中提供的知识库检索内容回答问题。

【输出格式要求】
<think>
- 检索相关性：...
- 答案要点：...
</think>
（正式回复，直接针对问题归纳作答）

要求：
1. 思考过程用 <think>...</think> 包裹，可折叠不打扰用户阅读正式回复
2. 只依据知识库内容作答，检索内容未覆盖的就如实说明，不要编造
3. 简洁清晰，用 markdown 列表；不要输出需求理解/覆盖维度/澄清问题等用例生成话术"""
        return """你是试飞员（TestPilot），知识库问答助手。严格基于用户消息中提供的知识库检索内容回答问题。

【输出格式要求】直接给出正式回复，针对问题归纳作答。

要求：
1. 不要输出 <think>...</think> 等思考过程标记，也不要写「让我想想」这类元话语
2. 只依据知识库内容作答，检索内容未覆盖的就如实说明，不要编造
3. 简洁清晰，用 markdown 列表；不要输出需求理解/覆盖维度/澄清问题等用例生成话术"""
    identity = _ROLE_IDENTITY.get(role, _ROLE_IDENTITY["qa"])
    if want_thinking:
        return f"""你是试飞员（TestPilot），{identity}。

【输出格式要求】严格按下述结构：
<think>
- 需求理解：...
- 覆盖维度：...
- 风险点 / 边界条件：...
</think>
（正式回复，正面回答用户，先复述需求理解，再列出覆盖维度，每条简短解释，最后给 1-3 个澄清问题）

要求：
1. 思考过程用 <think>...</think> 包裹，可折叠不打扰用户阅读正式回复
2. 正式回复要可直接生成测试用例，澄清问题要具体（如"主要覆盖正面/反面/边界？"）
3. 简洁专业，避免客套；用 markdown 列表"""
    return f"""你是试飞员（TestPilot），{identity}。

【输出格式要求】直接给出正式回复，先复述需求理解，再列出覆盖维度，每条简短解释，最后给 1-3 个澄清问题。

要求：
1. 不要输出 <think>...</think>、<thinking>...</thinking> 或任何思考过程标记，也不要写「让我想想」这类元话语
2. 正式回复要可直接生成测试用例，澄清问题要具体（如"主要覆盖正面/反面/边界？"）
3. 简洁专业，避免客套；用 markdown 列表"""


def _build_messages(user_text: str, history: list | None, attached_text: str,
                    want_thinking: bool = True, role: str = "qa",
                    db: Session | None = None, user_id: int | None = None) -> list:
    """组装 messages：system + history + 当前用户消息（附加上下文拼在消息里）。

    attached_text 承载两类内容：迭代任务摘要、用户上传文档的正文。
    上限 6000 字与解析链路（_ai_parse_business）保持一致，避免长文档把上下文打爆。
    want_thinking=False 时换用不含思考要求的系统提示词（光靠参数关不掉标签输出）。
    role：AI 回复身份（qa/pm/dev），默认 qa。
    V5.10：用户在「提示词」弹窗自定义了该角色变体 → 整段覆盖内置默认。
    """
    override = prompt_service.get_chat_override(db, user_id, role, want_thinking)
    system_prompt = override if override else _system_prompt_for(role, want_thinking)
    msgs = [{"role": "system", "content": system_prompt}]
    if history:
        msgs.extend(history[-10:])  # 截断最多 10 轮避免超 token
    user_content = user_text or "（用户仅发送了附件，请结合下方的文档内容作答）"
    if attached_text:
        user_content += f"\n\n【附加上下文（任务摘要 / 用户上传文档）】\n{attached_text[:6000]}"
    msgs.append({"role": "user", "content": user_content})
    return msgs


def _mock_thinking_for(user_text: str, attach_name: str = "") -> str:
    """mock 模式：思考过程。attach_name 非空表示本轮带了附件。"""
    u = (user_text or "").strip()
    if not u:
        u = "（无文字描述，仅附件）"
    short = u[:60] + ("…" if len(u) > 60 else "")
    lines = []
    if attach_name:
        lines.append(f"- 已读取附件：《{attach_name}》")
    lines += [
        "- 需求理解：用户描述「" + short + "」",
        "- 覆盖维度：输入边界 / 错误处理 / 权限控制 / 数据一致性 / 异常兼容",
        "- 风险点：未明确业务类型（功能 / 接口 / App），未明确测试范围与通过标准",
    ]
    return "\n".join(lines) + "\n"


def _mock_reply_for(user_text: str, history: list | None = None, attach_name: str = "") -> str:
    """mock 模式：正式回复模板。

    attach_name 非空 = 本轮带了附件（服务端已读到文档内容），走「已读文档」模板，
    不再反问业务规则，避免出现「上传了文档还被追问要需求」的错位体验。
    """
    u = (user_text or "").strip() or "你描述的场景"
    if attach_name:
        head = f"已读取附件《{attach_name}》"
        if (user_text or "").strip():
            head += f"，结合你说的「{u[:30]}{'…' if len(u) > 30 else ''}」"
        return (
            head + "，我先理一下：\n\n"
            "**可能覆盖的测试维度：**\n"
            "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
            "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
            "3. **权限控制**：未登录、不同角色、跨用户访问\n"
            "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
            "5. **异常兼容**：网络中断、超时、重试机制\n\n"
            "要生成用例的话，点下面的「生成测试用例」我就按这份文档开工。"
        )
    # 检查是否有历史上下文
    has_history = history and len(history) > 0
    last_user_msg = ""
    if has_history:
        # 找最后一条用户消息作为上下文
        for m in reversed(history):
            if m.get('role') == 'user' and m.get('content'):
                last_user_msg = m['content'][:50]
                break
    if has_history and last_user_msg:
        return (
            f"基于之前的对话（关于「{last_user_msg}」），继续补充：\n\n"
            "**可能覆盖的测试维度：**\n"
            "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
            "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
            "3. **权限控制**：未登录、不同角色、跨用户访问\n"
            "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
            "5. **异常兼容**：网络中断、超时、重试机制\n"
        )
    return (
        f"好的，关于「{u[:30]}{'…' if len(u) > 30 else ''}」，我先理一下：\n\n"
        "**可能覆盖的测试维度：**\n"
        "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
        "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
        "3. **权限控制**：未登录、不同角色、跨用户访问\n"
        "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
        "5. **异常兼容**：网络中断、超时、重试机制\n\n"
        "为了生成更精准的用例，能否告诉我：\n"
        "1. 这属于哪类系统（Web 功能 / 接口 / App 端）？\n"
        "2. 需要覆盖哪些角色（管理员 / 普通用户 / 访客）？\n"
        "3. 有没有特定业务规则（如金额上限、审批流）？"
    )


async def _mock_stream_chunks(user_text: str, attach_name: str = "", want_thinking: bool = True):
    """mock 模式流式切片：think 段 + reply 段，逐段 yield。

    want_thinking=False（用户关了「深度思考」）时只吐正式回复，不出思考段，
    与真实模型关闭思考后的表现保持一致。
    """
    thinking = _mock_thinking_for(user_text, attach_name)
    reply = _mock_reply_for(user_text, attach_name=attach_name)
    full = f" 思考\n{thinking}\n思考\n{reply}" if want_thinking else reply
    chunk_size = 12
    i = 0
    while i < len(full):
        piece = full[i:i+chunk_size]
        yield ("delta", piece)
        await asyncio.sleep(0.04)
        i += chunk_size
    yield ("done", {"full": full, "clean": reply})

# ============ 「按需深度思考」自动判定 ============
# 对话默认不再无差别开启推理：开思考时模型每次要先吐上万字 reasoning，首字节要等
# 十几秒，而闲聊与简单指令完全不需要。改为按需判定 —— 前端「总是深度思考」显式开启
# 时走 True（每轮都推理），未表态时走下面的规则。
# 实测依据：见 docs/项目1-模型池与思考控制执行方案-V1.0.md（关思考首字节 1s 级 vs 开思考十几秒）。
_THINK_INTENT_KW = (
    "为什么", "原因", "排查", "定位", "根因", "分析", "对比", "评估", "诊断",
    "影响面", "风险", "方案", "选型", "架构", "设计", "区别", "哪个好",
)
_THINK_ERROR_RE = re.compile(
    r"Traceback|Error|Exception|报错|错误|失败|异常|超时|timeout|500|502|404", re.I
)
_THINK_MIN_LEN = 60      # 长描述阈值（字符数）


def should_deep_think(user_text: str, attach_name: str = "", kb_mode: bool = False) -> bool:
    """判定本轮对话是否需要「深度思考」（先推理再作答）。

    规则刻意偏保守：误开只是多等几秒，误关只是答得浅一点。
    不收录「帮我写 / 帮我改」类词 —— 它们多数会走任务生成链路，
    而那条链路本身就关思考，不该在对话里触发推理。

    ⚠️ 第二个参数只看「用户主动上传的附件名」，**不能**改成 chat_stream 收到的
    attached_text：那个值是「任务摘要 + RAG 检索结果 + 附件正文」的拼接体，而 RAG
    在勾选了知识库时（V5.8 起不选 = 不检索）几乎总是非空 —— 拿它当判据会让本函数
    恒返回 True，按需判定形同虚设。实测踩过：传 null 的「你好」也开了 272 个 think 事件。
    """
    t = (user_text or "").strip()
    if not t:
        return False
    if kb_mode:
        return False          # 知识库问答要的是忠实复述，推理反而引入幻觉
    if attach_name:
        return True           # 用户主动上传文档 → 多半是要深度分析
    if len(t) >= _THINK_MIN_LEN:
        return True
    if any(k in t for k in _THINK_INTENT_KW):
        return True
    if _THINK_ERROR_RE.search(t):
        return True
    return False


async def chat_stream(
    db: Session,
    user: User | None,
    user_text: str,
    history: list | None,
    attached_text: str = "",
    attach_name: str = "",
    enable_thinking: bool | None = None,
    roles: list[str] | None = None,
    kb_mode: bool = False,
):
    """对话流式生成器（async）。

    走向：用户自配(user) 或 平台默认(platform) 有可用文本配置时走真实模型，
    否则走 mock 流式。真实调用失败时降级：未产出内容则追加 mock 兜底，
    已产出半截内容则仅提示中断（避免真假内容混排）。

    attached_text  ：附加上下文（任务用例摘要 / 上传文档正文），拼进用户消息注入模型。
    attach_name    ：附件文件名，仅用于 mock 模式体现「已读到文档」。
    enable_thinking：思考三态。None（默认）→ 按需自动判定（should_deep_think）；
                     True → 注入 enable_thinking 并让 think 事件透传（「总是深度思考」开启）；
                     False → 换用无思考要求的系统提示词、不注入参数，且丢弃 think 事件
                     （个别模型不认参数仍会吐推理，丢掉才符合"关了就不显示面板"的预期）。
    roles：参与角色列表（pm/qa/dev），取第一个合法角色作为 AI 回复身份；空则默认 qa。
    """
    if enable_thinking is None:
        # 判据用 attach_name 而非 attached_text：后者含 RAG 检索结果、默认非空（详见函数注释）
        enable_thinking = should_deep_think(user_text or "", attach_name, kb_mode)
    eff = resolve_effective(db, user)
    # 平台默认模型（source=platform，免费厂商由服务端 Key 兜底）同样算"已配好模型"。
    # 旧逻辑只认 user，导致平台默认配置被判为未配置、聊天恒走 mock 模板。
    use_real = eff.get("source") in ("user", "platform") and eff.get("text") is not None
    # 角色选择：kb_qa 模式强制走「知识库助手」中性人设（V4.2.2，避免 qa 模板污染 RAG 回答）；
    # 其余取列表中第一个合法角色，空则默认 qa（测试工程师）
    if kb_mode:
        role = "kb"
    else:
        role = next((r for r in (roles or []) if r in _ROLE_IDENTITY), "qa")
    messages = _build_messages(user_text or "", history, attached_text, enable_thinking,
                               role=role, db=db, user_id=user.id if user else None)
    if not use_real:
        # 演示模式（AITF_ALLOW_DEMO=1）才走旧演示话术；默认不静默兜底
        if config.AITF_ALLOW_DEMO:
            async for ev in _mock_stream_chunks(user_text or "", attach_name, enable_thinking):
                yield ev
            return
        yield ("notice", "未配置可用的文本模型，无法生成回复。请先在「设置 → 模型配置」中配置模型。")
        yield ("done", {"full": "", "degraded": True})
        return
    cfg = eff["text"]
    # V5.0 P1：优先走模型池（多条候选，撞限流自动切换）；池空时回落单条配置
    client = llm_pool.build_client(db, user, "text")
    if client is None:
        client = OpenAICompatClient(cfg["base_url"], cfg["api_key"], cfg["model"])
    produced = False  # 是否已吐出过真实内容（决定出错时能否安全降级到 mock）
    err = ""
    try:
        # 真实模型是同步 generator（httpx 同步流式），在线程池里跑
        loop = asyncio.get_running_loop()
        # 30s：真模型首字节常见 2-5s，原来 8s 会被误判成 ReadTimeout
        sync_gen = client.chat_stream(messages, timeout=30.0, enable_thinking=enable_thinking)
        while True:
            ev = await loop.run_in_executor(None, lambda: next(sync_gen, None))
            if ev is None:
                break
            # 客户端把网络/HTTP 异常也表达成 ("error", msg) 后 return（不抛异常），
            # 这里拦下来统一走降级，避免裸错误直接丢给用户、兜底内容被跳过
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "error":
                err = str(ev[1])
                break
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "think":
                if not enable_thinking:
                    continue   # 用户关了思考：丢弃推理内容，不展示思考面板
                yield ev
                continue
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "delta" and ev[1]:
                produced = True
            yield ev
    except Exception as e:  # noqa: BLE001  LLMError / httpx 异常一律降级
        err = f"{e.__class__.__name__}: {str(e)[:120]}"

    if not err:
        return
    if produced:
        # 已有半截真实内容：不追加模板（真假内容混排更难读），提示后正常收尾
        yield ("notice", f"生成中断：{err[:120]}")
        yield ("done", {"full": "", "degraded": True})
        return
    if config.AITF_ALLOW_DEMO:
        # 演示模式：提示已降级，再继续输出演示内容，用户至少能看到兜底回复
        yield ("notice", f"真实模型调用失败，已切换演示模式：{err[:120]}")
        async for ev in _mock_stream_chunks(user_text or "", attach_name, enable_thinking):
            yield ev
        return
    # 默认不静默兜底：明确告知失败原因，不返回演示话术（避免假内容混入）
    yield ("notice", f"真实模型调用失败：{err[:120]}")
    yield ("done", {"full": "", "degraded": True})


# ============ 视觉增强（两段式第一步） ============

_IMG_MD = re.compile(r"!\[([^\]]*)\]\((\s*(?:data:image/|https?://)[^)\s]+)\)")
_IMG_HTTP = re.compile(r'(?:data:image/|https?://)[^\s)"\']+', re.I)


def extract_image_refs(text: str) -> list[str]:
    """提取图片引用：markdown 图片语法（data: URI / http URL）+ 裸贴的图片 URL / data URI。"""
    refs = [m.group(2).strip() for m in _IMG_MD.finditer(text)]
    refs += _IMG_HTTP.findall(text)   # 裸贴的引用；md 里的重复项由下方去重消除
    # 去重 + 上限（防止文档几十张图把视觉模型跑爆）
    seen, out = set(), []
    for u in refs:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def vision_enrich(text: str, vision_client: OpenAICompatClient) -> tuple[str, int]:
    """把文本中的图片引用替换为视觉模型产出的文字描述。返回 (新文本, 处理图片数)。"""
    refs = extract_image_refs(text)
    if not refs:
        return text, 0
    for ref in refs:
        try:
            desc = vision_client.describe_image(ref)
            desc = " ".join(desc.split())[:1500]
            block = f"\n\n【截图解读】{desc}\n"
            text = text.replace(ref, block)
        except LLMError:
            # 单图失败不阻断流程，保留原引用
            continue
    return text, len(refs)


# ============ 连通测试 ============

def test_connectivity(base_url: str, api_key: str, model: str, kind: str = "chat") -> dict:
    """发一条最小请求验证 Key / 端点 / 模型可用。

    kind="chat"：POST {base_url}/chat/completions；kind="embedding"：POST {base_url}/embeddings。
    """
    import time
    t0 = time.time()
    try:
        if kind == "embedding":
            import httpx
            url = base_url.rstrip("/") + "/embeddings"
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": "测试"},
                timeout=20.0,
            )
            if resp.status_code != 200:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            dims = len(data["data"][0]["embedding"]) if data.get("data") else None
            return {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000),
                "reply": f"向量维度 {dims}",
                "dimensions": dims,
            }
        reply = OpenAICompatClient(base_url, api_key, model).chat(
            [{"role": "user", "content": "回复「ok」两个字即可。"}],
            temperature=0, max_tokens=16,
            enable_thinking=False,   # 连通测试只验 Key/端点/模型，无需思考（省时）
        )
        return {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000),
            "reply": (reply or "")[:80],
        }
    except LLMError as e:
        return {"ok": False, "latency_ms": round((time.time() - t0) * 1000), "error": str(e)[:300]}
