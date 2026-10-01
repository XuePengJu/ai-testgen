"""平台配置：读取 .env，定义路径与模型开关。"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # ai-testflow/

# 加载 .env（仅当环境变量未设置时填充，避免覆盖系统环境）
# 多环境隔离：默认 .env（本地开发）；线上可设 AITF_ENV_FILE=/path/.env.server 指向另一份，
# 线上线下用不同的 MySQL 与 embedding 模型，配置必须分开（不设时行为与原来一致）
_ENV_PATH = Path(os.getenv("AITF_ENV_FILE") or (BASE_DIR / ".env"))
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")

# 魔搭社区 ModelScope 免费推理（OpenAI 兼容）
# 注册 modelscope.cn → 绑定阿里云+实名 → 访问令牌页创建（格式 ms-xxxx）
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
MODELSCOPE_MODEL = os.getenv("MODELSCOPE_MODEL", "Qwen/Qwen3.8-27B")

# 智谱 GLM 免费模型服务端 Key（glm-4.7-flash / glm-4.6v-flash 等长期免费）
# 平台默认选 GLM 免费模型时，由本环境变量兜底，无需在界面填写 Key
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")

# ============ Embedding（V4.0 RAG 知识库）============
# 默认百炼 text-embedding-v3（OpenAI 兼容），可用环境变量切换任意 OpenAI 兼容 embedding
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", DASHSCOPE_API_KEY)
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

# 路径支持环境变量覆盖（测试隔离：AITF_ROOT_DIR 指向临时目录，避免污染真实数据）
_ROOT = Path(os.getenv("AITF_ROOT_DIR", str(BASE_DIR)))
UPLOAD_DIR = _ROOT / "uploads"
OUTPUT_DIR = _ROOT / "outputs"
DB_PATH = _ROOT / "app.db"

# ============ 向量库（V4.0 RAG 知识库）============
# 向量库目录（Chroma 持久化路径；AITF_ROOT_DIR 切换时随根目录走）
VECTOR_DIR = _ROOT / "vectors"
# 向量库 collection 名（单 collection + metadata 过滤，迁移 Qdrant 时换连接即可）
VECTOR_COLLECTION = os.getenv("VECTOR_COLLECTION", "ai-testflow-kb")

# ============ 认证与多用户（V2） ============
ENV = os.getenv("ENV", "dev")                       # dev / production
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 24                                # 注册用户 token 有效期
GUEST_TTL_HOURS = 24                                # 访客数据保留时长
GUEST_MAX_TASKS = int(os.getenv("GUEST_MAX_TASKS", "10"))    # 单访客任务上限
GUEST_DAILY_LIMIT = int(os.getenv("GUEST_DAILY_LIMIT", "5")) # 单 IP 24h 新建 guest 上限
ADMIN_BOOTSTRAP_PASSWORD = os.getenv("ADMIN_BOOTSTRAP", "")  # 迁移脚本预置 admin 密码

# ============ API 分级加密（V2.1） ============
# admin 明文（方便 Swagger 调试），user/guest 走 AES-256-GCM；设 0 可整体关闭（本地调试用）
API_ENCRYPT = os.getenv("API_ENCRYPT", "1") == "1"

# ============ 演示内容开关 ============
# 默认关闭（0）：未配置模型 / 模型调用失败时，一律明确报错或提示，
#   绝不静默返回 mock 假用例 / 演示话术 / 哈希向量（避免假内容被误当真实结果）。
# 设为 1：恢复旧「演示模式」，用于本地或现场演示开箱即跑。
AITF_ALLOW_DEMO = os.getenv("AITF_ALLOW_DEMO", "0") == "1"

# ============ 运维日志（M10） ============
# root logger 落盘级别（DEBUG/INFO/WARNING/ERROR），见 app/core/logging_config.py
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

def jwt_secret_is_placeholder() -> bool:
    return JWT_SECRET == "change-me-in-production"

# ============ LLM 调用后端（V3 LangChain 迁移）============
# langchain（默认）：init_chat_model + stream()；httpx：旧直连，应急回退
AITF_LLM_BACKEND = os.getenv("AITF_LLM_BACKEND", "langchain").strip().lower()
if AITF_LLM_BACKEND not in ("langchain", "httpx"):
    AITF_LLM_BACKEND = "langchain"

# 内置的用例生成核心库（已整合，使项目自包含、clone 即跑）
GENERATOR_CORE_DIR = BASE_DIR / "generator_core"

# generator_core 下是 config/ 与 src/ 两个**顶层包**，app 层多处直接
# `from src.models.testcase import ...` / `from src.utils.jsonx import ...`。
# 原实现只在 app/services/pipeline_lib.py 被导入时才注入该路径 —— 属于「谁先导入谁生效」
# 的隐式依赖（例如 app/api/knowledge.py 单独被导入时 src.* 不可用）。
# 统一提前到配置模块加载时注入：任何 import app.core.config 的模块都能安全使用 src.*。
if str(GENERATOR_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATOR_CORE_DIR))

# 前端静态目录（V2.8 React 重构完成，默认切指 frontend/dist 构建产物）
# 一键切回旧版：环境变量 AITF_FRONTEND=legacy 后重启（不动代码、不回滚 git）
_FRONTEND_MODE = os.getenv("AITF_FRONTEND", "react").strip().lower()
if _FRONTEND_MODE not in ("react", "legacy"):
    _FRONTEND_MODE = "react"
STATIC_DIR = BASE_DIR / ("frontend-legacy" if _FRONTEND_MODE == "legacy" else "frontend/dist")

# ============ 数据库方言（方案 A：SQLite / MySQL 双方言）============
# DB_TYPE=sqlite：本地文件零配置（默认，本机/测试/演示零改动）
# DB_TYPE=mysql ：走下面的连接字段；DB_* 整组仅在 mysql 下生效，sqlite 下忽略
DB_TYPE = os.getenv("DB_TYPE", "sqlite").strip().lower()
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "ai-testflow")
DB_CHARSET = os.getenv("DB_CHARSET", "utf8mb4")          # 中文/emoji 必须，否则乱码或写入报错
# —— MySQL 连接池（规避 "MySQL server has gone away"）——
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))      # 常驻连接数
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))  # 高峰溢出上限
DB_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"  # 取连接前探活→断线重连
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))  # 1h 回收，避开 MySQL wait_timeout 静默断连
DB_ECHO = os.getenv("DB_ECHO", "false").lower() == "true"    # 调试时置 true 打印 SQL
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()     # 可选：直填完整 URL 优先于上面 DB_* 字段

# ============ 记忆与调度（P0：对话记忆 + 个人知识库）============
# 控制后台调度器开关、记忆提炼的定时与批量上限、访客是否参与记忆等
AITF_SCHEDULER = os.getenv("AITF_SCHEDULER", "1") == "1"            # 后台调度器总开关（默认开）
AITF_MEMORY_ENABLED = os.getenv("AITF_MEMORY_ENABLED", "1") == "1"  # 对话记忆功能总开关（默认开）
AITF_MEMORY_CRON_HOUR = int(os.getenv("AITF_MEMORY_CRON_HOUR", "2"))          # 每日提炼触发：时
AITF_MEMORY_CRON_MINUTE = int(os.getenv("AITF_MEMORY_CRON_MINUTE", "0"))      # 每日提炼触发：分
AITF_MEMORY_TZ = os.getenv("AITF_MEMORY_TZ", "Asia/Shanghai")                 # 调度时区
AITF_MEMORY_MISFIRE_SEC = int(os.getenv("AITF_MEMORY_MISFIRE_SEC", "21600"))  # 错过触发的宽限秒数（6h）
AITF_MEMORY_BACKFILL_DAYS = int(os.getenv("AITF_MEMORY_BACKFILL_DAYS", "3"))  # 启动回填最近 N 天未提炼会话
AITF_MEMORY_MAX_USERS_PER_RUN = int(os.getenv("AITF_MEMORY_MAX_USERS_PER_RUN", "50"))  # 单轮最多处理用户数
AITF_MEMORY_MAX_CONV_PER_RUN = int(os.getenv("AITF_MEMORY_MAX_CONV_PER_RUN", "20"))    # 单用户单轮最多会话数
AITF_MEMORY_SKIP_GUEST = os.getenv("AITF_MEMORY_SKIP_GUEST", "1") == "1"      # 记忆提炼跳过共享访客
AITF_FILE_INGEST_GUEST = os.getenv("AITF_FILE_INGEST_GUEST", "0") == "1"      # 访客上传文件是否入库
AITF_MEMORY_TOPK = int(os.getenv("AITF_MEMORY_TOPK", "3"))                    # 检索注入个人记忆 top-k
AITF_MEMORY_SYSTEM_BRIEF = os.getenv("AITF_MEMORY_SYSTEM_BRIEF", "1") == "1"  # 注入个人记忆摘要到 system
AITF_MEMORY_DIGEST_DAILY = os.getenv("AITF_MEMORY_DIGEST_DAILY", "1") == "1"  # 每日定时生成记忆摘要
AITF_MEMORY_DIGEST_MANUAL = os.getenv("AITF_MEMORY_DIGEST_MANUAL", "1") == "1"  # 允许手动触发整理摘要


def is_mock() -> bool:
    """无百炼 Key 时走 mock 兜底，保证开箱即跑。"""
    return not bool(DASHSCOPE_API_KEY)
