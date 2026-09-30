"""V5.5 用例库资产化：存量任务一次性迁移。

做的事（幂等，可重复跑）：
1. 备份 tasks 表 → backup_20260928_tasks（CREATE TABLE IF NOT EXISTS ... AS SELECT）；
2. review_status 为空的存量任务统一置 'draft'（_ensure_columns 补列后新行自带默认值）；
3. 无语义任务名（「任务-xxxxxxxxxxxx」/「未命名任务」）用所属会话的首条用户消息改出可读名称，
   改名前记录原始名到 input_summary 尾部（溯源，不覆盖原 input_ref）。

用法：cd ai-testgen && .venv/bin/python scripts/migrate_case_library.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.models.conversation import Message  # noqa: E402
from app.models.task import Task  # noqa: E402

BACKUP_TABLE = "backup_20260928_tasks"
DEFAULT_NAME_RE = re.compile(r"^任务-[0-9a-f]{6,}$")
# 会话首条消息常见的固定前缀（emoji + 引导语），剥掉后才是有效标题
PREFIX_RE = re.compile(
    r"^(?:🌐\s*发起全链路测试：|🤖\s*发起探索式测试：|🔁\s*迭代《[^》]*》：)?\s*"
)


def derive_name(first_user_msg: str | None) -> str:
    """从会话首条用户消息提炼默认用例集名：剥前缀、压空白、截 60 字。"""
    if not first_user_msg:
        return ""
    s = PREFIX_RE.sub("", first_user_msg).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:60]


def main() -> None:
    with engine.connect() as conn:
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS SELECT * FROM tasks"))
        conn.commit()
    print(f"[1/3] 备份完成：{BACKUP_TABLE}")

    db = SessionLocal()
    try:
        n_draft = db.execute(
            text("UPDATE tasks SET review_status='draft' WHERE review_status IS NULL")
        ).rowcount
        db.commit()
        print(f"[2/3] review_status 补 draft：{n_draft} 行")

        tasks = db.execute(select(Task)).scalars().all()
        renamed = 0
        for t in tasks:
            if not (t.name and (DEFAULT_NAME_RE.match(t.name) or t.name == "未命名任务")):
                continue
            # 来源会话优先级：task.conversation_id → messages.task_id 反查（与 _resolve_conversation 同序）
            conv_id = t.conversation_id
            if not conv_id:
                row = db.execute(
                    select(Message.conversation_id)
                    .where(Message.task_id == t.id)
                    .order_by(Message.id.asc())
                ).first()
                conv_id = row[0] if row else None
            first_msg = None
            if conv_id:
                m = db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id, Message.role == "user")
                    .order_by(Message.id.asc())
                ).scalars().first()
                first_msg = m.content if m else None
            new_name = derive_name(first_msg)
            if not new_name:
                continue
            t.input_summary = f"{t.input_summary or ''}|原名:{t.name}"
            t.name = new_name
            renamed += 1
        db.commit()
        print(f"[3/3] 无语义任务改名：{renamed} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()
