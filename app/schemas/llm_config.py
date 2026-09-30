"""LLM 配置请求/响应模型（V2.4 FR-I）。

V5.4：单条配置下线后仅保留连通测试（LLMTestIn）与首页对话流（ChatIn）；
模型池的请求/响应模型见 llm_pool.py。
"""
from pydantic import BaseModel


class LLMTestIn(BaseModel):
    """测试连通：api_key 留空 = 复用池里同厂商已存 Key（免费厂商由服务端 Key 兜底）。"""
    provider: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    kind: str = "chat"              # chat | embedding（V4.0：向量模型走 /embeddings）


class ChatIn(BaseModel):
    """首页对话流：用户消息 + 可选历史（多轮）。"""
    message: str
    history: list[dict] = []         # [{role:"user"/"assistant", content:"..."}]
    conversation_id: str | None = None   # 归属会话（落库对话记录用；None 则不落库）
    task_id: str | None = None       # 迭代补充模式：关联的任务 id，AI 回复时附该任务用例摘要
    file_id: str | None = None       # 对话附件 id（POST /api/files 返回），AI 读取文档内容后作答
    thinking: bool | None = None     # 「总是深度思考」开关：True=每轮都推理；None=按需自动（复杂问题才推理）
    roles: list[str] | None = None   # 参与角色（pm/qa/dev），决定 AI 回复身份；空则默认测试工程师
    kb_id: str | None = None         # V4.0 RAG：单库检索（V4.1 kb_qa 遗留；兼容保留）
    kb_ids: list[str] = []           # V5.8 多选检索：勾选的知识库 id 列表；空 = 不检索（不再全库搜）
    mode: str | None = None          # V4.1 会话模式：workflow(默认)/kb_qa（kb_qa 前端已下线，兼容历史）
