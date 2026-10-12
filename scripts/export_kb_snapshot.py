"""导出知识库快照（源库 → 可迁移的目录），供演示站导入/每日重置恢复。

用途
----
演示站（/demo，独立 SQLite + 独立向量库）需要一份「初始知识库」，
且每日 02:00 重置清空用户数据后要能自动恢复。本脚本把**源实例**里
指定的知识库（默认本地「示例-ERP共享知识库」+ admin 个人库）导出成
自包含快照目录：

    <out>/
      meta.json      导出元信息（版本/时间/来源/知识库清单）
      bases.json     knowledge_bases 行
      docs.json      knowledges 行（file_path 重写为快照内相对路径）
      chunks.json    chunks 行
      files/knowledge/<kb_id>/<name>   原始上传文件
      vectors.json   Chroma 向量（id/embedding/metadata/document）

导入侧用 app/services/demo_kb_seed.py 的 seed_demo_kb()，同一套快照
既用于首次导入，也用于每日重置后的自动恢复（幂等）。

用法（在项目根，走 .env 指定的源库）
    .venv/bin/python scripts/export_kb_snapshot.py \
        --out /tmp/demo-kb-snapshot \
        --base 54125fc6a1d64252 \
        --base 495a5c9b96e1481c
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import UPLOAD_DIR, VECTOR_DIR, VECTOR_COLLECTION  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models.knowledge import Chunk, ChunkRevision, Knowledge, KnowledgeBase  # noqa: E402

SKIP_COLS = {"created_at", "updated_at", "deleted_at", "processed_at", "edited_at"}


def _row_dict(obj) -> dict:
    """ORM 行 → 纯 dict（datetime 转 ISO 字符串，跨方言可移植）。"""
    out: dict = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, datetime):
            val = val.isoformat(sep=" ")
        elif isinstance(val, bool):
            val = bool(val)
        out[col.name] = val
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="导出知识库快照")
    ap.add_argument("--out", required=True, help="输出目录（会被清空重建）")
    ap.add_argument("--base", action="append", required=True, help="知识库 id（可多次）")
    ap.add_argument("--export-vectors", default="1", help="1=导出 Chroma 向量（默认）")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "files" / "knowledge").mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        bases, docs, chunks, revisions = [], [], [], []
        for kb_id in args.base:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None:
                print(f"⚠️  知识库不存在，跳过：{kb_id}")
                continue
            bases.append(_row_dict(kb))
            kb_docs = db.query(Knowledge).filter(Knowledge.knowledge_base_id == kb_id).all()
            kb_chunks = db.query(Chunk).filter(Chunk.knowledge_base_id == kb_id).all()
            kb_revs = (
                db.query(ChunkRevision).filter(ChunkRevision.knowledge_base_id == kb_id).all()
            )
            print(f"知识库 {kb.name}（{kb_id}）：文档 {len(kb_docs)} / 分块 {len(kb_chunks)} / 修订 {len(kb_revs)}")

            # 文件复制 + file_path 重写为快照内相对路径
            dest_dir = out / "files" / "knowledge" / kb_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            for d in kb_docs:
                row = _row_dict(d)
                src = Path(d.file_path) if d.file_path else None
                if src and src.is_file():
                    name = src.name
                    shutil.copy2(src, dest_dir / name)
                    row["file_path"] = f"knowledge/{kb_id}/{name}"
                else:
                    row["file_path"] = ""
                docs.append(row)

            for c in kb_chunks:
                chunks.append(_row_dict(c))
            for r in kb_revs:
                revisions.append(_row_dict(r))

        if not bases:
            print("❌ 没有可导出的知识库")
            return 1

        (out / "bases.json").write_text(json.dumps(bases, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "revisions.json").write_text(json.dumps(revisions, ensure_ascii=False, indent=1), encoding="utf-8")

        # ---- Chroma 向量 ----
        vec_count = 0
        if args.export_vectors == "1":
            import chromadb

            client = chromadb.PersistentClient(path=str(VECTOR_DIR))
            try:
                coll = client.get_collection(VECTOR_COLLECTION)
            except Exception:  # noqa: BLE001
                coll = None
            if coll is None:
                print("⚠️  源向量集合不存在，导出 0 条向量")
                vectors = []
            else:
                got = coll.get(
                    where={"kb_id": {"$in": list({b["id"] for b in bases})}},
                    include=["embeddings", "metadatas", "documents"],
                )
                ids = got.get("ids") or []
                embs = got.get("embeddings")
                metas = got.get("metadatas") or []
                docs_txt = got.get("documents") or []
                vectors = []
                for i, cid in enumerate(ids):
                    emb = embs[i] if embs is not None and i < len(embs) else None
                    if emb is None:
                        continue
                    vectors.append({
                        "id": cid,
                        "embedding": [float(x) for x in emb],
                        "metadata": metas[i] if i < len(metas) else {},
                        "document": docs_txt[i] if i < len(docs_txt) else "",
                    })
                vec_count = len(vectors)
            (out / "vectors.json").write_text(json.dumps(vectors), encoding="utf-8")
            print(f"向量导出：{vec_count} 条（维度 {len(vectors[0]['embedding']) if vectors else 0}）")

        meta = {
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "source": f"db={db.bind.dialect.name} vector_dir={VECTOR_DIR}",
            "collection": VECTOR_COLLECTION,
            "kb_ids": [b["id"] for b in bases],
            "counts": {
                "bases": len(bases), "docs": len(docs),
                "chunks": len(chunks), "revisions": len(revisions), "vectors": vec_count,
            },
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✅ 快照已导出到 {out}")
        print(json.dumps(meta["counts"], ensure_ascii=False))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
