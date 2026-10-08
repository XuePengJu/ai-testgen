#!/usr/bin/env python3
"""清理评估套件遗留的 eval*/probe* 脏账号（V7.4）。

背景
----
``app/services/memory/eval.py`` 每跑一次黄金集就会 ``_mk_user`` 建一批账号，命名
``eval{用例序号}{角色}{uuid6}``。2026-10-03 那次评估对着**真实库**跑了约 5 遍，
在共享 MySQL 里留下 120 个 ``eval*`` 账号，连带它们的个人知识库 / 会话 / 记忆条目；
另有 1 个 ``probe_*``（当时一次性探针脚本所建，脚本未留档）。

现在 ``scripts/run_memory_eval.py`` 已有环境隔离（临时目录 + SQLite，见其第 23-32 行），
**不会再污染真实库**。本脚本只用于清理这批历史遗留。

安全设计
--------
- **默认 dry-run**：只统计并打印，一行数据都不动
- 真删必须显式 ``--apply``；且执行前先把待删行整体导出一份 JSON 备份
- 硬护栏：命中集合里若混进 ``guest`` 或任何 admin 账号，直接中止（宁可不删）
- 复用 ``app/services/user_purge.py: purge_user_data``，与「管理界面删用户」同一套级联逻辑

用法
----
    .venv/bin/python scripts/purge_eval_users.py                  # dry-run（默认）
    .venv/bin/python scripts/purge_eval_users.py --apply          # 真删（先备份）
    .venv/bin/python scripts/purge_eval_users.py --keep-probe --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ⚠️ 必须先导入全部模型，否则 Base.metadata 不完整：
# 本脚本要按 metadata 枚举「所有含 user_id 的表」来做统计与备份，而 metadata 只登记
# **已 import 过**的模型。不显式导入的话，knowledge.py 那一族（knowledge_bases /
# knownelges / chunks）根本不在表里 —— 曾经因此漏掉 121 个知识库的统计与备份。
# 这里等价于 init_db() 的模型导入，但**不执行任何 DDL**（不调 create_all）。
import importlib  # noqa: E402
import pkgutil  # noqa: E402

import app.models as _models_pkg  # noqa: E402

for _m in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"app.models.{_m.name}")

from sqlalchemy import func, or_, select  # noqa: E402

from app.core.config import DB_HOST, DB_NAME, DB_PORT, DB_TYPE  # noqa: E402
from app.core.db import Base, SessionLocal  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.user_purge import purge_user_data  # noqa: E402

PROTECTED_USERNAMES = {"guest"}

# 兜底自检：这些表必须出现在 metadata 里，否则说明模型导入不全、统计会漏
_EXPECTED_TABLES = {
    "users", "conversations", "messages", "categories", "tasks", "step_logs",
    "knowledge_bases", "knowledges", "chunks", "chunk_revisions",
    "memory_items", "memory_audits", "memory_retrieval_logs",
    "chat_attachments", "prompt_overrides", "llm_configs", "llm_model_pool", "llm_usage",
}


def _jsonable(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _user_scoped_tables() -> list[str]:
    return sorted(t.name for t in Base.metadata.tables.values() if "user_id" in t.columns)


def _target_users(db, keep_probe: bool) -> list[User]:
    pats = ["eval%"] if keep_probe else ["eval%", "probe%"]
    return list(db.execute(
        select(User).where(or_(*[User.username.like(p) for p in pats])).order_by(User.id)
    ).scalars().all())


def _conv_ids(db, ids: list[int]) -> list[str]:
    if not ids:
        return []
    return [r[0] for r in db.execute(
        select(Conversation.id).where(Conversation.user_id.in_(ids))).all()]


def _counts(db, ids: list[int]) -> dict[str, int]:
    """批量聚合：每张表一条 SQL，避免按用户循环产生上千次往返。"""
    out: dict[str, int] = {}
    for name in _user_scoped_tables():
        tbl = Base.metadata.tables[name]
        out[name] = int(db.execute(
            select(func.count()).select_from(tbl).where(tbl.c.user_id.in_(ids))
        ).scalar() or 0)
    conv_ids = _conv_ids(db, ids)
    out["messages"] = int(db.execute(
        select(func.count()).select_from(Message)
        .where(Message.conversation_id.in_(conv_ids))
    ).scalar() or 0) if conv_ids else 0
    return out


def _dump_backup(db, users: list[User], path: Path) -> None:
    """把待删数据整体导出为 JSON（批量查询，含会话消息）。"""
    ids = [u.id for u in users]
    payload: dict = {
        "generated_at": datetime.now().isoformat(),
        "target": {"db_type": DB_TYPE, "host": DB_HOST, "port": DB_PORT, "name": DB_NAME},
        "users": [
            {k: _jsonable(v) for k, v in
             dict(db.execute(select(User.__table__)
                             .where(User.__table__.c.id == u.id)).mappings().one()).items()}
            for u in users
        ],
        "data": {},
    }
    for name in _user_scoped_tables():
        tbl = Base.metadata.tables[name]
        payload["data"][name] = [
            {k: _jsonable(v) for k, v in dict(r).items()}
            for r in db.execute(select(tbl).where(tbl.c.user_id.in_(ids))).mappings().all()
        ]
    conv_ids = _conv_ids(db, ids)
    payload["data"]["messages"] = [
        {k: _jsonable(v) for k, v in dict(r).items()}
        for r in db.execute(select(Message.__table__)
                            .where(Message.__table__.c.conversation_id.in_(conv_ids))
                            ).mappings().all()
    ] if conv_ids else []

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 eval*/probe* 脏账号（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只统计不删）")
    ap.add_argument("--keep-probe", action="store_true", help="保留 probe_*，只清 eval*")
    ap.add_argument("--backup", default=None, help="备份 JSON 路径")
    args = ap.parse_args()

    print("=" * 66)
    print(f" 目标库：{DB_TYPE}://{DB_HOST}:{DB_PORT}/{DB_NAME}")
    print(f" 模式：{'APPLY（真删）' if args.apply else 'DRY-RUN（只统计）'}"
          f"{'   仅 eval*' if args.keep_probe else '   eval* + probe*'}")
    print("=" * 66)

    # ---- 自检：模型必须导入齐全，否则统计/备份会漏表 ----
    missing_tables = _EXPECTED_TABLES - set(Base.metadata.tables)
    if missing_tables:
        print("❌ 中止：模型导入不全，Base.metadata 缺少这些表，统计与备份会漏数据：")
        print("   " + ", ".join(sorted(missing_tables)))
        return 3

    db = SessionLocal()
    try:
        users = _target_users(db, args.keep_probe)
        if not users:
            print("没有匹配到任何待清理账号。")
            return 0

        bad = [u for u in users if u.username in PROTECTED_USERNAMES or u.role == "admin"]
        if bad:
            print("❌ 中止：命中集合里出现受保护账号（guest / admin），拒绝继续：")
            for u in bad:
                print(f"     id={u.id} {u.username} role={u.role}")
            return 2

        ids = [u.id for u in users]
        counts = _counts(db, ids)
        print(f"\n匹配到 {len(users)} 个账号（id {ids[0]}..{ids[-1]}），逐表统计：\n")
        for name in sorted(counts):
            if counts[name]:
                print(f"   {name:24} {counts[name]:6d}")
        print(f"   {'users':24} {len(users):6d}")
        print("\n   账号样例： " + ", ".join(u.username for u in users[:6]) +
              (" ..." if len(users) > 6 else ""))
        other = [u for u in db.execute(select(User)).scalars().all() if u not in users]
        print("   将被保留： " + ", ".join(f"{u.username}(id={u.id})" for u in other))

        if not args.apply:
            print("\n[DRY-RUN] 未做任何修改。确认无误后加 --apply 执行（会先自动备份）。")
            return 0

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(args.backup) if args.backup else (_ROOT / "outputs" / f"purge_backup_{ts}.json")
        _dump_backup(db, users, backup)
        print(f"\n[备份] 待删数据已导出：{backup}")

        print("\n开始清理 ...")
        grand: dict[str, int] = {}
        for i, u in enumerate(users, 1):
            counts_i = purge_user_data(db, u)
            db.delete(u)
            db.commit()
            for k, v in counts_i.items():
                grand[k] = grand.get(k, 0) + v
            if i % 20 == 0 or i == len(users):
                print(f"   进度 {i}/{len(users)}")

        print("\n清理完成，明细：")
        for k in sorted(grand):
            print(f"   {k:24} {grand[k]:6d}")
        print(f"   剩余匹配账号：{len(_target_users(db, args.keep_probe))}")
        print(f"\n备份留存：{backup}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
