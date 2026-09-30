"""Xmind 导出（纯标准库，无第三方依赖）。

生成 XMind 8（legacy）XML 格式：content.xml + meta.xml + styles.xml + META-INF/manifest.xml。

结构说明（对齐老板模板 测试用例模板.xmind）：
- 每个模块作为一个 topic
- 每个用例作为一个 topic：标题 `序号. 动作概括 -> 预期概括`，labels 标注 类型+优先级（如 正向用例/P0）
- 子节点用 labels 标注字段：前置条件 / 测试数据 / 操作步骤（每个步骤下挂对应的预期结果）
- 无"步骤"聚合节点，操作步骤直接平铺；预期结果作为每个步骤的子节点（V2.7：逐步预期，不再只挂最后一步）
"""
import re
import time
import uuid
import zipfile
from xml.sax.saxutils import escape

from src.models.testcase import TestCase, compose_title


def _tid() -> str:
    """XMind 风格 topic id（26 位小写字母数字）。"""
    return uuid.uuid4().hex[:26]


def _ts() -> str:
    return str(int(time.time() * 1000))


def _topic(
    title: str,
    children: list[str] | None = None,
    root: bool = False,
    labels: list[str] | None = None,
) -> str:
    """生成单个 topic XML。

    Args:
        title: 节点标题
        children: 子 topic XML 字符串列表
        root: 是否为根节点
        labels: 节点标签（XMind 中以彩色小标签展示，如 前置条件/操作步骤/预期结果）
    """
    attrs = f'id="{_tid()}" timestamp="{_ts()}"'
    if root:
        attrs += ' structure-class="org.xmind.ui.logic.right"'
    parts = [f"<topic {attrs}>", f"<title>{escape(title)}</title>"]
    if labels:
        inner = "".join(f"<label>{escape(l)}</label>" for l in labels)
        parts.append(f"<labels>{inner}</labels>")
    if children:
        inner = "".join(children)
        parts.append(f'<children><topics type="attached">{inner}</topics></children>')
    parts.append("</topic>")
    return "".join(parts)


def _case_title(c: TestCase) -> str:
    """用例纯标题：剥离开头的 `[模块名] ` 前缀，避免与模块节点重复。"""
    t = re.sub(r"^\[[^\]]+\]\s*", "", c.title).strip()
    return t or c.title


def _case_topic(c: TestCase, idx: int) -> str:
    """把一个用例展开成 topic + 子节点，对齐老板模板格式。

    用例节点：
      - 标题：`序号. 操作概括->预期概括`（类型/优先级不放标题）
      - labels：`[类型用例, 优先级]`（如 正向用例 / P0，以彩色标签展示）
    子节点（labels 标注字段，输入在前、操作在后）：
      - 前置条件（可选）
      - 测试数据（可选）
      - 操作步骤 1..N，**每个步骤下挂对应的预期结果**（V2.7 修复：不再只挂最后一步）
    """
    children: list[str] = []

    # 前置条件
    if c.pre_condition:
        children.append(_topic(c.pre_condition, labels=["前置条件"]))

    # 测试数据（输入在前，先给数据再执行操作）
    if c.test_data:
        children.append(_topic(c.test_data, labels=["测试数据"]))

    # 操作步骤（带编号平铺），每个步骤下挂对应预期结果
    steps = [s for s in c.steps if s]
    exps = c.resolved_expectations()  # 与 steps 一一对应（旧数据自动兜底）
    exp = c.expected.strip() if c.expected else ""
    if steps:
        for i, s in enumerate(steps, 1):
            sub = None
            se = exps[i - 1] if i - 1 < len(exps) else exp
            if se:
                sub = [_topic(se, labels=["预期结果"])]
            children.append(_topic(f"{i}. {s}", children=sub, labels=["操作步骤"]))
    elif exp:
        # 无步骤时预期结果兜底放同级，不丢数据
        children.append(_topic(exp, labels=["预期结果"]))

    # 标题：序号. 动作概括 -> 预期概括（完整预期挂在每个步骤子节点下）
    # V2.8-fix4：数据层的 title 已是复合形式，这里走 compose_title 保证幂等
    # （已含 `->` 时原样返回，不会拼成 A->B->B）；旧数据（纯动作标题）则按 expected 兜底拼接。
    title = f"{idx}. {compose_title(_case_title(c), exp)}"

    return _topic(
        title,
        children=children,
        labels=[f"{c.case_type.value}用例", c.priority.value],
    )


_CONTENT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" '
    'xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:svg="http://www.w3.org/2000/svg" '
    'xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink" '
    'modified-by="ai-testflow" timestamp="{ts}" version="2.0">'
)

_META = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<meta xmlns="urn:xmind:xmap:xmlns:meta:2.0" version="2.0">'
    "<Author><Name>ai-testflow</Name><Email/><Org/></Author>"
    "<Create><Time>{time_str}</Time></Create>"
    "<Creator><Name>ai-testflow</Name><Version>1.0</Version></Creator>"
    "</meta>"
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<xmap-styles xmlns="urn:xmind:xmap:xmlns:style:2.0" '
    'xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:svg="http://www.w3.org/2000/svg" '
    'version="2.0"><styles/></xmap-styles>'
)

_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0" password-hint="">'
    '<file-entry full-path="content.xml" media-type="text/xml"/>'
    '<file-entry full-path="META-INF/" media-type=""/>'
    '<file-entry full-path="META-INF/manifest.xml" media-type="text/xml"/>'
    '<file-entry full-path="meta.xml" media-type="text/xml"/>'
    '<file-entry full-path="styles.xml" media-type="text/xml"/>'
    "</manifest>"
)


def export_xmind(cases: list[TestCase], out_path: str) -> None:
    groups: dict[str, list[TestCase]] = {}
    for c in cases:
        groups.setdefault(c.module or "未分类", []).append(c)

    modules_xml: list[str] = []
    for mod, cs in groups.items():
        case_topics = [_case_topic(c, idx) for idx, c in enumerate(cs, 1)]
        modules_xml.append(_topic(mod, children=case_topics))

    root_xml = _topic("DBERP 测试用例", children=modules_xml, root=True)

    ts = _ts()
    content_xml = (
        _CONTENT_HEAD.format(ts=ts)
        + f'<sheet id="{_tid()}" modified-by="ai-testflow" timestamp="{ts}">'
        + root_xml
        + "<title>DBERP 测试用例</title>"
        + "</sheet></xmap-content>"
    )

    time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", content_xml)
        z.writestr("meta.xml", _META.format(time_str=time_str))
        z.writestr("styles.xml", _STYLES)
        z.writestr("META-INF/manifest.xml", _MANIFEST)
