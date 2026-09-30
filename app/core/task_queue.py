"""全局任务队列 + Worker 池 + 启动恢复。

替代 FastAPI BackgroundTasks：
- 任务状态持久化在 MySQL，部署重启不丢
- 固定 Worker 数控制全局并发（默认 5，环境变量 TASK_WORKERS 可调）
- 启动时自动恢复 running（上次中断）和 pending 任务
- 任务内多角色保持串行，并发上限 = Worker 数
"""
import logging
import os
import queue
import threading
import time

from app.core.db import SessionLocal
from app.models.task import Task
from app.workflow.engine import run_task

logger = logging.getLogger("task_queue")

WORKER_COUNT = int(os.getenv("TASK_WORKERS", "5"))
_task_queue: queue.Queue[str] = queue.Queue()
_workers_started = False


def enqueue(task_id: str) -> None:
    """任务入队（由 API 层调用）。"""
    _task_queue.put(task_id)
    logger.info("任务入队: %s (队列长度: %d)", task_id, _task_queue.qsize())


def _worker_loop(worker_id: int) -> None:
    """Worker 主循环：阻塞取任务 → 执行 → 循环。daemon 线程随进程退出。"""
    logger.info("Worker-%d 启动", worker_id)
    while True:
        try:
            task_id = _task_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            logger.info("Worker-%d 开始执行任务: %s", worker_id, task_id)
            t0 = time.time()
            run_task(task_id)
            logger.info("Worker-%d 任务完成: %s (耗时 %.1fs)", worker_id, task_id, time.time() - t0)
        except Exception:
            logger.exception("Worker-%d 执行任务异常: %s", worker_id, task_id)
        finally:
            _task_queue.task_done()


def start_workers() -> None:
    """启动 Worker 池（幂等，重复调用不重复创建）。"""
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for i in range(WORKER_COUNT):
        t = threading.Thread(
            target=_worker_loop, args=(i,), daemon=True, name=f"task-worker-{i}"
        )
        t.start()
    logger.info("任务队列启动: %d 个 Worker", WORKER_COUNT)


def recover_pending_tasks() -> None:
    """启动恢复：把 running（上次部署中断）和 pending 的任务重新入队。

    - running 任务：进程被杀导致状态未更新，重置为 pending 后入队
    - pending 任务：还没被 Worker 拾取，直接入队
    """
    db = SessionLocal()
    try:
        running = db.query(Task).filter(Task.status == "running").all()
        for t in running:
            t.status = "pending"
            logger.info("恢复中断任务: %s (原 running → pending)", t.id)
        if running:
            db.commit()

        pending = db.query(Task).filter(Task.status == "pending").all()
        for t in pending:
            _task_queue.put(t.id)
            logger.info("待执行任务入队: %s", t.id)
        if pending:
            logger.info("启动恢复完成: 共 %d 个任务入队", len(pending))
        else:
            logger.info("启动恢复: 无待执行任务")
    finally:
        db.close()


def wait_for_drain() -> None:
    """优雅关闭：阻塞直到队列中所有任务执行完毕。

    systemd 配置 TimeoutStopSec=60 兜底，超时后进程强制退出（daemon 线程自动结束）。
    """
    if _task_queue.unfinished_tasks > 0:
        logger.info("等待队列中 %d 个任务执行完毕...", _task_queue.unfinished_tasks)
    _task_queue.join()
    logger.info("任务队列已清空")
