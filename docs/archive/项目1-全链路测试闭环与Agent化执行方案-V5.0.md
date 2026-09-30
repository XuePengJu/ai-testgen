# 项目1-全链路测试闭环与 Agent 化执行方案-V5.0

> 状态：方案已评审定稿（2026-09-23），作为后续迭代的唯一依据，迭代时只补充实现细节，不重新出方案。
> **执行状态（2026-09-24 更新）**：**M0-M5 六期全部完成交付**。M4（V2 自愈循环）、M5（V3 探索式 Agent）于 2026-09-24 交付（代码+单测全过；真实 LLM 站点级验收待有效 API key）。详见第 12 节「执行状态与增量交付记录」。
> 叙事定位：从「AI 用例生成平台」升级为「AI 测试闭环平台」，并给出 Workflow → Agent 的演进路线。

## 1. 背景与目标

用户给一个被测 Web 系统 URL（可选：需求文档附件、登录账号密码）→ AI 自动抓取页面 → 自动生成测试用例（复用现有 4-Agent 链）→ 自动生成 Playwright(Python) 脚本 → 自动执行 → 生成执行报告 → 前端显示自动化执行列表与状态，点击查看报告（含截图、错误详情）。

另设 **M0 平台自身质量量化**（方案B：产品内质量看板）：一个以测试为核心的平台，自己的质量必须拿得出数字——把仓库里已有的 pytest（约 208 个测试函数）与 Node Playwright e2e（5 个脚本）的运行结果量化为结构化数据，并在平台产品内可视化。

### 已确认决策
| 决策点 | 结论 |
|---|---|
| 自动化栈 | Python Playwright + pytest（脚本 LLM 生成，执行器 subprocess 跑 pytest） |
| 执行环境 | 先本机 Mac 跑通，runner 留 server 扩展位 |
| 一期范围 | 全链路 MVP（浅层抓取 ≤8 页），深爬二期 |
| 登录态 | 支持账号密码登录（发起时选填，抓取与执行均注入） |
| 平台选型 | **不上 Dify**——本地 subprocess/浏览器生命周期/文件系统集成 Dify 沙箱做不了；自研编排是作品集核心含金量 |
| Agent 框架 | 一期手写循环；V3 循环复杂后可演进 LangGraph（延续 docs/项目1-LangChain迁移执行方案.md） |
| 自身质量量化 | **方案B：产品内质量看板**（管理页新 Tab + 运行入口 + 趋势），独立小期 M0，可与 M1 并行 |

### 现状缺口（已核查代码）
- 全仓库无爬虫/HTML 解析代码（httpx 仅用于 LLM 调用）；doc_extract 只吃本地文件
- 无 Python Playwright（仅 Node e2e 自测脚本）；app/ 内无任何 subprocess 执行设施
- 执行记录/执行报告不存在；Task.report_json 有值但 TaskOut 未暴露
- 平台自身测试只有终端输出：pytest 约 208 个测试函数、Node e2e 5 个 .mjs 脚本，均无量化产物
- 可复用：对话+任务编排骨架、StepLog 进度、线程队列模式、RAG 管道、zustand+轮询+Drawer Tab 模式

### 演进总路线
- **M0 = 平台自身质量量化**（独立小期）：pytest/e2e 结果采集 → 聚合 → 管理页质量看板，先行垫定"测试为核心"叙事
- **M1-M3（一期）= AI Workflow**：固定流水线（抓取→用例→脚本→执行→报告），先把闭环地基打牢
- **M4（V2 自愈循环）= Agent 化第一步**：执行失败后 LLM 诊断→改脚本→自动重跑，失败处理权从人移交给 LLM
- **M5（V3 探索式 Agent）= 全面 Agent 化**：LLM 持浏览器工具集自主探索站点，边探索边生成用例边执行

---

## 2. M0 平台自身质量量化（方案B：产品内质量看板）

> 执行状态：✅ **已完成（2026-09-23 交付）**。实际落地为**方案A**：独立「质量报告」页面（`frontend/src/pages/QualityPage.tsx`），对所有登录角色可见（含访客），「运行测试」按钮仅 admin 显示，后端 `/api/quality/*` 仍 admin only 双重把关——与原方案B（AdminPage 内 admin 专属 Tab）形态不同，功能等价且可见性更广，详见第 12 节。

### 2.1 目标与边界
把平台自身两套测试（Python pytest + Node e2e）的运行结果量化为结构化数据，在**平台产品内**（管理页「质量看板」Tab）展示并可触发运行。**边界**：量化对象是平台自身测试，与 M1-M3 的"被测系统执行报告"（ExecutionRun）完全独立，互不复用表结构。

### 2.2 分层设计

**① 采集层**（同方案A基建，方案B的数据底座）
- pytest：`pytest --json-report --json-report-file=quality_data/pytest-report.json --cov=app --cov-report=json`（pytest-json-report + pytest-cov）
- Node e2e：新增轻量 runner `frontend/scripts/run-e2e.mjs`——逐个执行 e2e-m1~m5.mjs，try/catch 捕获退出码与耗时，产出 `quality_data/e2e-report.json`（每套件：name/outcome/duration_ms/error）

**② 聚合层** `scripts/quality/aggregate_quality.py`
- 汇总两份原始 JSON → `quality_data/quality-summary.json`：
  ```
  { pytest: {total, passed, failed, skipped, duration_ms, coverage_pct, by_file: {test文件: {total, passed}}},
    e2e: {suites: [{name, outcome, duration_ms}], total, passed},
    generated_at }
  ```
- 每次聚合 append 一行到 `quality_data/history.jsonl`（趋势图数据源；`quality_data/` 加入 .gitignore，历史数据本地留存）

**③ API 层** `app/api/quality.py`（**admin only**，质量数据面向维护者）
| 方法 | 路径 | 职责 |
|---|---|---|
| GET | `/api/quality/summary` | 读最新 quality-summary.json（无文件返回空态提示） |
| POST | `/api/quality/run` | 后台触发"跑 pytest + e2e + 聚合"（subprocess，复用执行队列模式单 worker；running 时 409） |
| GET | `/api/quality/run/status` | 当次运行状态（running/completed/failed + 各阶段进度文案） |
| GET | `/api/quality/history` | 读 history.jsonl，返回趋势数组（按时间升序，最多近 30 次） |

**④ 前端**：AdminPage 加「质量看板」Tab（`frontend/src/components/admin/QualityBoard.tsx`）
- 顶部汇总卡 4 枚：pytest 用例数 / pytest 通过率 / 代码覆盖率 % / e2e 套件通过率
- 按测试文件的用例分布条形图（纯 CSS/SVG，不引图表库）
- 历史趋势折线（通过率 + 覆盖率，读 /history）
- 「▶ 运行测试」按钮 → POST /api/quality/run → 2s 轮询 status → 完成刷新汇总
- failed 时展示失败用例清单（test 文件/用例名/报错摘要）

### 2.3 依赖与配置
- requirements.txt 追加：`pytest-json-report`、`pytest-cov`（与一期 2.6 合并安装）
- config.py 新增：`QUALITY_DATA_DIR`（默认 `quality_data/`）、`QUALITY_EXEC_TIMEOUT=900`
- 运行前提：本机已 `playwright install chromium`（e2e 用）；Node 环境复用 frontend/node_modules

### 2.4 验收标准
- 管理页质量看板展示真实数据：pytest 用例数/通过率/覆盖率、e2e 套件结果均为实测值而非写死
- 点「运行测试」→ 后台真实执行 → 完成后数字更新，history.jsonl 追加一行，趋势图多一个点
- 非 admin 访问 /api/quality/* 返回 403
- curl 全链路验证：run → status 轮询 → summary 数据一致

---

## 3. 一期设计（M1-M3）

> 执行状态：✅ **M1/M2/M3 全部完成（2026-09-23/24 交付）**。2026-09-24 增量交付（crawler 修复、探索可视化、探索录屏、e2e 自动建会话）详见第 12 节。

### 3.1 数据模型（`app/models/automation.py`）
- `TestTarget`：id/user_id/name/base_url/username/password_enc(Fernet 加密，密钥派生自 JWT_SECRET)/notes/created_at
- `ExecutionRun`：id/task_id(FK)/user_id/trigger/status(pending|running|completed|failed)/progress/total/passed/failed/skipped/duration_ms/report_json(Text)/auto_dir/error/created_at/started_at/finished_at
- `Task` 仅加 1 字段：`target_id: str | None`；复用为编排容器（`kind="e2e"`）
- 注册进 `app/models/__init__.py`，走 `init_db()` create_all 自动建表（SQLite/MySQL 双方言零迁移）

### 3.2 新 API（`app/api/automation.py`）
| 方法 | 路径 | 职责 |
|---|---|---|
| POST/GET | `/api/targets` | 创建/列出被测系统（密码加密，永不回传） |
| GET/DELETE | `/api/targets/{id}` | 详情/删除（校验属主） |
| POST | `/api/tasks/{task_id}/run-auto` | 触发执行：建 ExecutionRun → 入执行队列；已有进行中 run 返回 409 |
| GET | `/api/tasks/{task_id}/executions` | 执行记录列表（新→旧） |
| GET | `/api/executions/{run_id}` | 执行详情（含结构化报告；running 带实时 progress） |
| POST | `/api/executions/{run_id}/retry` | 复制新 run 重跑 |
| GET | `/api/executions/{run_id}/files/{path}` | 报告附属文件（截图/trace），路径白名单限定 shots/、trace/ |

`app/api/tasks.py`：create_task 支持 `kind=e2e` + `target_id` 或内联 url/username/password；TaskOut 加 `target_id`、`has_auto`。

### 3.3 后端新模块
1. **抓取服务 `app/services/web_crawler.py`**：httpx 抓入口页 → BeautifulSoup 解析 `<a>` → 同域去重 BFS ≤8 页；「可见文本<200字且无表单」判定 JS 渲染 → Playwright 渲染降级；每页抽结构化 PageDesc（title/nav/forms/links/text_digest）；有账号密码且存在 password 表单 → 登录并 `storage_state` 存任务目录复用；输出 `to_markdown()` 喂 ParserAgent。
2. **脚本生成 Agent `app/workflow/agents/scripter_agent.py`**：prompt 硬约定：page fixture、get_by_role/get_by_label 选择器、expect() 断言、每用例一个 `test_{case_id}` 函数；按 module 分批（≤8 条/批）；ast.parse 语法校验失败重试 1 次；conftest.py 模板固化在代码里（BASE_URL、storage_state 注入、失败自动截图），**不由 LLM 生成**。
3. **执行 Runner `app/services/auto_runner.py` + `app/core/exec_queue.py`**：照抄 task_queue 模式但 1 个 worker；subprocess 跑 `pytest --json-report --browser=chromium`，cwd 锁 auto 目录、timeout 600s、no shell、env 白名单、凭据走环境变量不落盘；解析 pytest-json-report → 组装 report_json（summary + 每用例 outcome/error/截图相对路径）→ run 终态落库。

### 3.4 工作流接入（`app/workflow/engine.py`）
```
STEPS_E2E = [crawler 抓取页面 → parser 解析规格 → generator AI生成用例
           → reviewer 质量校验 → scripter 生成脚本 → exporter 导出文件]
kind == "e2e" 时走 STEPS_E2E，否则现有 STEPS
```
前端 `TaskStepsCard.tsx` 硬编码 STEP_TITLES 改数据驱动（按后端 steps[].title 渲染 + 新步骤图标映射），存量 4 步任务渲染不变。

### 3.5 前端
- 发起：TaskList 头部「🌐 全链路测试」按钮 → `E2ETaskModal.tsx`（选已有 target 或现场填 URL+账密+可选附件）→ POST /api/tasks(kind=e2e)，不侵入对话流
- 执行+报告：TaskDetailDrawer 第 4 个 Tab「自动化」（仅 e2e/has_auto 显示）→ `ExecutionPanel.tsx`：执行按钮 / 执行列表（状态徽标+通过率+耗时，running 2s 轮询）/ 点击 run 内嵌报告视图（汇总条 + 用例明细 + 截图 lightbox）
- 修改：client.ts、types.ts、taskStore.ts、App.tsx（Modal 挂载）

### 3.6 依赖与配置
- requirements.txt 追加：`beautifulsoup4` `playwright` `pytest` `pytest-playwright` `pytest-json-report`
- 初始化：`pip install -r requirements.txt && playwright install chromium`
- config.py 新增：`AUTO_MAX_PAGES=8`、`AUTO_EXEC_TIMEOUT=600`、`EXEC_WORKERS=1`

---

## 4. V2 自愈循环设计（M4）——Agent 化第一步

> 执行状态：✅ **已完成**（2026-09-24，见 12.3）。`ExecutionRun` 已有 `heal_round`/`heal_log` 字段（app/models/automation.py），`app/services/auto_healer.py` 已建（438 行）；pytest 273 passed。遗留：LLM 真调验收待有效 API key（诊断链路已用 FakeLLM 全链路验证）。

### 4.1 目标与边界
执行失败后，LLM 自动完成「读报错+失败截图 → 诊断根因 → 改写脚本 → 重跑」循环（≤3 轮），把失败处理权从人移交给 LLM。**边界：只修脚本（选择器/等待/定位），绝不放松断言**——防止把真 bug 修没了。

### 4.2 数据模型变更
`ExecutionRun` 加 2 个字段：
- `heal_round: int = 0`（已进行的自愈轮次）
- `heal_log: Text`（JSON 数组，每轮一条：`{round, suspects:[{case_id, error, screenshot}], diagnosis, changed_files:[], rerun_outcome}`）

不新增表、不新增 run 记录——自愈在同一 run 内循环，前端 progress 实时显示「自愈第 N 轮」。

### 4.3 新模块 `app/services/auto_healer.py`
```
heal(run, failed_items, auto_dir, llm_client) -> HealResult
```
单轮流程：
1. **取证**：收集每个失败用例的 pytest 报错全文 + 失败截图 + 对应脚本源码片段 + 当时的页面快照描述（PageDesc 已存任务目录）
2. **诊断**（LLM，prompt 硬约束）：判定根因类别——`selector`（选择器失效/页面结构变化）/ `timing`（等待不足）/ `env`（环境/数据问题，不可修）/ `product_bug`（断言失败但页面行为符合需求描述，疑似真缺陷）；**禁止输出"修改/删除断言"类修复**
3. **修复**：`selector`/`timing` 类 → LLM 输出修复后的完整函数/文件 → `ast.parse` 校验 → 覆盖写回 auto 目录（改动前旧文件备份到 `auto/heal_backup/round{n}/`）
4. **重跑**：只重跑失败的用例（pytest 指定 node id，省时间）
5. **收敛判定**：全部通过 → run=completed，heal_log 记录各轮明细；`product_bug`/`env` 或轮次耗尽 → run=completed（带 `suspected_bugs` 列表进 report_json），不再重试

触发方式：`POST /tasks/{id}/run-auto` 加 Form 参数 `auto_heal: bool = True`；报告 JSON 顶层加 `heal: {rounds, log, suspected_bugs[]}`。

### 4.4 前端
ExecutionPanel 报告视图：run 行加「自愈 N 轮」徽章；报告内新增「自愈过程」折叠区（每轮：诊断结论 / 改了哪个文件 / 重跑结果）；`suspected_bugs` 独立高亮区块（测试用例视角的产出物，可复制进缺陷记录）。

### 4.5 验收标准
- demo 页故意用错选择器 → 系统 ≤3 轮内自愈通过，heal_log 有完整链路
- demo 页故意写错业务预期（真缺陷场景）→ 系统不修改断言，标记 suspected_bug 并停止
- 自愈全程 StepLog/progress 可观测，重跑只跑失败用例

---

## 5. V3 探索式测试 Agent 设计（M5）——全面 Agent 化

> 执行状态：✅ **已完成**（2026-09-24，见 12.3）。`app/services/explorer_agent.py` 已建（~660 行，ReAct 循环 + 7 工具 + 三重护栏）；前端探索时间线/发起入口已交付。实现偏差：LLM 决策采用等价 JSON 协议（详见 12.3）；遗留：真实站点 ≥10 用例全链路验收待有效 API key。

### 5.1 目标与定位
零文档站点：只给 URL（+可选账号密码），LLM 持浏览器工具集自主探索——感知页面→决策测什么→生成用例→（复用 M2）生成脚本→执行→报告。从「固定流水线」变为「ReAct 感知-决策循环」。

### 5.2 架构：ReAct 循环 Runner
新模块 `app/services/explorer_agent.py` + 独立任务类型 `kind="explore"`（复用任务队列，StepLog 记录每步）：

```
while not done and steps < MAX_STEPS(30):
    obs = snapshot(page)          # accessibility tree 摘要 + 元素 ref 编号（省 token）
    action = llm.decide(history, obs, goal)   # function calling 选工具
    result = execute_tool(action) # 沙箱内执行，域名锁定
    history.append(action, result)
    若 LLM 认为某功能流已探索充分 → 调 submit_cases(cases) 提交用例
```

### 5.3 工具集（OpenAI function calling，llm_service 已兼容）
| 工具 | 职责 |
|---|---|
| `browser_navigate(url)` | 跳转（域白名单 = target 域，越域拒绝） |
| `browser_click(ref)` | 点击元素（ref 来自最近一次 snapshot） |
| `browser_fill(fields)` | 填表单（含登录：首步自动完成登录后 storage_state 复用） |
| `browser_snapshot()` | 返回页面可交互元素摘要（role/name/ref），不返回原始 HTML |
| `browser_screenshot()` | 截图存档，供用例证据与报告 |
| `browser_back()` | 后退 |
| `submit_cases(cases)` | 提交结构化用例（复用现有 case schema），触发 M2 脚本生成与执行 |

### 5.4 护栏
- 域名锁定（越域工具调用直接拒绝并反馈 LLM）；危险操作黑名单（含「删除/支付/提交订单」等文案的按钮点击需 LLM 二次确认理由，一期直接黑名单拒绝）
- 预算控制：MAX_STEPS=30、单任务 token 上限、总时长上限；超限优雅收敛（已提交用例照常走下游）
- 每步落 StepLog（name=explore，progress=当前页面+动作），前端可见探索时间线

### 5.5 前端
任务详情「自动化」Tab 复用；explore 任务新增「探索过程」时间线（每步：截图缩略 + 动作 + LLM 理由），报告视图与 M3 相同。

### 5.6 LangGraph 演进判据（何时迁移）
出现以下任一情况再迁移，否则手写 while 循环足够：
1. 循环内需多角色并行（探索 Agent + 用例评审 Agent 协作）
2. 需要人机协同中断/恢复（human-in-the-loop 暂停确认）
3. 状态机分支复杂到难以单文件维护
迁移方式：把 explorer 循环改为 LangGraph StateGraph（节点=工具执行/LLM 决策，边=条件路由），llm_service 不动。

### 5.7 验收标准
- 对一个全新演示站点零文档发起 → Agent 自主探索产出 ≥10 条用例 → 自动生成脚本执行 → 报告含探索时间线
- 越域/危险操作被正确拦截；预算超限能收敛并交付已产出内容

---

## 6. 版本规划与验收

| 期 | 内容 | 验证方式 | 状态（2026-09-24） |
|---|---|---|---|
| **M0 质量看板** | quality 采集/聚合/聚合 API + 质量看板 + 运行入口 + 趋势 | 2.4 节验收标准 | ✅ 已完成（落地为方案A独立页） |
| M1 抓取+生成 | TestTarget/targets API + web_crawler + STEPS_E2E + TaskStepsCard 数据驱动 + E2ETaskModal | e2e 任务指向公网站点 → 产出用例 + 六步 StepLog 可观测 | ✅ 已完成（含 09-24 探索可视化增量） |
| M2 脚本+执行 | scripter_agent + conftest 模板 + exec_queue + auto_runner + run-auto/executions API | 点执行 → chromium headless 跑通 → ExecutionRun.report_json 可读；demo 页含故意失败断言验证截图/错误采集 | ✅ 已完成 |
| M3 前端闭环 | Drawer 自动化 Tab + ExecutionPanel 报告视图 + 截图文件接口 | 纯 UI 走通：发起→生成→执行→看报告→重试 | ✅ 已完成 |
| M4 自愈循环 | auto_healer + ExecutionRun.heal 字段 + 报告自愈区 + suspected_bugs | 4.5 节验收标准 | ✅ 已完成（代码+单测；LLM 真调验收待 key，见 12.3） |
| M5 探索式 Agent | explorer_agent + 工具集 + 护栏 + 探索时间线 | 5.7 节验收标准 | ✅ 已完成（代码+单测+真调冒烟；站点级验收待 key，见 12.3） |

注：M0 独立于 M1-M3 主线，可并行/先行；M1 完成后界面即有「URL→自动出用例」可见功能，M3 收口完整闭环。

## 7. 风险汇总
1. LLM 生成选择器脆弱 → 页面结构化描述 + get_by_role 约定 + 失败截图，M4 自愈兜底
2. 自愈误掩盖真 bug → prompt 硬约束「不许放松断言」+ product_bug 判定 + heal_backup 可回溯
3. 被测站反爬/不可达 → crawler 步骤显式 failed + 中文错误文案
4. subprocess 安全 → cwd 锁定/no shell/timeout/env 白名单/凭据不落盘
5. 探索 Agent 跑飞/危险操作 → 域名锁定 + 操作黑名单 + 预算上限
6. 资源竞争 → 执行 worker=1（被测执行与质量运行共用队列时串行）
7. 密码安全 → Fernet 加密、API 永不回传
8. 质量看板数据真实性 → 全部来自实测聚合 JSON，禁止写死展示值；运行失败显式 failed 不出假数据
9. **执行期数据污染**（2026-09-23 追加，见第 11 节）→ 本轮唯一命名（`run_tag`）+ owned-record 作用域 + 台账化清理；改删只作用于本轮自建记录，找不到即 skip；清理失败如实暴露为 `partial` 并列出残留，不假装干净

## 8. 关键文件清单（累计）
**新增**：app/models/automation.py、app/schemas/automation.py、app/api/automation.py、app/api/quality.py(M0)、app/services/web_crawler.py、app/services/auto_runner.py、app/services/data_cleaner.py(第11节)、app/services/auto_healer.py(M4)、app/services/explorer_agent.py(M5)、app/core/exec_queue.py、app/workflow/agents/scripter_agent.py、scripts/quality/aggregate_quality.py(M0)、frontend/scripts/run-e2e.mjs(M0)、frontend/src/components/admin/QualityBoard.tsx(M0)、frontend/src/components/task/ExecutionPanel.tsx、frontend/src/components/chat/E2ETaskModal.tsx
**修改**：app/models/__init__.py、app/api/tasks.py、app/workflow/engine.py、app/workflow/agents/templates/scripter_case.txt(第11节)、main.py、app/core/config.py、requirements.txt、AdminPage.tsx(M0)、TaskDetailDrawer.tsx、TaskStepsCard.tsx、taskStore.ts、client.ts、types.ts、App.tsx

## 9. 执行约定
- 每期完成本地实测（pytest 全绿 + curl 端到端 + 浏览器截图）后向老板汇报；**不经允许不部署云端**
- 迭代时：按本期章节实现，实现细节变化直接更新本文档对应章节并注明日期

## 10. 并发执行计划与依赖

依赖关系（→ 表示阻塞）：
```
M0 ┐
M1 ┘ → M2 → ┬ M3 → M5
            └ M4 ┘
```

| Wave | 可并发任务 | 启动条件 |
|---|---|---|
| **Wave 1（立即）** | M0 ∥ M1 | 无；两任务代码区域不重叠（M0 摸 pytest/quality/admin，M1 摸 web_crawler/engine/e2e modal） |
| Wave 2 | M2 | M1 完成（需 Task.target_id + cases） |
| Wave 3 | M3 ∥ M4 | M2 完成（M3 需 ExecutionRun API+report_json；M4 需执行+失败截图；两者前端/后端分工可并行） |
| Wave 4 | M5 | M2+M3 完成（复用脚本生成+执行，需前端报告视图） |

并发边界：
- M0 与 M1 共用 requirements.txt 安装（pytest-json-report / playwright 等）—— 先一次性装好避免互相踩
- M3（前端）与 M4（后端 auto_healer）在 Wave 3 并行时，ExecutionRun 表的 heal_round/heal_log 字段由 M4 加，M3 前端先按「字段可选」处理，避免 M4 未完成时 M3 卡死
- M5 探索 Agent 复用 M2 的 scripter+auto_runner 与 M3 的报告视图，必须在 Wave 4

已在项目 todo 系统创建 M0-M5 共 6 个任务（tag：全链路闭环），优先级：M0/M1/M2=high、M3/M4=medium、M5=low；依赖关系见上表，按 Wave 推进。
**执行进度（2026-09-24）**：Wave 1（M0 ∥ M1）、Wave 2（M2）、Wave 3 的 M3 均已完成；Wave 3 的 M4 与 Wave 4（M5）待启动。

---

## 11. 改进点增补：测试数据隔离与自动清理（2026-09-23 增补，M2 增量）

> 状态：**待评审，未落地**（2026-09-24 核对：`ExecutionRun` 无 `run_tag`/`cleanup_json` 字段、`TestTarget` 无 `delete_strategy` 字段、`app/services/data_cleaner.py` 未建，scripter prompt 亦无第 7/8/9 条约定）。来源为对标外部开源项目 LayaPilot 的 owned-record 机制，用于填补本文档在「执行期数据安全」上的空白。
> 详细方案：`docs/项目1-测试数据隔离与自动清理改进方案-V1.0.md`（本节仅为摘要与本计划的接入声明，冲突时以该文档为准）。

### 11.1 为什么增补

本文档原 8 条风险与全部设计章节均**未覆盖执行期数据安全**：`ExecutionRun` 无本轮唯一标识，`scripter_case.txt` 第 6 条反而要求「输入数据严格按用例 test_data 填写」，conftest 模板无任何数据准备/清理 hook。后果是被测系统（DBERP 进销存等真实业务库）会被写入固定值测试数据 —— 用例不幂等、改删类用例可能命中真实数据、跑完不清理、M4 自愈重跑使脏数据按轮次倍增。

**成本窗口**：M2 执行链路已动工，此刻落地仅为「2 字段 + 1 模板函数 + 1 执行阶段」；待 M3/M4/M5 铺开后再补需三处联动改造。

### 11.2 设计摘要

- **三条原则**：自建 → 自改 → 自删；记账优先于清理；清理失败 ≠ 执行失败
- **`ExecutionRun` +2 字段**：`run_tag`（本轮唯一标识，幂等复用）、`cleanup_json`（清理台账）
- **`TestTarget` +1 字段**：`delete_strategy`（`ui` / `api` / `none`，none 时报告显式声明残留）
- **执行器**：起跑生成并落库 `run_tag` → 注入 `AITF_RUN_TAG` → 主 pytest 结束后**无条件**执行独立清理阶段 → `report_json` 顶层新增 `cleanup` 段（与 M4 的 `heal` 段并列）
- **conftest 模板**：新增 `run_tag` fixture、`unique_name(prefix)`、`register_created` / `register_deleted` 记账函数（模板仍由代码生成，不经 LLM）
- **scripter prompt**：新增第 7/8/9 条硬约定（数据必须由 `unique_name()` 派生、改删必须按本轮唯一名过滤、找不到即 `skip` 绝不退化为取列表第一条）
- **与 M4 衔接**：自愈**复用同一 `run_tag`**，每轮重跑前做局部清理，收敛判定只认断言不看清理
- **与 M3 衔接**：报告视图新增「测试数据」区块（创建 N / 已清理 M / 残留 K，K>0 高亮），字段按「可选」渲染以免卡住并行开发

### 11.3 对本文档既有章节的覆盖与增补声明

| 本文档位置 | 变更性质 | 内容 |
|---|---|---|
| 3.1 数据模型 | **增补** | `ExecutionRun` 追加 `run_tag`、`cleanup_json`；`TestTarget` 追加 `delete_strategy` |
| 3.3.3 执行 Runner | **增补** | 新增清理阶段与 `app/services/data_cleaner.py`；`report_json` 顶层新增 `cleanup` 段 |
| 3.3.2 脚本生成 Agent | **增补** | conftest 模板新增 4 个记账/fixture 元素；prompt 第 6 条改为「按字段语义填写，值经 `unique_name()` 派生」，并新增第 7/8/9 条 |
| 3.6 依赖与配置 | **增补** | `AUTO_DATA_ISOLATION=True`、`AUTO_CLEANUP=True` |
| 3.2 新 API | **增补** | `POST run-auto` 新增可选 Form 参数 `cleanup: bool = True`；`ExecutionRunOut` 新增 `run_tag` / `cleanup`（均可选，向后兼容） |
| 4.3 自愈循环 | **约束** | 自愈必须复用同一 `run_tag`；重跑前局部清理；收敛判定不含清理结果 |
| 6 版本规划 | **归属调整** | 本节内容并入 **M2**（不新增期、不新增 Wave）；前端「测试数据」区块并轨 **M3** |
| 7 风险汇总 | **追加第 9 条** | 执行期数据污染 —— 应对：本轮唯一命名 + owned-record 作用域 + 台账化清理，清理失败如实暴露为 `partial` |
| 8 关键文件清单 | **追加** | 新增 `app/services/data_cleaner.py` |
| 10 并发执行计划 | **不变** | 仍在 Wave 2（M2 窗口内），不改变现有依赖链 |

### 11.4 契约前置

落地前**必须**先增补 `docs/contract-m2-execution.md` 第 1 / 2 / 4 节（产物目录新增 `cleanup_ledger.jsonl`、`report_json` 新增 `cleanup` 段、`auto_runner` 新增清理阶段），并经 m2-engine / m2-scripter / m3-scaffold 三方确认 —— 禁止单方面偏离契约。

### 11.5 验收标准

见改进方案文档第 5 节 6 条，其中前 4 条为 M2 窗口内必须通过项：
1. 同一任务连跑 2 次，被测系统本轮记录残留为 0，第二次不被名称重复阻塞
2. 用例 test_data 写死「张三」，实际写入值为 `张三_AITF_...` 形态
3. 列表含 5 条同名真实数据时，改删类用例只命中本轮记录
4. 清理失败时 `cleanup.status="partial"`、`leftovers` 非空、run 仍为 `completed`

### 11.6 执行约定

沿用第 9 节：每期完成本地实测（pytest 全绿 + curl 端到端 + 浏览器截图）后向老板汇报；**不经允许不部署云端**。

---

## 12. 执行状态与增量交付记录（2026-09-24 更新）

> 本节为实际交付台账，与上文各期设计章节的「执行状态」标注同步维护；实现细节变化直接更新本节，不重写方案。

### 12.1 各期完成情况

| 期 | 状态 | 交付日 | 关键产物核对 |
|---|---|---|---|
| M0 平台自身质量量化 | ✅ 完成 | 2026-09-23 | `app/api/quality.py`（summary/run/run/status/history 4 端点）、`frontend/src/pages/QualityPage.tsx`（方案A独立页，全登录角色可见）、`frontend/src/components/admin/QualityBoard.tsx` |
| M1 全链路（抓取+生成） | ✅ 完成 | 2026-09-23 | `app/services/web_crawler.py`（546 行）、`app/workflow/engine.py` STEPS_E2E 六步、`E2ETaskModal.tsx`、`TaskStepsCard.tsx` 数据驱动 |
| M2 脚本+执行引擎 | ✅ 完成 | 2026-09-23 | `app/workflow/agents/scripter_agent.py`（conftest 模板代码固化 + ast.parse 校验重试）、`app/services/auto_runner.py`、`app/core/exec_queue.py`、`app/models/automation.py`（TestTarget/ExecutionRun） |
| M3 前端闭环 | ✅ 完成 | 2026-09-23 | `ExecutionPanel.tsx`（669 行）、`app/api/automation.py` 12 端点（含 retry 与 files 白名单接口） |
| M4 自愈循环 | ✅ 完成 | 2026-09-24 | `app/services/auto_healer.py`（438 行：四分类诊断/断言保护/heal_backup/只重跑失败 node）、`ExecutionRun.heal_round`/`heal_log`（db.py `_ensure_columns` 幂等迁移）、`run-auto` `auto_heal` 参数、`HealSection.tsx`（自愈折叠区+疑似缺陷警示区+徽章）、tests/test_auto_heal.py 5 用例 |
| M5 探索式 Agent | ✅ 完成 | 2026-09-24 | `app/services/explorer_agent.py`（~660 行：ReAct+7 工具+域名锁定+危险操作黑名单+三重预算收敛）、`STEPS_EXPLORE` 三步流水线、tasks.py kind=explore（复用会话兜底）、`ExploreTimeline.tsx`（探索时间线）、`E2ETaskModal` 类型切换、tests/test_explorer_agent.py 17 用例 |
| 第 11 节数据隔离 | ⬜ 未落地 | — | 无 run_tag/cleanup_json/delete_strategy 字段，`data_cleaner.py` 未建 |

验收基线（2026-09-24 复核更新）：pytest **348 passed, 0 failed**（231 → 249 → 251 → 273 → 348，M4/M5 合入后全量实测）、前端 tsc 零错误、真实 DBERP 站点联调通过（8 页 + 8 张截图 + 256KB 探索录屏）、M5 LLM 真调冒烟通过（glm-4.5-flash 自主探索 8 步产出 3 用例 + 8 步骤截图）。

### 12.2 2026-09-24 增量交付

**a) crawler 修复与环境自适应**
- httpx 登录修复：hidden 字段（如 CSRF token）保留页面原值提交，原实现清空导致 DBERP 登录失败（`web_crawler.py` `_try_login`，注释「关键修复：hidden 字段保留原值」）
- 新增 Playwright 真实登录 + 登录后同域 BFS 探索 + 每页 full_page 截图（`_explore_with_playwright`）
- 环境自适应降级：Playwright 未装 / 浏览器缺失 / 启动失败 → 回退 httpx 静态抓取（不崩）；root 环境自动加 `--no-sandbox`（`_chromium_launch_args`）

**b) 探索可视化**
- 新增 3 个端点：`GET /tasks/{task_id}/pages`、`GET /tasks/{task_id}/pages/screenshot/{name}`（文件名正则白名单 `^page-\d+\.png$` 防穿越）、`GET /tasks/{task_id}/video`
- 聊天流步骤卡「抓取页面」完成后就地展开页面卡片网格（截图走 blob objectURL 鉴权），失败/无产物回退 JSON 展示（`TaskStepsCard.tsx`）
- 「自动化」Tab 新增页面探索区块：抓取页数 / 登录徽标 / 模式徽标 / 截图墙 + lightbox + 录屏入口（`ExecutionPanel.tsx`）

**c) 探索录屏**
- Playwright `record_video_dir` 录制探索过程（1280x720），浏览器关闭后归档为任务目录 `videos/explore.webm`
- 前端「▶ 观看探索录屏」播放入口（步骤卡与自动化 Tab 均有）

**d) e2e 任务自动建会话**
- `create_task`（kind=e2e）无 conversation_id 时兜底自动创建会话 + 写入任务卡消息并回填 `conversation_id`，弹窗挂当前会话（`app/api/tasks.py`）

**e) 验收基线升级**
- pytest 231 → 249 → **251 passed**；tsc 零错误；真实 DBERP 联调（Playwright 登录 → 8 页探索 → 8 截图 → 256KB explore.webm）全链路打通

### 12.3 M4/M5 交付记录（2026-09-24，三子代理并行 Wave A/B/C）

**M4 自愈循环（对照 4.3 节）**
- `auto_healer.py`：取证（pytest 报错全文 + 失败截图路径 + 函数源码 ast 提取 + PageDesc 摘要，纯文本模式，签名预留视觉输入升级位）→ 诊断四分类（prompt 硬约束禁止修改/删除/弱化断言，JSON 解析校验失败重试 1 次）→ 修复（文件名白名单防 LLM 路径穿越、ast.parse 校验、**代码级断言保护** `_assertions_preserved`：旧文件每条 assert 必须原样存在）→ 备份 `heal_backup/round{n}/` → 只重跑失败 node id → 收敛判定（全过 fixed / product_bug·env·轮次耗尽 → suspected_bugs）
- 链路：`run-auto` 加 `auto_heal: bool = Form(True)`；`AUTO_HEAL_ROUNDS=3`（config.py）；每轮即时落库 heal_round/heal_log + StepLog「自愈第 N 轮」；report_json 顶层 `heal: {rounds, log, suspected_bugs}`
- 前端：run 行「自愈 N 轮」橙色徽章（running 期间轮询可见）、`HealSection.tsx` 自愈过程折叠区 + ⚠ 疑似缺陷警示区（一键复制清单）
- 验收：5 单测（真实 pytest 子进程：selector 修复全链路/product_bug 零改动/断言保护拒绝放水/轮次耗尽收敛/保护函数单测）；**遗留**：LLM 真调验收待有效 API key

**M5 探索式 Agent（对照 5.2-5.4 节）**
- `explorer_agent.py`：ReAct 循环（MAX_STEPS=30 / token / 时长三重预算，os.getenv 直读）+ 7 工具（navigate/click/fill/snapshot/screenshot/back/submit_cases，ref 编号快照省 token）+ 护栏（域名锁定拒绝原因回喂 LLM、危险操作文案黑名单含 logout/delete 等英文、连续 3 次重复动作注入收敛警告、超限优雅收敛保留已提交用例）
- 实现偏差（等价实现）：llm_service 未透传原生 function calling tools 参数 → 采用 **JSON 决策协议**（模型每轮严格输出 `{reason, tool, args}`，parse_decision 分发），护栏语义不变，任何 OpenAI 兼容端点可跑；后续如需原生 FC 仅需 llm_service 暴露 tools 通道后替换 `_build_decision_messages`
- 链路：`STEPS_EXPLORE = [探索式测试, 脚本生成, 导出文件]`；tasks.py kind=explore 复用 e2e 的 target 创建与会话兜底（「🤖 发起探索式测试」）；探索登录态 `explore/storage_state.json` 经 `AITF_STORAGE_STATE` 注入执行子进程；截图端点白名单扩为 `^(page|step)-\d+\.png$` 按前缀路由 pages//explore/
- 前端：`ExploreTimeline.tsx` 探索时间线（每步序号/动作/URL/AI 理由/结果 + 截图缩略 lightbox，解析失败回退原 JSON）；`E2ETaskModal` e2e/explore 类型切换
- 验收：17 单测（fake LLM 全链路/越域拒绝/黑名单/预算收敛/token 预算/真实 Playwright file:// 冒烟）；**LLM 真调冒烟通过**：glm-4.5-flash 对本地页面自主探索 8 步（导航→快照→填表→点击→提交）产出 3 条结构化用例 + 8 张步骤截图；**遗留**：真实站点 ≥10 用例站点级验收待有效 API key

### 12.4 待办清单（按优先级，2026-09-24 更新）

1. **更换有效 LLM API key**（用户操作，阻塞项）：当前 .env 智谱 401 / 魔搭 400——M4 真实自愈验收、M5 站点级验收、探索任务线上质量均依赖；探索建议 glm-4.5-flash 级以上模型
2. **M4/M5 真实验收（Wave C）**：key 到位后执行——故意错选择器 ≤3 轮自愈通过（4.5 节）+ 全新站点零文档探索 ≥10 用例全链路（5.7 节）
3. **第 11 节测试数据隔离落地**：`run_tag`/`cleanup_json`/`delete_strategy` 字段 + `data_cleaner.py` + scripter prompt 第 7/8/9 条（落地前须按 11.4 增补 contract-m2）
4. **M1 生成质量调优**：generator 对单页站会虚构子任务（用例与实际页面不对应），需约束 generator 严格基于 PageDesc
5. **执行 progress 实时性**：当前轮询粒度粗，考虑 SSE 或更细进度上报
6. **服务器部署 Playwright 三件套**：`pip install playwright pytest-playwright pytest-json-report` + `playwright install chromium`（root 环境已适配 `--no-sandbox`）

> 已完成移除：~~M4 自愈循环~~、~~M5 探索式 Agent~~（见 12.3）、~~DBERP 文档默认账密过期更正~~（2026-09-24 已更正，dberp-kb 4 文件 7 处补注 admin/admin123）。
