"""ParserAgent：解析规格文件 → 测试单元（接口 / 业务需求）。

入参：input_path（规格文件绝对路径）、kind（api / business）、client（可选，真实模型客户端）
出参：(units, summary, details_json)
  - summary：步骤日志用的一句话（如「AI 理解拆解得到 3 个测试点」）
  - details_json：除 units 外，额外携带 {"title": 任务名建议, "req_summary": 需求摘要}
    供工作流引擎回写 task.name / task.input_summary / 会话名

business 模式：优先用真实模型理解整段需求、拆成测试点（不依赖 Markdown 标题，
一段话 / 一堆文字都能拆）；AI 不可用或解析失败时回退正则切标题，正则也没切出
则整段当一个测试点（保底不出现 0 条用例）。

命名（V2.9 新增）：模型在同一次调用里顺带产出「需求摘要 + 20 字内任务名」，
避免前端直接拿用户原话截断当名字；无 AI 时用第一个测试点名兜底。
"""
import json
import logging
import re

from app.services.doc_extract import extract_text_safe
from app.services.pipeline_lib import lib_parse
from src.models.testcase import RequirementUnit
from src.utils import jsonx

logger = logging.getLogger(__name__)


_AI_PARSE_PROMPT = """你是资深软件测试工程师。请对下面这段业务需求做三件事：

一、总结需求：用一句话说明这段需求是做什么的（summary，30 字以内，直接描述需求主题，
   不要以「本需求」「该需求」开头，不要复述原话）。
二、给出任务名：一个能代表这段需求的名词短语（title，20 字以内，如「登录功能」「退货退款流程」
   「订单管理」；不要带"测试""测试用例""用例"等后缀，不要标点符号、书名号、引号，不要编号）。
三、拆解测试点：把需求拆成若干「可独立测试的测试点」，要求：
   1. 通读整段需求，识别所有可测试的功能点 / 场景 / 业务规则，归纳合并成核心测试点
   2. 不要过度拆分，控制在 3~8 个测试点；相关的小功能点合并到同一个测试点里
   3. name：一句话概括该测试点（10~30 字，简洁明确）
   4. description：补充该测试点的关键规则、字段、边界条件或前置条件（可空）

只输出 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块，格式严格为：
{"summary":"需求一句话总结","title":"任务名","units":[{"name":"测试点名称","description":"补充说明"}]}

需求文本：
{text}"""

# 文件名 / 任务名非法字符（含制表换行）
_ILLEGAL_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
# 任务名首尾需要剥掉的标点与空白（含 markdown 标题符 #）
_NAME_TRIM_CHARS = " \u3000.。;；,，、-—_~～·•…!！?？\"'“”‘’()（）[]【】<>《》#"
# 需要剥掉的冗余后缀（prompt 已要求不带，这里是兜底）
_NAME_SUFFIXES = ("的测试用例", "测试用例", "测试案例", "用例设计", "测试方案", "测试", "用例")


def clean_task_name(raw: str, max_len: int = 20) -> str:
    """把模型给的任务名洗成可安全用作任务名 / 导出文件名的短名。

    - 文件系统非法字符（`/ \\ : * ? " < > |` 与换行制表）→ 空格
    - 剥掉首尾标点与空白（含 markdown 标题符 `#`，避免出现「## 登录功能」这种名字）
    - 剥掉「测试用例 / 测试 / 用例」等冗余后缀（"登录功能的测试用例" → "登录功能"）
    - 超长按 max_len 截断
    """
    s = _ILLEGAL_NAME_CHARS.sub(" ", raw or "").strip()
    s = s.strip(_NAME_TRIM_CHARS)
    for suffix in _NAME_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)].strip(_NAME_TRIM_CHARS)
    return s[:max_len].strip()


def _fallback_naming(units: list[RequirementUnit]) -> tuple[str, str]:
    """无 AI / AI 未给名字时的兜底：拿第一个测试点当需求主题。"""
    if not units:
        return "", ""
    base = clean_task_name(units[0].name)
    if not base:
        return "", ""
    req_summary = f"{base}（共 {len(units)} 个测试点）" if len(units) > 1 else base
    return base, req_summary


def _parse_ai_output(raw: str) -> tuple[list[RequirementUnit], str, str]:
    """解析模型输出 → (units, title, req_summary)。

    兼容两种形态：新格式 `{"summary","title","units":[...]}` 与旧格式裸数组 `[{...}]`。
    解析不出来时返回空 units，由调用方回退正则解析。
    """
    text = raw or ""
    arr: list | None = None
    title = ""
    req_summary = ""

    # ① 优先尝试对象格式（jsonx 配对解析：不会像贪婪正则那样吞掉对象后的内容）
    obj = jsonx.find_dict(text, need_keys=("units",))
    if isinstance(obj, dict) and isinstance(obj.get("units"), list):
        arr = obj["units"]
        title = str(obj.get("title") or "").strip()
        req_summary = str(obj.get("summary") or "").strip()

    # ② 回退旧格式：裸数组（元素键是 name，与用例的 title 不同，故单独传 keys）
    if arr is None:
        arr = jsonx.find_dict_list(text, keys=("name",))

    if arr is None:
        # 显性留痕：旧实现静默返回空，会让「模型没给 JSON」与「给了但格式不对」无法区分
        logger.warning(
            "测试点解析无产出，回退正则切标题：原文 %d 字，reason=%s",
            len(text),
            "invalid_json" if jsonx.has_array_literal(text) else "no_json",
        )
        return [], title, req_summary

    units: list[RequirementUnit] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        desc = str(item.get("description", "")).strip()
        units.append(RequirementUnit(
            name=name[:80],
            kind="module",
            description=desc[:500],
            params=[name[:80]],
        ))
    return units, title, req_summary


def _ai_parse_business(text: str, client) -> tuple[list[RequirementUnit], str, str]:
    """用真实模型把整段需求拆成测试点，并顺带产出需求摘要与任务名。

    失败返回 ([], "", "")（由调用方回退正则解析）。
    """
    if client is None:
        return [], "", ""
    prompt = _AI_PARSE_PROMPT.replace("{text}", text.strip()[:6000])
    try:
        raw = client.generate(prompt)
    except Exception:
        return [], "", ""
    return _parse_ai_output(raw)


def run_parser(input_path: str, kind: str, client=None):
    """返回 (units, summary, details_json)。

    details_json 含 units（供前端展开查看）+ title / req_summary（供引擎命名）。
    """
    units: list[RequirementUnit] = []
    mode = "解析"
    ai_title = ""
    ai_req_summary = ""

    # 统一抽取文本：docx/pdf 这类二进制文档交给 doc_extract，不再裸 read_text
    # （旧写法对二进制文件直接抛 UnicodeDecodeError，会把整个任务打成 failed）
    text = extract_text_safe(input_path)

    if kind == "business" and text.strip():
        units, ai_title, ai_req_summary = _ai_parse_business(text, client)
        if units:
            mode = "AI 理解拆解"

    if not units:
        # 兜底：正则切标题。把已抽取的文本传下去，避免二次读文件再踩二进制；
        # text 为空（如 swagger json 不在白名单）时退回按路径读文件。
        units = lib_parse(kind, input_path, text=text or None)
        mode = "正则解析" if kind == "business" else "解析"

    # 命名：优先用模型给的，缺失/洗不出内容则用测试点兜底
    fallback_title, fallback_req_summary = _fallback_naming(units)
    title = clean_task_name(ai_title) or fallback_title
    req_summary = ai_req_summary or fallback_req_summary

    summary = f"{mode}得到 {len(units)} 个测试点"
    if client is None:
        summary += "（未配置可用模型，当前为规则解析，请到【模型设置】配置真实模型）"
    details = {
        "units": [{"name": u.name, "description": u.description} for u in units],
        "title": title,
        "req_summary": req_summary,
    }
    return units, summary, json.dumps(details, ensure_ascii=False)
