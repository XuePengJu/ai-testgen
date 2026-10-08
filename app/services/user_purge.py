"""用户从属数据级联清理（唯一实现，V7.4）。

为什么要有本模块
----------------
历史上「删除某用户的全部数据」这套逻辑**分散在两处各写了一份**：

- ``app/api/users.py: delete_user``（管理界面删用户）
- ``app/jobs/guest_cleaner.py: _delete_user_data``（清空共享访客数据）

而且**两份都漏表**，删完会留下孤儿行：

- 两份都漏：``conversations`` / ``messages`` / ``chat_attachments`` /
  ``prompt_overrides`` / ``llm_configs`` / ``llm_model_pool`` / ``llm_usage`` /
  ``memory_retrieval_logs``
- ``delete_user`` 还额外漏了 ``categories``（只有 guest_cleaner 那份删了）

漏表的后果是实打实的：``conversations.user_id`` **没有外键约束**（全库唯一的外键是
``messages.conversation_id → conversations.id``），所以数据库不会替你级联，删完用户
那一堆会话就永久悬空、谁也查不到也删不掉。

做法
----
收敛成唯一入口 ``purge_user_data``，三个调用点共用：

- ``app/api/users.py: delete_user``
- ``app/jobs/guest_cleaner.py: reset_shared_guest_data``
- ``scripts/purge_eval_users.py``（清理评估套件遗留的 eval*/probe* 脏账号）

⚠️ 以后新增任何带 ``user_id`` 的表，**必须同步本模块的 ``_USER_SCOPED_TABLES``**，
否则又会漏。``tests/test_delete_user_cascade.py`` 会按 ``Base.metadata`` 动态枚举
所有含 ``user_id`` 的表做兜底断言，漏了会直接红。
"""
from __future__ import annotations

import logging
import shutil

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import OUTPUT_DIR, UPLOAD_DIR
from app.models.category import Category
from app.models.chat_file import ChatAttachment
from app.models.conversation import Conversation, Message
from app.models.llm_config import LLMConfig
from app.models.llm_pool import LLMModelPool
from app.models.llm_usage import LLMUsage
from app.models.memory import MemoryRetrievalLog
from app.models.prompt_override import PromptOverride
from app.models.task import StepLog, Task
from app.models.user import User

logger = logging.getLogger("user_purge")

# 单表直删（表 -> 模型）。这些表的行只按 user_id 归属，无二级依赖。
# 注意：knowledge 家族与 memory_items/memory_audits 不在此处——它们由
# purge_user_knowledge 统一处理（同时要清 Chroma 向量）。
_USER_SCOPED_TABLES = (
    ("categories", Category),
    ("chat_attachments", ChatAttachment),
    ("prompt_overrides", PromptOverride),
    ("llm_configs", LLMConfig),
    ("llm_model_pool", LLMModelPool),
    ("llm_usage", LLMUsage),
    ("memory_retrieval_logs", MemoryRetrievalLog),
)

# 本模块承诺「删用户时会被清空」的表名全集（**不含 users**——账号行由调用方删除）。
# 仅供 tests/test_delete_user_cascade.py 做动态兜底断言：它从 Base.metadata 枚举所有
# 带 user_id 的表，与这个集合求差集，差集非空即测试失败（= 有人加了 user_id 表但没同步这里）。
PURGED_TABLES: frozenset[str] = frozenset(
    {name for name, _ in _USER_SCOPED_TABLES}
    | {
        "conversations",
        "messages",          # 无 user_id，经 conversations 级联
        "tasks",
        "step_logs",         # 无 user_id，经 tasks 级联
        "knowledge_bases",
        "knowledges",
        "chunks",
        "chunk_revisions",
        "memory_items",
        "memory_audits",
    }
)


def purge_user_data(db: Session, user: User, *, delete_files: bool = True) -> dict:
    """清空 ``user`` 的全部从属数据，**保留 users 行**（由调用方决定是否删账号）。

    - 不抛异常：各子步骤失败只记 WARNING，尽力清理剩余部分（删账号不应因清理失败卡死）
    - 结束时 ``db.commit()``
    - 返回各表删除计数，便于调用方/脚本核对
    """
    uid = user.id
    counts: dict[str, int] = {}

    # ---- 1) 会话与消息 ----
    # messages.conversation_id 是唯一的外键，必须先删消息再删会话（否则 FK 报错）
    conv_ids = [
        r[0] for r in db.execute(
            select(Conversation.id).where(Conversation.user_id == uid)
        ).all()
    ]
    counts["messages"] = 0
    if conv_ids:
        counts["messages"] = int(
            db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids))).rowcount or 0
        )
    counts["conversations"] = int(
        db.execute(delete(Conversation).where(Conversation.user_id == uid)).rowcount or 0
    )

    # ---- 2) 任务与步骤日志 ----
    task_ids = [
        r[0] for r in db.execute(select(Task.id).where(Task.user_id == uid)).all()
    ]
    counts["step_logs"] = 0
    if task_ids:
        counts["step_logs"] = int(
            db.execute(delete(StepLog).where(StepLog.task_id.in_(task_ids))).rowcount or 0
        )
    counts["tasks"] = int(
        db.execute(delete(Task).where(Task.user_id == uid)).rowcount or 0
    )

    # ---- 3) 附件原文件路径先记下来（行删掉之后就查不到了）----
    attachment_paths: list[str] = []
    if delete_files:
        attachment_paths = [
            r[0] for r in db.execute(
                select(ChatAttachment.file_path).where(ChatAttachment.user_id == uid)
            ).all() if r[0]
        ]

    # ---- 4) 单表直删 ----
    for name, model in _USER_SCOPED_TABLES:
        counts[name] = int(
            db.execute(delete(model).where(model.user_id == uid)).rowcount or 0
        )

    # ---- 5) 知识库 + 记忆条目/审计 + Chroma 向量（复用既有实现）----
    # 说明：purge_user_knowledge 内部自带一次 commit，会把上面 1~4 步的待删一并提交。
    # 它失败时下面的 db.commit() 兜底，避免整体回滚成"半清理"状态。
    try:
        from app.services.memory.store import purge_user_knowledge

        counts.update(purge_user_knowledge(db, uid))
    except Exception as e:  # noqa: BLE001
        logger.warning("用户 %s 知识库/记忆清理失败（继续清理其余数据）：%s", uid, e)
    db.commit()

    # ---- 6) 磁盘文件 ----
    counts["files"] = 0
    if delete_files:
        # 6a. 聊天附件原文件（file_path 为 uploads 下的相对路径；做目录逃逸防护）
        for rel in attachment_paths:
            try:
                p = (UPLOAD_DIR / rel).resolve()
                if p.is_file() and str(p).startswith(str(UPLOAD_DIR.resolve())):
                    p.unlink()
                    counts["files"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("删除附件文件失败 %s：%s", rel, e)
        # 6b. 用户数据目录（uploads/<data_dir>、outputs/<data_dir>）
        if user.data_dir:
            for base in (UPLOAD_DIR, OUTPUT_DIR):
                d = base / user.data_dir
                # 防路径逃逸：data_dir 必须是单层目录名
                if d.exists() and d.name == user.data_dir:
                    counts["files"] += sum(1 for _ in d.rglob("*") if _.is_file())
                    shutil.rmtree(d, ignore_errors=True)

    logger.info("已清理用户 %s(%s) 数据：%s", uid, user.username, counts)
    return counts
