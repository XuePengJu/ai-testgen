# M2/M3 执行链路契约（Wave 2 并发依据）

> 作者：统筹者，2026-09-23。m2-engine / m2-scripter / m3-scaffold 三个智能体共同遵守本契约。
> 任何一方需要偏离契约，必须写进自己的汇报交统筹者裁决，禁止单方面偏离。

> **【联调期补充裁决】**
> 1. 详情接口 GET /api/executions/{run_id} 返回**解析后的 `report` 对象**（DB 列仍 report_json）；列表接口不带 report
> 2. 截图命名保持 scripter 现状（nodeid 含 [chromium] 参数化后缀），auto_runner 靠函数名包含匹配兜底
> 3. ExecutionRun.task_id 为**逻辑外键**（无物理 FK，先例：Task.target_id/user_id）——MySQL 5.7 collation 分裂（tasks=utf8mb4_general_ci vs 库默认 unicode_ci）会让物理 FK 报 1215、跨表 JOIN 报 1267
> 4. **collation 长期策略**：本地库保持现状；今后所有新表一律 `__table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_general_ci"}` 对齐 tasks；统一 CONVERT 到库默认属专门维护期动作，不混入功能迭代
> 5. mapping.json key = 函数名（test_tc_001 形态）；case 有 ID 无生成 node 时，report 中按 skipped 处理并在 error 注明「脚本生成失败」

## 1. 脚本产物目录布局（m2-scripter 产出，m2-engine 消费）

```
outputs/u_{user_id}/{task_id}/auto/          ← ExecutionRun.auto_dir 存这个相对路径
├── conftest.py                              ← 模板固化在代码里（m2-scripter 负责写入），不由 LLM 生成
├── mapping.json                             ← {"test_tc_001": "TC-001", ...} 节点名→用例ID 映射
├── test_cases_0.py / test_cases_1.py ...    ← 按 module 分批（每批 ≤8 条用例）的测试脚本
├── report.json                              ← pytest --json-report 输出（m2-engine 执行后产生）
├── shots/                                   ← 失败截图（conftest hook 自动写入，文件名={node_id 安全化}.png）
└── storage_state.json                       ← 登录态（M1 抓取已存，存在则 conftest 注入）
```

- 函数命名：`test_{case_id 小写、'-' 换 '_'}`，如 `TC-001` → `test_tc_001`；同时写 mapping.json
- **失败截图命名（2026-09-23 裁决定死）**：`re.sub(r"[^A-Za-z0-9._-]", "_", node_id) + ".png"`（node_id 不带 auto/ 前缀）；
  conftest 截图 hook 与 auto_runner 截图回填两侧必须用同一规则（auto_runner 另有函数名包含匹配兜底）
- conftest 模板要求：`page` fixture 用 pytest-playwright 自带；`base_url` 从环境变量 `AITF_BASE_URL` 读；
  `AITF_STORAGE_STATE` 环境变量指向 storage_state.json 时注入 browser context；失败自动截图到 `AITF_SHOTS_DIR`

## 2. ExecutionRun 模型（m2-engine 实现，m3-scaffold 按此渲染）

字段（`app/models/automation.py` 追加，与 V5.0 文档 3.1 一致）：
`id(str pk uuid)/task_id(str FK tasks.id)/user_id/trigger("manual"|"retry")/status("pending"|"running"|"completed"|"failed")/progress(int)/total(int)/passed(int)/failed(int)/skipped(int)/duration_ms(int)/report_json(Text)/auto_dir(str)/error(Text nullable)/created_at/started_at/finished_at`

report_json 结构（m2-engine 组装，m3-scaffold 的 TS 类型按此对齐）：
```json
{
  "summary": {"total": 7, "passed": 5, "failed": 1, "skipped": 1, "duration_ms": 12345},
  "cases": [
    {"case_id": "TC-001", "node_id": "test_cases_0.py::test_tc_001",
     "title": "用例标题", "outcome": "passed|failed|skipped",
     "duration_ms": 100, "error": "断言信息或 null", "screenshot": "shots/test_tc_001.png 或 null"}
  ],
  "environment": {"browser": "chromium", "base_url": "https://..."}
}
```

## 3. API 契约（m2-engine 实现，m3-scaffold 调用）

| 方法 | 路径 | 请求/响应要点 |
|---|---|---|
| POST | `/api/tasks/{task_id}/run-auto` | 登录用户；无 cases_json→400；已有 pending/running→409；返回 `{run_id, status:"pending"}` |
| GET | `/api/tasks/{task_id}/executions` | 新→旧列表 `ExecutionRunOut[]` |
| GET | `/api/executions/{run_id}` | 详情：含解析后的 report 对象（running 时只带 progress/total） |
| POST | `/api/executions/{run_id}/retry` | 复制配置建新 run（trigger="retry"），返回新 run_id；原 run 运行中→409 |
| GET | `/api/executions/{run_id}/files/{path}` | 附属文件；路径白名单前缀 `shots/`、`trace/`，禁止 `..` 与绝对路径，404 兜底 |

权限：全部要求登录（user/admin；guest 复用任务属主校验规则——guest 只能操作共享 guest 自己的任务）。
TaskOut 增量：`target_id`、`has_auto`（bool，该任务存在 auto 目录且有脚本）。

## 4. 执行器行为（m2-engine）

- `app/core/exec_queue.py`：照抄 task_queue 模式，worker 数 = `EXEC_WORKERS`（1）；**lifespan 启动**（main.py：recover_pending_runs + start_workers；API 层懒启动幂等兜底）
- `auto_runner.py`：subprocess `.venv/bin/python -m pytest auto/ --json-report --json-report-file=auto/report.json --browser=chromium -x 无`；
  **不 shell=True、timeout=AUTO_EXEC_TIMEOUT、cwd=任务目录、env 白名单**（PATH/HOME/PYTHONPATH + AITF_BASE_URL/AITF_STORAGE_STATE/AITF_SHOTS_DIR）；
  凭据（用户名密码）经环境变量 `AITF_AUTH_USER/AITF_AUTH_PASS` 注入，不落盘不进日志
- 解析 report.json + mapping.json → 按「契约 2」组装 report_json → 更新 run 终态（completed 即使有用例失败；仅进程级崩溃/超时才 failed）
- StepLog 不接入（执行不属生成流水线；进度靠 ExecutionRun.progress + 前端轮询）

## 5. scripter_agent 行为（m2-scripter）

- 签名沿用 Agent 模式：`run_scripter(task, cases, step) -> (result, summary, details_json)`，写 StepLog
- prompt 硬约定（V5.0 文档 3.3.2）：page fixture、get_by_role/get_by_label 优先、expect() 断言、每用例一函数
- 按 module 分批 ≤8 条/批 → 每批一次 LLM 调用产出一个 test_cases_{n}.py
- ast.parse 校验失败重试 1 次；仍失败该批标记 error 但不中断整链（StepLog 记明）
- conftest.py + mapping.json 由代码生成（见契约 1），LLM 只产测试函数体
- 产出后步骤 summary 报告：「N 个用例 → M 个脚本文件」

## 6. 前端（m3-scaffold）

- TaskDetailDrawer 第 4 个 Tab「自动化」（kind=e2e 或 has_auto 时显示）
- `ExecutionPanel.tsx`：执行按钮（POST run-auto）→ 2s 轮询 run status；执行列表（状态徽标/通过率/耗时）；
  点击 run → 内嵌报告视图：汇总条（total/passed/failed/skipped/耗时）+ 用例明细表（outcome 图标/error 展开文案）
  + 失败截图缩略（GET files/{path}，点击 lightbox 放大）
- types.ts 新增 `ExecutionRun`/`ExecutionReport` 等类型严格对齐契约 2；client.ts 新增 5 个封装
- 联调前用契约 mock 数据自测组件逻辑（npm build + 本地 mock 渲染），真数据联调由统筹者收口
