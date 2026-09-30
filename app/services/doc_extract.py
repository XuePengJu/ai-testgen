"""统一文档文本抽取：docx / pdf / md / txt / xlsx / xmind → 纯文本。

背景：平台早期只认纯文本输入（`read_text(encoding="utf-8")`），上传 docx/pdf
会直接抛 UnicodeDecodeError 把解析步骤打成 failed，而界面 tooltip 却写着支持
这些格式。本模块作为**唯一入口**，保证「界面允许上传的格式，后端一定读得出文本」。

- `.docx`  → python-docx，正文段落 + 表格行（按 ` | ` 拼接）
- `.pdf`   → pdfplumber，逐页抽取，跳过空白页
- `.md / .markdown / .txt` → utf-8 读取，非法字节用 errors="replace" 容错
- `.xlsx / .xls` → openpyxl（V4.0），遍历全部 sheet，行内单元格按 ` | ` 拼接
- `.xmind` → 标准库 zipfile + json（V4.0），解析 content.json 主题树 → 标题层级
- 其他扩展名 → 抛 UnsupportedFormatError（调用方转 400 或走原文件兜底）

被 parser_agent（AI 解析）与 pipeline_lib（正则兜底）共用，两边行为一致。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

# 界面允许上传、后端保证能解析的扩展名（前端 accept 与此保持一致）
SUPPORTED_EXTS: tuple[str, ...] = (
    ".docx", ".pdf", ".md", ".markdown", ".txt", ".xlsx", ".xls", ".xmind",
)

# 单次抽取字符上限：防御超大文档把模型上下文和内存打爆
MAX_CHARS = 200_000


class UnsupportedFormatError(ValueError):
    """扩展名不在白名单内。"""


class ExtractError(RuntimeError):
    """文件损坏 / 加密 / 依赖缺失等导致抽取失败。"""


def is_supported(name: str) -> bool:
    """判断文件名（或路径）的扩展名是否在白名单内。"""
    return Path(name).suffix.lower() in SUPPORTED_EXTS


def supported_hint() -> str:
    """给用户看的支持格式说明（与前端 accept 同源，避免两边写歪）。"""
    return " / ".join(e.lstrip(".") for e in SUPPORTED_EXTS)


def _extract_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError as e:  # pragma: no cover - 依赖缺失属部署问题
        raise ExtractError("缺少 python-docx 依赖，无法解析 .docx") from e
    try:
        doc = docx.Document(str(path))
    except Exception as e:  # noqa: BLE001 - 损坏/加密/非 docx 一律按失败处理
        raise ExtractError(f"docx 解析失败：{e}") from e

    lines: list[str] = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            lines.append(t)
    # 需求文档的规则常放在表格里，一并抽出来
    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - 依赖缺失属部署问题
        raise ExtractError("缺少 pdfplumber 依赖，无法解析 .pdf") from e
    pages: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                txt = (page.extract_text() or "").strip()
                if txt:
                    pages.append(txt)
    except Exception as e:  # noqa: BLE001 - 加密/损坏 pdf
        raise ExtractError(f"pdf 解析失败：{e}") from e
    return "\n\n".join(pages)


def _extract_plain(path: Path) -> str:
    # errors="replace"：容忍历史文件里的非法字节，避免整份文档读不出来
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_xlsx(path: Path) -> str:
    """Excel：遍历全部 sheet，行内单元格按 ` | ` 拼接（openpyxl，依赖已有）。"""
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - 依赖缺失属部署问题
        raise ExtractError("缺少 openpyxl 依赖，无法解析 .xlsx") from e
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - 损坏/加密/非 xlsx 一律按失败处理
        raise ExtractError(f"xlsx 解析失败：{e}") from e
    lines: list[str] = []
    try:
        for ws in wb.worksheets:
            lines.append(f"## Sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    lines.append(" | ".join(cells))
    finally:
        wb.close()
    return "\n".join(lines)


def _walk_xmind_topic(topic: dict, lines: list[str], level: int) -> None:
    """递归遍历 XMind 主题树 → Markdown 标题层级（可复用为分块 context_header）。"""
    title = (topic.get("title") or "").strip()
    if title:
        lines.append(f"{'#' * min(level + 1, 6)} {title}")
    children = ((topic.get("children") or {}).get("attached")) or []
    for child in children:
        _walk_xmind_topic(child, lines, level + 1)


def _parse_xmind_xml(xml_bytes: bytes) -> str:
    """旧版 XMind 8 content.xml 兜底解析：取全部 title 节点按层级输出。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    lines: list[str] = []

    def _walk(el, level: int) -> None:
        for node in el:
            tag = node.tag.rsplit("}", 1)[-1]  # 剥命名空间
            if tag == "title" and (node.text or "").strip():
                lines.append(f"{'#' * min(level + 1, 6)} {node.text.strip()}")
            elif tag == "children":
                _walk(node, level)
            elif tag == "topic":
                _walk(node, level + 1)

    _walk(root, 0)
    return "\n".join(lines)


def _extract_xmind(path: Path) -> str:
    """XMind：本质是 zip 容器，优先 content.json（Zen/8+），content.xml 兜底。"""
    try:
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
            if "content.json" in names:
                data = json.loads(zf.read("content.json").decode("utf-8", errors="replace"))
                lines: list[str] = []
                for sheet in data if isinstance(data, list) else [data]:
                    root = sheet.get("rootTopic") or {}
                    if root:
                        _walk_xmind_topic(root, lines, 0)
                return "\n".join(lines)
            if "content.xml" in names:
                return _parse_xmind_xml(zf.read("content.xml"))
            raise ExtractError("xmind 文件缺少 content.json/content.xml，无法解析")
    except zipfile.BadZipFile as e:
        raise ExtractError("xmind 解析失败：不是有效的 zip 容器") from e
    except json.JSONDecodeError as e:
        raise ExtractError(f"xmind content.json 解析失败：{e}") from e


def extract_text(path: str | Path) -> str:
    """按扩展名抽取纯文本。

    返回可能为空字符串（空白文档、纯扫描件 pdf）—— 空结果由调用方兜底，
    这里不抛异常，避免「文档没文字」被当成解析失败。

    抛 ``UnsupportedFormatError``（格式不在白名单）或 ``ExtractError``（解析失败）。
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        text = _extract_docx(p)
    elif ext == ".pdf":
        text = _extract_pdf(p)
    elif ext in (".md", ".markdown", ".txt"):
        text = _extract_plain(p)
    elif ext in (".xlsx", ".xls"):
        text = _extract_xlsx(p)
    elif ext == ".xmind":
        text = _extract_xmind(p)
    else:
        raise UnsupportedFormatError(f"暂不支持的文件格式：{ext or '(无扩展名)'}")
    return text[:MAX_CHARS].strip()


def extract_text_safe(path: str | Path) -> str:
    """``extract_text`` 的静默版：任何失败都返回空串。

    供解析链路使用 —— 抽取失败时不应中断任务，而是让调用方回退到
    原文件解析（swagger / markdown）或整段兜底。
    """
    try:
        return extract_text(path)
    except (UnsupportedFormatError, ExtractError, OSError, ValueError):
        return ""
