"""知识库摘要存量数据修复：剥掉 {"summary": ...} 形状的 JSON 壳。

背景：历史版本 LLM 生成摘要时，偶尔把 ``{"summary": "..."}`` 形状的 JSON
字符串整段写进 knowledges.wiki_summary，前端把原始 JSON 当摘要展示。
新入库链路已在 app/services/knowledge/ingest.py:extract_summary_text 兜底，
本脚本只修存量数据。

做的事（幂等，可重复跑）：
1. 备份 knowledges 表的摘要相关列 → backup_20260928_knowledge_summaries
   （CREATE TABLE IF NOT EXISTS ... AS SELECT，参照 migrate_case_library.py 模式）；
2. 扫描 wiki_summary 以 ``{`` 开头的行，用 extract_summary_text 解析；
3. 解析结果与原值不同 → 更新回纯文本。

⚠️ 安全保护：默认 DRY_RUN 只打印将要修改的行，不执行任何 UPDATE。
确认预览无误后加 ``--apply`` 才真正落库：
    cd ai-testgen && .venv/bin/python scripts/migrate_kb_summary.py           # 预览
    cd ai-testgen && .venv/bin/python scripts/migrate_kb_summary.py --apply   # 执行
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.models.knowledge import Knowledge  # noqa: E402
from app.services.knowledge.ingest import extract_summary_text  # noqa: E402

BACKUP_TABLE = "backup_20260928_knowledge_summaries"
APPLY = "--apply" in sys.argv


def main() -> None:
    mode = "APPLY（真实写入）" if APPLY else "DRY_RUN（仅预览，不写库）"
    print(f"运行模式：{mode}")
    if not APPLY:
        print("提示：加 --apply 才会执行 UPDATE；当前只打印变更预览。\n")

    with engine.connect() as conn:
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS "
            "SELECT id, knowledge_base_id, title, wiki_summary, wiki_category, updated_at "
            "FROM knowledges"
        ))
        conn.commit()
    n_backup = engine.connect().execute(
        text(f"SELECT COUNT(*) FROM {BACKUP_TABLE}")).scalar_one()
    print(f"[1/3] 备份完成：{BACKUP_TABLE}（{n_backup} 行）")

    db = SessionLocal()
    try:
        # 只扫 JSON 形状的候选行，缩小范围
        rows = db.execute(
            select(Knowledge).where(Knowledge.wiki_summary.like("{%"))
        ).scalars().all()
        print(f"[2/3] 扫描 wiki_summary 以 '{{' 开头的行：{len(rows)} 条")

        changed: list[tuple[str, str, str]] = []  # (id, 旧值前 60 字, 新值前 60 字)
        for k in rows:
            cleaned = extract_summary_text(k.wiki_summary)
            if cleaned == (k.wiki_summary or ""):
                continue
            changed.append((k.id, (k.wiki_summary or "")[:60], cleaned[:60]))
            if APPLY:
                k.wiki_summary = cleaned
        if APPLY and changed:
            db.commit()

        print(f"[3/3] {'已更新' if APPLY else '将更新（DRY_RUN）'}：{len(changed)} 条")
        for doc_id, old, new in changed:
            print(f"  - {doc_id}\n      旧: {old}\n      新: {new}")
        if not APPLY and changed:
            print("\n预览确认无误后执行：.venv/bin/python scripts/migrate_kb_summary.py --apply")
    finally:
        db.close()


if __name__ == "__main__":
    main()
