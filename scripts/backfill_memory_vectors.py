"""V7.2 存量记忆条目向量回填（幂等，100/批）。

做的事：扫全部 active 条目，分批调 vectorstore.index_memory_vectors 补向量。
- mock embedding（无真实 Embedding 配置）时**全部静默跳过**，绝不写假向量
- upsert 天然幂等：重复执行安全（内容没变的条目重写同 id 同向量）
- 打印统计：扫描数 / 写入数

用法：cd ai-testgen && python scripts/backfill_memory_vectors.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.models.memory import MemoryItem  # noqa: E402
from app.services.knowledge.vectorstore import (  # noqa: E402
    index_memory_vectors,
    using_mock_embedding,
)

BATCH = 100  # 单批条数（与 embedding 批量上限留余量）


def main() -> int:
    init_db()
    if using_mock_embedding():
        print("未配置真实 Embedding（mock 模式）：跳过回填，绝不写入假向量。")
        return 0
    db = SessionLocal()
    scanned = indexed = 0
    try:
        while True:
            rows = db.execute(
                select(MemoryItem)
                .where(MemoryItem.status == "active")
                .order_by(MemoryItem.id)
                .offset(scanned)
                .limit(BATCH)
            ).scalars().all()
            if not rows:
                break
            scanned += len(rows)
            indexed += index_memory_vectors(list(rows))
            print(f"已处理 {scanned} 条（写入向量 {indexed}）")
    finally:
        db.close()
    print(f"回填完成：active 条目 {scanned}，写入向量 {indexed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
