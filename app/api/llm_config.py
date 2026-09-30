"""LLM 模型配置端点（V2.4 FR-I；V5.4 起单条配置已下线，模型池是唯一配置入口）。

- GET  /api/llm/providers        厂商预设列表（登录即可见）
- GET  /api/llm/effective        当前生效模型（任务页展示）
- POST /api/llm/test             连通测试（不落库）
- POST /api/llm/test-default/{slot}  测试当前生效模型（所有登录角色）

单条配置端点（/llm/config、/llm/platform-config）已随 V5.4 移除；
个人/平台模型统一走 /api/llm/pool、/api/llm/platform-pool（见 llm_pool.py）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.providers import PROVIDERS
from app.models.llm_pool import LLMModelPool
from app.models.user import User
from app.schemas.llm_config import ChatIn, LLMTestIn
from app.services import llm_pool, llm_service
from app.core.config import AITF_ALLOW_DEMO

router = APIRouter()


# ---------- 厂商预设 ----------

@router.get("/llm/providers")
def list_providers(user: User = Depends(get_current_user)):
    return PROVIDERS


# ---------- 当前生效模型 ----------

@router.get("/llm/effective")
def get_effective(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    eff = llm_service.resolve_effective(db, user)
    emb = llm_service.resolve_embedding(db, user.id)

    # V5.0 P2：池优先展示。池非空时实际调度的就是池（见 llm_pool.build_client），
    # 此处若仍展示单条配置，会出现"界面显示 A、实际用 B"的矛盾。
    p_text = llm_pool.pool_first(db, user, "text")
    p_vision = llm_pool.pool_first(db, user, "vision")
    p_emb = llm_pool.pool_first(db, user, "embedding")
    return {
        "source": "pool" if p_text else eff["source"],
        "text": llm_service.public_view(p_text or eff["text"]),
        "vision": llm_service.public_view(p_vision or eff["vision"]),
        "embedding": llm_service.public_view(p_emb or emb["cfg"]),
        "embedding_source": "pool" if p_emb else emb["source"],
        # V5.1 P1：三槽的池现状（各含 owner/total/enabled/available/cooling/hit）。
        # 供「模型调度」摘要卡使用 —— 走的是 get_current_user，**访客也能看到条数**，
        # 从而不再需要前端为算条数而拉池列表（那一组请求访客必 403）。
        # 上方 5 个旧字段全部保留不动，前端可平滑过渡。
        "pools": {
            s: llm_pool.pool_stats(db, user, s)
            for s in ("text", "vision", "embedding")
        },
    }


# ---------- 生效模型可用性测试（不落库） ----------

# 错误归类与文案已上移到 app/services/llm_pool.py —— service 层的池化调度也要用，
# 放在 API 层会造成 service → API 的反向依赖。这里保留同名别名，调用点零改动。
_ERR_LABEL = llm_pool.ERR_LABEL
_classify_error = llm_pool.classify_error


@router.post("/llm/test-default/{slot}")
def test_default_slot(slot: str,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """测试当前生效模型可用性（服务端解析配置并持有 Key，客户端无需提供；结果不落库）。

    - 所有登录角色可用（含访客）：解析优先级与 resolve_effective 一致（我的配置 > 平台默认）
    - 错误归类：auth / rate_limit / quota / network / server / not_configured / other
    """
    if slot not in ("text", "vision", "embedding"):
        raise HTTPException(400, detail="slot 只能是 text / vision / embedding")
    if slot == "embedding":
        # 向量模型走独立解析链（用户配置 > 平台 > env > mock），mock/未配置明确报 not_configured
        cfg = llm_pool.pool_first(db, user, slot) or llm_service.resolve_embedding(db, user.id)["cfg"]
    else:
        # V5.0 P2：池优先（与 build_client 实际调度一致），池空才回落单条配置
        pooled = llm_pool.pool_first(db, user, slot)
        eff = llm_service.resolve_effective(db, user)
        cfg = pooled or (eff["text"] if slot == "text" else eff["vision"])
    if not cfg:
        return {"ok": False, "err_type": "not_configured",
                "error_label": _ERR_LABEL["not_configured"],
                "error": "该槽位未配置或未生效（服务器未配对应厂商 Key）", "model": None}
    result = llm_service.test_connectivity(
        cfg["base_url"], cfg["api_key"], cfg["model"],
        kind="embedding" if slot == "embedding" else "chat",
    )
    out = {**result, "model": cfg["model"], "provider_label": cfg["provider_label"]}
    if not out.get("ok"):
        et = _classify_error(out.get("error", ""))
        out["err_type"] = et
        out["error_label"] = _ERR_LABEL.get(et, _ERR_LABEL["other"])
    return out


# ---------- 连通测试 ----------

@router.post("/llm/test")
def test_llm(body: LLMTestIn,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """测试连通（不落库）。api_key 留空 → 复用已保存的 Key（先当前用户，后平台）。"""
    api_key = body.api_key.strip()
    if not api_key:
        if body.provider == "ollama":
            # 本地端点不校验 Key：任意占位即可触发连通测试
            api_key = "ollama"
        else:
            server_k = llm_service._server_key(body.provider)
            if server_k:
                # 免费厂商：Key 由服务端环境变量提供，直接兜底；
                # 不复用已存 Key（否则会拿个人百炼 Key 去打 ModelScope 端点 → 401）
                api_key = server_k
            else:
                # 从模型池复用同厂商已存 Key（个人池优先，admin 追加平台池）
                owner_ids = [user.id] + ([0] if user.role == "admin" else [])
                slots = ("embedding",) if body.kind == "embedding" else ("vision", "text")
                for oid in owner_ids:
                    for slot in slots:
                        row = db.execute(
                            select(LLMModelPool).where(
                                LLMModelPool.user_id == oid,
                                LLMModelPool.slot == slot,
                                LLMModelPool.provider == body.provider,
                                LLMModelPool.enabled.is_(True),
                            ).order_by(LLMModelPool.priority.asc(), LLMModelPool.id.asc())
                        ).scalars().first()
                        if row:
                            # 复用 _pool_row_to_cfg：解密失败自动置空 + 免费厂商由服务端环境变量 Key 兜底
                            cfg = llm_service._pool_row_to_cfg(row, oid)
                            if cfg.get("api_key"):
                                api_key = cfg["api_key"]
                                break
                    if api_key:
                        break
    if not api_key:
        raise HTTPException(400, detail="未找到可用的 API Key（免费厂商请确认服务端已配置对应环境变量 Key，或在表单中手动输入）")
    if not body.base_url.strip() or not body.model.strip():
        raise HTTPException(400, detail="base_url 与 model 不能为空")
    return llm_service.test_connectivity(body.base_url.strip(), api_key, body.model.strip(), kind=body.kind)


# ---------- 首页对话流（AI 测试工程师对话） ----------

_CHAT_SYSTEM_PROMPT = (
    "你是一位资深软件测试工程师，正在与用户沟通测试用例设计需求。"
    "请用中文、简洁、专业地回复。你的目标是：\n"
    "1. 理解用户给出的测试需求；\n"
    "2. 简要分析可以覆盖哪些测试维度（如功能、边界、异常、权限、兼容性等）；\n"
    "3. 针对不清晰的地方提出 1-3 个澄清问题；\n"
    "4. 如果用户已表达清楚，可在回复末尾引导用户点击「生成测试用例」按钮开始生成；\n"
    "5. 不要一次性输出大量用例表格，保持对话感。"
)


@router.post("/chat")
def chat(body: ChatIn,
         db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    """首页对话流：用户发消息 → AI 测试工程师回复。

    - 复用当前生效文本模型（用户配置 > 平台默认 > 服务器环境变量 > mock 兜底）
    - 未配置真实模型时返回 mock 回复，仍可演示完整交互
    """
    eff = llm_service.resolve_effective(db, user)
    cfg = eff.get("text")
    messages = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
    for h in (body.history or []):
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": body.message})

    if not cfg or not cfg.get("api_key"):
        if AITF_ALLOW_DEMO:
            # 演示模式（AITF_ALLOW_DEMO=1）才给模板回复，明确标注 used_mock
            reply = (
                f"收到你的需求：「{body.message[:60]}{'…' if len(body.message) > 60 else ''}」。\n\n"
                "从测试设计角度，我建议先明确以下几个维度：\n"
                "1. **功能路径**：正常流程与异常流程分别是什么？\n"
                "2. **边界条件**：有无长度、次数、金额、时间等限制？\n"
                "3. **权限与状态**：不同角色/状态下行为是否一致？\n\n"
                "如果你已经考虑清楚，可以直接点击下方的「生成测试用例」按钮，我会调用 4 个 Agent 开始生成。"
            )
            return {"reply": reply, "source": eff.get("source", "mock"), "model": None, "used_mock": True}
        # 默认不静默兜底：明确告诉用户没配模型
        raise HTTPException(
            status_code=400,
            detail="未配置可用的文本模型，无法生成回复。请先在「设置 → 模型配置」中配置模型。",
        )

    try:
        # V5.0 P1：模型池优先（撞限流自动切换）；池空回落单条生效配置
        client = llm_pool.build_client(db, user, "text") or llm_service.OpenAICompatClient(
            cfg["base_url"], cfg["api_key"], cfg["model"])
        reply = client.chat(messages, temperature=0.5, max_tokens=2048)
        return {
            "reply": reply,
            "source": eff.get("source"),
            "model": llm_pool.describe_model(db, user, "text") or cfg.get("model"),
            "provider_label": cfg.get("provider_label"),
            "used_mock": False,
        }
    except llm_service.LLMError as e:
        if AITF_ALLOW_DEMO:
            # 演示模式：真实失败时回退模板，保证前端不挂
            reply = (
                f"收到你的需求：「{body.message[:60]}{'…' if len(body.message) > 60 else ''}」。\n\n"
                f"（真实模型暂时不可用：{str(e)[:80]}，已切换为兜底回复）\n\n"
                "建议先明确：功能路径、边界条件、权限与状态。确认后点击下方「生成测试用例」开始生成。"
            )
            return {"reply": reply, "source": eff.get("source"), "model": cfg.get("model"), "used_mock": True}
        # 默认不静默兜底：真实失败原样暴露
        raise HTTPException(status_code=502, detail=f"真实模型调用失败：{str(e)[:200]}")
