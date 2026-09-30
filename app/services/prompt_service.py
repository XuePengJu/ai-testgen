"""提示词自定义服务（V5.10 FR-AK）。

可编辑提示词清单（registry）：
- 对话系统提示词 8 个：chat_role_{qa|pm|dev|kb}_{think|plain}
    默认源 = llm_service._system_prompt_for（代码内置）
- 用例生成模板 6 个：gen_tpl_{api|req}_{qa|pm|dev}
    默认源 = generator_core/config/prompts/*.txt（api_case / requirement_case ± _pm/_dev）

生效规则：prompt_overrides 表有记录 → 整段覆盖；无记录 → 内置默认。
默认值永远以代码/文件为源、不进库——恢复默认 = 删记录，零丢失风险。
"""
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_override import PromptOverride
from app.core.utils import utcnow
# 注意：llm_service 只在函数内延迟 import（其顶部 import 本模块，顶层互引会循环）

# ---- 对话层 registry ----

CHAT_ROLES = {
    "qa": "测试工程师",
    "pm": "产品经理",
    "dev": "开发工程师",
    "kb": "知识库问答助手",
}
CHAT_VARIANTS = {"think": "深度思考版", "plain": "标准回复版"}


def chat_key(role: str, variant: str) -> str:
    return f"chat_role_{role}_{variant}"


def chat_default(role: str, variant: str) -> str:
    from app.services import llm_service  # 延迟 import 防循环
    return llm_service._system_prompt_for(role, variant == "think")


# ---- 生成模板层 registry ----

# kind → 文件名（generator_core/config/prompts/ 下）；_ROLE_TPL_SUFFIX 同款映射
GEN_KINDS = {"api": "接口用例", "req": "业务需求用例"}
_GEN_FILE = {"api": "api_case{suffix}.txt", "req": "requirement_case{suffix}.txt"}
_GEN_SUFFIX = {"qa": "", "pm": "_pm", "dev": "_dev"}
# 生成模板 .format 填充变量（case_generator._build_prompt 传入，缺一则运行时崩）
GEN_REQUIRED_VARS = ("name", "path", "description", "params", "kind", "constraints")


def gen_key(kind: str, role: str) -> str:
    return f"gen_tpl_{kind}_{role}"


def gen_default(kind: str, role: str) -> str:
    from generator_core.config import settings

    fname = _GEN_FILE[kind].format(suffix=_GEN_SUFFIX[role])
    return (settings.PROMPTS_DIR / fname).read_text(encoding="utf-8")


# ---- registry 汇总 ----

ALL_KEYS: dict[str, dict] = {}


def _register():
    for role, role_label in CHAT_ROLES.items():
        for variant, variant_label in CHAT_VARIANTS.items():
            k = chat_key(role, variant)
            ALL_KEYS[k] = {
                "group": "chat",
                "name": f"对话 · {role_label}（{variant_label}）",
                "description": "AI 对话回复的人设与行为要求；保存后下一条消息即生效",
            }
    for kind, kind_label in GEN_KINDS.items():
        for role, role_label in CHAT_ROLES.items():
            if role == "kb":
                continue  # 生成模板只有 qa/pm/dev 三角色
            k = gen_key(kind, role)
            ALL_KEYS[k] = {
                "group": "gen",
                "name": f"生成模板 · {kind_label} × {role_label}",
                "description": "用例生成的提示词模板；必须保留 {name} 等占位符，保存时强校验",
            }


_register()


# ---- 校验 ----

CHAT_MAX_LEN = 8000


def _validate(key: str, content: str) -> None:
    if not content or not content.strip():
        raise ValueError("提示词内容不能为空")
    if len(content) > CHAT_MAX_LEN * 4:
        raise ValueError(f"内容过长（上限 {CHAT_MAX_LEN * 4} 字符）")
    if key.startswith("chat_role_"):
        return
    # 生成模板：占位符必须是 6 个合法变量之一（模板可以少用，如 requirement 模板不用 {path}），
    # 但至少保留一个业务输入变量（{name}/{description}），且试填充不抛错（防裸花括号炸 format）
    item = ALL_KEYS[key]
    if item["group"] != "gen":
        return
    try:
        fields = {f for _, f, _, _ in string.Formatter().parse(content) if f}
        content.format(name="n", path="p", description="d", params="pa",
                       kind="k", constraints="c")
    except (KeyError, ValueError, IndexError) as e:
        raise ValueError(f"模板包含非法占位符或不成对的花括号：{e}")
    illegal = fields - set(GEN_REQUIRED_VARS)
    if illegal:
        raise ValueError(
            "存在不支持的占位符：" + "、".join("{" + v + "}" for v in sorted(illegal))
            + "（可用：{name} {path} {description} {params} {kind} {constraints}）"
        )
    if not fields & {"name", "description"}:
        raise ValueError("缺少业务占位符：模板至少要保留 {name} 或 {description} 之一，否则生成无业务输入")


# ---- 读写 ----

def _get_row(db: Session, user_id: int, key: str) -> PromptOverride | None:
    return db.execute(
        select(PromptOverride).where(
            PromptOverride.user_id == user_id, PromptOverride.key == key,
        )
    ).scalar_one_or_none()


def get_override(db: Session, user_id: int, key: str) -> str | None:
    """生效内容：有自定义返回自定义，否则 None（调用方用默认）。"""
    row = _get_row(db, user_id, key)
    return row.content if row and row.content.strip() else None


def get_chat_override(db: Session, user_id: int | None, role: str, want_thinking: bool) -> str | None:
    if db is None or user_id is None:
        return None
    return get_override(db, user_id, chat_key(role, "think" if want_thinking else "plain"))


def get_gen_override(db: Session, user_id: int | None, kind: str, role: str) -> str | None:
    if db is None or user_id is None:
        return None
    # 运行时 kind 取值为 api / action / requirement（case_generator._build_prompt），
    # 归一到 registry 的 api / req 两类
    norm = "api" if kind in ("api", "action") else "req"
    return get_override(db, user_id, gen_key(norm, role))


def default_of(key: str) -> str:
    item = ALL_KEYS.get(key)
    if item is None:
        raise KeyError(key)
    if item["group"] == "chat":
        role, variant = key.removeprefix("chat_role_").rsplit("_", 1)
        return chat_default(role, variant)
    kind, role = key.removeprefix("gen_tpl_").split("_", 1)
    return gen_default(kind, role)


def put_override(db: Session, user_id: int, key: str, content: str) -> None:
    if key not in ALL_KEYS:
        raise KeyError(key)
    _validate(key, content)
    row = _get_row(db, user_id, key)
    if row:
        row.content = content
        row.updated_at = utcnow()
    else:
        row = PromptOverride(user_id=user_id, key=key, content=content)
        db.add(row)
    db.commit()


def delete_override(db: Session, user_id: int, key: str) -> bool:
    row = _get_row(db, user_id, key)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True
