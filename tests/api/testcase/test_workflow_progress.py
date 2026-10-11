"""工作流「过程态快照」尽力而为语义测试（`app.workflow.engine._commit_progress`）。

回归背景（Task #5）：V1.3 的过程态快照把「全部已产出用例」序列化写入
`tasks.partial_cases_json`；MySQL 侧该列原为 `TEXT`（上限 65,535 字节），中文用例 JSON
很容易溢出 → `DataError(1406) Data too long`。而 `progress_cb` 的异常会被
`generator_core ... case_generator._emit` 的 `except Exception: pass` 吞掉，没人 rollback，
主线程随后的 `db.commit()` 即抛 PendingRollbackError → 一个「尽力而为」的 UI 预览快照
把整个生成任务拖成 failed。

修复后不变式：`_commit_progress` 无论如何都**不抛出**、且**不把 Session 留在
PendingRollback**；快照失败只应停用快照（`snapshot_ok=False`），进度仍照常提交。

落位说明：本组用例需要**真实 DB Session + tasks/step_logs 两张表**来断言「已回滚」与
「后续 commit 真成功」，故放在 `tests/api/`（该层 conftest 提供 `db_session` 夹具）；
`tests/unit/` 的契约是零依赖、不建库，不适合承载。
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.models.task import StepLog, Task
from app.workflow.engine import _commit_progress
# 以模块名引用 TestCase：避免 pytest 把导入的 `TestCase` 误当测试类收集
# （PytestCollectionWarning）。`cases_to_json` 依赖 pydantic 的 model_dump，不接受裸 dict。
from src.models import testcase as tc


def _mk_running_task(db, tid: str) -> StepLog:
    """建一个 running 任务 + 其 generator StepLog，返回该 StepLog。"""
    db.add(Task(id=tid, name="x", kind="business", source_type="text", status="running"))
    db.add(StepLog(task_id=tid, name="generator", title="AI 生成用例", status="running"))
    db.commit()
    return db.query(StepLog).filter(StepLog.task_id == tid).one()


def _case(title: str) -> "tc.TestCase":
    """构造真实 TestCase（`cases_to_json` 依赖 pydantic 的 model_dump，不接受裸 dict）。"""
    return tc.TestCase(title=title, case_type=tc.CaseType.POSITIVE)


class TestCommitProgressBestEffort:
    @pytest.mark.title("过程态快照落库失败不拖垮任务")
    def test_snapshot_failure_is_harmless(self, db_session, monkeypatch):
        """首次 commit 抛错（模拟列溢出）→ 不抛出、停用快照、已回滚、后续进度 commit 仍成功。"""
        db = db_session
        step = _mk_running_task(db, "t_prog_best_effort")
        task = db.get(Task, "t_prog_best_effort")
        accumulated = [_case("用例1")]

        real_commit, real_rollback = db.commit, db.rollback
        stats = {"commit": 0, "rollback": 0}

        def flaky_commit():
            stats["commit"] += 1
            if stats["commit"] == 1:      # 仅第一次（= 快照那次）注入溢出
                raise OperationalError(
                    "UPDATE tasks SET partial_cases_json=%(partial_cases_json)s WHERE tasks.id = %(tasks_id)s",
                    {},
                    Exception('(1406, "Data too long for column \'partial_cases_json\' at row 1")'),
                )
            return real_commit()

        def counting_rollback():
            stats["rollback"] += 1
            return real_rollback()

        monkeypatch.setattr(db, "commit", flaky_commit)
        monkeypatch.setattr(db, "rollback", counting_rollback)

        # 第 1 次：走快照分支 → 注入异常必须被内部吞掉（调用方不感知）
        ok = _commit_progress(db, step, task, accumulated, cur=1, total=3,
                              label="采购模块", cases_so_far=10, snapshot_ok=True)
        assert ok is False                    # 快照已停用
        assert stats["rollback"] >= 1         # 失败路径确实回滚
        assert stats["commit"] == 2           # 失败 1 次 + 纯进度提交 1 次
        assert task.partial_cases_json is None  # 脏属性已清（不会在下次 commit 复爆）

        # 第 2 次：Session 未停在 PendingRollback → 纯进度提交必须真成功
        ok2 = _commit_progress(db, step, task, accumulated, cur=2, total=3,
                               label="采购模块", cases_so_far=20, snapshot_ok=ok)
        assert ok2 is False                   # 保持停用
        db.refresh(step)
        assert "2/3" in (step.progress or "")  # 进度照常落库（快照停用不影响 UI 进度）

    @pytest.mark.title("过程态快照成功写入且进度一并落库")
    def test_snapshot_success_writes_and_keeps_progress(self, db_session):
        """正常路径：快照写入成功、snapshot_ok 保持 True、进度同时落库。"""
        db = db_session
        step = _mk_running_task(db, "t_prog_ok")
        task = db.get(Task, "t_prog_ok")

        ok = _commit_progress(db, step, task, [_case("快照用例")], cur=1, total=2,
                              label="L", cases_so_far=1, snapshot_ok=True)
        assert ok is True
        db.refresh(step)
        db.refresh(task)
        assert "1/2" in (step.progress or "")
        assert task.partial_cases_json and "快照用例" in task.partial_cases_json
