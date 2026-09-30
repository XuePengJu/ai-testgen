"""清空旧任务测试数据（一次性，部署/重构时执行）。

删除范围：tasks / step_logs 表全量 + outputs / uploads 目录下的导出与上传文件。
保留：用户账号（users）、分类（categories）、.DS_Store 与目录结构。

老板明确：旧任务不做保留，直接删除（都是测试数据）。本地库与服务器库各自独立，需分别执行。
用法：cd 项目根目录 && python scripts/clear_legacy_data.py

注：已改为 SQLAlchemy 执行，不再依赖 sqlite3，SQLite / MySQL 双方言通用。
"""
import shutil
import sys
from pathlib import Path

# 让脚本在任意 cwd 下都能 import app 包（项目根目录加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.config import OUTPUT_DIR, UPLOAD_DIR
from app.core.db import engine


def clear() -> None:
    # 1. 清空表数据（跨方言，走 SQLAlchemy engine）
    with engine.connect() as conn:
        n_tasks = conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
        n_steps = conn.execute(text("SELECT COUNT(*) FROM step_logs")).scalar()
        conn.execute(text("DELETE FROM step_logs"))
        conn.execute(text("DELETE FROM tasks"))
        conn.commit()
    print(f"已清空 tasks（{n_tasks} 条）/ step_logs（{n_steps} 条）")

    # 2. 清空导出与上传文件（保留 .DS_Store 与目录结构）
    for base in (OUTPUT_DIR, UPLOAD_DIR):
        if not base.exists():
            continue
        n = 0
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name != ".DS_Store":
                p.unlink()
                n += 1
        print(f"已清空 {base.name} 下 {n} 个文件")

    print("完成")


if __name__ == "__main__":
    clear()
