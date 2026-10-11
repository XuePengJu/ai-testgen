#!/usr/bin/env python3
"""知识库向量补建：把「MySQL 里有 chunks、本地向量库里没向量」的知识库重新嵌入。

背景（2026-10-11 现场）：DB 与向量库分离存储——MySQL（.env 指向的远程库）存
chunks，本地 Chroma（项目根 vectors/）存向量。项目拆分/迁移/换目录后，DB 数据
还在而本地向量库是空的，表现为：知识库页面一切正常（parse_status=ready），但
对话引用永远检索不到该库（向量通道 0 命中，只剩 BM25 关键词兜底）。

用法：
    .venv/bin/python scripts/backfill_kb_vectors.py                # 扫描全部库，缺向量的才补
    .venv/bin/python scripts/backfill_kb_vectors.py --kb <kb_id>   # 只补指定库
    .venv/bin/python scripts/backfill_kb_vectors.py --dry-run      # 只报告不写入

注意：
- 写入目标是**本机** Chroma（vectors/），不改 DB；幂等（upsert，可重复跑）。
- embedding 用库属主（user_id 最小的协作者）当前配置，与对话检索同模型才可匹配。
- 必须在**服务停止**时跑：Chroma 不支持跨进程并发写，服务运行中补建的向量
  对已打开的旧客户端不可见（要再重启一次才生效）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import llm_service  # noqa: E402
from app.services.knowledge import vectorstore as vs  # noqa: E402


def _kb_chunk_ids(db, kb_id: str) -> list[str]:
    """该库当前可检索 chunks 的 id 列表（与对话检索口径一致：双软删过滤）。"""
    rows = db.execute(
        select(Chunk.id)
        .join(Knowledge, Chunk.knowledge_id == Knowledge.id)
        .where(
            Chunk.knowledge_base_id == kb_id,
            Chunk.is_enabled.is_(True),
            Chunk.deleted_at.is_(None),
            Knowledge.deleted_at.is_(None),
        )
    ).scalars().all()
    return list(rows)


def _vector_ids_for_kb(kb_id: str) -> set[str]:
    """本地向量库里该 kb 现有的向量 id 集合。"""
    got = vs._collection().get(where={"kb_id": kb_id}, include=[])
    return set(got.get("ids") or [])


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库向量补建（本地 Chroma）")
    ap.add_argument("--kb", help="只处理指定知识库 id；缺省扫描全部库")
    ap.add_argument("--dry-run", action="store_true", help="只报告缺口，不写入")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        kbs = db.execute(select(KnowledgeBase)).scalars().all()
        if args.kb:
            kbs = [k for k in kbs if k.id == args.kb]
            if not kbs:
                print(f"知识库 {args.kb} 不存在")
                return

        # 全库统一用第一个库属主的 embedding 配置（与对话检索同源）
        owner = db.get(User, min(k.user_id for k in kbs))
        vs.configure_embedding(llm_service.resolve_embedding(db, owner.id).get("cfg"))
        if vs.using_mock_embedding():
            print("⚠️ 当前解析到 mock embedding——补出来的是假向量，请先配好真实 Embedding Key")
            return

        for kb in kbs:
            chunk_ids = _kb_chunk_ids(db, kb.id)
            if not chunk_ids:
                continue
            missing = set(chunk_ids) - _vector_ids_for_kb(kb.id)
            print(f"库「{kb.name}」({kb.id})：可检索 chunks={len(chunk_ids)}，缺向量={len(missing)}")
            if not missing or args.dry_run:
                continue
            docs = db.execute(
                select(Knowledge).where(Knowledge.knowledge_base_id == kb.id,
                                        Knowledge.deleted_at.is_(None))
            ).scalars().all()
            total = 0
            for doc in docs:
                chunks = db.execute(
                    select(Chunk).where(Chunk.knowledge_id == doc.id,
                                        Chunk.is_enabled.is_(True),
                                        Chunk.deleted_at.is_(None))
                    .order_by(Chunk.chunk_index)
                ).scalars().all()
                todo = [c for c in chunks if c.id in missing]
                if not todo:
                    continue
                payload = [{"id": c.id, "content": c.content or "",
                            "chunk_index": c.chunk_index} for c in todo]
                vs.index_chunks(kb.id, doc.id, doc.user_id, kb.visibility,
                                payload, doc.file_name or doc.title or "")
                total += len(payload)
                print(f"  + {doc.title[:28]:<30} {len(payload)} 块")
            print(f"  小计：{total} 条向量已补建")
        print("完成。若服务在运行中，请重启服务后再验证（Chroma 旧客户端看不到外部写入）。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
