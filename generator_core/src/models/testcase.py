"""测试用例数据模型（Pydantic v2，缺失时降级 dataclass，保证零依赖也能跑）。"""
import re
from enum import Enum
from typing import List, Optional

try:
    from pydantic import BaseModel, Field
    _PYDANTIC = True
except ImportError:
    from dataclasses import dataclass, field
    _PYDANTIC = False


class CaseType(str, Enum):
    POSITIVE = "正向"
    NEGATIVE = "异常"
    BOUNDARY = "边界值"
    SCENARIO = "场景组合"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


def align_step_expectations(
    steps: list[str],
    step_expectations: list[str] | None,
    expected: str,
) -> list[str]:
    """保证「每个步骤都有预期结果」的硬约束。

    规则：
    1. step_expectations 数量与步骤数一致且全部非空 → 原样使用
    2. 只有 1 个步骤 → 用整体 expected 作为该步骤预期
    3. 多步骤但逐步预期缺失/不齐 → 每步挂整体 expected（冗余但信息完整）
    4. steps 为空 → 返回空列表

    返回与过滤空串后的 steps 一一对应的预期列表。
    """
    steps = [s for s in (steps or []) if str(s).strip()]
    if not steps:
        return []
    if step_expectations and len(step_expectations) == len(steps) and all(
        str(x).strip() for x in step_expectations
    ):
        return [str(x).strip() for x in step_expectations]
    exp = (expected or "").strip()
    if len(steps) == 1:
        return [exp]
    return [exp] * len(steps)


# ============ 复合标题：`动作概括 -> 预期结果`（V2.8-fix4）============

TITLE_SEP = " -> "
_TITLE_SPLIT_RE = re.compile(r"\s*(?:->|→)\s*")

# 「无信息量套话」子句（作为标题里的预期片段时应剥掉）
_BOILERPLATE_RES = (
    re.compile(r"^.*(?:流程|功能|操作|页面|界面|系统|整体)(?:均|都)?(?:正常|顺利|成功)(?:完成|运行|可用|显示)?$"),
    re.compile(r"^无(?:异常|报错|错误)$"),
    re.compile(r"^符合预期$"),
)


def split_title(title: str) -> tuple[str, str]:
    """拆分复合标题 → (动作概括, 预期片段)。非复合标题返回 (title, "")。"""
    parts = _TITLE_SPLIT_RE.split((title or "").strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return (title or "").strip(), ""


def strip_title_expected(title: str) -> str:
    """取标题的动作部分（做去重键/身份匹配时用，避免预期改一个字就被当成新用例）。"""
    return split_title(title)[0]


def condense_expected(expected: str) -> str:
    """把整体预期压缩成适合放进标题的「实质结果断言」。

    规则：按中英文逗号/分号切子句，剥掉**开头连续**的无信息量套话子句
    （如「上架状态下商品退款流程正常完成」「操作成功」「无异常」），但至少保留
    最后一个子句，避免标题片段变空。
    """
    exp = (expected or "").strip()
    if not exp:
        return ""
    parts = [p.strip().rstrip("。.!！") for p in re.split(r"[，,；;]", exp)]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return exp.rstrip("。.!！")
    i = 0
    while i < len(parts) - 1 and any(r.match(parts[i]) for r in _BOILERPLATE_RES):
        i += 1
    return "，".join(parts[i:])


def compose_title(title: str, expected: str, sep: str = TITLE_SEP) -> str:
    """合成复合标题：`动作概括 -> 预期结果`。

    幂等：标题里已含 `->` / `→` 时原样返回，避免二次拼成 `A->B->B`。
    无预期时只返回动作部分（保证不为空）。
    """
    t = (title or "").strip() or "未命名用例"
    if "->" in t or "→" in t:
        return t
    exp = condense_expected(expected)
    return f"{t}{sep}{exp}" if exp else t


def ensure_compound_titles(cases: list) -> list:
    """批量把用例标题补齐为复合形式（幂等，兼容 TestCase 对象与 dict）。

    只改写非空标题；已含 `->` 的标题原样保留，不会重复追加。
    """
    def _get(c, key: str):
        return (c.get(key) if isinstance(c, dict) else getattr(c, key, None)) or ""

    def _set(c, key: str, val: str) -> None:
        if isinstance(c, dict):
            c[key] = val
        else:
            setattr(c, key, val)

    for c in cases:
        title = _get(c, "title")
        if not title:
            continue
        new_title = compose_title(title, _get(c, "expected"))
        if new_title != title:
            _set(c, "title", new_title)
    return cases


def ensure_case_ids(cases: list) -> list:
    """补全缺失的用例ID，保证每条用例都有 TC 编号（兼容 TestCase 对象与 dict）。

    规则：沿用现有最大的 `TC-数字` 序号（兼容 TC-001 / TC001 / TC-1）递增；
    全部缺失时从 TC-001 开始。已有 ID 保持不变。
    """
    _get = lambda c: (c.case_id if not isinstance(c, dict) else (c.get("case_id") or ""))
    _set = lambda c, v: (setattr(c, "case_id", v) if not isinstance(c, dict) else c.__setitem__("case_id", v))

    max_n = 0
    for c in cases:
        cid = str(_get(c) or "").strip()
        m = re.match(r"^TC-?0*(\d+)$", cid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    for c in cases:
        if not str(_get(c) or "").strip():
            max_n += 1
            _set(c, f"TC-{max_n:03d}")
    return cases


if _PYDANTIC:
    class TestCase(BaseModel):
        case_id: str = ""
        title: str
        module: str = ""
        case_type: CaseType
        priority: Priority = Priority.P1
        pre_condition: str = ""
        steps: List[str] = Field(default_factory=list)
        step_expectations: List[str] = Field(default_factory=list)
        expected: str = ""
        test_data: Optional[str] = None

        def resolved_expectations(self) -> list[str]:
            """返回与 steps 一一对应的逐步预期（旧数据缺失时自动兜底）。"""
            return align_step_expectations(self.steps, self.step_expectations, self.expected)

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), "\n".join(self.resolved_expectations()),
                    self.expected, self.test_data or ""]

        def to_dict(self):
            return self.model_dump()

    class RequirementUnit(BaseModel):
        """解析后的测试单元（接口 / 模块 / action）"""
        name: str
        kind: str = "api"            # api | module | action
        path: str = ""
        description: str = ""
        params: List[str] = Field(default_factory=list)
        constraints: str = ""
else:
    @dataclass
    class TestCase:
        case_id: str = ""
        title: str = ""
        module: str = ""
        case_type: CaseType = CaseType.POSITIVE
        priority: Priority = Priority.P1
        pre_condition: str = ""
        steps: List[str] = field(default_factory=list)
        step_expectations: List[str] = field(default_factory=list)
        expected: str = ""
        test_data: Optional[str] = None

        def resolved_expectations(self) -> list[str]:
            """返回与 steps 一一对应的逐步预期（旧数据缺失时自动兜底）。"""
            return align_step_expectations(self.steps, self.step_expectations, self.expected)

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), "\n".join(self.resolved_expectations()),
                    self.expected, self.test_data or ""]

        def to_dict(self):
            return {
                "case_id": self.case_id, "title": self.title, "module": self.module,
                "case_type": self.case_type.value, "priority": self.priority.value,
                "pre_condition": self.pre_condition, "steps": self.steps,
                "step_expectations": self.step_expectations,
                "expected": self.expected, "test_data": self.test_data,
            }

    @dataclass
    class RequirementUnit:
        name: str = ""
        kind: str = "api"
        path: str = ""
        description: str = ""
        params: List[str] = field(default_factory=list)
        constraints: str = ""
