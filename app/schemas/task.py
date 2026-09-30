"""任务相关的 Pydantic 响应模型。"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class StepLogOut(BaseModel):
    name: str
    title: str
    status: str
    progress: Optional[str] = None
    started_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    error: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    name: str
    kind: str
    source_type: str
    status: str
    # V5.5 用例库资产化：评审状态 draft/reviewed（与生成过程状态 status 分离）
    review_status: str = "draft"
    cases_count: int
    duration_ms: float
    formats: str = "xlsx,json,xmind"
    roles: str = '["qa"]'  # 多角色协作（V3.1）：参与生成的角色 JSON 数组
    category_id: Optional[int] = None
    parent_task_id: Optional[str] = None
    # M2 执行引擎：任务目录 auto/ 下已生成自动化脚本（前端据此显示「自动化」Tab/执行入口）
    has_auto: bool = False
    # 所属会话：详情页「继续优化」据此跳回会话并挂载迭代引用。
    # 历史任务（conversation_id 为空）由 api/tasks.py 的 _resolve_conversation 反查兜底。
    conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    steps: list[StepLogOut] = []
    # 结构化用例列表（仅详情接口包含；列表接口为 []）。
    # 由 api/tasks.py 的 _to_out(include_cases=True) 注入，避免大 payload 拖慢列表渲染。
    cases: list[dict[str, Any]] = []

    class Config:
        from_attributes = True


class TaskPatchIn(BaseModel):
    """V5.5 用例库：任务（用例集）部分更新入参。全部可选，只更新提供的字段。"""
    name: Optional[str] = None
    review_status: Optional[str] = None  # draft / reviewed
