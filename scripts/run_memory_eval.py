#!/usr/bin/env python3
"""V7.3 记忆评估跑分入口（独立于 pytest，可进 CI 门禁）。

用法：
    .venv/bin/python scripts/run_memory_eval.py

- 自建一次性 SQLite 环境（tmp 目录，不碰本地/生产库），init_db 后跑黄金集
- 五类指标任一不达阈值 → 退出码 1（可直接挂 CI 门禁）
- 每次跑分落一行 MemoryEvalRun（表缺失时跳过落库不阻断跑分）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ---- 环境隔离（照 tests/conftest.py 的口径，必须在 import app 前设置）----
_TMP = tempfile.mkdtemp(prefix="aitf_eval_")
os.environ["AITF_ROOT_DIR"] = _TMP
os.environ["DB_TYPE"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "eval-only-secret-key-not-for-prod")
os.environ.setdefault("DASHSCOPE_API_KEY", "")
os.environ.setdefault("AITF_ALLOW_DEMO", "1")
os.environ.setdefault("AITF_SCHEDULER", "0")
os.environ.setdefault("API_ENCRYPT", "0")


def main() -> int:
    from app.core.db import SessionLocal, init_db
    init_db()

    from app.services.memory.eval import format_report, run_suite
    out = run_suite()
    print(format_report(out))

    # ---- 跑分留痕（MemoryEvalRun 表由 V7.2/V7.3 地基批次建；缺失不阻断）----
    try:
        from app.models.memory import MemoryEvalRun
        db = SessionLocal()
        try:
            db.add(MemoryEvalRun(
                biz_date=datetime.utcnow().strftime("%Y-%m-%d"),
                total=out["total"], passed=out["passed"],
                metrics=json.dumps(out["metrics"], ensure_ascii=False),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001  留痕失败不影响跑分结论
        print(f"[warn] MemoryEvalRun 落库跳过：{type(e).__name__}: {e}")

    if out["threshold_fail"]:
        print(f"\n结果：不达标（{', '.join(out['threshold_fail'])}）")
        return 1
    print("\n结果：全部指标达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
