"""模型包：在此 import 各模型模块，确保注册到 Base.metadata。

init_db()（app/core/db.py）create_all 前也会直接 import，这里兜底保证
任何「先 import app.models」的调用路径都能发现全部模型。
"""
from app.models.llm_pool import LLMModelPool  # noqa: F401  V5.0 P1：多模型池
from app.models.prompt_override import PromptOverride  # noqa: F401  V5.10：提示词自定义
