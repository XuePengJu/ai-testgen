"""演示站配置（V9.0，子路径反代方案）。

部署形态（单项目双实例）：
- 正式站：端口 8005，.env.prod（DB_TYPE=mysql，MySQL 生产库）
- 演示站：端口 8006，.env.demo（DB_TYPE=sqlite，独立 app.demo.db + 独立 JWT_SECRET）
- Nginx：/demo/ 子路径 → 8006，rewrite 去掉前缀（演示站后端零感知前缀）

本模块只负责读取演示模式相关开关与参数；演示模式的具体行为
（免登录 token、每日重置任务、index.html 注入 Banner）分别在
app/api/demo.py 与 app/jobs/demo_reset.py 中实现。

安全要点：
- AITF_DEMO_ENABLED 是**进程级**开关：只有演示站实例（.env.demo）置 1，
  正式站实例（.env.prod）保持 0，互不感知。
- 演示站必须使用独立 JWT_SECRET（crypto.derive_key 用 JWT_SECRET 派生
  会话密钥 → 演示站 token/密文与正式站天然隔离，交叉请求必 401/解密失败）。
"""
import os

# ============ 演示模式总开关（进程级） ============
# 1 = 演示站实例（免登录 admin + 每日重置 + 前端 Banner）
# 0 = 正式站 / 本地开发（全部演示行为关闭）
AITF_DEMO_ENABLED = os.getenv("AITF_DEMO_ENABLED", "0") == "1"

# ============ 每日重置任务 ============
# 每天凌晨 AITF_DEMO_RESET_HOUR:AITF_DEMO_RESET_MINUTE 触发（Asia/Shanghai）
AITF_DEMO_RESET_HOUR = int(os.getenv("AITF_DEMO_RESET_HOUR", "2"))
AITF_DEMO_RESET_MINUTE = int(os.getenv("AITF_DEMO_RESET_MINUTE", "0"))
AITF_DEMO_RESET_TZ = os.getenv("AITF_DEMO_RESET_TZ", "Asia/Shanghai")

# ============ 演示站引导链接（前端 Banner「前往正式站」按钮） ============
# 注意：演示站实例本身不带子路径前缀（Nginx 已 rewrite 掉），
# 所以正式站链接直接用裸域名即可。
AITF_DEMO_GUIDE_URL = os.getenv("AITF_DEMO_GUIDE_URL", "https://ai-testgen.agentest.vip")

# ============ 演示站对外子路径前缀（前端 API 基址） ============
# Nginx 把 /demo/ 反代到演示站进程并 rewrite 去前缀，但**浏览器侧**的绝对路径
# /api 不会带前缀，会绕过反代直击正式站后端 —— 因此由后端在 index.html 注入
# window.__AITF_API_BASE__，前端所有请求出口统一补前缀（见 frontend api/client.ts）。
# 留空 = 不注入（如演示站将来改走独立子域名/根路径部署时）。
AITF_DEMO_BASE_PATH = os.getenv("AITF_DEMO_BASE_PATH", "/demo")

# ============ 重置后是否重播种示例数据 ============
# 1 = 重置后调用 sample_seeder 重新播种（访客/首个注册账号都有示例任务与对话）
# 0 = 重置后保持空库（仅保留 admin/guest 账号）
AITF_DEMO_SEED_SAMPLE = os.getenv("AITF_DEMO_SEED_SAMPLE", "1") == "1"

# ============ 演示站管理账号 ============
# 免登录默认进入的管理员用户名（须存在且 role=admin；不存在时由
# demo token 端点尝试用 AITF_DEMO_ADMIN_PASSWORD 幂等创建）
AITF_DEMO_ADMIN_USERNAME = os.getenv("AITF_DEMO_ADMIN_USERNAME", "admin")
AITF_DEMO_ADMIN_PASSWORD = os.getenv("AITF_DEMO_ADMIN_PASSWORD", "Admin@123")

# 演示站 token 有效期（小时）。演示站建议长 TTL，避免用户演示中途掉线。
AITF_DEMO_TOKEN_TTL_HOURS = int(os.getenv("AITF_DEMO_TOKEN_TTL_HOURS", "72"))

# ============ 清理范围（每日重置删什么） ============
# 1 = 连 admin 自己的业务数据也清空（推荐：演示站就该每天归零）
# 0 = 只清非 admin 用户数据，admin 数据保留
AITF_DEMO_PURGE_ADMIN = os.getenv("AITF_DEMO_PURGE_ADMIN", "1") == "1"

# ============ 初始知识库快照（V9.4） ============
# 每日重置会把所有用户的知识库一并清空，演示站需要一份「初始知识库」在
# 重置后自动恢复（快照由 scripts/export_kb_snapshot.py 导出）。
# 留空 = 不恢复（正式站/未配置实例天然 no-op）。
AITF_DEMO_KB_SEED_DIR = os.getenv("AITF_DEMO_KB_SEED_DIR", "")

# 快照知识库归属的用户 id（演示站账号 id 与源实例不同，需重映射；0/空=沿用快照原值）
AITF_DEMO_KB_OWNER_USER_ID = int(os.getenv("AITF_DEMO_KB_OWNER_USER_ID", "0") or 0)
