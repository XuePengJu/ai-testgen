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


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
