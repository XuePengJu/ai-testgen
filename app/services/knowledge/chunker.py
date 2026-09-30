"""结构化分块器（V4.1，增强数据清洗）。

输入 doc_extract 抽取的纯文本（含 Markdown 标题，xmind 天然是标题层级），输出
结构化分块，规则：

0. **数据清洗（V4.1 新增，降噪）**
   - 统一换行/去零宽字符/删除 HTML 注释/压缩多余空行
   - 过滤 Markdown 水平分隔线（``---``/``***``/``___``）、代码围栏开关行
   - 过滤表格对齐分隔行（``|---|:--:|``，无文本信息）
   - 块级最小有效内容阈值：去掉标点/符号后中/英/数字符不足阈值的块不单独成块，
     短块向后合并到下一块，文档末尾仍为纯符号的残块直接丢弃（避免检索噪声）
1. **标题感知**：``#{1,6} `` 标题行作为 section 边界，标题路径写进
   ``context_header``；同时把标题原文前置到该 section 的首个正文/表格块，
   让标题文字参与向量化
2. **表格整块**：连续 `` | `` 行聚合成 ``chunk_type='table'`` 块（剔除分隔行）
3. **大小控制**：普通内容按目标块大小聚合，空行为自然切分点，带 ~10% 重叠

输出 dict 结构：{content, context_header, chunk_type, start_at, end_at}
"""
from __future__ import annotations

import re

# 目标块大小（估算 token）：中文 1 字 ≈ 0.75 token
TARGET_TOKENS = 400
MIN_TOKENS = 80
MAX_TOKENS = 900
OVERLAP_RATIO = 0.10
# 一个独立块至少包含的有效字符（中文/字母/数字）数，低于此值视为噪声残块
MIN_MEANINGFUL_CHARS = 15

_TITLE_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_LINE_RE = re.compile(r"^.*\|.*$")  # 含 | 的行视为表格行
# 水平分隔线：去空白后为 3+ 个连续的 -、*、_（兼容 "- - -"、"* * *"）
_HR_COMPACT_RE = re.compile(r"^[\-*_]{3,}$")
_FENCE_RE = re.compile(r"^(?:`{3,}|~{3,})[\s`~=A-Za-z]*$")  # 代码围栏开关
# 整行仅 1-6 个 #（无标题文字，如孤立的 "##"）
_BARE_HASH_RE = re.compile(r"^#{1,6}\s*$")
# 网页/文档代码块“复制”按钮文本被误复制进正文；兼容语言标识粘连，如
# "Copy code"、"javascriptCopy code"、"jsonCopy code"（前缀仅允许语言名字符，不含空格，避免误伤 "you can copy code"）
_COPY_BTN_RE = re.compile(r"^(?:[a-z0-9+#.\-]{0,20}?)copy\s*code\s*$|^copied!?$", re.IGNORECASE)
_UI_CN_RE = re.compile(r"^(?:复制代码|已复制|点击复制|复制)\s*[。.!！]?$")
# 有效内容字符：中文、字母、数字（下划线/标点/符号不计）
_VALID_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _est_tokens(text: str) -> int:
    """粗略 token 估算（中文友好）：字符数 * 0.75。"""
    return int(len(text) * 0.75)


def _valid_count(text: str) -> int:
    """有效内容字符数（剔除空白、标点、装饰符号后）。"""
    return len(_VALID_RE.findall(text or ""))


def clean_raw_text(text: str) -> str:
    """入库前的全文级清洗（也可供检索侧复用）。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 删除 HTML 注释（含跨行）
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    # 零宽字符 / BOM
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    # 行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # 3+ 连续空行压成 1 个空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_noise_line(stripped: str) -> bool:
    """整行噪声：水平分隔线（含带空格写法）、代码围栏开关、孤立 # 标记。"""
    if _HR_COMPACT_RE.match(re.sub(r"\s+", "", stripped)):
        return True
    if _FENCE_RE.match(stripped):
        return True
    if _BARE_HASH_RE.match(stripped):
        return True
    if _COPY_BTN_RE.match(stripped) or _UI_CN_RE.match(stripped):
        return True
    return False


def _is_table_separator(stripped: str) -> bool:
    """表格对齐分隔行，如 |---|:--:| 或 ---|---（只含 | : - 空格且含 -）。"""
    if "|" not in stripped or "-" not in stripped:
        return False
    return re.sub(r"[\|\s:\-]", "", stripped) == ""


def chunk_text(text: str) -> list[dict]:
    """把抽取文本切成结构化分块列表（含清洗/降噪）。"""
    text = clean_raw_text(text)
    if not text:
        return []
    lines = text.splitlines()
    chunks: list[dict] = []
    header_stack: list[str] = []          # 当前标题路径
    pending_heads: list[str] = []         # 本 section 尚未写入任何块的标题原文
    buf_lines: list[str] = []             # 普通段落缓冲
    carry_lines: list[str] = []           # 上一块留下的短内容，待并入下一块
    table_lines: list[str] = []           # 表格行缓冲
    buf_start = 0
    abs_pos = 0                            # 累计字符位置（start_at/end_at）

    def header_path() -> str:
        return " > ".join(header_stack)

    def emit(content: str, chunk_type: str, start: int) -> None:
        """落一个块（标题前置 + 短内容合并已在调用前处理好）。"""
        chunks.append({
            "content": content.strip(),
            "context_header": header_path(),
            "chunk_type": chunk_type,
            "start_at": start,
            "end_at": abs_pos,
        })

    def flush_table() -> None:
        nonlocal table_lines, pending_heads
        if not table_lines:
            return
        body = "\n".join(table_lines)
        # 表格至少要有表头/数据行的有效字符，否则视为噪声丢弃
        if _valid_count(body) >= MIN_MEANINGFUL_CHARS:
            head = pending_heads
            pending_heads = []
            emit("\n".join(head + table_lines) if head else body, "table", buf_start)
        table_lines = []

    def flush_text() -> None:
        nonlocal buf_lines, carry_lines, buf_start, pending_heads
        if not buf_lines:
            return
        merged = carry_lines + buf_lines
        buf_lines = []
        content = "\n".join(merged).strip()
        if _valid_count(content) >= MIN_MEANINGFUL_CHARS:
            head = pending_heads
            pending_heads = []
            carry_lines = []
            emit("\n".join(head + merged) if head else content, "text", buf_start)
        else:
            # 短块：缓存并到下一块（标题保留，等待后续正文）
            carry_lines = merged

    def flush_all() -> None:
        flush_table()
        flush_text()

    for line in lines:
        stripped = line.strip()
        # 空行：自然切分点
        if not stripped:
            if table_lines:
                flush_table()
            elif buf_lines and _est_tokens("\n".join(carry_lines + buf_lines)) >= MIN_TOKENS:
                flush_text()
            abs_pos += 1
            continue

        # 整行噪声（分隔线 / 代码围栏）：直接丢弃
        if _is_noise_line(stripped):
            abs_pos += len(line) + 1
            continue

        m = _TITLE_RE.match(stripped)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            # 空标题 / 纯符号标题（如 "##"、"# ---"、"### ==="）无有效文字，
            # 不建立章节、不前置进块，按噪声行丢弃
            if _valid_count(title) == 0:
                abs_pos += len(line) + 1
                continue
            flush_all()
            header_stack = header_stack[: level - 1] + [title]
            pending_heads = pending_heads[: level - 1] + [stripped]
            buf_start = abs_pos
            abs_pos += len(line) + 1
            continue

        if _TABLE_LINE_RE.match(stripped):
            # 表格对齐分隔行（|---|:--:|）剔除，不进块
            if _is_table_separator(stripped):
                abs_pos += len(line) + 1
                continue
            flush_text()
            if not table_lines:
                buf_start = abs_pos
            table_lines.append(stripped)
            if _est_tokens("\n".join(table_lines)) > MAX_TOKENS:
                flush_table()
            abs_pos += len(line) + 1
            continue

        # 普通行：切表格 → 入段落缓冲
        if table_lines:
            flush_table()
        if not buf_lines:
            buf_start = abs_pos
        buf_lines.append(stripped)
        joined = "\n".join(carry_lines + buf_lines)
        # 超目标大小 → 切块（保留 ~10% 重叠：末尾最多 2 行作为重叠前缀）
        if _est_tokens(joined) >= TARGET_TOKENS:
            content = "\n".join(buf_lines)
            overlap_len = max(0, int(len(buf_lines) * OVERLAP_RATIO))
            if overlap_len >= 2:
                buf_lines = buf_lines[-overlap_len:]
                buf_start = abs_pos - len("\n".join(buf_lines))
            else:
                buf_lines = []
                buf_start = abs_pos
        abs_pos += len(line) + 1

    flush_all()

    # 文档末尾仍残留的短内容：若全文一个有效块都没有，才兜底保留，否则丢弃
    if not chunks and carry_lines:
        tail = "\n".join(pending_heads + carry_lines).strip()
        if _valid_count(tail) >= MIN_MEANINGFUL_CHARS:
            emit(tail, "text", buf_start)

    return chunks
