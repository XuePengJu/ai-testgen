"""演示站知识库播种（V9.4）。

职责
----
把「知识库快照」（由 ``scripts/export_kb_snapshot.py`` 导出）灌入当前实例，
并在**每日重置清空数据后自动恢复**，让演示站始终有一份可浏览、可检索的
示例知识库（默认：ERP 共享库 + admin 个人库）。

为什么需要它
------------
演示站每日 02:00 重置会调 ``user_purge.purge_user_data`` 清掉**所有用户**
的知识库（含 global 共享库），否则第二天演示站又是空的；而知识点（分块 +
向量）重建成本高（119 块）且演示站没有 Embedding Key 无法重新向量化。
因此采用「快照恢复」：DB 行 + 原始文件 + Chroma 向量整体回灌。

幂等性
------
按主键存在即跳过（``bases`` / ``docs`` / ``chunks`` / ``chunk_revisions``），
向量走 ``upsert``；重复调用只补齐缺失部分，不产生重复行。
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import demo_config
from app.core.config import UPLOAD_DIR
from app.models.knowledge import Chunk, ChunkRevision, Knowledge, KnowledgeBase

logger = logging.getLogger("services.demo_kb_seed")

# 快照里 datetime 统一是 "%Y-%m-%d %H:%M:%S" 字符串（见导出脚本）
_DT_KEYS = ("created_at", "updated_at", "deleted_at", "processed_at")


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:  # noqa: BLE001
        logger.warning("快照文件解析失败 %s：%s", path, e)
        return []


def _parse_dt(val):
    if not val or isinstance(val, datetime):
        return val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(str(val), fmt)
        except ValueError:
            continue
    return None


def _clean(row: dict, *, extra_skip: set[str] | None = None) -> dict:
    """快照行 → ORM 可用的 kwargs（datetime 还原，去掉不属于该表的键）。"""
    skip = {"_sa_instance_state"} | (extra_skip or set())
    out: dict = {}
    for k, v in row.items():
        if k in skip:
            continue
        out[k] = _parse_dt(v) if k in _DT_KEYS else v
    return out


def seed_demo_kb(db: Session, snapshot_dir: Path, *, owner_user_id: int | None = None) -> dict:
    """把快照灌入当前实例（幂等）。返回各项新增计数。

    owner_user_id 非空时，把快照里所有者的 user_id 统一改写为该值
    （演示站账号 id 与源实例不同，必须重映射，否则行归属到不存在的用户）。
    """
    snapshot_dir = Path(snapshot_dir)
    if not (snapshot_dir / "meta.json").exists():
        logger.info("快照不存在，跳过知识库播种：%s", snapshot_dir)
        return {"skipped": True}

    counts = {"bases": 0, "docs": 0, "chunks": 0, "revisions": 0, "vectors": 0, "files": 0}
    files_root = snapshot_dir / "files"

    # ---- 1) 知识库 ----
    bases = _load(snapshot_dir / "bases.json")
    for row in bases:
        if db.get(KnowledgeBase, row["id"]) is not None:
            continue
        kw = _clean(row)
        if owner_user_id is not None:
            kw["user_id"] = owner_user_id
        db.add(KnowledgeBase(**kw))
        counts["bases"] += 1
    db.commit()

    # ---- 2) 文档（含原始文件回灌）----
    docs = _load(snapshot_dir / "docs.json")
    for row in docs:
        kw = _clean(row)
        if owner_user_id is not None:
            kw["user_id"] = owner_user_id
        # file_path：快照存的是相对路径（knowledge/<kb>/<name>）→ 还原为当前实例绝对路径
        rel = kw.get("file_path") or ""
        if rel and not Path(rel).is_absolute():
            target = UPLOAD_DIR / rel
            src = files_root / rel
            try:
                if src.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(src, target)
                        counts["files"] += 1
                    kw["file_path"] = str(target)
                else:
                    kw["file_path"] = ""
            except Exception as e:  # noqa: BLE001
                logger.warning("知识库文件回灌失败 %s：%s", rel, e)
                kw["file_path"] = ""
        if db.get(Knowledge, row["id"]) is not None:
            continue
        db.add(Knowledge(**kw))
        counts["docs"] += 1
    db.commit()

    # ---- 3) 分块 ----
    for row in _load(snapshot_dir / "chunks.json"):
        if db.get(Chunk, row["id"]) is not None:
            continue
        kw = _clean(row)
        if owner_user_id is not None:
            kw["user_id"] = owner_user_id
        db.add(Chunk(**kw))
        counts["chunks"] += 1
    db.commit()

    # ---- 4) 分块修订历史 ----
    for row in _load(snapshot_dir / "revisions.json"):
        if db.get(ChunkRevision, row["id"]) is not None:
            continue
        kw = _clean(row)
        if owner_user_id is not None:
            kw["user_id"] = owner_user_id
        db.add(ChunkRevision(**kw))
        counts["revisions"] += 1
    db.commit()

    # ---- 5) 向量（Chroma upsert，幂等）----
    vectors = _load(snapshot_dir / "vectors.json")
    if vectors:
        try:
            from app.services.knowledge import vectorstore

            vectorstore.upsert_raw_vectors(
                ids=[v["id"] for v in vectors],
                embeddings=[v["embedding"] for v in vectors],
                documents=[v.get("document") or "" for v in vectors],
                metadatas=[v.get("metadata") or {} for v in vectors],
            )
            counts["vectors"] = len(vectors)
        except Exception as e:  # noqa: BLE001
            logger.warning("向量回灌失败（知识库仍可 BM25 检索）：%s", e)

    logger.info("演示站知识库播种完成：%s", counts)
    return counts


def seed_demo_kb_from_config(db: Session) -> dict:
    """按 AITF_DEMO_KB_SEED_DIR 配置播种（未配置/非演示站 → no-op）。"""
    if not demo_config.AITF_DEMO_ENABLED:
        return {"skipped": True}
    raw = (demo_config.AITF_DEMO_KB_SEED_DIR or "").strip()
    if not raw:
        return {"skipped": True}
    owner = demo_config.AITF_DEMO_KB_OWNER_USER_ID
    return seed_demo_kb(db, Path(raw), owner_user_id=owner or None)
