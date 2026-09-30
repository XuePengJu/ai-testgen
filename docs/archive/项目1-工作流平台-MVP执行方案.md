# 项目1 · AI 测试工作流平台（MVP 执行方案）

> ⚠️ **修订记录（2026-09-15 V2.12，已部署上线）**：按 V2.10 ~ V2.12 实际落地同步本文：
>
> 1. **V2.10 统一入口与会话内迭代（FR-Q）**：详情抽屉移除全部输入表单，底部「💬 继续优化（回到会话）」为唯一迭代入口；会话输入框迭代引用 chip 机制（先沟通、点「⚡ 生成用例」才跑 `POST /tasks/{id}/iterate`）；`TaskOut` 补 `conversation_id`（`messages.task_id` 反查兜底）；修复示例数据播种（MySQL 下 StepLog 外键 1452）。详见「8.1.8」。
> 2. **V2.11 版本链聚合（FR-R）**：新增 `frontend/src/utils/taskChain.ts` 纯前端解析 `parent_task_id` 链（后端零改动），三处消费：抽屉标题行版本切换器、会话流旧版本卡折叠、任务列表同链聚合 + 版本徽章。详见「8.1.9」。
> 3. **V2.12 导图控件迁出画布（FR-S）**：MindElixir 右下角工具栏（导出 XMind/全屏/缩放/百分比/居中）经 React portal 迁入详情抽屉底栏，与「继续优化」同排；保留左上方向切换栏。详见「8.1.10」。
> 4. **数据层与部署事实更新**：生产库为 **MySQL**（阿里云线上库，地址见 `.env`，不写入文档），本地保留 SQLite 降级（`app/core/db.py` 双方言）；公网入口现行方案为宝塔 Nginx 反代（历史 cloudflared 隧道方案已废弃），cpolar 方案更早废弃。
> 5. 本文仍为架构底座文档（版本无关），功能演进以 PRD / README / 各版本执行方案为准。

> ⚠️ **修订记录（2026-09-14 V2.9）**：按 V2.9 功能增强同步文档指针：
>
> 1. 新增对话附件文档解析（FR-N）、深度思考管道与 Markdown 渲染（FR-O）、用例标题复合化与导图布局（FR-P），详见《项目1-功能增强执行方案-V2.9.md》与更新后的 PRD/README。
> 2. 用例 ID 回归修复（A 后端读取侧 / B 前端显示 / C 存量 DB 回填）已落地并自验通过，详见 V2.9 执行方案 §4。
> 3. 本文为架构底座文档（版本无关），目录树/接口设计保持最新；功能演进以 PRD/README/V2.9 执行方案为准。

> ⚠️ **修订记录（2026-09-12 V2.7.1）**：按 V2.7 发布后的 4 个修复提交同步：
>
> 1. 用例 ID 兜底（`97c9a70`）：新增 `generator_core/src/models/testcase.py#ensure_case_ids()`，在生成入库、迭代合并、导出、前端展示四处兜底；已有 ID 保持不变，缺失的按现有最大序号递增补全（兼容 TC-001/TC001/TC-1 格式），保证用例 ID 列永不为空。新增 `tests/test_case_id.py`（6 条），全量 136 条通过。
> 2. 详情抽屉体验（`c6c0e3a`/`f0540c3`）：抽屉头部（标题+状态+Tab 栏）吸顶固定，内容区上下分栏、独立滚动。
> 3. 本文同步：第3节目录树补 `iterate.py`/`import_agent.py`/`supplement_agent.py`；第4.2 节 llm 端点表按实际路由重写，补任务迭代/删除端点；第5节补抽屉交互描述。

> ⚠️ **修订记录（2026-09-11 V2.7 增强）**：步骤级预期结果（xmind 格式修复）：
>
> 1. TestCase 加 `step_expectations`（与 steps 一一对应）+ `align_step_expectations()` 对齐兜底，保证每步必有预期。
> 2. 四份 LLM prompt 统一要求逐步输出预期；mock_generator 16 条用例配逐步预期。
> 3. xmind 导出每步挂预期子节点；xlsx 新增「步骤预期」列；导入层还原逐步预期并兼容旧格式。
> 4. 前端思维导图每步挂预期、用例表格内联逐步预期。
> 5. 新增 `tests/test_step_expected.py`（16 条），全量 130 条通过。详见「8.1.7」。

> ⚠️ **修订记录（2026-09-09 V2.7）**：新增用例迭代与导入功能（FR-M）：
>
> 1. 数据模型：Task 加 `parent_task_id` 字段，`_ensure_columns()` 自动迁移。
> 2. 新增 `app/workflow/agents/import_agent.py`（xmind/xlsx/json 用例文件导入解析器）、`app/workflow/agents/supplement_agent.py`（带已有用例上下文的增量生成）、`app/workflow/iterate.py`（迭代流水线：加载基础用例→增量生成→合并去重→质量校验→导出）。
> 3. API：新增 `POST /api/tasks/{task_id}/iterate`（FormData: instruction + 可选 file + conversation_id），预创建子任务空壳后后台执行，与 create_task 模式一致。
> 4. 对话上下文：ChatIn 加可选 `task_id`，AI 回复时自动携带任务用例摘要。
> 5. 前端：上传 accept 扩展 xmind/xlsx/json、任务气泡加「➕ 继续补充」、AI 确认按钮根据模式切换「✨ 补充生成」、详情抽屉加迭代版本链。
> 6. 每次迭代生成独立子任务（parent_task_id 关联），版本号 v2/v3 自动命名，历史版本可查看/预览/下载。
> 7. 测试：新增 `tests/test_iterate.py`（18 条），全量 114 条通过。

> ⚠️ **修订记录（2026-09-09）**：按 V2.6 实际代码实现做全面更正：
>
> 1. 第1节技术栈：AI 模型从"阿里百炼"更新为多厂商支持（7+ 预设）；前端从"原生 HTML+JS MVP"更新为对话驱动 Buddy 助手；部署更新为 FastAPI 同源伺服 + cpolar 内网穿透。
> 2. 第3节目录树：全面更新为实际结构（前端移至 `frontend/`，补入 `app/api/{chat,conversations,llm_config}.py`、`app/core/providers.py`、`app/models/{conversation,llm_config}.py`、`app/schemas/{conversation,llm_config}.py`、`app/services/{llm_service,sample_seeder}.py`）。
> 3. 第4节 API 设计：补充全部路由模块（auth/guest/users/categories/llm_config/chat/conversations）。
> 4. 第5节前端：更新为 Buddy 对话驱动首页 + 左侧边栏 + 详情抽屉。
> 5. 第8节后新增「8.1.3 V2.4 模型配置」「8.1.4 V2.5 详情页增强」「8.1.5 V2.6 对话驱动」。
>
> ⚠️ **修订记录（2026-08-29）**：本方案初稿写于 MVP 立项阶段，后经 V2 认证、V2.1 API 分级加密、V2.3 任务分类多次迭代。本次按**实际代码实现**做了如下更正/补充（原 MVP 立项存档已于 2026-09-12 删除，内容已被本文全量取代，历史版本可溯 git）：
>
> 1. 第0节：原 CLI 原型 `ai-testcase-generator/` 已于 2026-08-29 确认废弃并删除，其能力已整体并入 `generator_core/`；平台为唯一入口。
> 2. 第3节目录树：更新为实际结构（去掉不存在的 `app/models/case.py`、`app/services/{parser,generator,exporter}.py`，补入 V2/V2.1/V2.3 新增的 `app/api/{auth,guest,users,categories}.py`、`app/core/{crypto,middleware}.py`、`app/jobs/`、`app/models/category.py`、`generator_core/`）。
> 3. 第8节后新增「8.1 已落地补充」，记录 V2.1 分级加密与 V2.3 任务分类。
> 4. 第9.3.1 示例代码变量名 `SECRET_KEY` 统一为实际 `config.JWT_SECRET`；第9.4 补 `POST /api/admin/guests/clean-all`。

> 定位：作品集「门面担当」全栈产品的**第一版**。原规划描述——接口管理 + AI 自动生成用例 + 定时执行 + 报告 + 质量看板，AI Agent 编排测试流程，在线可演示。  
> 本方案锁定 **后端 + Agent 编排优先** 形态，前端完整版（React）后补。

---

## 0. 与已有 CLI 版的关系（已更新）

> **状态变更（2026-08-29）**：原 CLI 原型 `ai-testcase-generator/`（DBERP 实战版）已于 2026-08-29 确认**废弃并删除**。其全部已验证能力（双输入源解析、AI 生成 + 测试方法论策略、mock 兜底、xlsx/json/xmind 导出、DBERP 素材）已**整体并入**本仓库的 `generator_core/`（即原 `src/` 目录），并进一步封装为平台级 Agent。因此平台即唯一入口，不再保留独立 CLI。

`generator_core/`（DBERP 实战版内核）是**已验证的原型逻辑载体**：解析、生成、导出、DBERP 接口/业务素材都已跑通。

本平台 = 把原型**平台化 / 生产化**：

- 复用：`generator_core/` 下的解析器、用例模型、生成策略、导出器、DBERP 素材、mock 兜底逻辑
- 新增：任务调度状态机、多 Agent 编排、可观测日志、REST API、轻量前端、用户认证与多用户隔离（V2）、API 分级加密（V2.1）、任务多级分类（V2.3）

---

## 1. 技术栈

| 层     | 选型                                              | 说明                                                                      |
| ----- | ----------------------------------------------- | ----------------------------------------------------------------------- |
| 后端框架  | **FastAPI**                                     | 异步、自带 Swagger、Python AI 生态友好                                            |
| AI 模型 | **多厂商 OpenAI 兼容协议**                             | 7+ 预设（阿里百炼/智谱/腾讯混元/DeepSeek/Kimi/豆包/自定义），用户级 API Key 自管；无 Key 时 mock 兜底 |
| 数据库   | **MySQL（生产）／ SQLite（本地降级）**           | 存任务/步骤日志/用例/用户/会话/分类/模型配置；`app/core/db.py` 双方言适配，生产为阿里云远程库 3356 端口 |
| 任务编排  | 自研状态机 + 步骤调度                                    | 四 Agent 串联，每步可观测、可重试                                                    |
| 对话驱动  | **SSE 流式输出 + 会话持久化**                            | Buddy 助手自然语言交互，多轮上下文，对话与消息落库可回放                                         |
| 前端    | **React 18 + TypeScript + Vite（zustand）**       | V2.8 起已由原生单文件前端重构为 React 工程，构建产物 `frontend/dist` 同源托管；旧版保留在 `frontend-legacy/`（`AITF_FRONTEND=legacy` 可回退） |
| 部署    | **FastAPI 同源伺服（单端口 8000）+ 阿里云 + cloudflared 命名隧道** | 前端静态文件由 FastAPI 挂载，前后端同域；systemd 单元 `ai-testflow.service` 托管 uvicorn（`Restart=on-failure`） |

---

## 2. 架构：四 Agent 编排工作流

```
提交规格 ──▶ [任务状态机] ──▶ ParserAgent ──▶ GeneratorAgent ──▶ ReviewerAgent ──▶ ExporterAgent ──▶ 完成
                  │               │                │                 │                 │
                  └───────────────┴────────────────┴─────────────────┴─────────────────┘
                                 每步写入 step_log（状态/输入摘要/输出摘要/耗时/错误）
```

| Agent              | 职责         | 输入 → 输出                                          |
| ------------------ | ---------- | ------------------------------------------------ |
| **ParserAgent**    | 解析规格为测试单元  | 规格文件/文本 → `ApiEndpoint[]` / `RequirementUnit[]`  |
| **GeneratorAgent** | 按策略调模型生成用例 | 测试单元 + 策略(等价类/边界值/场景/异常) → `TestCase[]`（mock 兜底） |
| **ReviewerAgent**  | 校验 + 质量门禁  | `TestCase[]` → 校验报告(结构/Pydantic/覆盖率/异常占比/去重)     |
| **ExporterAgent**  | 导出多格式      | `TestCase[]` → xlsx / json / xmind 文件            |

**任务状态机**：`pending → running → completed | failed`（任一步骤失败 → failed，记录错误步骤）。

**可观测**：每个 step 记录 `name / status / started_at / finished_at / duration_ms / input_summary / output_summary / error`。

---


## 3. 目录结构（已更新为 V2.6 实际实现）

```
ai-testflow/
├── main.py                  # FastAPI 入口（挂载 API + 前端静态文件 + vendor）
├── requirements.txt
├── .env.example
├── frontend/                # 前端（V2.6 从 app/static 移至此处）
│   ├── index.html           # 单文件前端（对话驱动 + 登录/分类/拖拽/思维导图/加密）
│   ├── config.js            # 前端 API 基址配置（同源时空字符串）
│   ├── favicon.svg
│   └── vendor/              # 第三方库本地化（mind-elixir 等）
├── app/
│   ├── core/
│   │   ├── config.py        # 配置（路径、JWT_SECRET、ENV、CORS、UPLOAD/OUTPUT_DIR）
│   │   ├── db.py            # SQLite 连接 + 建表（含 category/conversation 等迁移）
│   │   ├── security.py      # bcrypt 哈希 + JWT 签发/校验（get_current_user 依赖）
│   │   ├── crypto.py        # API 分级加密（AES-256-GCM，V2.1）
│   │   ├── middleware.py     # 请求体解密/响应加密中间件（V2.1）
│   │   ├── providers.py     # 多厂商 LLM 预设（百炼/智谱/混元/DeepSeek/Kimi/豆包，V2.4）
│   │   └── utils.py         # 时间等工具
│   ├── models/
│   │   ├── task.py          # Task + StepLog（SQLAlchemy，含 category_id/conversation_id）
│   │   ├── user.py          # User / GuestCreationLog / CleanLog（三级角色）
│   │   ├── category.py      # 任务多级分类树（V2.3）
│   │   ├── conversation.py  # 会话 + 消息（对话持久化，V2.6）
│   │   └── llm_config.py    # 用户级模型配置（V2.4）
│   ├── schemas/
│   │   ├── task.py          # Pydantic 请求/响应
│   │   ├── conversation.py  # 会话/消息 Pydantic（V2.6）
│   │   └── llm_config.py    # 模型配置 Pydantic（V2.4）
│   ├── services/
│   │   ├── pipeline_lib.py  # 生成流水线封装（解析→生成→导出）
│   │   ├── llm_service.py   # OpenAI 兼容 HTTP 直连客户端（V2.4）
│   │   └── sample_seeder.py # 示例数据播种
│   ├── workflow/
│   │   ├── engine.py        # 状态机 + 步骤调度
│   │   ├── iterate.py       # 迭代流水线：加载基础用例→增量生成→合并去重→质量校验→导出（V2.7）
│   │   └── agents/
│   │       ├── parser_agent.py
│   │       ├── generator_agent.py
│   │       ├── reviewer_agent.py
│   │       ├── exporter_agent.py
│   │       ├── import_agent.py      # 用例文件导入解析（xmind/xlsx/json，V2.7）
│   │       └── supplement_agent.py  # 带已有用例上下文的增量生成（V2.7）
│   ├── api/
│   │   ├── tasks.py         # 任务 REST 端点（含上传/下载）
│   │   ├── auth.py          # 注册/登录/改密/me（V2）
│   │   ├── guest.py         # 访客 token / 转正（V2）
│   │   ├── users.py         # 用户管理 / 访客治理 / 统计（V2）
│   │   ├── categories.py    # 分类 CRUD + 任务归类（V2.3）
│   │   ├── llm_config.py    # 模型配置 / 连通测试（V2.4）
│   │   ├── chat.py          # Buddy 对话 SSE 流式（V2.6）
│   │   ├── conversations.py # 会话 CRUD + 消息（V2.6）
│   │   └── deps.py          # 鉴权依赖
│   ├── jobs/
│   │   └── guest_cleaner.py # 访客 TTL 清理（APScheduler，V2）
│   └── static/              # 旧静态目录（已废弃，前端移至 frontend/）
├── generator_core/          # 从 CLI 原型继承的生成内核（V2 起并入）
│   ├── config/settings.py
│   └── src/
│       ├── models/testcase.py        # 桥接测试用例模型
│       ├── parser/{markdown,swagger}_parser.py
│       ├── generator/{case_generator,mock_generator,strategies,llm_client}.py
│       └── exporter/{excel,json,xmind}_exporter.py
├── scripts/
│   └── migrate_v2.py        # V2 数据迁移 + 引导默认 admin
├── uploads/  outputs/       # 上传 / 导出目录（按用户 data_dir 分目录）
└── tests/
```

---

## 4. API 设计

### 4.1 核心路由模块

| 路由文件               | 前缀     | 说明                      |
| ------------------ | ------ | ----------------------- |
| `tasks.py`         | `/api` | 任务 CRUD、上传、下载、详情        |
| `auth.py`          | `/api` | 注册/登录/改密/me             |
| `guest.py`         | `/api` | 访客 token / 转正           |
| `users.py`         | `/api` | 用户管理 / 访客治理 / 统计（admin） |
| `categories.py`    | `/api` | 分类 CRUD + 任务归类（V2.3）    |
| `llm_config.py`    | `/api` | 模型配置 / 连通测试（V2.4）       |
| `chat.py`          | `/api` | Buddy 对话 SSE 流式（V2.6）   |
| `conversations.py` | `/api` | 会话 CRUD + 消息（V2.6）      |


### 4.2 核心端点

| 方法              | 路径                                  | 说明                                                  |
| --------------- | ----------------------------------- | --------------------------------------------------- |
| POST            | `/api/tasks`                        | 提交任务：上传规格文件 **或** 粘贴文本 + 选素材类型(api/business) + 导出格式 |
| GET             | `/api/tasks`                        | 任务列表（状态/进度/用例数，admin 可 `?all=true`）                 |
| GET             | `/api/tasks/{id}`                   | 任务详情 + 四步骤日志 + 用例概览                                 |
| GET             | `/api/tasks/{id}/download?fmt=xlsx` | 下载导出文件                                              |
| POST            | `/api/tasks/{id}/iterate`           | 用例迭代：instruction + 可选导入文件，生成子任务版本链（V2.7）            |
| DELETE          | `/api/tasks/{id}`                   | 删除任务                                                |
| POST            | `/api/chat/stream`                  | Buddy 对话 SSE 流式输出（V2.6）                             |
| POST            | `/api/chat`                         | Buddy 对话（非流式兜底，V2.6）                                |
| POST/GET/DELETE | `/api/conversations`                | 会话创建/列表/删除（V2.6）                                    |
| POST            | `/api/conversations/{id}/messages`  | 追加消息（V2.6）                                          |
| GET             | `/api/llm/providers`                | 厂商预设列表（V2.4）                                        |
| GET             | `/api/llm/effective`                | 当前生效配置（用户级覆盖平台级，V2.4）                               |
| GET/PUT         | `/api/llm/config`                   | 用户模型配置读取/保存（Key 脱敏返回/加密落库，V2.4）                     |
| DELETE          | `/api/llm/config/{slot}`            | 删除指定槽位用户配置（V2.4）                                    |
| GET/PUT         | `/api/llm/platform-config`          | （admin）平台默认配置读取/设置（V2.4）                            |
| DELETE          | `/api/llm/platform-config/{slot}`   | （admin）删除平台默认槽位（V2.4）                               |
| POST            | `/api/llm/test-default/{slot}`      | （admin）平台默认配置连通测试（V2.4）                             |
| POST            | `/api/llm/test`                     | 模型连通测试（用户配置或平台默认，V2.4）                              |
| GET             | `/health`                           | 健康检查                                                |

FastAPI 自带 `/docs` Swagger 交互文档。

---

## 5. 前端（V2.6 对话驱动形态）

`frontend/index.html` 原生单文件实现（无构建、无框架）：

- **首页主区域**：Buddy 对话助手
  - 对话式输入框，自然语言描述测试需求
  - AI 回复 SSE 流式逐字渲染，思考过程可折叠
  - 预设场景卡片（Web 登录用例 / 接口参数校验 / App 端功能 / 需求文档解析）
  - 对话内联展示任务生成进度：四 Agent 节点实时刷新，点击节点展开详情
  - 任务完成后"查看完整用例"按钮，当前页弹出详情抽屉（不新开标签页）
- **左侧边栏**：
  - 「+ 新对话」按钮
  - 历史会话列表（按时间倒序，点击切换）
  - 任务分类树（全部/未分类/自定义多级分类，拖拽归类）
- **任务详情抽屉**（右侧滑出，3 Tab；头部含标题+状态+Tab 栏吸顶固定，内容区上下分栏、独立滚动）：
  - 🧠 思维导图（MindElixir 在线渲染）
  - 📋 测试用例表格（按模块分组）
  - ⚙️ 工作流步骤（四步时间线 + 质量报告）
- **顶栏**：身份徽标（访客倒计时/用户名/管理员）、模型状态胶囊、退出按钮、产品路线图悬浮入口
- **安全**：AES-256-GCM 纯 JS 解密（user/guest 角色 API 流量加密），不依赖 crypto.subtle

完整 React + 质量看板版本作为第二阶段。

---

## 6. 验收标准（MVP 完成定义）

1. `uvicorn main:app` 启动，访问 `/docs` 与 `/` 正常
2. 提交 DBERP 规格（api 或 business），任务从 `pending → running → completed`，四步骤日志齐全
3. 导出的 xlsx/json/xmind 文件可下载、内容结构化、异常/边界用例占比 ≥ 50%
4. **无百炼 Key 时 mock 兜底跑通全流程**（降低演示门槛）
5. 任一步骤异常 → 任务 `failed` 且错误步骤可定位
6. README 含架构图、快速开始、演示说明

---

## 7. 任务拆分（见 TaskList）

T7 方案 → T6 骨架 → T8 工作流引擎 → T9 接入模块 → T10 四 Agent → T11 API → T12 前端 → T13 跑通+README

---

## 8. 后续演进（非 MVP）

- **用户认证 + 多用户数据隔离（设计见第 9 节，V2 优先实施）** ✅ 已落地
- **API 分级加密（V2.1）** ✅ 已落地
- **任务多级分类 + 拖拽归类（V2.3）** ✅ 已落地
- **多厂商模型配置 + 双模型视觉理解（V2.4）** ✅ 已落地
- **任务详情 3 Tab + 思维导图在线预览（V2.5）** ✅ 已落地
- **对话驱动 Buddy 助手 + 会话持久化（V2.6）** ✅ 已落地
- 完整 React 前端 + 质量看板（覆盖率/异常占比可视化）
- 定时执行（Celery/APScheduler）+ Allure 报告
- 接真实 DBERP 后端做端到端接口自动化闭环
- 部署优化：Docker 化 + 国内 CDN 加速

## 8.1 已落地补充（V2.1 / V2.3，实际已实现）

> 以下为初稿未覆盖、但已随迭代实装的模块，供与实际代码对照。

### 8.1.1 V2.1 · API 分级加密（防抓包明文泄露）

- **动机**：平台走 HTTP 演示时，响应体明文可被抓包直接读取。对 `user` / `guest` 角色的敏感响应做端到端加密，`admin` 保持明文便于 Swagger 调试。
- **方案**：`app/core/crypto.py` 实现纯 JS 可解密的 **AES-256-GCM**（NIST 测试向量验证 + Python 双向互通）；会话密钥由 `HKDF(JWT_SECRET, user_id)` 确定性派生，不落库。
- **落地**：`app/core/middleware.py` 在响应返回前对受保护字段加密、请求体（如需）解密；前端 `index.html` 用 Web Crypto / 纯 JS 解密。强制加密、无后门。

### 8.1.2 V2.3 · 任务多级分类 + 拖拽归类（FR-H）

- **动机**：任务列表从「扁平列表」升级为「按系统/模块组织的多级分类树」，支持自由拖拽归类。
- **方案**：`app/models/category.py` 多级树（name / parent_id / user_id 隔离 / sort）；`app/api/categories.py` 提供分类 CRUD、重命名/移动（防环校验）、任务归类接口（`PUT /api/categories/move-task/{task_id}`）、级联删除回落未分类；前端 `index.html` 用 HTML5 原生拖拽（任务拖到分类归类、分类拖到另一分类变子级、目标高亮）。

### 8.1.3 V2.4 · 多厂商模型配置 + 双模型视觉理解（FR-I）

- **动机**：解决「平台只能 mock 跑」的问题，让每个用户配置自己的大模型 API Key，真实调用 LLM 生成用例。
- **方案**：`app/core/providers.py` 内置 7+ 厂商预设（阿里百炼/智谱/腾讯混元/DeepSeek/Kimi/豆包/自定义），选中预设只需填 API Key；`app/services/llm_service.py` OpenAI 兼容协议 HTTP 直连（去除 SDK 依赖）；`app/api/llm_config.py` 配置 CRUD + 连通测试；`app/models/llm_config.py` 用户级配置持久化；API Key AES-256-GCM 加密落库，回显脱敏。
- **双模型**：可选配置图像识别模型，输入含截图时两段式——视觉模型逐图识别输出文字描述 → 与文档文本合并后交默认模型生成用例。

### 8.1.4 V2.5 · 任务详情 3 Tab 重构 + 思维导图在线预览（FR-J）

- **动机**：任务详情从「四步骤时间线」扩展为三 Tab 布局，强化用例可视化与评审体验。
- **方案**：详情抽屉右侧滑出（不新开页面），默认停在思维导图 Tab；`frontend/vendor/` 本地化 MindElixir 渲染库（120KB，零依赖），模块→用例层级展开，节点挂载类型/优先级标签（彩色胶囊）；测试用例 Tab 按模块分组表格展示完整字段；工作流步骤 Tab 保留四步时间线 + 质量报告。

### 8.1.5 V2.6 · 对话驱动 Buddy 助手 + 会话持久化（FR-L）

- **动机**：产品核心交互形态升级，从「表单提交任务」变为「自然语言对话驱动」，降低使用门槛，增强 AI 产品体验。
- **方案**：`app/api/chat.py` 实现 `POST /api/chat/stream` SSE 流式输出（逐字渲染 + 思考过程可折叠）；`app/api/conversations.py` 会话 CRUD + 消息追加；`app/models/conversation.py` Conversation（id/user_id/title/created_at/updated_at）+ Message（id/conversation_id/role/content/thinking/task_id/created_at）；assistant 消息关联 task_id，回放时按 task_id 实时拉取任务节点与用例，避免冗余存储；前端首页改为对话流，左侧边栏历史会话列表，对话内联展示任务生成进度，详情抽屉替代新标签页。


### 8.1.6 V2.7 · 用例迭代与导入（FR-M）

- **动机**：解决「生成后无法再修改」的痛点——AI 首次生成的用例可能不完善，用户需要在会话中继续补充；同时本地已有的 xmind/xlsx 用例文件需要上传解析后检查完善。
- **方案**：
  - **数据模型**：Task 加 `parent_task_id`（迭代来源），`_ensure_columns()` 自动迁移。
  - **导入解析器**（`app/workflow/agents/import_agent.py`）：支持 xmind（兼容 XMind 8 legacy XML + 新版 JSON）、xlsx（列名映射+别名兼容）、json（平台导出格式），解析失败明确报错不静默。
  - **增量生成**（`app/workflow/agents/supplement_agent.py`）：上下文只传压缩摘要（模块+标题+类型统计，防 token 超限），prompt 明确要求不重复已有用例、只补指令涉及范围，mock 兜底。
  - **迭代流水线**（`app/workflow/iterate.py`）：加载基础用例（原任务 cases_json + 可选导入文件）→ 增量生成 → 合并去重（键=标题+模块+类型+预期前20字）→ 质量校验（复用 reviewer）→ 导出（复用 exporter）。每次迭代生成独立子任务（parent_task_id 关联），版本号 v2/v3 基于 root 链自动计算，不覆盖原任务。
  - **API**：`POST /api/tasks/{task_id}/iterate`（FormData: instruction + 可选 file + conversation_id），预创建子任务空壳后后台执行，与 create_task 模式一致；权限校验（仅 owner/admin）、状态校验（仅 completed/failed）、并发保护（running 子任务拒绝重复提交）。
  - **对话上下文**：ChatIn 加可选 `task_id`，AI 回复时自动携带任务用例摘要（模块分布+标题列表），补充模式下 AI 知道当前在给哪个任务补用例。
  - **前端**：上传 accept 扩展 xmind/xlsx/json；已完成任务气泡加「➕ 继续补充」按钮进入补充模式；AI 确认按钮根据 supplementTaskId 切换「✨ 补充生成」；详情抽屉测试用例 Tab 加迭代版本链（v1→v2→v3，点击切换查看/预览/下载）。

### 8.1.7 V2.7 · 步骤级预期结果（xmind 格式修复）

- **动机**：xmind/思维导图中"有操作步骤但无预期结果"——原实现把整体预期只挂在最后一步下，前面步骤只有操作描述。
- **方案**：
  - **数据模型**：`TestCase` 加 `step_expectations: list[str]`（与 `steps` 一一对应），保留 `expected` 作整体总结；`align_step_expectations()` 强制对齐兜底（数量一致全非空→原样用；单步→整体预期；多步缺失→每步挂整体预期），保证"每步必有预期"。
  - **生成层**：requirement/api/supplement/reviewer 四份 prompt 统一要求输出 `step_expectations` 且与 steps 严格一一对应；各 `_normalize`/`_parse` 解析后对齐兜底；mock_generator 16 条用例配逐步预期。
  - **导出层**：xmind 每个步骤节点下挂对应预期子节点（不再只挂最后一步）；xlsx 新增「步骤预期」列（`to_row` 同步 10 列）。
  - **导入层**：xmind XML/JSON 解析每个步骤下的预期子节点还原 `step_expectations`，旧格式（仅最后一步有）自动兜底，整体预期缺失时取最后一步预期；xlsx/json 新增列别名兼容新旧格式。
  - **兼容层**：`iterate._parse_cases_json` 读旧库数据自动补齐字段，老任务迭代不丢内容。
  - **前端**：思维导图每步挂对应预期子节点；用例表格步骤列内联 `↳ 预期: xxx`，不增加列不挤布局。
  - **测试**：新增 `tests/test_step_expected.py` 16 条（对齐兜底 7、xmind 导出/导入闭环 3、mock 2、导入兼容 4），全量 130 条通过。

### 8.1.8 V2.10 · 统一入口与会话内迭代（FR-Q）

- **动机**：迭代入口分散（详情页一套表单 + 会话另一套），用户困惑"该在哪补充需求"；且 `ChatIn.task_id` 自 V2.7 起预留却从未接完，迭代链路在会话中不可见。
- **产品原则**：**主页会话输入框是"发起工作"的唯一入口** —— 新任务 / 旧任务迭代 / 用例迭代全部在此发起；详情抽屉零输入表单，只做结果查看 + 导出 + 引导回会话。
- **方案**：
  - **前端分流**：`chatStore` 维护 `iterTaskId / iterTaskName / iterNotes`；无 chip 走 `/chat/stream`，有 chip 时**发消息只沟通**（携带 `task_id`，后端注入旧任务用例摘要），点 chip 上的「⚡ 生成用例」才调 `POST /tasks/{id}/iterate`，instruction = 累积的补充要求。
  - **后端补链**：`iterate_task` 端点落 user 指令 + assistant 占位两条消息；`run_iterate` 结尾原有逻辑回填最后一条 assistant 消息的 `task_id`，会话里原位渲染任务卡（V2.7 预留的能力至此接完）。
  - **会话绑定**：`TaskOut` 新增 `conversation_id`；`app/api/tasks.py#_resolve_conversation()` 三级兜底（①已有值 ②`messages.task_id` 反查回填 ③新建会话并绑定），列表接口不触发以避免 N+1。
  - **示例数据修复**：`sample_seeder` 在 MySQL 下每个 Task 插入后显式 `db.flush()` 再写 StepLog（否则 FK 1452 且异常被静默吞），并同步建会话 + 消息回填 `conversation_id`。
  - **导出格式对齐**：示例任务与真实任务的 `formats` 统一为 `xlsx / json / xmind`（此前 ChatPanel 硬编码仅 `xlsx`）。

### 8.1.9 V2.11 · 版本链聚合（FR-R）

- **动机**：同一任务迭代多次后，历史版本散落在任务列表与会话流里，翻页才能找到旧版本。
- **方案（后端零改动）**：新增 `frontend/src/utils/taskChain.ts` 纯函数解析：
  - `buildChain(tasks, id)` —— 沿 `parent_task_id` 回溯到链根，再收集全部后代，按创建顺序编号 v1…vN；
  - `isLatestOfChain` / `groupByChain` —— 供列表与会话去重使用。
  - **三处消费**：①`TaskDetailDrawer` 标题行版本切换器（`vN ▾`，切换即 `openDetail`；菜单 `max-height + overflow` 限高滚动，点击外部 / 切换任务自动收起）②会话流中旧版本任务卡折叠为一行细条（`task-card-stale`）③`TaskList` 同链聚合成一行，卡片内页脚「▾ 共 N 个版本」展开历史，历史行带左侧竖线缩进与 `[v1]` 版本徽章。
  - **已验证真实数据**：`用户登录注册 v1→v2`、`采购入库管理 v1→v2→v3` 链路解析正确，无跨任务串链。

### 8.1.10 V2.12 · 导图控件迁出画布（FR-S）

- **动机**：MindElixir 右下角浮动工具栏（导出 XMind / 全屏 / 缩放）压住节点文字；而底栏本来就独占一行，宽度充足。
- **关键坑（查 `node_modules/mind-elixir/dist` 源码得来）**：`toolBar: false` 会**同时**关掉右下角 `.rb` 与左上角方向切换 `.lt`（库内 `qt(){ container.append(Gt()); container.append(Kt()) }` 同一开关）。故必须保留 `toolBar: true` + CSS 隐藏 `.mind-elixir-toolbar.rb`，方向切换功能才不丢。
- **方案**：
  - `MindMapTab.tsx` 重写：删除往库 DOM 注入导出按钮的 hack；控件组经 `createPortal` 渲染进 `TaskDetailDrawer` 底栏的 `#mm-ctrl-slot`（effect 内 `getElementById`，父组件 DOM 提交后才存在）。
  - 行为全部用公开 API 复刻库内置实现：`mind.scale(mind.scaleVal ± mind.scaleSensitivity)`、`mind.toCenter()`、`container.requestFullscreen()`（`scaleVal` / `scaleSensitivity` 为公开属性）；监听画布 `wheel` + rAF 同步百分比，点击百分比一键回 100%；`fullscreenchange` 后重跑 `fitInitialView` 补居中。
  - `TaskDetailDrawer` 底栏左段为插槽，右段「需要补充用例？ + 💬 继续优化」保持不变 —— **迭代唯一入口不受影响**。

---

## 9. 用户认证与多用户设计（V2）

> 目标：从"单机单用户演示"升级为"多用户 SaaS 形态"——三级角色（**访客 guest / 注册用户 user / 管理员 admin**），访客免注册按 IP 体验、数据 24h 自动回收；注册用户数据持久；管理员负责用户与访客治理。这也是面试讲点：**认证安全 + 数据权限隔离 + 多租户数据生命周期** 是测试工程师做测开/平台必备考点。


### 9.0 角色与数据生命周期总览

| 维度   | 访客 guest                                                    | 普通用户 user                                     | 管理员 admin          |
| ---- | ----------------------------------------------------------- | --------------------------------------------- | ------------------ |
| 身份来源 | 按 IP 自动创建（`guest_<ip_hash>`）                                | 注册（用户名+邮箱+密码）                                 | 首个注册用户 / 环境变量预置    |
| 登录方式 | 免登录，首次访问自动发 guest token                                     | 账号密码 → JWT                                    | 账号密码 → JWT         |
| 数据保留 | **1 天（24h）**，到期自动删除                                         | 永久（用户注销前）                                     | 永久                 |
| 文件目录 | `uploads/guest_<ip_hash>/`、`outputs/guest_<ip_hash>/`（临时目录） | `uploads/u_<user_id>/`、`outputs/u_<user_id>/` | 同 user             |
| 任务上限 | 单访客 ≤10 个任务（防滥用）                                            | 无硬限制（可配）                                      | 无限制                |
| 可见任务 | 仅自己的                                                        | 仅自己的                                          | 全部（`?all=true`）    |
| 管理能力 | 无                                                           | 无                                             | 用户管理 / 访客治理 / 全局任务 |

```
访客生命周期：
IP 首次访问 ──▶ 创建 guest 用户（expires_at = now + 24h）
            ──▶ 签发 guest JWT（role=guest，exp 与 expires_at 对齐）
            ──▶ 建临时目录 uploads/guest_<hash>/ outputs/guest_<hash>/
24h 到期（定时任务，每小时扫一次 + 访客访问时懒清理双保险）
            ──▶ 删除该 guest 的 tasks / step_logs / 上传与导出文件 / 临时目录
            ───▶ 物理删除 guest 用户记录，清理动作写入 clean_log（审计）
```

- **IP 获取**：`X-Forwarded-For`（nginx 反代场景取第一跳）> `request.client.host`；本地演示即 127.0.0.1
- **IP 哈希存目录名**（不存明文 IP，降低隐私敏感度；DB 中存 `ip_hash` 用于同 IP 复用 guest）
- **同 IP 二次访问**：若该 IP 的 guest 未过期 → 直接续发 token（数据续用）；已过期 → 新建 guest（旧数据已清）
- 访客过期判定以 `User.expires_at` 为准，guest JWT 的 `exp` 设为 `expires_at` 时刻，token 失效与数据删除同步

### 9.1 技术选型

| 组件    | 选型                                               | 理由                                              |
| ----- | ------------------------------------------------ | ----------------------------------------------- |
| 密码哈希  | `passlib[bcrypt]`                                | 业界标准，自带盐，不存明文                                   |
| Token | `pyjwt`（JWT HS256）                               | 无状态、FastAPI 生态最简；不引入 session/Redis 复杂度          |
| 鉴权方式  | `OAuth2PasswordBearer`                           | FastAPI 原生支持，`/docs` 里可直接调试探 Token              |
| 依赖新增  | `passlib[bcrypt]>=1.7` `pyjwt>=2.8` `bcrypt<4.1` | 轻量，无编译依赖（bcrypt≥4.1 与 passlib 1.7.x 不兼容，必须 pin） |

```bash
pip install "passlib[bcrypt]" pyjwt
```


### 9.2 数据模型变更

```python
# app/models/user.py
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=True)      # guest 无邮箱 → 允许 NULL
    password_hash = Column(String(128), nullable=True)           # guest 无密码 → 允许 NULL
    role = Column(String(16), default="user")                    # guest / user / admin
    ip_hash = Column(String(64), index=True)                     # 仅 guest：同 IP 复用
    expires_at = Column(DateTime, nullable=True)                 # 仅 guest：now + 24h
    data_dir = Column(String(128))                               # u_<id> / guest_<ip_hash>
    is_active = Column(Boolean, default=True)                    # 软禁用
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)

# Task 表新增字段（SQLite 迁移脚本处理）
user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

# 防滥用计数（独立于 users 表，不随 guest 删除而丢失，见下）
class GuestCreationLog(Base):
    __tablename__ = "guest_creation_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip_hash = Column(String(64), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # 只追加不删（或只清 7 天前记录），保证"单 IP 24h ≤ 5 个 guest"可验证

# 清理审计（guest 用户记录本身物理删，审计走这张表，不与 username unique 冲突）
class CleanLog(Base):
    __tablename__ = "clean_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guest_ip_hash = Column(String(64))
    deleted_tasks = Column(Integer, default=0)
    deleted_files = Column(Integer, default=0)
    trigger = Column(String(16))            # scheduler / manual / lazy（访问时懒清理）
    cleaned_at = Column(DateTime, default=datetime.utcnow)
```

- `email`/`password_hash` 改为可空：访客无邮箱无密码，靠 IP + guest token 识别
- `Task.user_id` 允许 NULL：存量任务迁移时归到引导创建的默认管理员名下，不丢数据
- `StepLog` 不加用户字段（通过 task → user 间接归属，避免冗余）
- **guest username 规则**：`guest_<ip_hash>_<seq>`（seq 按该 ip_hash 历史创建数递增，从 GuestCreationLog 取）——guest 记录**到期物理删除**，审计由 `clean_log` 承担，不再走"软删保留 7 天"路线（软删会撞 username unique，导致同 IP 重建 guest 失败）
- **防滥用计数独立建表**：`guest_creation_log` 只记录 ip_hash + 时间，不随 guest 清理删除——否则计数器随 guest 记录一起被删，"单 IP 24h ≤ 5"形同虚设；超限返回 429
- 文件隔离：上传/导出按 `data_dir` 分目录，访客临时目录随 TTL 整目录删除，注册用户目录独立互不影响


### 9.3 认证流程

```
访客（免注册体验）：
GET / 或 POST /api/guest/token ──▶ 取 IP → ip_hash
    ├─ 存在未过期 guest(ip_hash) ──▶ 直接签发 guest JWT（exp = expires_at）
    └─ 不存在/已过期 ──▶ 建 guest 用户 + 临时目录 ──▶ 签发 guest JWT
受保护接口对 guest 同样有效：guest token 一样走 get_current_user，role=guest 仅能力受限

注册用户：
注册 POST /api/auth/register ──▶ 校验用户名/邮箱唯一 ──▶ bcrypt 哈希入库（首个用户自动 role=admin）
登录 POST /api/auth/login ─────▶ 校验密码 ──▶ 签发 JWT（payload: user_id/username/role，exp 24h）
受保护接口 ──▶ Authorization: Bearer <token> ──▶ get_current_user 解码校验
                                            ├─ token 无效/过期 ──▶ 401
                                            ├─ 解码后**必须回查 DB**（不是纯无状态）：
                                            │    ├─ 用户不存在/ is_active=False ──▶ 401（禁用即时生效）
                                            │    └─ guest 且 expires_at < now ──▶ 401 + 顺带懒清理该 guest + 前端引导注册
                                            └─ 访问他人资源 ───────▶ 404（不暴露存在性）
```

**安全细节**：

- 密码强度：≥8 位且含字母+数字（注册时校验）
- JWT secret 从 `.env` 读取（`JWT_SECRET`），`.env.example` 提供占位；**启动时检测**：若为默认占位值则打 WARNING（演示可跑），生产环境拒绝启动
- 限速：登录接口失败 5 次锁 10 分钟（内存计数即可，MVP 不引 Redis；注意仅在单进程 uvicorn 下有效，多 worker 需换共享存储——MVP 明确单进程部署）
- 管理员引导：首个注册用户 = admin；后续可用环境变量 `ADMIN_BOOTSTRAP` 预置
- 访客防滥用：guest 任务上限 10 个 + 单 IP 每 24h 最多新建 5 个 guest 身份（**计数走 `guest_creation_log` 独立表，超限 429**，不随 guest 记录删除失效）
- 访客转正：guest 在过期前可「一键转正」——原 guest 的任务与文件迁入新注册用户目录，数据不丢
- **IP 信任边界**：`X-Forwarded-For` 客户端可伪造——只有部署在自管 nginx 后（nginx 重写 XFF 为真实来源）才开启 `uvicorn --proxy-headers` 解析；本地/直连部署一律用 `request.client.host`。防止公网伪造 XFF 无限刷 guest
- **依赖版本坑**：`passlib 1.7.x` 与 `bcrypt>=4.1` 组合会报 `__about__` 警告/异常，requirements 里 pin `bcrypt<4.1`；bcrypt 仅取密码前 72 字节，注册时顺带校验长度上限

**访客清理任务（TTL 24h）**：

- `app/jobs/guest_cleaner.py`，APScheduler 每小时执行（随 FastAPI 生命周期启动，不引 Celery）
- 逻辑：`DELETE FROM users WHERE role='guest' AND expires_at < now`
  - 级联删除该 guest 的 tasks / step_logs（DB 外键 ON DELETE CASCADE 或手动删）
  - `shutil.rmtree(uploads/guest_<hash>/, outputs/guest_<hash>/)`（`ignore_errors=True` 防并发占用）
- 兜底：启动时也跑一次（服务重启间隔可能超 1h）
- **懒清理**：guest 请求进来发现 `expires_at < now` 时同步执行清理再返回 401——把"过期后数据仍存活最长 1h"的窗口收窄到"该访客下次访问即清"
- 删除动作写 `clean_log` 表（admin 可见"今晨清理了 N 个访客"），面试可讲数据生命周期治理


### 9.3.1 关键实现示例（评审问题的落地代码）

**① guest username 防撞唯一约束**（seq 从 GuestCreationLog 取历史创建数，记录物理删不冲突）：

```python
seq = db.query(GuestCreationLog).filter(
    GuestCreationLog.ip_hash == ip_hash).count()
username = f"guest_{ip_hash}_{seq}"            # 永不重复
user = User(username=username, role="guest", ip_hash=ip_hash,
            expires_at=now + timedelta(hours=24),
            data_dir=f"guest_{ip_hash}_{seq}")
```

**② 防滥用计数走独立表**（guest 记录被删计数仍在，超限 429）：

```python
def get_or_create_guest(ip_hash: str, db: Session) -> User:
    # 未过期的 guest 直接续用
    guest = db.query(User).filter(User.ip_hash == ip_hash,
                                  User.role == "guest",
                                  User.expires_at > now()).first()
    if guest:
        return guest
    # 24h 窗口内创建次数（日志只追加不删，计数才有效）
    recent = db.query(GuestCreationLog).filter(
        GuestCreationLog.ip_hash == ip_hash,
        GuestCreationLog.created_at >= now() - timedelta(hours=24)).count()
    if recent >= 5:
        raise HTTPException(429, "该 IP 今日访客体验次数已用完，请注册")
    # 新建 guest + 追加创建日志
    ...
    db.add(GuestCreationLog(ip_hash=ip_hash))
```

**③ get_current_user 解码后回查 DB**（禁用即时生效 + 过期 guest 懒清理）：

```python
async def get_current_user(token: str = Depends(oauth2_scheme),
                           db: Session = Depends(get_db)) -> User:
    try:
        # 实际密钥取自配置：config.JWT_SECRET（对应 .env 的 JWT_SECRET，占位值 change-me-in-production）
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401)
    user = db.get(User, payload["user_id"])     # 每次都查库
    if not user or not user.is_active:          # admin 禁用 → 下一请求即 401
        raise HTTPException(401)
    if user.role == "guest" and user.expires_at < datetime.utcnow():
        clean_guest(user, db, trigger="lazy")   # 顺手懒清理
        raise HTTPException(401, "体验已到期，数据已清理")
    return user
```

> 代价是每请求多一次 SQLite 主键查询（微秒级，可忽略）；面试讲点：无状态 JWT 吊销难，用轻量回查折中。

**④ XFF 伪造防护（nginx 侧强制重写）**：

```nginx
# 仅自管反代场景；nginx 覆盖客户端伪造的头
proxy_set_header X-Forwarded-For $remote_addr;
```

```python
# main.py 启动兜底
if config.JWT_SECRET == DEFAULT_PLACEHOLDER:
    if config.ENV == "production":
        raise RuntimeError("生产环境必须设置 JWT_SECRET")
    logger.warning("JWT_SECRET 为默认占位值，仅限本地演示")
```


### 9.4 API 变更

| 方法     | 路径                            | 鉴权             | 说明                                                    |
| ------ | ----------------------------- | -------------- | ----------------------------------------------------- |
| POST   | `/api/guest/token`            | 无              | 按 IP 建/复用访客身份，返回 guest JWT + 剩余有效时长；超 24h/5 个上限 → 429 |
| POST   | `/api/guest/upgrade`          | Bearer + guest | 访客转注册用户（任务与文件迁移到新账户）                                  |
| POST   | `/api/auth/register`          | 无              | 注册，返回用户信息（不含 hash）                                    |
| POST   | `/api/auth/login`             | 无              | 返回 `access_token` + `token_type`                      |
| POST   | `/api/auth/change-password`   | Bearer         | 修改本人密码（旧密码校验，改后旧 token 仍有效至 exp，可接受）                  |
| GET    | `/api/auth/me`                | Bearer         | 当前用户信息（guest 含 expires_at 倒计时）                        |
| GET    | `/api/users`                  | Bearer + admin | 用户列表（含 guest，标记角色/过期时间/任务数）                           |
| PATCH  | `/api/users/{id}`             | Bearer + admin | 启用/禁用注册用户；禁用 guest = 立即清理其数据                          |
| DELETE | `/api/users/{id}`             | Bearer + admin | 删除用户（级联任务/文件；admin 本人不可删）                             |
| POST   | `/api/admin/guests/clean`     | Bearer + admin | 手动触发访客清理（返回清理数量）                                      |
| POST   | `/api/admin/guests/clean-all` | Bearer + admin | 批量清空**全部**访客及其数据（定向清理之外的兜底，初稿未列）                      |
| GET    | `/api/admin/stats`            | Bearer + admin | 统计：注册用户数 / 活跃访客数 / 24h 清理数                            |
| POST   | `/api/tasks`                  | Bearer         | 创建时写入 `user_id`，文件落 `data_dir` 目录                     |
| GET    | `/api/tasks`                  | Bearer         | **只返回当前用户的任务**（admin 可带 `?all=true` 看全部）              |
| GET    | `/api/tasks/{id}`             | Bearer         | 非本人且非 admin → 404                                     |
| GET    | `/api/tasks/{id}/download`    | Bearer         | 同上（防 URL 直链越权下载）                                      |
| GET    | `/health`                     | 无              | 保持公开                                                  |

### 9.5 前端改动（MVP 原生 HTML）

- 登录页增加「游客体验」入口：点击 → `POST /api/guest/token` → 存 token 进主页（免注册）
- 未登录访问 `/` 不再强制跳登录，而是弹「登录 / 注册 / 游客体验」三选一
- 顶栏显示身份徽标：`访客（剩余 xx 小时）` / 用户名 / `管理员`；访客顶栏常驻「注册保留数据」引导按钮
- 访客任务数达 10 个时前端 toast 提示上限并引导注册
- Token 存 `localStorage`，`fetch` 统一注入 `Authorization` 头；401 时清 token → 访客过期则提示"体验已到期，数据已清理，注册后可长期保留"
- 顶栏显示用户名 + 退出按钮；admin 用户多一个「用户管理」入口（用户/访客列表、启停、手动清理访客、统计看板）
- 任务列表页加「我的任务」筛选（admin 可切「全部」）

### 9.6 数据迁移（SQLite）

自写轻量迁移脚本 `scripts/migrate_v2.py`：

1. `ALTER TABLE tasks ADD COLUMN user_id INTEGER`（SQLite 支持 ADD COLUMN）
2. 建表 `users` / `guest_creation_log` / `clean_log`（email/password_hash 可空，含 ip_hash/expires_at/data_dir），创建默认 admin（用户名 `admin`，密码从环境变量读，默认随机生成打印一次）
3. `UPDATE tasks SET user_id = <admin_id> WHERE user_id IS NULL`
4. 已有 `uploads/`、`outputs/` 平铺文件迁入 `outputs/u_<admin_id>/`
5. 幂等：检测列已存在则跳过；**admin 已存在也跳过（不重置密码、不重复打印）**——脚本可安全重复执行

### 9.7 测试设计（本职专业度，重点写进 README）

认证模块是**接口测试实战素材**，`tests/` 补充以下用例集：

| 类别     | 用例                                                                                                                                                                  |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 注册     | 正常注册 / 用户名重复 409 / 邮箱格式非法 422 / 密码强度不足 422 / 用户名超长                                                                                                                  |
| 登录     | 正确密码 / 密码错误 401（提示模糊化）/ 用户不存在 401（与密码错误同文案，防枚举）/ 禁用用户 403 / 连续错 5 次锁定                                                                                               |
| Token  | 缺失 Authorization 401 / 格式错误 401 / 过期 401（伪造 exp 验证）/ 篡改签名 401 / **用户被禁用后存量 token 立即 401（DB 回查生效）**                                                                  |
| 越权     | 用户 A 访问用户 B 的任务详情 404 / 越权下载他人导出文件 404 / user 调 admin 接口 403 / guest 调 admin 接口 403                                                                                 |
| 防滥用    | 同 IP 24h 第 6 个 guest → 429（guest 记录已删计数仍在）/ guest 第 11 个任务 → 429 / 伪造 X-Forwarded-For 在直连部署下不影响判 IP                                                                 |
| 并发     | 同一账号并发登录多端 token 互不影响（无状态 JWT 天然支持）                                                                                                                                 |
| 访客生命周期 | 同 IP 首访自动建 guest / 同 IP 二访复用同一 guest / 不同 IP 各自 guest 互相隔离 / guest 任务上限第 11 个 429 / 过期 guest token 401 / 过期 guest 数据与临时目录被清理 / 清理后同 IP 再访新建 guest / 访客转注册后任务与文件完整迁移 |
| 访客清理任务 | 到期 guest 的 tasks/step_logs 被级联删 / 临时目录（上传+导出）被删 / 未到期 guest 不被误删 / 服务重启后清理兜底执行 / admin 手动清理接口生效并返回数量 / **懒清理：过期 guest 携旧 token 访问 → 401 且数据同步被清**                   |
| 角色权限矩阵 | 三角色 × 核心接口状态码全组合（guest/user/admin × 8 接口 = 24 条断言，表驱动参数化跑）                                                                                                          |

### 9.8 实施拆分（V2 迭代）

- T14：User 模型（三级角色 + guest 字段）+ GuestCreationLog/CleanLog 表 + 注册/登录/改密 API + bcrypt/JWT（0.5 天）
- T15：get_current_user 依赖（**每请求回查 DB：is_active + guest expires_at**）+ 存量接口加鉴权 + 任务按 user_id 过滤 + 文件按 data_dir 分目录（0.5 天）
- T16：访客身份：`POST /api/guest/token`（IP→ip_hash 建/复用 guest）+ guest 任务上限 + 单 IP 24h 5 个身份上限（guest_creation_log）+ 访客转注册迁移（0.5 天）
- T17：访客清理：APScheduler 每小时清理过期 guest（DB 级联 + 临时目录删除 + clean_log 审计）+ 启动兜底 + 访问时懒清理 + admin 手动清理接口（0.5 天）
- T18：迁移脚本 + 引导 admin（0.5 天）
- T19：前端：登录/注册/游客体验三入口 + 401 拦截 + 顶栏身份徽标与到期倒计时（0.5 天）
- T20：登录限速 + admin 用户管理页（用户/访客列表、启停、统计看板）（0.5 天）
- T21：认证/越权/访客生命周期/角色矩阵测试用例集 + README 更新（0.5 天）

### 9.9 验收标准

- [ ] 未带 Token 访问 `/api/tasks` → 401；注册登录后可正常提交任务
- [ ] 用户 A 无法看到/下载用户 B 的任何任务（404）
- [ ] admin 可查看全部任务、管理用户启停、手动清理访客、看到统计
- [ ] 密码哈希入库（非明文）、JWT 过期自动登出
- [ ] **访客：同 IP 免登录自动获得身份；数据/文件隔离在 guest\_<ip_hash> 临时目录；24h 后任务、日志、文件全部自动删除且同 IP 再访是全新身份**
- [ ] 访客任务上限 10 个生效；同 IP 24h 第 6 个 guest 身份 → 429；访客转注册后数据完整迁移不丢
- [ ] admin 禁用用户后，该用户**存量 token 立即 401**（get_current_user 回查 DB）
- [ ] 越权/认证/访客生命周期测试用例集 ≥ 30 条且全部通过
- [ ] 存量 SQLite 数据迁移后任务不丢失
