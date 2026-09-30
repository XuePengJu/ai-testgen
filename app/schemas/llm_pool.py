"""模型池请求/响应模型（V5.0 P1）。"""
from pydantic import BaseModel, field_validator

_SLOTS = ("text", "vision", "embedding")


class PoolItemIn(BaseModel):
    """新增/修改池内一条模型。

    api_key 约定（与 LLMConfigIn 一致）：PUT 时缺省 = 保留原 Key；显式传空串 = 清除。
    priority 缺省 None = 不改（新增时用 100）。
    """
    provider: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    paid: bool = False
    note: str = ""
    enabled: bool = True
    priority: int | None = None

    @field_validator("model", "base_url")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} 不能为空")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def _prio_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9999):
            raise ValueError("priority 需在 1~9999 之间")
        return v


class PoolItemOut(BaseModel):
    id: int
    slot: str
    provider: str
    provider_label: str = ""
    base_url: str
    model: str
    api_key_masked: str = ""          # 如 ****abcd；空 = 靠服务端环境变量兜底
    priority: int
    enabled: bool
    paid: bool
    note: str = ""
    key_fingerprint: str = ""         # 前端判重用（不泄露 Key）
    # 健康状态
    cooldown_until: str | None = None
    cooling: bool = False
    last_error: str | None = None
    success_count: int = 0
    fail_count: int = 0
    effective: bool = False           # 是否为当前实际生效的池（用户池非空时平台池为 False）


class PoolReorderIn(BaseModel):
    ids: list[int]

    @field_validator("ids")
    @classmethod
    def _not_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("ids 不能为空")
        return v


class PoolEnabledIn(BaseModel):
    enabled: bool
