"""数据库连接与 ORM 基类（SQLite / MySQL 双方言，方案 A）。

方言选择优先级：
  1. DATABASE_URL 非空 → 直接用它（直填覆盖，优先级最高）
  2. DB_TYPE == "mysql" → 按 DB_* 字段拼 mysql+pymysql URL，带连接池参数
  3. 其它（默认 sqlite）→ 本地文件 sqlite:///{DB_PATH}，零配置
"""
import logging
import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text, inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import (
    DB_PATH, DATABASE_URL, DB_TYPE, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD,
    DB_NAME, DB_CHARSET, DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_PRE_PING,
    DB_POOL_RECYCLE, DB_ECHO,
)

logger = logging.getLogger(__name__)


def _build_engine():
    # 1) 显式直填 DATABASE_URL 优先（高级用法）
    if DATABASE_URL:
        return create_engine(DATABASE_URL, pool_pre_ping=DB_POOL_PRE_PING, echo=DB_ECHO)
    # 2) MySQL
    if DB_TYPE == "mysql":
        _pwd = quote_plus(DB_PASSWORD)  # 对密码里的 @ : / 等特殊字符安全转义
        url = (
            f"mysql+pymysql://{DB_USER}:{_pwd}@{DB_HOST}:{DB_PORT}/"
            f"{DB_NAME}?charset={DB_CHARSET}"
        )
        return create_engine(
            url,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_pre_ping=DB_POOL_PRE_PING,
            pool_recycle=DB_POOL_RECYCLE,
            echo=DB_ECHO,
        )
    # 3) SQLite（默认，零配置）
    return create_engine(
        f"sqlite:///{DB_PATH}",
        # timeout=30：后台任务/调度器/请求并发写时给足锁等待，避免 "database is locked"
        connect_args={"check_same_thread": False, "timeout": 30},
    )


engine = _build_engine()

# 沙箱环境（如 WorkBuddy 沙箱）会拦截 SQLite 的 journal 文件写，
# 任何一次 DB 写都会让连接报废、进程退出。设置内存 journal 可规避该限制。
# 仅 SQLite + AITF_DB_MEMORY_JOURNAL=1 时启用；生产（含 MySQL）默认关闭。
if os.environ.get("AITF_DB_MEMORY_JOURNAL") == "1" and engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _set_memory_journal(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=MEMORY")
        cur.execute("PRAGMA synchronous=OFF")
        cur.close()

class Base(DeclarativeBase):
    """ORM 基类（SQLAlchemy 2.0 风格）。"""
    pass

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（首次运行时调用）。"""
    import app.models.task  # noqa: F401  确保模型注册到 Base
    import app.models.user  # noqa: F401
    import app.models.category  # noqa: F401
    import app.models.llm_config  # noqa: F401
    import app.models.conversation  # noqa: F401
    import app.models.knowledge  # noqa: F401  # V4.0 RAG 知识库（7 张表）
    import app.models.llm_pool  # noqa: F401  # V5.0 P1：多模型池 llm_model_pool
    import app.models.llm_usage  # noqa: F401  # V5.12：LLM 调用用量
    import app.models.request_stat  # noqa: F401  # V5.12：HTTP 请求量统计
    import app.models.chat_file  # noqa: F401  # P0：聊天附件表
    import app.models.job  # noqa: F401  # P0：后台任务运行表
    import app.models.memory  # noqa: F401  # V7.0：条目级记忆（memory_items / memory_audits）
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """轻量幂等列迁移（已存在表补新列；跨方言，替代原 PRAGMA 实现）。"""
    insp = sa_inspect(engine)
    if not insp.has_table("tasks"):
        return
    cols = {c["name"] for c in insp.get_columns("tasks")}
    alters = []
    if "category_id" not in cols:
        alters.append("ADD COLUMN category_id INTEGER")
    if "is_sample" not in cols:
        alters.append("ADD COLUMN is_sample BOOLEAN NOT NULL DEFAULT 0")
    if "conversation_id" not in cols:
        alters.append("ADD COLUMN conversation_id VARCHAR(64)")
    if "parent_task_id" not in cols:
        alters.append("ADD COLUMN parent_task_id VARCHAR(64)")
    # M1 全链路：e2e 任务关联被测系统（可空外键，老库补列）
    if "target_id" not in cols:
        alters.append("ADD COLUMN target_id VARCHAR(64)")
    # V3.1 多角色协作：roles JSON 数组文本（MySQL 不允许 TEXT 带 DEFAULT，Python 层兜底）
    if "roles" not in cols:
        alters.append("ADD COLUMN roles TEXT")
    # V5.5 用例库资产化：评审状态（draft/reviewed），存量任务统一默认草稿
    if "review_status" not in cols:
        alters.append("ADD COLUMN review_status VARCHAR(16) NOT NULL DEFAULT 'draft'")
    if alters:
        with engine.connect() as conn:
            for a in alters:
                conn.execute(text(f"ALTER TABLE tasks {a}"))
            conn.commit()

    # conversations 补列（V4.1：会话模式与知识库归属，会话列表按 mode 隔离）
    if insp.has_table("conversations"):
        ccol = {c["name"] for c in insp.get_columns("conversations")}
        if "mode" not in ccol:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN mode VARCHAR(16) DEFAULT 'workflow'"))
                conn.commit()
        if "kb_id" not in ccol:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN kb_id VARCHAR(64)"))
                conn.commit()

    # knowledges 补列（V4：Wiki AI 主题分类）
    if insp.has_table("knowledges"):
        kcol = {c["name"] for c in insp.get_columns("knowledges")}
        if "wiki_category" not in kcol:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE knowledges ADD COLUMN wiki_category VARCHAR(50)"))
                conn.commit()

    # step_logs 补列（V3：生成用例实时子进度）
    # 注意：MySQL 不允许 TEXT 列带 DEFAULT（1101），不能写 DEFAULT ''
    if insp.has_table("step_logs"):
        scol = {c["name"] for c in insp.get_columns("step_logs")}
        if "progress" not in scol:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE step_logs ADD COLUMN progress TEXT"))
                conn.commit()

    # messages 补列（V4.5.2：RAG 引用溯源持久化，JSON 文本；老库启动自动补）
    if insp.has_table("messages"):
        mcol = {c["name"] for c in insp.get_columns("messages")}
        if "citations" not in mcol:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE messages ADD COLUMN citations TEXT"))
                conn.commit()

    # categories 补列（分类树并入站点节点：target_id 站点关联 + is_auto 页面自动派生标记）
    if insp.has_table("categories"):
        cat_cols = {c["name"] for c in insp.get_columns("categories")}
        cat_alters = []
        if "target_id" not in cat_cols:
            cat_alters.append("ADD COLUMN target_id VARCHAR(64)")
        if "is_auto" not in cat_cols:
            # TINYINT(1) 双方言通吃（SQLite 按亲和性收 BOOLEAN，MySQL 即 BOOLEAN 底层）
            cat_alters.append("ADD COLUMN is_auto TINYINT(1) NOT NULL DEFAULT 0")
        if cat_alters:
            with engine.connect() as conn:
                for a in cat_alters:
                    conn.execute(text(f"ALTER TABLE categories {a}"))
                conn.commit()
        # target_id 索引（幂等：先查再建；MySQL 不支持 CREATE INDEX IF NOT EXISTS）
        idx_names = {i["name"] for i in sa_inspect(engine).get_indexes("categories")}
        if "idx_categories_target" not in idx_names:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE INDEX idx_categories_target ON categories (target_id)"))
                conn.commit()

    # execution_runs 补列（M4 自愈循环：轮次 + 过程日志；老库启动自动补）
    # 注意：MySQL 不允许 TEXT 列带 DEFAULT（1101），heal_log 不能写 DEFAULT ''
    if insp.has_table("execution_runs"):
        rcol = {c["name"] for c in insp.get_columns("execution_runs")}
        if "heal_round" not in rcol or "heal_log" not in rcol:
            with engine.connect() as conn:
                if "heal_round" not in rcol:
                    conn.execute(text(
                        "ALTER TABLE execution_runs ADD COLUMN heal_round INTEGER NOT NULL DEFAULT 0"))
                if "heal_log" not in rcol:
                    conn.execute(text("ALTER TABLE execution_runs ADD COLUMN heal_log TEXT"))
                conn.commit()
        # V5.14.6 报告列扩容：report_json TEXT（64KB）写不下大报告
        # （真实故障：32 条用例的报告 ~77KB → DataError 1406，报告整体丢失）
        # 仅 MySQL 执行：SQLite 不支持 MODIFY COLUMN，测试环境（sqlite）直接跳过
        rtype = next((c["type"] for c in insp.get_columns("execution_runs")
                      if c["name"] == "report_json"), None)
        rtype_s = str(rtype).upper() if rtype else ""
        if DB_TYPE == "mysql" and rtype_s.startswith("TEXT"):
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE execution_runs MODIFY COLUMN report_json MEDIUMTEXT"))
                conn.commit()
                logger.warning("report_json 列已从 %s 扩容为 MEDIUMTEXT", rtype)

    # ============ P0 对话记忆 + 个人知识库：补列 / 建表 / 幂等索引 ============
    # knowledge_bases 补列：is_personal 个人记忆库标记（每人至多一个，私密可见）
    # 布尔列统一 TINYINT(1) NOT NULL DEFAULT 0（SQLite 按亲和性收 BOOLEAN，MySQL 即 BOOLEAN 底层）
    if insp.has_table("knowledge_bases"):
        pkb_cols = {c["name"] for c in insp.get_columns("knowledge_bases")}
        if "is_personal" not in pkb_cols:
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE knowledge_bases ADD COLUMN is_personal TINYINT(1) NOT NULL DEFAULT 0"))
                conn.commit()

    # knowledges 补列：source_key 幂等覆盖键（非记忆/附件文档为 NULL）+ 唯一索引
    if insp.has_table("knowledges"):
        src_cols = {c["name"] for c in insp.get_columns("knowledges")}
        if "source_key" not in src_cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE knowledges ADD COLUMN source_key VARCHAR(128)"))
                conn.commit()
        # 幂等建唯一索引（新列全为 NULL，唯一索引不冲突；MySQL 不支持 IF NOT EXISTS 故先查再建）
        src_idx = {i["name"] for i in sa_inspect(engine).get_indexes("knowledges")}
        if "uq_knowledges_source_key" not in src_idx:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_knowledges_source_key ON knowledges (source_key)"))
                conn.commit()

    # conversations 补列：对话记忆提炼水位与手动整理状态机 6 列
    if insp.has_table("conversations"):
        mem_cols = {c["name"] for c in insp.get_columns("conversations")}
        mem_alters = []
        if "mem_dirty" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_dirty TINYINT(1) NOT NULL DEFAULT 0")
        if "mem_last_msg_id" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_last_msg_id INTEGER")
        if "mem_doc_id" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_doc_id VARCHAR(64)")
        if "mem_at" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_at DATETIME")
        if "mem_status" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_status VARCHAR(16) NOT NULL DEFAULT 'idle'")
        if "mem_error" not in mem_cols:
            mem_alters.append("ADD COLUMN mem_error VARCHAR(500)")
        if mem_alters:
            with engine.connect() as conn:
                for a in mem_alters:
                    conn.execute(text(f"ALTER TABLE conversations {a}"))
                conn.commit()
        # mem_dirty 索引（待提炼会话扫描用；幂等先查再建）
        mem_idx = {i["name"] for i in sa_inspect(engine).get_indexes("conversations")}
        if "idx_conversations_mem_dirty" not in mem_idx:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE INDEX idx_conversations_mem_dirty ON conversations (mem_dirty)"))
                conn.commit()

    # 新表：chat_attachments（聊天附件）/ job_runs（任务运行记录）
    # create_all 已随模型导入建齐；此处兜底（老库因故缺表时按 metadata 单独补建）
    if not insp.has_table("chat_attachments"):
        Base.metadata.tables["chat_attachments"].create(bind=engine)
    if not insp.has_table("job_runs"):
        Base.metadata.tables["job_runs"].create(bind=engine)

    # 兜底唯一索引（正常由建表时的 Index(unique=True) 带出；缺则幂等补建）
    if insp.has_table("job_runs"):
        job_idx = {i["name"] for i in sa_inspect(engine).get_indexes("job_runs")}
        if "uq_job_runs_job_date" not in job_idx:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_job_runs_job_date ON job_runs (job_id, biz_date)"))
                conn.commit()
    if insp.has_table("chat_attachments"):
        att_idx = {i["name"] for i in sa_inspect(engine).get_indexes("chat_attachments")}
        if "uq_chat_attachments_file_id" not in att_idx:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_chat_attachments_file_id ON chat_attachments (file_id)"))
                conn.commit()

    # ============ V7.0 条目级记忆：兜底建表 / 幂等索引 ============
    # create_all 已随 init_db 的模型导入建齐；此处兜底（老库因故缺表时按
    # metadata 单独补建）。索引先 get_indexes 查名再建（MySQL 无 IF NOT EXISTS）。
    if not insp.has_table("memory_items"):
        Base.metadata.tables["memory_items"].create(bind=engine)
    if not insp.has_table("memory_audits"):
        Base.metadata.tables["memory_audits"].create(bind=engine)
    if insp.has_table("memory_items"):
        mi_idx = {i["name"] for i in sa_inspect(engine).get_indexes("memory_items")}
        # 名称 → DDL（与 app/models/memory.py __table_args__ 一一对应）
        mi_need = {
            "uq_memory_items_active":
                "CREATE UNIQUE INDEX uq_memory_items_active ON memory_items (user_id, dedupe_key)",
            "idx_memory_items_user_status":
                "CREATE INDEX idx_memory_items_user_status ON memory_items (user_id, status)",
            "idx_memory_items_user_kind":
                "CREATE INDEX idx_memory_items_user_kind ON memory_items (user_id, kind)",
            "idx_memory_items_status_expires":
                "CREATE INDEX idx_memory_items_status_expires ON memory_items (status, expires_at)",
            "idx_memory_items_conv":
                "CREATE INDEX idx_memory_items_conv ON memory_items (conversation_id)",
        }
        for _name, _ddl in mi_need.items():
            if _name not in mi_idx:
                with engine.connect() as conn:
                    conn.execute(text(_ddl))
                    conn.commit()
                    logger.info("已补建索引 %s（老库幂等迁移）", _name)
    if insp.has_table("memory_audits"):
        ma_idx = {i["name"] for i in sa_inspect(engine).get_indexes("memory_audits")}
        ma_need = {
            "idx_memory_audits_item":
                "CREATE INDEX idx_memory_audits_item ON memory_audits (item_id)",
            "idx_memory_audits_user":
                "CREATE INDEX idx_memory_audits_user ON memory_audits (user_id)",
        }
        for _name, _ddl in ma_need.items():
            if _name not in ma_idx:
                with engine.connect() as conn:
                    conn.execute(text(_ddl))
                    conn.commit()
                    logger.info("已补建索引 %s（老库幂等迁移）", _name)

    # V7.3 地基：检索埋点 / 评估运行两表兜底（create_all 已随模型导入建齐，
    # 此处兜老库缺表；索引走模型 index/Index，无需手工补建）
    if not insp.has_table("memory_retrieval_logs"):
        Base.metadata.tables["memory_retrieval_logs"].create(bind=engine)
    if not insp.has_table("memory_eval_runs"):
        Base.metadata.tables["memory_eval_runs"].create(bind=engine)

    # V5.14：llm_usage 补内容快照两列（老库幂等迁移；create_all 不会给已有表加列）
    # 注意 MySQL 5.7 严格模式 TEXT 列不能带 DEFAULT → 用 nullable 列，空值由应用层兜 ""
    if insp.has_table("llm_usage"):
        lu_cols = {c["name"] for c in sa_inspect(engine).get_columns("llm_usage")}
        for _col in ("prompt_preview", "completion_preview"):
            if _col not in lu_cols:
                with engine.connect() as conn:
                    conn.execute(text(
                        f"ALTER TABLE llm_usage ADD COLUMN {_col} TEXT NULL"))
                    conn.commit()
                    logger.info("已补列 llm_usage.%s（老库幂等迁移）", _col)


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
