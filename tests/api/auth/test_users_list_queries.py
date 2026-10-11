"""`GET /api/users` 的查询条数回归（V7.5）。

背景 bug
--------
原来的实现是 N+1：

    for u in db.execute(select(User)).scalars().all():
        n = db.execute(select(func.count()).select_from(Task).where(Task.user_id == u.id))

2026-10-08 实测：评估脚本遗留的脏账号把用户数撑到 124 个，叠加远程 MySQL
单次往返 ~180ms → **单次请求 22.7 秒**（浏览器 Network 面板实测 22.69s）。

修法：改成一次 `GROUP BY Task.user_id` 聚合，SQL 条数固定为 2，与用户数无关。

本文件的断言方式
----------------
不去断言「SQL 条数是 4 条」这种脆弱数字，而是断言**不随用户数增长**：
先测一次基线，再新注册 3 个用户后测一次，两者之差必须 <= 1。
这样即使以后前面加了权限查询/埋点查询导致基数变化，测试依然成立；
而一旦有人改回 N+1，条数会立刻随用户数上涨 → 直接红。
"""
import uuid

import pytest
from sqlalchemy import event, func, select

from app.core.db import SessionLocal, engine
from app.models.task import Task
from app.models.user import User


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture()
def sql_counter():
    """记录被执行的 SQL 语句（同一进程内 TestClient 走的就是这个 engine）。"""
    stmts: list[str] = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        stmts.append(statement)

    event.listen(engine, "before_cursor_execute", _rec)
    try:
        yield stmts
    finally:
        event.remove(engine, "before_cursor_execute", _rec)


def _register(client, prefix: str) -> int:
    uname = f"{prefix}_{uuid.uuid4().hex[:6]}"
    r = client.post("/api/auth/register", json={
        "username": uname, "email": f"{uname}@example.com", "password": "Queries123"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_users_list_query_count_does_not_scale(client, accounts, sql_counter):
    """核心回归：用户数增加，`GET /api/users` 的 SQL 条数不得随之增长。"""
    admin = _hdr(accounts["admin"]["token"])

    sql_counter.clear()
    r = client.get("/api/users", headers=admin)
    assert r.status_code == 200, r.text
    base = len(sql_counter)
    assert base > 0, "没统计到任何 SQL，监听可能没生效"
    assert base <= 10, f"基线 SQL 条数 {base} 偏多，请检查是否有其他 N+1"

    # 新增 3 个用户后重测
    for _ in range(3):
        _register(client, "qprobe")

    sql_counter.clear()
    r2 = client.get("/api/users", headers=admin)
    assert r2.status_code == 200, r2.text
    after = len(sql_counter)

    assert after - base <= 1, (
        f"用户数 +3 后 SQL 条数从 {base} 涨到 {after} —— 疑似又退化成 N+1。"
        "任务数必须用一次 GROUP BY 聚合取回（见 app/api/users.py: list_users），"
        "不要按用户循环 count。"
    )


def test_users_list_task_counts_are_correct(client, accounts):
    """功能护栏：改聚合后每人的任务数必须仍与库中实际值一致。"""
    _register(client, "qcount")  # register 会播种示例任务，保证有非零计数

    users = client.get("/api/users", headers=_hdr(accounts["admin"]["token"])).json()
    assert users, "用户列表不应为空"

    db = SessionLocal()
    try:
        expected = dict(db.execute(
            select(Task.user_id, func.count()).group_by(Task.user_id)
        ).all())
        all_ids = {u.id for u in db.execute(select(User)).scalars().all()}
    finally:
        db.close()

    assert {u["id"] for u in users} == all_ids, "列表应覆盖全部用户"
    for row in users:
        assert row["tasks"] == int(expected.get(row["id"], 0)), \
            f"用户 {row['username']} 的任务数不对"
    assert any(row["tasks"] > 0 for row in users), \
        "应有用户带示例任务（否则这条测试测不到计数逻辑）"
