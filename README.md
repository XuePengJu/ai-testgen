# ai-testgen — AI 对话 + 测试用例生成 + 知识库平台

> 求职作品集「用 AI 重新定义测试效率」双产品之二：**ai-testgen**（AI 对话 · 用例生成 · 知识库）。
> 原姊妹项目 **ai-testflow**（页面探索 · 自动化执行 · 质量看板，已于 2026-09-30 拆分后退役），两产品完全独立（独立代码库/账号/数据库）。

## 功能

- **AI 会话**：SSE 流式对话 + 任务步骤卡实时进度；**个人记忆库默认必检**——个人知识库恒定参与检索（top_k=3 固定槽，与勾选库两次检索按 chunk_id 去重、记忆优先）；输入区 🧠 **一键存入**：把当前会话的对话记录与附件整理写入个人记忆库（会话记忆幂等覆盖 + 附件兜底入库 + 刷新当日记忆日报）；`kb_qa` 为历史模式（V5.8 起已下线独立 Tab，问答统一回主对话自动检索 + 引用溯源）
- **用例生成管道**：需求解析 → 用例生成 → 评审 → 导出（xmind/xlsx/markdown），支持对话内迭代优化、版本链、用例库资产化（草稿/已评审/分类树/概览指标卡）
- **知识库**：批量/目录上传、解析入库、自动摘要与分类、Wiki 索引、图谱实体/关系、RAG 向量检索（Chroma）；**个人记忆库**（注册自动创建、私密可见、列表置底）随对话自动沉淀记忆，聊天附件统一登记入库（副本持久保存 + 后台向量化）
- **提示词定制**：14 个提示词键位用户级覆盖（会话人设/生成模板）
- **模型池**：多 Provider 多槽位（zhipu/百炼/魔搭/custom），单模型与池模式并存，连通性测试
- **多用户**：注册/登录/JWT、访客体验模式（配额限制）、管理员用户管理、接口 AES-GCM 分级加密

## 技术栈

FastAPI + SQLAlchemy（MySQL/SQLite 双方言）· React18 + TypeScript + Vite · LangChain · ChromaDB · zustand

## 快速启动

```bash
./start.sh                    # 建 venv + 装依赖 + 启动（默认 8005 端口）
# PORT=9001 ./start.sh        # 换端口
# SKIP_DEPS=1 ./start.sh      # 跳过依赖检查
```

- 前端构建产物 `frontend/dist/` 由后端静态托管，访问 `http://127.0.0.1:8005/`
- Swagger：`http://127.0.0.1:8005/docs`
- **首个注册用户自动成为 admin**；之后注册为普通 user；未登录自动进访客模式
- 数据库见 `.env`（默认 SQLite 零配置；MySQL 填 `DB_TYPE=mysql` + `DB_*`，库需 utf8mb4）

## 测试

测试资产统一在 `tests/` 单根，按 **层次 × 模块** 两维组织，一套 `pytest` 命令覆盖全部：

```
tests/
├── unit/<模块>/     # 纯函数单测（零依赖、秒级；不建库、不启 TestClient）
├── api/<模块>/      # 进程内接口测试（TestClient）；触及库的用例带 @pytest.mark.integration
├── ui/<模块>/       # 浏览器端到端（Playwright，需真实服务 + 已构建 dist）
└── manual/          # 不被 pytest 收集的手工脚本（README 截图 / 演示录屏）
```

模块名对齐后端业务域：`auth` / `chat` / `knowledge` / `memory` / `testcase` / `llm` / `common`（ui/manual 另有 `stats` / `smoke` / `visual` / `docs`）。

常用命令（全项目端口统一 **8005**）：

```bash
# 单测（秒级，零依赖）
pytest tests/unit -m unit

# 接口层（进程内，无外部服务）
pytest tests/api

# 按业务模块跑（目录选择，零维护）
pytest tests/api/memory

# 全量门禁（排除视觉、演示与重型慢速类）
pytest -m "not visual and not demo and not slow"

# 浏览器端到端：先起后端 + 构建 dist，再跑
./start.sh &                        # 后端（默认 8005）
npm --prefix frontend run build      # server 夹具会校验 dist 新鲜度
AITF_BASE_URL=http://127.0.0.1:8005 pytest tests/ui -m "not visual and not demo and not slow" --tracing=off
                                                                                # ↑ 本沙箱必加，见下
```

> 跑 ui 层请用 `-m "not visual and not demo and not slow"` 而不是 `-m e2e_ui`：视觉用例同时挂了 `e2e_ui` + `visual` 两个 marker，前者会把视觉基线一并选中。

> **`slow` = 业务主线重型用例**（`tests/ui/e2e/test_business_main_flow.py`：真实大模型全链路，
> 问答 → 生成用例 → 查看 → 迭代 → 导出，单次 ≈8–12min）。**默认门禁不跑**（已并入 `addopts` 的
> `-m "not demo and not slow"`），按需显式运行：`pytest tests/ui/e2e -m "slow" --tracing=off`。

- 目标地址唯一真源 = **`AITF_BASE_URL`**（不再使用 pytest-playwright 的 `--base-url`）
- e2e 起始态按路径自动推导：`tests/ui/auth/**` → **未登录**；其余 ui 模块 → **`test` 账户**（`AITF_E2E_USER` / `AITF_E2E_USER_PWD`，默认 `test`/`test1234`）；带 `@pytest.mark.admin` → **admin**（`AITF_E2E_ADMIN` / `AITF_E2E_PWD`）
- 端到端产物统一落 `reports/e2e-artifacts/`（视频/截图/trace，已 gitignore）；`reports/e2e-report.json` 按原看板结构导出
- 视口默认 **1440×900**（`AITF_E2E_VIEWPORT=WxH` 覆盖；**别改小**——实测 1280×720 会让 m3 的思维导图 `.me-tpc` 不渲染、稳定失败）。录屏默认 **1280×720**（`AITF_E2E_VIDEO_SIZE=WxH` 覆盖）——**录屏尺寸必须显式给**：缺省时 Playwright 会把画面等比缩进 800×800 框内（1440×900 → 800×500），字会发糊；要像素级清晰可设 `AITF_E2E_VIDEO_SIZE=1440x900`（与视口一致）
- 前端 `e2e-crypto.ts`（AES-GCM 前后端互通验证）**保留在 Node 侧**运行：`node --experimental-strip-types frontend/scripts/e2e-crypto.ts`（它验证的是 TS 实现本身，无法用 Python 替代）；其余 e2e 脚本已于 S4 退役，全部迁入 `tests/ui/`

### 用例中文可读名（`title` marker）

默认展示的用例名是「模块路径 + 英文函数名 + `[chromium]」。给用例函数加一行
`@pytest.mark.title("…")` 即可让 **junit / `reports/e2e-report.json` / 回放页 / 录屏 HUD** 显示面向
业务的一句话中文名——**不改变 pytest nodeid**，也不影响 `-q` / `--collect-only` 输出：

```python
@pytest.mark.title("会话归档规则验证")
def test_conv_archive(page, hud, server, js_errors):
    ...
```

- 缺名用例默认**降级为函数名**运行，并在收集期给出告警（不阻塞存量用例）；
  `pytest --strict-titles` 可把「缺名 / 非法名（空、与函数名相同）」升为收集期报错（默认关）。
- 终端可加 `--show-titles` 打印「中文名 ← nodeid」对照表（默认关，`-q` 输出不受影响）。
- marker 约定、命名风格、降级策略与 unit/api 推广流程见
  [`docs/测试用例中文可读名规范-V1.0.md`](docs/测试用例中文可读名规范-V1.0.md)；
  回放页生成器见 `scripts/gen_e2e_video_review.py`。

⚠️ **本地 WorkBuddy 沙箱环境**：起后端与跑 pytest 都需加 `env -u PYTHONPATH`（剥离沙箱注入的 safe-delete shim；否则删号类接口会 500 并把服务带崩）。普通环境 / CI 无需此项。

⚠️ **同一个沙箱还会拦 Playwright 的 trace 收尾**——必须知道，否则会把「环境问题」误读成「用例失败」：Playwright 抓 trace 时先在临时目录暂存 `playwright-artifacts-*/*.network`，收尾再拷进 `--output`；本沙箱对这个拷贝**无差别拒绝**（报错 `Tracing.stop: Brokered host copy source refused by file policy`），且**与暂存目录位置无关**——实测把 `TMPDIR` 重定向到工作区内后照样被拒，故不再做「自动重定向」这类无效改动。后果是**用例断言全过、却被标 teardown ERROR**（`reports/e2e-report.json` 里历史那批「失败」即此因）。

→ **本地跑 ui 请显式 `--tracing=off`**（需要录屏再另加 `AITF_E2E_VIDEO=on`，默认即开）。**CI 与普通开发环境不受影响**，保留默认 `retain-on-failure`，以便失败时留 trace 排查。

## 与 ai-testflow 的关系

本项目由 ai-testflow 于 2026-09-30 拆分而来（对话/生成/知识域），拆分后**独立演进**。以下共享代码为复制分叉，bug fix 需双向 port（同步清单）：

| 同步项 | 文件 |
|---|---|
| 接口加密 | `app/core/crypto.py` + `app/core/middleware.py` + `frontend/src/crypto/aesGcm.ts`（本项目派生前缀 `atgen-*`，原项目 `aitf-*`，**互不通用**） |
| 认证 | `app/core/security.py` + `app/api/deps.py` + `frontend/src/contexts/AuthContext.tsx` |
| LLM 设施 | `app/services/{langchain_client,llm_service,llm_pool,prompt_service}.py` |
| API 客户端 | `frontend/src/api/apiJson.ts`（api() 封装与错误处理） |
| 工具 | `frontend/src/utils/time.ts`（时区约定：后端一律存 UTC） |

## 已知技术债 / 注意事项

- `explorer_agent` 原反向依赖 `generator_core`（align_step_expectations）——该依赖随探索域留在原项目，本项目 generator_core 仅服务生成管道
- `tasks.target_id`、`categories.target_id/is_auto` 列保留但不再使用（拆分历史遗留，无 FK）
- `Message.task_id` 为逻辑外键；历史 `workflow` 模式会话正常工作
- 依赖 `generator_core/`（legacy `src.*` 包）经 `pipeline_lib` 注入 sys.path，整目录不可拆
- vite 构建 JS 单 chunk ~1.6MB（echarts/mind-elixir 大依赖），后续可做 code-split
- pytest 强制 SQLite（根 `conftest.py` 做 env 隔离），不触碰 `.env` 里的远程 MySQL
- M 系列里程碑用例已于 2026-10-10 去编号整理：`test_case_detail.py`（原 m3）摘除失效 xfail 后曾因**断言正则漏配两位数版本 v10–v19 + 第⑥步 60s 超时过紧**稳定失败，已修复（正则放宽 + 超时 120s）复绿；m2 会话主链路**可完成但单次成本 ~4–6min**（实测 228–402s，非不可修），按成本移入 `tests/manual/chat/m2_conversation_flow.py`（不参与门禁），详见 `docs/M系列里程碑用例-清理整理方案-V1.0.md`

## 目录结构

```
ai-testgen/
├── main.py              # FastAPI 入口（lifespan: init_db + guest + task_queue）
├── conftest.py          # 根：日志隔离 + 测试 env 隔离（SQLite）+ sys.path + 按路径补模块 marker
├── pytest.ini           # testpaths + markers + addopts(-m "not demo and not slow")
├── app/
│   ├── api/             # auth/guest/users/chat/conversations/tasks/knowledge/prompts/llm_*/files/categories
│   ├── core/            # config/db/security/crypto/middleware/access_log/task_queue
│   ├── models/          # user/conversation/task/knowledge(7表)/llm/category/prompt_override
│   ├── services/        # llm_*/knowledge(向量检索)/doc_extract/pipeline_lib/sample_seeder
│   └── workflow/        # engine(parser→generate→review→export) + agents + iterate
├── generator_core/      # legacy src.* 包（生成管道内核，整目录不可拆）
├── frontend/            # React18+TS+Vite（rail 四入口：AI 会话/用例库/知识库/设置）
├── tests/               # pytest：unit/api/ui/manual 分层；后端 582 用例 + ui e2e
├── reports/             # 测试产物（gitignore）：*-junit.xml / e2e-report.json / e2e-artifacts/
├── docs/                # 文档 + archive（含测试体系整合与 e2e 迁移方案）
└── scripts/             # 迁移/部署脚本 + start_local.sh
```
