"""记忆检索埋点（V7.3 地基）：_build_rag_context 出口采样一行。

范式照抄 app/models/llm_usage.py（V5.12）：
- **独立 Session**：不占用请求级 db 会话，提交失败互不影响
- **采样率** AITF_MEMORY_LOG_SAMPLE（[0,1]，默认 0.2）：高频对话下控表膨胀
- **任何异常静默失败**：埋点绝不影响主检索/主对话链路

评估脚本（V7.3 eval.py）按本表算 hit_rate / latency 分位 / item 命中占比。
"""
from __future__ import annotations

import json
import logging
import random

from app.core import config  # 用模块属性访问：采样率可被测试 monkeypatch

logger = logging.getLogger("memory.logs")


def log_retrieval(user_id: int, query: str, *, hits_total: int = 0,
                  item_hits: int = 0, latency_ms: int = 0,
                  hit_ids: list[str] | None = None) -> None:
    """采样落一条检索记录（任何异常静默，绝不抛出）。"""
    try:
        sample = float(config.AITF_MEMORY_LOG_SAMPLE)
        if sample <= 0:
            return
        if sample < 1.0 and random.random() > sample:
            return  # 采样命中之外直接丢弃
        from app.core.db import SessionLocal
        from app.models.memory import MemoryRetrievalLog
        db = SessionLocal()
        try:
            db.add(MemoryRetrievalLog(
                user_id=int(user_id or 0),
                query=(query or "")[:200],
                hits_total=int(hits_total or 0),
                item_hits=int(item_hits or 0),
                latency_ms=int(latency_ms or 0),
                hit_ids=json.dumps((hit_ids or [])[:10], ensure_ascii=False),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001  埋点静默失败（连日志都只留一行）
        logger.debug("检索埋点落库失败（静默）：%s", e)
