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
./start.sh                    # 建 venv + 装依赖 + 启动（默认 8001 端口）
# PORT=9001 ./start.sh        # 换端口
# SKIP_DEPS=1 ./start.sh      # 跳过依赖检查
```

- 前端构建产物 `frontend/dist/` 由后端静态托管，访问 `http://127.0.0.1:8001/`
- Swagger：`http://127.0.0.1:8001/docs`
- **首个注册用户自动成为 admin**；之后注册为普通 user；未登录自动进访客模式
- 数据库见 `.env`（默认 SQLite 零配置；MySQL 填 `DB_TYPE=mysql` + `DB_*`，库需 utf8mb4）

## e2e 冒烟

```bash
cd frontend
M1_URL=http://127.0.0.1:8001 node scripts/e2e-m1-browser.mjs   # 认证链路
node scripts/run-e2e.mjs                                        # m1/m2/m3 汇总 runner
```

⚠️ e2e 一律用 `M*_URL` 指向本项目端口（本地 8001 / 生产 8002）；默认值 8000 是已下线原项目 ai-testflow，勿误用。

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
- 本地 e2e 测试强制 SQLite（tests/conftest.py），不触碰远程 MySQL

## 目录结构

```
ai-testgen/
├── main.py              # FastAPI 入口（lifespan: init_db + guest + task_queue）
├── app/
│   ├── api/             # auth/guest/users/chat/conversations/tasks/knowledge/prompts/llm_*/files/categories
│   ├── core/            # config/db/security/crypto/middleware/access_log/task_queue
│   ├── models/          # user/conversation/task/knowledge(7表)/llm/category/prompt_override
│   ├── services/        # llm_*/knowledge(向量检索)/doc_extract/pipeline_lib/sample_seeder
│   └── workflow/        # engine(parser→generate→review→export) + agents + iterate
├── generator_core/      # legacy src.* 包（生成管道内核，整目录不可拆）
├── frontend/            # React18+TS+Vite（rail 四入口：AI 会话/用例库/知识库/设置）
├── tests/               # pytest（425 基线全绿，SQLite 隔离）
├── docs/                # 文档 + archive（含测试用例归属盘点）
└── scripts/             # 迁移/部署脚本 + start_local.sh
```
