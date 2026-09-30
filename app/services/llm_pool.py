"""多模型池调度（V5.0 P1；V5.4 起单条配置 llm_configs 已下线，池是唯一配置入口）。

对外只有一个入口：`build_client(db, user, slot)`。

    pools 非空 → PoolClient（撞限流/额度尽自动换下一个候选）
    池为空     → 回落 resolve_effective（现内部为：个人池 > 平台池 > env > mock）
    都不可用   → None（调用方按"未配置模型"处理）

错误分类（`classify_error`）原先写在 app/api/llm_config.py，本模块在 service 层
也要用 → 上移到此处，API 层改为 import，避免 API ↔ service 反向依赖。
"""
from __future__ import annotations

import re
from datetime import timedelta

from app.core import config
from app.core.utils import utcnow
from app.services.langchain_client import (
    LangChainClient,
    _HttpxCompatClient,
    LLMError,
    _TIMEOUT,
)

# 与 llm_service 同一套后端开关（langchain 默认 / httpx 应急）
_LLM_BACKEND = getattr(config, "AITF_LLM_BACKEND", "langchain")
_Client = LangChainClient if _LLM_BACKEND == "langchain" else _HttpxCompatClient


# ============ 错误归类与冷却策略 ============

ERR_LABEL = {
    "auth": "API Key 已过期或无效",
    "rate_limit": "限流 · 请稍后再试",
    "quota": "额度已用尽",
    "network": "网络错误 · 请稍后再试",
    "server": "模型服务异常 · 请稍后再试",
    "not_found": "端点或模型不存在",
    "not_configured": "未配置或未生效",
    "other": "调用失败",
}

# 冷却时长（秒）；None = 不冷却（配置性问题，改配置即恢复）
COOLDOWN_SECONDS: dict[str, float] = {
    "rate_limit": 300,        # 限流：5 分钟后大概率恢复
    "server": 60,             # 服务端 5xx：1 分钟
    "network": 60,            # 超时 / 连接失败：1 分钟
    "auth": 1800,             # Key 失效：30 分钟（改 Key 会清冷却）
    "not_found": 1800,        # 模型/端点不存在：30 分钟
    "other": 1800,            # 400 参数错等：30 分钟
    "quota": None,            # 额度尽：按日，单独算到次日 00:05
}


def classify_error(msg: str) -> str:
    """从 LLMError 文本归类错误类型（HTTP 状态码优先）。

    与迁移前的 api/llm_config._classify_error 行为一致，仅把 404 单独归为
    not_found（原先落进 other，前端提示不准确）。
    """
    m = re.search(r"HTTP (\d{3})", msg or "")
    if m:
        code = int(m.group(1))
        if code in (401, 403):
            return "auth"
        if code == 429:
            return "rate_limit"
        if code == 402:
            return "quota"
        if code == 404:
            return "not_found"
        if code >= 500:
            return "server"
        return "other"
    if "网络错误" in (msg or "") or "timeout" in (msg or "").lower():
        return "network"
    return "other"


def cooldown_until_for(kind: str, now=None):
    """按错误类型给出冷却到期时间（naive UTC）；无需冷却返回 None。"""
    now = now or utcnow()
    if kind == "quota":
        # 按日额度：冷却到次日 00:05（留 5 分钟给服务商重置）
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        return nxt
    secs = COOLDOWN_SECONDS.get(kind)
    if not secs:
        return None
    return now + timedelta(seconds=secs)


def _cooling(cand: dict, now) -> bool:
    cd = cand.get("cooldown_until")
    return bool(cd) and cd > now


# ============ 池化客户端 ============

class PoolClient:
    """多模型池客户端：对外接口与 OpenAICompatClient 完全一致。

    调度规则（不是"失败就无脑换"）：

    | 错误 | 动作 |
    |---|---|
    | rate_limit / quota / server / network | 换下一个，并给该条写冷却 |
    | auth / not_found / other(400)          | 换下一个，写 30 分钟冷却（改配置即清） |
    | 调用成功但解析出 0 条（质量类）        | **不切换** —— 语义是"模型答不出"，换一个大概率同样答不出 |

    冷却期内该条被过滤；全池都在冷却 → 取最早到期的一条"破格尝试"，避免任务无限等待。
    健康状态写回走独立 Session（可能在 executor 线程里调用，不能共用请求级 session）。
    """

    def __init__(self, candidates: list[dict], slot: str = "text", persist: bool = True):
        if not candidates:
            raise LLMError("模型池为空")
        self.candidates = list(candidates)
        self.slot = slot
        self.persist = persist
        self._skip: set[int] = set()      # 本实例内已判失败的候选（避免同任务内反复撞）
        self.switch_log: list[dict] = []  # 切换留痕：[{from, to, reason}]
        for i, c in enumerate(self.candidates):
            c["_seq"] = i

    # ---------- 内部工具 ----------

    def _client(self, cand: dict):
        return _Client(cand["base_url"], cand["api_key"], cand["model"])

    def _ordered(self) -> list[dict]:
        now = utcnow()
        avail = [c for c in self.candidates if c["_seq"] not in self._skip]
        ready = [c for c in avail if not _cooling(c, now)]
        if ready:
            return ready
        if not avail:
            return []
        # 全池冷却中 → 破格尝试冷却最早到期的一条
        return [min(avail, key=lambda c: c.get("cooldown_until") or now)]

    def _persist(self, cand: dict, ok: bool, err: str = "", kind: str = "") -> None:
        """健康状态写回（独立会话 + 短事务；任何异常都不得阻断主调用）。"""
        if not self.persist or not cand.get("id"):
            return
        try:
            from app.core.db import SessionLocal
            from app.models.llm_pool import LLMModelPool

            db = SessionLocal()
            try:
                row = db.get(LLMModelPool, cand["id"])
                if row is None:
                    return
                if ok:
                    row.success_count = (row.success_count or 0) + 1
                    row.cooldown_until = None
                    row.last_error = None
                else:
                    row.fail_count = (row.fail_count or 0) + 1
                    row.last_error = (err or "")[:500]
                    row.last_error_at = utcnow()
                    row.cooldown_until = cooldown_until_for(kind)
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001  状态写回失败不影响这次生成
            pass

    def _on_fail(self, cand: dict, err: str) -> str:
        kind = classify_error(err)
        self._skip.add(cand["_seq"])
        self._persist(cand, False, err, kind)
        return kind

    def _note_switch(self, cand: dict, errors: list[str]) -> None:
        if errors:
            self.switch_log.append({
                "from": errors[-1].split("：")[0],
                "to": cand.get("model", ""),
                "reason": errors[-1],
            })

    # ---------- 对外接口 ----------

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
             timeout: float = _TIMEOUT, enable_thinking: bool | None = None) -> str:
        errors: list[str] = []
        for cand in self._ordered():
            try:
                out = self._client(cand).chat(
                    messages, temperature=temperature, max_tokens=max_tokens,
                    timeout=timeout, enable_thinking=enable_thinking,
                )
            except LLMError as e:
                msg = str(e)
                self._on_fail(cand, msg)
                errors.append(f'{cand.get("model")}：{msg}')
                continue
            self._persist(cand, True)
            self._note_switch(cand, errors)
            return out
        raise LLMError(self._summary(errors))

    def generate(self, prompt: str, enable_thinking: bool | None = None) -> str:
        return self.chat([{"role": "user", "content": prompt}],
                         enable_thinking=enable_thinking)

    def describe_image(self, image_url: str, hint: str = "") -> str:
        errors: list[str] = []
        for cand in self._ordered():
            try:
                out = self._client(cand).describe_image(image_url, hint)
            except LLMError as e:
                msg = str(e)
                self._on_fail(cand, msg)
                errors.append(f'{cand.get("model")}：{msg}')
                continue
            self._persist(cand, True)
            return out
        raise LLMError(self._summary(errors))

    def chat_stream(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
                    timeout: float = _TIMEOUT, enable_thinking: bool | None = None):
        """流式：事件协议与单条客户端一致（think / delta / done / error）。

        ⚠️ 只在「未产出任何 delta」时允许换候选 —— 已吐字再换会出现两个模型的
        文本混排，用户看到的是语义断裂的拼接内容。
        """
        errors: list[str] = []
        for cand in self._ordered():
            produced = False
            failed = ""
            try:
                for ev in self._client(cand).chat_stream(
                        messages, temperature=temperature, max_tokens=max_tokens,
                        timeout=timeout, enable_thinking=enable_thinking):
                    if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "error":
                        failed = str(ev[1])
                        break
                    if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "delta" and ev[1]:
                        produced = True
                    yield ev
            except Exception as e:  # noqa: BLE001  客户端构造失败等
                failed = f"{e.__class__.__name__}: {str(e)[:120]}"

            if not failed:
                self._persist(cand, True)
                self._note_switch(cand, errors)
                return
            self._on_fail(cand, failed)
            errors.append(f'{cand.get("model")}：{failed}')
            if produced:
                # 已吐字：不切换（避免真假内容混排），提示后收尾
                yield ("done", {"full": "", "clean": "", "degraded": True,
                                "error": failed})
                return
        yield ("error", self._summary(errors))

    @staticmethod
    def _summary(errors: list[str]) -> str:
        if not errors:
            return "模型池无可用候选"
        return "模型池全部候选失败：" + "；".join(errors[:3])


# ============ 对调用点暴露的门面 ============

def resolve_pool(db, user, slot: str = "text") -> list[dict]:
    """解析该槽位的候选池（延迟 import 避免与 llm_service 循环依赖）。"""
    from app.services import llm_service
    return llm_service.resolve_pool(db, user, slot)


def _single_client_cls():
    """回落单条时取 llm_service.OpenAICompatClient。

    ⚠️ 必须运行时从 llm_service 取（而不是用本模块的 `_Client` 常量）：一是保证
    "单条配置走哪个后端"只有一个权威来源，二是既有测试都通过替换
    `llm_service.OpenAICompatClient` 来模拟模型，patch 点必须保持一致。
    """
    from app.services import llm_service
    return llm_service.OpenAICompatClient


def build_client(db, user, slot: str = "text"):
    """构建该槽位可用的客户端：池优先，池空回落单条，都不可用返回 None。"""
    pool = resolve_pool(db, user, slot)
    if pool:
        return PoolClient(pool, slot=slot)

    from app.services import llm_service
    if slot == "embedding":
        if db is None:
            return None      # embedding 解析必须查库，无会话时视为不可用
        cfg = llm_service.resolve_embedding(db, user.id if user is not None else None).get("cfg")
    else:
        eff = llm_service.resolve_effective(db, user)
        cfg = eff.get("text") if slot == "text" else eff.get("vision")
    if not cfg or not cfg.get("api_key"):
        return None
    try:
        return _single_client_cls()(cfg["base_url"], cfg["api_key"], cfg["model"])
    except LLMError:
        return None


def describe_model(db, user, slot: str = "text") -> str:
    """展示用模型描述：池非空 →「模型池 N 条 · 首选 X」，池空 →「X · 厂商」。"""
    from app.services import llm_service
    pool = resolve_pool(db, user, slot)
    if pool:
        return f'模型池 {len(pool)} 条 · 首选 {pool[0].get("model", "")}'
    if slot == "embedding":
        cfg = llm_service.resolve_embedding(db, user.id if user is not None else None).get("cfg")
    else:
        eff = llm_service.resolve_effective(db, user)
        cfg = eff.get("text") if slot == "text" else eff.get("vision")
    if not cfg:
        return ""
    return f'{cfg.get("model", "")} · {cfg.get("provider_label", "")}'.strip(" ·")


def is_cooling(cand: dict) -> bool:
    """候选是否处于冷却期（API 层展示健康状态用）。"""
    return _cooling(cand, utcnow())


def pool_first(db, user, slot: str = "text") -> dict | None:
    """该槽位池中「当前实际会用」的那条候选（展示与一键测通专用）。

    取 priority 升序里第一条未冷却的；若全部处于冷却期 → 返回第一条
    （界面显示黄灯提示，而不是整块空白）。池为空 → None，调用方回落单条配置。

    与 build_client 的口径一致：池非空时实际调度用的就是池，所以
    /llm/effective 与 /llm/test-default 必须看池，否则界面与实际行为不符。
    """
    pool = resolve_pool(db, user, slot)
    if not pool:
        return None
    now = utcnow()
    for cand in pool:
        if not _cooling(cand, now):
            return cand
    return pool[0]


def pool_stats(db, user, slot: str = "text") -> dict:
    """该槽位池的现状统计（`/llm/effective` 的 pools 摘要用；只读、无副作用）。

    owner / active 与 resolve_pool_ex（即 build_client 的真实调度口径）同源，
    所以摘要卡说「由谁接管」与实际行为永远一致 —— 包括访客：effective 走
    get_current_user，访客也能拿到平台池的条数。

    total / enabled 取自该槽位**生效池**的全部条目（含已停用），与池卡卡头
    「模型池 N 条 · 可用 M」同一口径，避免上下两处数字打架。
    """
    from app.services import llm_service

    pool, owner = llm_service.resolve_pool_ex(db, user, slot)
    total, enabled = _pool_row_counts(db, user, owner, slot)
    now = utcnow()
    available = sum(1 for c in pool if not _cooling(c, now))
    hit = 0
    for i, cand in enumerate(pool, start=1):
        if not _cooling(cand, now):
            hit = i
            break
    return {
        "active": available > 0,   # 有可用候选才算池真的接管
        "owner": owner,
        "total": total,            # 含已停用，与池卡卡头同源
        "enabled": enabled,
        "available": available,
        "cooling": len(pool) - available,
        "hit": hit,                # 1-based 优先序号；0 = 无可用候选
    }


def _pool_row_counts(db, user, owner: str | None, slot: str) -> tuple[int, int]:
    """(该槽位生效池的全部条数, 启用条数)。无池或 owner 为 None → (0, 0)。"""
    if db is None or owner is None:
        return 0, 0
    from sqlalchemy import select

    from app.models.llm_pool import LLMModelPool

    owner_id = user.id if (owner == "personal" and user is not None) else 0
    flags = db.execute(
        select(LLMModelPool.enabled).where(
            LLMModelPool.user_id == owner_id, LLMModelPool.slot == slot)
    ).scalars().all()
    return len(flags), sum(1 for f in flags if f)
