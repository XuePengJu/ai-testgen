"""解析业务需求 Markdown → 测试单元列表（按二级及以上标题切分章节）。"""
import re
from pathlib import Path
from src.models.testcase import RequirementUnit


def parse_markdown(path: str | Path, text: str | None = None) -> list[RequirementUnit]:
    """把需求文本按 Markdown 二级及以上标题切分成测试单元。

    text：已抽取好的纯文本（docx/pdf 由上层 doc_extract 抽取后传入）。
    为 None 时按路径读文件；此时要求文件是 UTF-8 纯文本。
    """
    if text is None:
        text = Path(path).read_text(encoding="utf-8")
    units = []
    # 按 Markdown 二级及以上标题切分（覆盖 ## 与 ### 章节）
    # \A 用于覆盖「文件第一个字符就是 ##」的情况：旧写法只匹配 \n 开头的标题，
    # 会把第一小节连同标题并进 parts[0] 被丢弃（表现：首个测试点凭空消失）。
    parts = re.split(r"(?:\A|\n)#{2,}\s+", text)
    for part in parts[1:]:
        lines = part.strip().split("\n")
        title = lines[0].strip().split("（")[0].strip()
        body = "\n".join(lines[1:]).strip()
        if not title:
            continue
        units.append(RequirementUnit(
            name=title,
            kind="module",
            description=body[:500],
            params=[title],
        ))
    # 兜底：没有切出任何标题章节时，把整段文本当作一个测试点（保底不出现 0 条用例）
    if not units and text.strip():
        first_line = text.strip().split("\n")[0].strip()
        title = first_line[:40] if first_line else "未命名需求"
        units.append(RequirementUnit(
            name=title,
            kind="module",
            description=text.strip()[:500],
            params=[title],
        ))
    return units

