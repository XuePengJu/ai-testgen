#!/usr/bin/env python3
"""One-shot migration: local SQLite (app.db) -> MySQL (configured via .env DB_*).

- Pure table-level copy via SQLAlchemy MetaData.reflect (no business-model dependency).
- Keeps original row ids (uuid strings); wraps the whole batch in a transaction.
- Guards: aborts if any MySQL target table already has rows (avoid dirty overwrite).
- Password is read from .env at runtime; the script itself contains no credentials.
"""
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine, MetaData, select, insert, text

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"


def _load_env() -> dict:
    cfg = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def _env(cfg: dict, key: str, default: str = ""):
    import os
    return os.getenv(key, cfg.get(key, default))


def main():
    cfg = _load_env()
    db_type = _env(cfg, "DB_TYPE", "sqlite")
    if db_type != "mysql":
        raise SystemExit(f"[ABORT] DB_TYPE must be 'mysql' to migrate, got '{db_type}'")

    host = _env(cfg, "DB_HOST", "127.0.0.1")
    port = int(_env(cfg, "DB_PORT", "3306"))
    user = _env(cfg, "DB_USER", "")
    pwd = _env(cfg, "DB_PASSWORD", "")
    name = _env(cfg, "DB_NAME", "ai-testgen")
    charset = _env(cfg, "DB_CHARSET", "utf8mb4")

    mysql_url = f"mysql+pymysql://{user}:{quote_plus(pwd)}@{host}:{port}/{name}?charset={charset}"

    sqlite_path = BASE_DIR / "app.db"
    if not sqlite_path.exists():
        raise SystemExit(f"[ABORT] sqlite db not found: {sqlite_path}")
    sqlite_url = f"sqlite:///{sqlite_path}"

    # 打印连接信息（隐藏密码）
    print(f"[migrate] sqlite : {sqlite_path}")
    print(f"[migrate] mysql  : {user}@{host}:{port}/{name} (charset={charset})")

    sqlite_engine = create_engine(sqlite_url)
    mysql_engine = create_engine(mysql_url)

    sqlite_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)
    mysql_meta = MetaData()
    mysql_meta.reflect(bind=mysql_engine)

    # ---- 预检：MySQL 目标表必须为空，避免脏写 ----
    with mysql_engine.connect() as c:
        for t in mysql_meta.tables:
            n = c.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar() or 0
            if n > 0:
                raise SystemExit(
                    f"[ABORT] mysql table `{t}` already has {n} rows; "
                    f"refusing to overwrite. Truncate first or use a fresh db."
                )
    print("[migrate] precheck OK: all MySQL target tables are empty")

    TABLES = [
        "users", "categories", "llm_configs", "guest_creation_log",
        "tasks", "conversations", "messages", "clean_log", "step_logs",
    ]

    # ---- 执行迁移（整批事务）----
    with sqlite_engine.connect() as sc, mysql_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        total = 0
        for t in TABLES:
            if t not in sqlite_meta.tables or t not in mysql_meta.tables:
                print(f"[skip] {t}: not present in both dbs")
                continue
            src = sqlite_meta.tables[t]
            dst = mysql_meta.tables[t]
            rows = sc.execute(select(src)).mappings().all()
            if not rows:
                print(f"[empty] {t}: 0 rows")
                continue
            conn.execute(insert(dst), [dict(r) for r in rows])
            total += len(rows)
            print(f"[ok]    {t}: {len(rows)} rows")
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    print(f"[done] total inserted: {total}")

    # ---- 行数核对 ----
    print("\n=== 行数核对 (sqlite vs mysql) ===")
    all_ok = True
    with sqlite_engine.connect() as sc, mysql_engine.connect() as mc:
        for t in TABLES:
            if t not in sqlite_meta.tables:
                continue
            sn = sc.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar() or 0
            mn = mc.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar() or 0
            flag = "OK" if sn == mn else "MISMATCH"
            if sn != mn:
                all_ok = False
            print(f"  {t}: sqlite={sn}  mysql={mn}  [{flag}]")
    print("RESULT:", "ALL OK" if all_ok else "MISMATCH FOUND")


if __name__ == "__main__":
    main()
