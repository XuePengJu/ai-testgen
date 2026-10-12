"""演示站手动重置 API（V9.0）。

- POST /api/demo/reset：立即执行一次数据重置（运维/联调用，不等凌晨 2 点）。
  仅演示实例 + 管理员可调；返回删除计数。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_admin
from app.api.demo import _require_demo_enabled
from app.jobs.demo_reset import reset_demo_data
from app.core.db import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/demo", tags=["演示站"])

logger = logging.getLogger("api.demo_reset")


@router.post("/reset")
def manual_reset(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """手动触发演示站数据重置（仅演示实例管理员可调）。

    正常无需使用 —— 每天 02:00 自动执行；此接口用于联调/演示前快速归零。
    """
    _require_demo_enabled()
    counts = reset_demo_data(db)
    logger.info("手动触发演示站重置：%s", counts)
    return {"ok": True, "message": "演示站数据已重置", "counts": counts}
