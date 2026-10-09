"""AI 测试工作流平台 · 入口。

启动：uvicorn main:app --reload --port 8005
访问：http://127.0.0.1:8005  （前端 dashboard）
      http://127.0.0.1:8005/docs （Swagger 接口文档）

V2：认证 + 多用户（guest/user/admin）。反代部署时才开 --proxy-headers。
"""
import logging
import os
from contextlib import asynccontextmanager

# 必须在任何 chromadb 相关 import 之前：系统 sqlite3 < 3.35 时切换为 pysqlite3，
# 否则 ChromaDB 会直接抛 RuntimeError，导致向量库/知识库整体不可用（详见模块注释）。
from app.core import sqlite_compat  # noqa: F401  isort:skip

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """静态资源挂载：追加 no-cache，避免浏览器缓存旧 index.html/JS 导致「改完不生效」。

    Vite 产物文件名带内容哈希、本就强缓存友好；关键是不让固定名 index.html 命中旧缓存、
    从而引用旧哈希的 JS。只作用于 /assets 与 /vendor 挂载点，不影响 /api。
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

from app.api import auth, categories, chat, conversations, files, guest, knowledge, llm_config, llm_pool, memory, prompts, stats, tasks, users
from app.core.config import STATIC_DIR, jwt_secret_is_placeholder, ENV, AITF_MEMORY_VEC_BACKFILL_ON_BOOT
from app.core.db import init_db, engine
from app.core.logging_config import setup_logging

logger = logging.getLogger("main")

# M10 运维日志落盘：root logger → logs/app.log（滚动），uvicorn 日志合并进 root。
# 必须在 app 创建前执行，让后续 lifespan / 各 API 模块的 INFO 日志都能被捕获。
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # P0：存量用户个人记忆库回填（幂等；失败只记日志，不阻断启动）
    try:
        from app.core.db import SessionLocal
        from app.jobs.personal_kb import backfill_personal_kbs
        _backfill_db = SessionLocal()
        try:
            backfill_personal_kbs(_backfill_db)
        finally:
            _backfill_db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("个人记忆库回填失败（不影响启动）：%s", e)

    # V7.2：存量记忆条目向量回填开关（默认关）。刻意**不在启动路径做批量
    # 回填**（懒加载脚本逻辑不进请求进程，避免启动阻塞）——置 1 时给出明确
    # 提示，实际回填走独立脚本：python scripts/backfill_memory_vectors.py
    if AITF_MEMORY_VEC_BACKFILL_ON_BOOT:
        logger.warning(
            "AITF_MEMORY_VEC_BACKFILL_ON_BOOT=1：本版本不在启动时回填，"
            "请执行 python scripts/backfill_memory_vectors.py（幂等，100/批）")

    # JWT_SECRET 启动检测：默认占位值 → 演示 WARNING / 生产拒启
    if jwt_secret_is_placeholder():
        if ENV == "production":
            raise RuntimeError("生产环境必须设置 JWT_SECRET 环境变量")
        logger.warning("JWT_SECRET 为默认占位值，仅限本地演示使用")

    # 单一固定共享 guest（幂等建账号 + 清旧动态 guest 遗留）
    from app.core.db import SessionLocal
    from app.jobs.guest_cleaner import ensure_shared_guest
    _bootstrap_db = SessionLocal()
    try:
        ensure_shared_guest(_bootstrap_db)
    finally:
        _bootstrap_db.close()

    # 任务队列：启动恢复（running→pending）+ 启动 Worker 池
    from app.core import task_queue
    task_queue.recover_pending_tasks()
    task_queue.start_workers()

    # V5.12 可观测性：请求统计周期落库线程（access_log 内存聚合 → request_stats 表）
    from app.core import access_log
    access_log.start_stats_flusher()

    # P3：APScheduler 接入 —— 每晚 02:00 对话记忆提炼 + 启动回填缺失日期
    # （失败只记日志不阻断启动；AITF_SCHEDULER=0 时全部为 no-op）
    try:
        from app.jobs.scheduler import init_scheduler, maybe_backfill_missing_days
        init_scheduler()
        maybe_backfill_missing_days()
    except Exception as e:  # noqa: BLE001
        logger.warning("记忆调度器初始化失败（不影响启动）：%s", e)

    yield

    # 优雅关闭：等待队列中任务执行完毕（systemd TimeoutStopSec 兜底）
    task_queue.wait_for_drain()

    # P3：关闭记忆提炼调度器（幂等；wait=False 不阻塞退出）
    try:
        from app.jobs.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.warning("记忆调度器关闭失败：%s", e)

    # 关闭前把剩余统计聚合落库，防丢尾部数据
    access_log.flush_stats_now()


app = FastAPI(title="AI 测试工作流平台", version="0.2.0", lifespan=lifespan)

# CORS：生产用 CORS_ORIGINS 环境变量限定前端域名（逗号分隔）；缺省 "*"（本地演示）
_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 接口出入参访问日志（M10.1）：注册在加密中间件**之前** = 位于其内层，
# 看到的是解密后的入参与加密前的明文出参（加密层在外侧加解密，本层只管如实记录）
from app.core.access_log import AccessLogMiddleware  # noqa: E402
app.add_middleware(AccessLogMiddleware)

# API 分级加密（admin 明文 / user+guest AES-256-GCM）——最后注册 = 最外层
from app.core.middleware import ApiCryptoMiddleware  # noqa: E402
app.add_middleware(ApiCryptoMiddleware)

app.include_router(tasks.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(guest.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(categories.router, prefix="/api")
app.include_router(llm_config.router, prefix="/api")
app.include_router(llm_pool.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(prompts.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(memory.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "db_dialect": engine.dialect.name}


@app.get("/", response_class=HTMLResponse)
def index():
    # no-cache：入口页不缓存，部署后普通刷新即生效（assets 带内容哈希可强缓存）
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/config.js")
def config_js():
    """前端 API 基址配置（仅旧版单文件前端需要；React 版走相对路径 /api）"""
    from fastapi.responses import PlainTextResponse

    _f = STATIC_DIR / "config.js"
    if not _f.exists():
        return PlainTextResponse("/* React 前端无需 config.js */", media_type="application/javascript")
    return PlainTextResponse(_f.read_text(encoding="utf-8"), media_type="application/javascript")


@app.get("/favicon.svg")
@app.get("/favicon.ico")
def favicon():
    """站点图标（与左上角 logo 同款：渐变方块 + 白色机器人脸）"""
    from fastapi.responses import FileResponse

    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


# 静态资源：目录存在才挂载，同一份代码兼容新旧前端
# - React 版（frontend/dist）：/assets/*（Vite 构建产物，带内容哈希可永久缓存）
# - 旧版（frontend-legacy）：/vendor/*（mind-elixir 等第三方库本地化）
_assets_dir = STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", NoCacheStaticFiles(directory=str(_assets_dir)), name="assets")

_vendor_dir = STATIC_DIR / "vendor"
if _vendor_dir.is_dir():
    app.mount("/vendor", NoCacheStaticFiles(directory=str(_vendor_dir)), name="vendor")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8005, reload=True)
