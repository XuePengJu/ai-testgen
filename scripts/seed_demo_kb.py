"""按快照播种演示站初始知识库（幂等，可反复执行）。

首次导入与每日重置后恢复走的是同一套逻辑
（app/services/demo_kb_seed.py），本脚本只是它的命令行入口：

    # 用当前实例配置（.env / AITF_ENV_FILE）里的快照目录
    AITF_ENV_FILE=/root/ai-testgen/.env.demo venv/bin/python scripts/seed_demo_kb.py

    # 或显式指定快照目录与归属账号
    python scripts/seed_demo_kb.py --snapshot /root/ai-testgen-demo/seed --owner-user-id 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.db import SessionLocal  # noqa: E402
from app.services.demo_kb_seed import seed_demo_kb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="按快照播种知识库（幂等）")
    ap.add_argument("--snapshot", default="", help="快照目录（默认取 AITF_DEMO_KB_SEED_DIR）")
    ap.add_argument("--owner-user-id", type=int, default=0, help="归属账号 id（0=沿用快照原值）")
    args = ap.parse_args()

    snapshot = args.snapshot
    if not snapshot:
        from app.core import demo_config

        snapshot = demo_config.AITF_DEMO_KB_SEED_DIR
        if not args.owner_user_id:
            args.owner_user_id = demo_config.AITF_DEMO_KB_OWNER_USER_ID
    if not snapshot:
        print("❌ 未指定快照目录（--snapshot 或 AITF_DEMO_KB_SEED_DIR）")
        return 1

    db = SessionLocal()
    try:
        counts = seed_demo_kb(db, Path(snapshot), owner_user_id=args.owner_user_id or None)
    finally:
        db.close()
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
