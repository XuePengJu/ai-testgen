"""V2 迁移：tasks 加 user_id / 建 users 等表 / 引导默认 admin / 存量归档。

幂等可重复执行：列已存在跳过；admin 已存在跳过（不重置密码）。

用法：python scripts/migrate_v2.py
"""
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import inspect, text  # noqa: E402

from app.core.config import UPLOAD_DIR, OUTPUT_DIR, ADMIN_BOOTSTRAP_PASSWORD  # noqa: E402
from app.core.db import engine, SessionLocal, init_db  # noqa: E402
from app.core import security  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.user import User  # noqa: E402


def ensure_task_user_id() -> None:
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("tasks")]
    if "user_id" in cols:
        print("[skip] tasks.user_id 已存在")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN user_id INTEGER"))
    print("[ok]   tasks.user_id 已添加")


def ensure_admin(db) -> User:
    admin = db.query(User).filter(User.role == "admin").first()
    if admin:
        print(f"[skip] admin 已存在（{admin.username}），不重置密码")
        return admin
    import secrets
    pwd = ADMIN_BOOTSTRAP_PASSWORD or ("Admin@" + secrets.token_hex(6))
    admin = User(
        username="admin", email="admin@local",
        password_hash=security.hash_password(pwd),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    admin.data_dir = f"u_{admin.id}"
    db.commit()
    print(f"[ok]   默认 admin 已创建，密码：{pwd}（仅本次打印，请妥善保存）")
    return admin


def adopt_orphan_tasks(db, admin: User) -> None:
    n = db.query(Task).filter(Task.user_id.is_(None)).count()
    if n == 0:
        print("[skip] 无存量任务需要归属")
        return
    db.query(Task).filter(Task.user_id.is_(None)).update({"user_id": admin.id})
    db.commit()
    print(f"[ok]   {n} 个存量任务已归到 {admin.username} 名下")


def move_flat_files(admin: User) -> None:
    """平铺的 uploads/ outputs/ 文件迁入 admin 目录。"""
    moved = 0
    for base in (UPLOAD_DIR, OUTPUT_DIR):
        dst = base / admin.data_dir
        dst.mkdir(parents=True, exist_ok=True)
        for f in base.iterdir():
            if f.is_file():
                shutil.move(str(f), str(dst / f.name))
                moved += 1
    print(f"[ok]   {moved} 个平铺文件已迁入 {admin.data_dir}/")


def main() -> None:
    init_db()  # 建新表（users / guest_creation_log / clean_log）
    ensure_task_user_id()
    db = SessionLocal()
    try:
        admin = ensure_admin(db)
        adopt_orphan_tasks(db, admin)
        move_flat_files(admin)
    finally:
        db.close()
    print("迁移完成 ✅")


if __name__ == "__main__":
    main()
