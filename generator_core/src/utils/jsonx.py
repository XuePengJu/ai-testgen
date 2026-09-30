"""LLM 输出 JSON 提取公共工具（D 修复续，2026-09-24）。

统一替换历史上散落多处的贪婪正则 ``re.search(r"\\[.*\\]", text, re.S)``。
该写法在真实模型输出下有 4 类确定性失效，任一情况都会让 ``json.loads`` 失败，
而旧调用点普遍 ``return []`` / ``pass`` 静默吞掉，表现为「跑了几分钟一条没产出」：

  1. 数组后面还有别的 ``[...]``（引用标记 ``[1]``、脚注、下一个 JSON 块）
     → 贪婪匹配一路吞到最后一个 ``]``，拼出的字符串必然非法；
  2. 字符串值内部含 ``]`` → 匹配提前截断；
  3. 数组不在文本末尾（前后夹带解释文字 / markdown 围栏）→ 边界不可控；
  4. 输出多个 JSON 块 → 只会拿到「第一个 ``[`` 到最后一个 ``]``」之间的脏字符串。

本模块按「括号配对切片 + 逐候选宽松解析」实现：字符串/转义感知，逐个顶层片段尝试，
并支持尾随逗号（模型高频笔误）修复。调用方拿到 ``None`` 时应显性上报，不要静默吞。
"""
import json
import re

# 列表元素「像用例」的特征键（用于排除正文里的 ``[1]`` 这类普通括号）
CASE_KEYS = ("title", "steps", "expected")

# 尾随逗号：对象/数组收尾前的多余逗号（模型高频笔误）
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


def balanced_slices(text: str, opener: str, closer: str) -> list[str]:
    """切出所有**顶层配对**的 opener..closer 片段（字符串 / 转义感知）。

    相比贪婪正则的四个硬伤都能规避（见模块 docstring）。返回按出现顺序排列的片段列表；
    未闭合的尾部片段不返回（截断的输出本来也无法解析）。
    """
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            if depth == 0:
                start = i
            depth += 1
        elif ch == closer and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start:i + 1])
                start = -1
    return out


def repair_json(s: str) -> str:
    """轻量修复常见非严格 JSON：对象/数组的尾随逗号。"""
    return _TRAILING_COMMA_RE.sub(r"\1", s or "")


def load_candidate(s: str):
    """尝试解析候选片段（原样 → 尾随逗号修复），均失败返回 None。"""
    for cand in (s, repair_json(s)):
        try:
            return json.loads(cand)
        except Exception:  # noqa: BLE001  候选片段非法是常态，继续试下一个
            continue
    return None


def pick_dict_list(obj, keys: tuple = ()) -> list | None:
    """从解析结果中挑出「元素为 dict 的列表」。

    - list：至少一个元素是 dict（``keys`` 非空时要求命中其中任一 key，
      以此排除正文里的 ``[1]`` 这类普通括号数组）
    - dict：优先取其值中的列表；否则把对象本身当成单条记录（模型偶尔不包数组）
    """
    def _ok(items: list) -> bool:
        dicts = [x for x in items if isinstance(x, dict)]
        if not dicts:
            return False
        if not keys:
            return True
        return any(any(k in d for k in keys) for d in dicts)

    if isinstance(obj, list):
        return obj if _ok(obj) else None
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and _ok(v):
                return v
        if keys and any(k in obj for k in keys):
            return [obj]
    return None


def find_dict_list(text: str, keys: tuple = ()) -> list | None:
    """在任意文本中找「元素为 dict 的列表」：先试顶层数组，再试对象包裹的数组。

    这是替换 ``re.search(r"\\[.*\\]")`` 的主入口。找不到返回 None（调用方须显性上报）。
    """
    for chunk in balanced_slices(text, "[", "]"):
        arr = pick_dict_list(load_candidate(chunk), keys)
        if arr:
            return arr
    for chunk in balanced_slices(text, "{", "}"):
        arr = pick_dict_list(load_candidate(chunk), keys)
        if arr:
            return arr
    return None


def find_dict(text: str, need_keys: tuple = ()) -> dict | None:
    """在任意文本中找第一个满足 ``need_keys`` 的 JSON 对象（need_keys 空则不校验键）。

    用于「对象包裹式」输出（如解析链路的 ``{"summary","title","units":[...]}``）。
    """
    for chunk in balanced_slices(text, "{", "}"):
        obj = load_candidate(chunk)
        if isinstance(obj, dict) and (not need_keys or all(k in obj for k in need_keys)):
            return obj
    return None


def has_array_literal(text: str) -> bool:
    """文本里是否存在数组字面量（用于区分「没给 JSON」与「给了但 JSON 不合法」）。"""
    return bool(balanced_slices(text, "[", "]"))


def has_object_literal(text: str) -> bool:
    """文本里是否存在对象字面量（同上，供诊断口径使用）。"""
    return bool(balanced_slices(text, "{", "}"))
