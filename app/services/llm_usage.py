"""LLM 用量记录器（V5.12）：langchain_client 埋点的落库出口。

设计约束：
- 独立 Session（可能在 executor 线程 / 生成器 finally 里调用，不能共用请求级 session）
- 任何异常静默吞掉：统计失败绝不能影响主调用链
"""
import logging

logger = logging.getLogger(__name__)


def record_usage(model: str, user_id: int = 0, slot: str = "text", action: str = "chat",
                 ok: bool = True, latency_ms: int = 0, prompt_chars: int = 0,
                 completion_chars: int = 0, error: str = "") -> None:
    """一次真实模型调用落一行 llm_usage；失败仅打 DEBUG，不抛出。"""
    try:
        from app.core.db import SessionLocal
        from app.models.llm_usage import LLMUsage

        db = SessionLocal()
        try:
            db.add(LLMUsage(
                user_id=user_id or 0,
                model=(model or "")[:128],
                slot=(slot or "text")[:16],
                action=(action or "chat")[:16],
                ok=bool(ok),
                latency_ms=max(0, int(latency_ms or 0)),
                prompt_chars=max(0, int(prompt_chars or 0)),
                completion_chars=max(0, int(completion_chars or 0)),
                error=(error or "")[:200],
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001  统计不干扰业务
        logger.debug("llm_usage 记录失败（忽略）: %s", e)
