"""Wiki 分类标签解析与兜底（纯函数，无 IO 依赖）。

M7 归类降级：LLM 输出的分类标签可能以多种形状出现——
- ``{"summary": "...", "category": "测试基础"}`` JSON（合并进摘要调用的输出）
- ``{"category": "工具配置"}`` JSON（轻量独立分类调用的输出）
- 代码围栏包裹的 JSON（```json ... ```）
- 纯文本（直接输出分类名，或带解释的多行文本）
- 空串 / None / 拒答（"未知"、"无法分类" 等）

本模块负责把任意形状解析成规范的短分类标签，解析失败统一回落
"未分类"，保证 wiki_category 列永远有可展示的值。
"""
from __future__ import annotations

import json
import re

# 代码围栏：LLM 偶尔把 JSON 包在 ```json ... ``` 里
_FENCE_RE = re.compile(r"```(?:json)?\s*|\s```", re.MULTILINE)

# 分类标签长度上限（与 models.knowledge.Knowledge.wiki_category String(50) 对齐再留余量）
MAX_CATEGORY_LEN = 20

# 默认分类
DEFAULT_CATEGORY = "未分类"

# LLM 拒答/无意义输出的常见说法 → 视为未分类
_INVALID_CATEGORY_VALUES = {
    "", "未分类", "无", "未知", "none", "null", "n/a", "na", "unknown",
    "无法分类", "无法确定", "其他", "其它", "uncategorized", "general",
}


def strip_code_fences(text: str) -> str:
    """剥掉 LLM 输出首尾的 ```json 代码围栏（有则剥，无则原样）。"""
    if not text:
        return ""
    s = text.strip()
    if not s.startswith("```"):
        return s
    # 去掉首行 ```json / ``` 与结尾 ```
    body = re.sub(r"^```(?:json)?\s*", "", s)
    body = re.sub(r"\s*```\s*$", "", body)
    return body.strip()


def _first_balanced_json(s: str) -> str | None:
    """从文本中截取第一个花括号配平的 JSON 片段（含嵌套对象）。

    旧实现用 r'\\{[^}]+\\}' 遇嵌套对象会截断成非法 JSON；
    这里逐字符扫描配平，返回完整片段；找不到返回 None。
    """
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def normalize_category(raw: str | None, fallback: str = DEFAULT_CATEGORY) -> str:
    """把 LLM 给出的分类值清洗成规范短标签。

    规则：
    - None/空 → fallback；
    - 剥代码围栏、成对引号、首尾空白；
    - 拒答/无意义值（未知/无/none 等）→ fallback；
    - 截断到 MAX_CATEGORY_LEN（宁短勿丢）。
    """
    if raw is None:
        return fallback
    s = strip_code_fences(str(raw)).strip().strip('"').strip("'").strip()
    if s.lower() in _INVALID_CATEGORY_VALUES or s in _INVALID_CATEGORY_VALUES:
        return fallback
    if not s:
        return fallback
    return s[:MAX_CATEGORY_LEN]


def parse_llm_category(content: str | None, fallback: str = DEFAULT_CATEGORY) -> str:
    """从 LLM 分类调用的原始输出中解析分类标签。

    解析优先级：
    1. 输出含配平 JSON 且是 dict → 取 category 字段（缺失则 fallback）；
    2. 无 JSON → 取首个非空行，若该行是"标签: 值"形状取值部分，
       再剥掉"分类："等前缀与句尾标点；
    3. 清洗后仍无效 → fallback。
    """
    if not content or not str(content).strip():
        return fallback
    s = strip_code_fences(str(content))
    # 1) JSON 形状
    fragment = _first_balanced_json(s)
    if fragment:
        try:
            data = json.loads(fragment)
            if isinstance(data, dict):
                val = data.get("category", data.get("分类"))
                if isinstance(val, (str, int)):
                    return normalize_category(str(val), fallback)
                return fallback
        except (json.JSONDecodeError, ValueError):
            pass  # 伪 JSON：走纯文本兜底
    # 2) 纯文本：取第一个非空行
    line = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    if not line:
        return fallback
    # 截断的伪 JSON（如 '{"category": "测试基础'）：剥掉行首 { 后按 "label: 值" 解析
    line = line.lstrip("{").strip()
    # "分类：测试基础" / "category: tools" / '"category": "tools"' 形状取冒号后段
    m = re.match(r"^['\"]?(?:分类|类别|主题|category|topic)['\"]?\s*[:：]\s*(.+)$", line, re.IGNORECASE)
    if m:
        line = m.group(1).strip()
    # 去句尾标点与解释性后缀（如"测试基础。" / "测试基础 —— 适用于..."）
    line = re.split(r"[。．.；;！!？?\s——]", line, maxsplit=1)[0].strip()
    # 剥掉残留的包裹引号（伪 JSON 值可能带前导 "）
    line = line.strip().strip('"').strip("'").strip()
    # 仍像 JSON 残片/键名（冒号结尾、内嵌引号或花括号）→ 视为解析失败
    if line.endswith((":", "：")) or '"' in line or "'" in line or "{" in line:
        return fallback
    return normalize_category(line, fallback)
