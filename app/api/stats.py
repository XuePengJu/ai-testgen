"""使用统计接口（V5.12 可观测性，admin-only）。

- GET /api/stats/overview        概览：今日/累计的会话、消息、任务、用户、LLM 调用、HTTP 请求
- GET /api/stats/daily?days=14   按天趋势（缺失天补 0）
- GET /api/stats/llm/recent      最近 LLM 调用明细（时间/模型/成败/耗时/错误）

口径说明：created_at / bucket 均为 UTC，「今日」= UTC 当天（前端展示日期不做时区换算）。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.llm_usage import LLMUsage
from app.models.request_stat import RequestStat
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix="/stats", tags=["stats"])


def _day_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day)


def _count(db: Session, model, today_start: datetime | None = None) -> int:
    q = db.query(func.count(model.id))
    if today_start is not None:
        q = q.filter(model.created_at >= today_start)
    return int(q.scalar() or 0)


def _daily_map(db: Session, model, start_day: datetime) -> dict[str, int]:
    """model.created_at 按天分组计数 → {"YYYY-MM-DD": n}（仅 start_day 之后）。"""
    rows = (
        db.query(func.date(model.created_at), func.count(model.id))
        .filter(model.created_at >= start_day)
        .group_by(func.date(model.created_at))
        .all()
    )
    return {str(d): int(n) for d, n in rows if d is not None}


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    now = utcnow()
    today_start = _day_start(now)
    today_bucket = now.strftime("%Y%m%d") + "00"  # 当天 00:00 起的分钟桶前缀

    llm_total = _count(db, LLMUsage)
    llm_today = _count(db, LLMUsage, today_start)
    llm_fail = int(
        db.query(func.count(LLMUsage.id)).filter(LLMUsage.ok.is_(False)).scalar() or 0
    )
    avg_ms = db.query(func.avg(LLMUsage.latency_ms)).filter(LLMUsage.ok.is_(True)).scalar()
    by_model_rows = (
        db.query(LLMUsage.model, func.count(LLMUsage.id))
        .group_by(LLMUsage.model)
        .order_by(func.count(LLMUsage.id).desc())
        .limit(5)
        .all()
    )
    req_total = int(db.query(func.coalesce(func.sum(RequestStat.cnt), 0)).scalar() or 0)
    req_today = int(
        db.query(func.coalesce(func.sum(RequestStat.cnt), 0))
        .filter(RequestStat.bucket >= today_bucket)
        .scalar() or 0
    )

    return {
        "generated_at": now.isoformat(),
        "today": {
            "conversations": _count(db, Conversation, today_start),
            "messages": _count(db, Message, today_start),
            "tasks": _count(db, Task, today_start),
            "llm_calls": llm_today,
            "requests": req_today,
        },
        "total": {
            "users": _count(db, User),
            "guests": int(
                db.query(func.count(User.id)).filter(User.role == "guest").scalar() or 0
            ),
            "conversations": _count(db, Conversation),
            "messages": _count(db, Message),
            "tasks": _count(db, Task),
            "llm_calls": llm_total,
            "llm_fail": llm_fail,
            "llm_avg_ms": int(avg_ms) if avg_ms is not None else None,
            "requests": req_total,
        },
        "llm_by_model": [{"model": m or "-", "calls": int(n)} for m, n in by_model_rows],
    }


@router.get("/daily")
def daily(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    now = utcnow()
    start = _day_start(now) - timedelta(days=days - 1)
    axis = [(start + timedelta(days=i)) for i in range(days)]
    keys = [d.date().isoformat() for d in axis]

    conv = _daily_map(db, Conversation, start)
    msg = _daily_map(db, Message, start)
    task = _daily_map(db, Task, start)
    llm = _daily_map(db, LLMUsage, start)
    req_rows = (
        db.query(func.substr(RequestStat.bucket, 1, 8), func.sum(RequestStat.cnt))
        .group_by(func.substr(RequestStat.bucket, 1, 8))
        .all()
    )
    req = {f"{str(b)[:4]}-{str(b)[4:6]}-{str(b)[6:8]}": int(n)
           for b, n in req_rows if b}

    def _series(m: dict[str, int]) -> list[int]:
        return [m.get(k, 0) for k in keys]

    return {
        "days": keys,
        "conversations": _series(conv),
        "messages": _series(msg),
        "tasks": _series(task),
        "llm_calls": _series(llm),
        "requests": _series(req),
    }


@router.get("/llm/recent")
def llm_recent(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    rows = (
        db.query(LLMUsage)
        .order_by(LLMUsage.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "model": r.model,
            "slot": r.slot,
            "action": r.action,
            "ok": bool(r.ok),
            "latency_ms": r.latency_ms,
            "prompt_chars": r.prompt_chars,
            "completion_chars": r.completion_chars,
            "user_id": r.user_id,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/llm/{call_id}")
def llm_detail(
    call_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """单条 LLM 调用详情（V5.14）：含请求 / 返回内容快照（各最多 4000 字符）。

    快照自 V5.14 起记录；此前 的历史行两列为空串，前端按「未记录快照」提示。
    """
    r = db.get(LLMUsage, call_id)
    if r is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="调用记录不存在")
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "model": r.model,
        "slot": r.slot,
        "action": r.action,
        "ok": bool(r.ok),
        "latency_ms": r.latency_ms,
        "prompt_chars": r.prompt_chars,
        "completion_chars": r.completion_chars,
        "user_id": r.user_id,
        "error": r.error,
        "prompt_preview": r.prompt_preview or "",
        "completion_preview": r.completion_preview or "",
    }
