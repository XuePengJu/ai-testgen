"""生成核心编排：解析单元 →（真实模型，支持多角色）→ 合并去重 → 编号。

V3.1 多角色协作（轻量版）：每个测试点可按角色（产品 pm / 测试 qa / 开发 dev）
分别以各自视角生成用例，再合并去重。默认仅测试（qa），行为与旧版完全一致。

演示模式开关（AITF_ALLOW_DEMO）：默认关闭（0）。
- 关闭：未注入模型 / 模型调用失败 → 直接抛错，绝不静默返回假用例。
- 开启：恢复旧行为（mock_generate 出规则化演示用例），仅用于本地或现场演示。

D 修复（2026-09-24）：LLM 输出解析不再用贪婪正则 ``re.search(r"\\[.*\\]")``，
改为公共工具 ``src.utils.jsonx`` 的「括号配对切片 + 逐候选宽松解析」，并把
「调用成功但解析出 0 条」显性上报（logger.warning + CaseGenerator.parse_failures）。

V1.3 提速（2026-10-11）：
- **强制关思考**：结构化抽取（只输出 JSON）开思考会让耗时可翻数倍，且思考 token 挤占
  max_tokens 把正文截断成空。统一走 ``call_generate`` 传 ``enable_thinking=False``。
- **子任务并发**：``generate`` 默认并发 ``GENERATE_CONCURRENCY`` 路（环境变量
  ``AITF_GEN_CONCURRENCY`` 可调，1 = 退回串行）。实测 7 个子任务串行 353s，
  并发后墙钟从「累加」变「取最大」。
- 用例顺序仍严格按 unit × role 原顺序收敛后再去重编号，保证前端预览序号与
  完成后的正式编号走向一致。
"""
import inspect
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import settings
from src.models.testcase import TestCase, RequirementUnit, align_step_expectations
from src.generator.mock_generator import mock_generate
from src.utils import jsonx

logger = logging.getLogger(__name__)

# 角色标识 → 展示名（进度回调/汇总用）
ROLES_ORDER = ["pm", "qa", "dev"]
ROLE_LABELS = {"pm": "产品", "qa": "测试", "dev": "开发"}


def _env_int(name: str, default: int, lo: int = 0) -> int:
    """容错读整数环境变量：非法值回落默认，避免一个手滑的环境变量把生成打挂。"""
    try:
        return max(lo, int(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


# 子任务并发度（V1.3）：N 个「测试点 × 角色」并行调用模型，墙钟耗时从「累加」变「取最大」。
# 1 = 退回旧的严格串行行为；调大可进一步缩短，但更容易撞 429（模型池有冷却+切换兜底）。
GENERATE_CONCURRENCY = _env_int("AITF_GEN_CONCURRENCY", 4, lo=1)


def _accepts_enable_thinking(fn) -> bool:
    """client.generate 是否接受 enable_thinking 关键字。

    测试替身（以及外部注入的极简 client）常只收一个 prompt，传关键字会 TypeError；
    不可内省的对象一律按「不支持」处理，回落单参调用。
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "enable_thinking" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def call_generate(client, prompt: str) -> str:
    """调模型生成用例，并强制关闭思考（enable_thinking=False）。

    结构化抽取（要求「只输出 JSON」）开思考有三重代价：
      1. 整个来回耗时可翻数倍（思考 token 也在解码）；
      2. 思考 token 挤占 max_tokens，正文被截断成空（表现为「解析无产出 · 原文 0 字」）；
      3. 正文退化成摘要式少量结果。
    项目内既有结论见 app/services/memory/llm.py 与 app/services/langchain_client.py 的
    ``chat`` docstring；本函数把该结论落到用例生成这条链路上。
    """
    if _accepts_enable_thinking(client.generate):
        return client.generate(prompt, enable_thinking=False)
    return client.generate(prompt)

# 角色 → 模板后缀（qa 用现有模板，无后缀）
_ROLE_TPL_SUFFIX = {"pm": "_pm", "dev": "_dev", "qa": ""}


class NoModelError(RuntimeError):
    """未注入真实模型、且未开启演示模式时抛出（调用方据此向用户暴露失败原因）。"""


def _no_model_hint(role: str) -> str:
    return (
        f"未配置可用的文本模型，无法生成真实用例（角色 {role}）。"
        "请先在「设置 → 模型配置」配置模型；"
        "如需本地演示可设环境变量 AITF_ALLOW_DEMO=1。"
    )


def parse_roles(raw) -> list[str]:
    """把任务上的 roles 配置解析为合法角色列表（未知角色忽略，空则默认 qa）。"""
    if isinstance(raw, str):
        raw = raw.replace("，", ",")
        items = [s.strip().lower() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(s).strip().lower() for s in raw if str(s).strip()]
    else:
        items = []
    roles = [r for r in ROLES_ORDER if r in items]
    return roles or ["qa"]


# ============ LLM 输出解析（D 修复：括号配对 + 宽松兜底） ============

def parse_cases_detailed(text: str) -> tuple[list[TestCase], dict]:
    """解析 LLM 输出为 TestCase 列表，并返回诊断信息（供上层显性上报）。

    解析原语全部来自 ``src.utils.jsonx``（括号配对切片 + 尾随逗号修复），
    不用贪婪正则——见该模块 docstring 列出的 4 类失效场景。

    诊断字段：``reason``、``raw_items``（原始条目数）、
    ``skipped``（结构不合法被丢弃的条目数）、``raw_len``（原文长度）。

    reason 口径（互斥，便于定位是「模型没给 JSON」还是「给了但不对」）：
      - ``ok``           ：解析出至少 1 条用例
      - ``empty_array``  ：JSON 合法但没有「元素为对象的用例条目」
                           （含 ``[]`` 与正文括号 ``[1]`` 这类）
      - ``invalid_json`` ：找到 ``[...]`` 字面量但 JSON 不合法（尾随逗号修复后仍失败）
      - ``no_json``      ：全文没有 ``[...]`` / ``{...}`` 字面量（纯文字回答）
    """
    diag = {"reason": "ok", "raw_items": 0, "skipped": 0, "raw_len": len(text or "")}
    if not text or not text.strip():
        diag["reason"] = "no_json"
        return [], diag

    raw_list = jsonx.find_dict_list(text, jsonx.CASE_KEYS)
    if raw_list is None:
        # 区分「JSON 合法但没用例」/「有数组字面量但 JSON 非法」/「压根没给 JSON」
        saw_legal_list = any(
            isinstance(jsonx.load_candidate(c), list)
            for c in jsonx.balanced_slices(text, "[", "]")
        )
        if saw_legal_list:
            diag["reason"] = "empty_array"
        elif jsonx.has_array_literal(text):
            diag["reason"] = "invalid_json"
        else:
            diag["reason"] = "no_json"
        return [], diag

    diag["raw_items"] = len(raw_list)
    out: list[TestCase] = []
    for item in raw_list:
        try:
            out.append(TestCase(**CaseGenerator._normalize(item)))
        except Exception:  # noqa: BLE001  单条结构不合法只丢弃该条，不拖垮整批
            diag["skipped"] += 1
    if not out:
        diag["reason"] = "empty_array"
    return out, diag


class CaseGenerator:
    def __init__(self, client=None, roles=None, template_loader=None):
        """client：平台注入的 LLM 客户端（OpenAI 兼容）。

        未注入（无可用模型）时：AITF_ALLOW_DEMO=1 → mock 演示兜底；
        否则抛 NoModelError，绝不自动调用环境里的百炼 Key（避免无效 Key 直接 401 报错）。
        roles：参与生成的角色列表（pm/qa/dev），默认 ["qa"]。
        template_loader：可选自定义模板加载器 callable(kind, role) -> str | None（V5.10）。
        返回非空字符串则用作用例生成模板（用户在平台「提示词」弹窗自定义），
        返回 None/空则回落内置 prompts 文件默认模板。"""
        self.injected = client
        self.roles = parse_roles(roles)
        self.template_loader = template_loader
        # 「模型调用成功但没解析出用例」的测试点清单（供上层显性提示，不再静默丢）
        self.parse_failures: list[dict] = []
        self.last_diag: dict = {}
        # 子任务并发（V1.3）：parse_failures 会被多线程同时追加，必须加锁
        self._lock = threading.Lock()

    def _load_template(self, kind: str, role: str = "qa") -> str:
        if self.template_loader:
            custom = self.template_loader(kind, role)
            if custom and custom.strip():
                return custom
        suffix = _ROLE_TPL_SUFFIX.get(role, "")
        fname = f"api_case{suffix}.txt" if kind in ("api", "action") else f"requirement_case{suffix}.txt"
        return (settings.PROMPTS_DIR / fname).read_text(encoding="utf-8")

    def _build_prompt(self, unit: RequirementUnit, role: str = "qa") -> str:
        tpl = self._load_template(unit.kind, role)
        return tpl.format(
            name=unit.name,
            path=unit.path or "-",
            description=unit.description or "-",
            params="; ".join(unit.params) or "-",
            kind=unit.kind,
            constraints=unit.constraints or "-",
        )

    @staticmethod
    def _normalize(raw: dict) -> dict:
        """兼容 LLM 返回的字段名大小写/中英差异，并对齐逐步预期。"""
        ct = raw.get("case_type") or raw.get("caseType") or "正向"
        pr = raw.get("priority") or raw.get("优先级") or "P1"
        steps = raw.get("steps", []) or []
        expected = raw.get("expected", "")
        se = raw.get("step_expectations") or raw.get("stepExpectations") or []
        return {
            "title": raw.get("title", "未命名用例"),
            "module": raw.get("module", ""),
            "case_type": ct,
            "priority": pr,
            "pre_condition": raw.get("pre_condition", "") or raw.get("preCondition", ""),
            "steps": steps,
            "step_expectations": align_step_expectations(steps, se, expected),
            "expected": expected,
            "test_data": raw.get("test_data") or raw.get("testData"),
        }

    @staticmethod
    def _parse_llm(text: str) -> list[TestCase]:
        """兼容入口：只返回用例列表（诊断信息见 parse_cases_detailed）。"""
        return parse_cases_detailed(text)[0]

    def generate_for_unit(self, unit: RequirementUnit, role: str = "qa",
                          client=None) -> list[TestCase]:
        """为单个「测试点 × 角色」生成用例。

        client：本次调用实际使用的模型客户端；None → 用构造时注入的 self.injected
        （并发场景由调用方决定是否传独立实例）。

        空正文（HTTP 成功但正文 0 字）由底层客户端重试 + 模型池切换候选兜底，
        本层只负责把「最终仍无产出」显性记进 parse_failures，不静默吞掉。
        """
        used_mock = False
        cases: list[TestCase] = []
        diag: dict = {}
        try:
            cases, diag = self._generate_inner(unit, role, client)
        except NoModelError:
            raise  # 未配模型：必须向上暴露，不用假用例掩盖
        except Exception:
            if settings.ALLOW_DEMO:
                cases = mock_generate(unit)  # 仅演示模式兜底
                used_mock = True
            else:
                raise

        if not cases and not used_mock:
            # 调用成功却 0 条：多半是解析失败（reason 已由 _generate_inner 记入 last_diag）
            with self._lock:
                self.parse_failures.append({
                    "unit": unit.name,
                    "role": role,
                    "reason": diag.get("reason", "unknown"),
                    "skipped": diag.get("skipped", 0),
                    "raw_len": diag.get("raw_len", 0),
                })
        return cases

    def _generate_inner(self, unit: RequirementUnit, role: str = "qa",
                        client=None) -> tuple[list[TestCase], dict]:
        """真实调用一次模型并解析。返回 (cases, diag)；未配模型时抛 NoModelError。"""
        client = self.injected if client is None else client
        if client is None:
            # 未注入模型：仅演示模式允许假的规则用例兜底
            if settings.ALLOW_DEMO:
                return mock_generate(unit), {"reason": "demo", "raw_len": 0, "skipped": 0}
            raise NoModelError(_no_model_hint(role))
        # 平台注入的真实模型：OpenAI 兼容调用；结构化抽取强制关思考（见 call_generate）
        prompt = self._build_prompt(unit, role)
        text = call_generate(client, prompt)
        cases, diag = parse_cases_detailed(text)
        self.last_diag = diag
        if not cases:
            logger.warning(
                "用例解析无产出（unit=%s role=%s）：reason=%s 原文 %d 字 丢弃 %d 条",
                unit.name, role, diag["reason"], diag["raw_len"], diag["skipped"],
            )
        elif diag["skipped"]:
            logger.warning(
                "用例部分丢弃（unit=%s role=%s）：%d/%d 条结构不合法",
                unit.name, role, diag["skipped"], diag["raw_items"],
            )
        return cases, diag

    def generate(self, units: list[RequirementUnit], progress_cb=None,
                 roles=None, concurrency: int | None = None) -> list[TestCase]:
        """为全部测试点按角色生成用例（unit × role 双层）。

        V1.3 并发：默认并发 GENERATE_CONCURRENCY 路（AITF_GEN_CONCURRENCY 可调，1 = 串行）。
        并发只影响「墙钟耗时」，用例顺序仍严格按 unit × role 的原顺序收敛，再去重编号 ——
        这样前端的预览序号与完成后的正式编号走向一致，不会来回跳。

        progress_cb(cur, total, label, cases_so_far, new_cases)：每个「测试点×角色」完成后
        回调一次（多线程下已加锁串行回调），供上层做实时子进度 + 增量落库。
        """
        role_list = parse_roles(roles) if roles is not None else self.roles
        plan = [(u, role) for role in role_list for u in units]
        total = len(plan)
        buckets: list[list[TestCase]] = [[] for _ in plan]
        done = 0
        cb_lock = threading.Lock()

        def _emit(idx: int, unit: RequirementUnit, role: str, produced: list[TestCase]) -> None:
            nonlocal done
            buckets[idx] = produced
            with cb_lock:
                done += 1
                if not progress_cb:
                    return
                label = f"{unit.name} · {ROLE_LABELS.get(role, role)}视角"
                try:
                    progress_cb(done, total, label,
                                sum(len(b) for b in buckets), produced)
                except Exception:  # noqa: BLE001  进度回调失败绝不能影响生成
                    pass

        def _work(idx: int, unit: RequirementUnit, role: str) -> None:
            _emit(idx, unit, role, self.generate_for_unit(unit, role))

        level = GENERATE_CONCURRENCY if concurrency is None else max(1, concurrency)
        workers = min(level, total)
        if workers <= 1:
            for i, (u, role) in enumerate(plan):
                _work(i, u, role)
        else:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="gen") as pool:
                futures = [pool.submit(_work, i, u, r)
                           for i, (u, r) in enumerate(plan)]
                for fut in as_completed(futures):
                    fut.result()  # 任一子任务抛错 → 冒泡（与串行语义一致）

        all_cases: list[TestCase] = [c for b in buckets for c in b]
        # 去重（跨角色输出合并后，按标题/模块/类型去重）
        seen, dedup = set(), []
        for c in all_cases:
            key = (c.title, c.module, c.case_type.value)
            if key in seen:
                continue
            seen.add(key)
            dedup.append(c)
        for i, c in enumerate(dedup, 1):
            c.case_id = f"TC-{i:03d}"
        return dedup
