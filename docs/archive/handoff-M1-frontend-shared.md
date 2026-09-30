# M1 → 统筹者交接文件（前端共享文件 + main.py 注册）

> 作者：m1-crawler（M1 抓取+生成）。日期：2026-09-23。
> 本文件记录 M1 需要落在**共享文件**里的增量，由统筹者合并；M1 智能体按约束未改动这些文件。

## 1. main.py：注册 automation 路由（必须，否则 /api/targets 404）

```python
# import 行追加 automation：
from app.api import auth, automation, categories, chat, conversations, files, guest, knowledge, llm_config, tasks, users

# include_router 区块追加：
app.include_router(automation.router, prefix="/api")
```

已在 TestClient 侧验证：挂载后 targets CRUD + e2e 全链路任务 25/25 断言通过。

## 2. frontend/src/types.ts：新增 TargetItem（可选，建议合并）

E2ETaskModal 已 inline 定义最小集，不改 types.ts 也能工作；为后续 M3（Drawer 自动化 Tab）复用，建议追加：

```ts
/** 与 app/schemas/automation.py:TargetOut 对齐（M1 全链路：被测系统） */
export interface TargetItem {
  id: string;
  name: string;
  base_url: string;
  auth_type: string;
  has_auth: boolean;
  created_at?: string | null;
}
```

合并后可把 `E2ETaskModal.tsx` 里的 inline `interface TargetItem` 删除改为 import（非必须）。

## 3. frontend/src/api/client.ts：无必须改动

E2ETaskModal 只用了现有的 `apiJson` / `API` / `toast` 导出，无新增。

## 4. app/core/db.py：M1 有两处小改动（⚠️ 与 M0 并发者知会）

M1 按设计文档「沿用项目现有加列/迁移方式」改了 db.py（不在此前所有权限列表内，特此说明）：

1. `init_db()` 内追加一行模型注册：`import app.models.automation  # noqa: F401`
2. `_ensure_columns()` 的 tasks 补列区块追加（老库补 target_id 列）：
   ```python
   # M1 全链路：e2e 任务关联被测系统（可空外键，老库补列）
   if "target_id" not in cols:
       alters.append("ADD COLUMN target_id VARCHAR(64)")
   ```

若 M0 也改了 db.py，合并时保留双方增量即可（互不冲突）。

## 5. chatStore：e2e 任务不进会话消息流（设计取舍，遗留）

E2ETaskModal 发起任务后走 `taskStore.refresh() + startPolling()`，任务出现在左侧任务列表与详情抽屉；**不往 chatStore.messages 里插消息**（避免改共享 store）。若产品上要求会话流里也渲染 e2e 任务卡，M3 再补：`chatStore.confirmCreateTask` 模式照抄，FormData 换成 e2e 字段。

## 6. M1 改动文件清单（自有范围）

新增：`app/models/automation.py`、`app/schemas/automation.py`、`app/api/automation.py`、`app/services/web_crawler.py`、`app/workflow/agents/scripter_agent.py`（M2 占位骨架）、`frontend/src/components/chat/E2ETaskModal.tsx`
修改：`app/models/task.py`（Task.target_id 列）、`app/models/__init__.py`（注册 TestTarget）、`app/workflow/engine.py`（STEPS_E2E 六步 + crawler/parser/scripter 分发）、`app/api/tasks.py`（kind=e2e + url/target_id/username/password 表单参数）、`frontend/src/components/chat/TaskStepsCard.tsx`（步骤标题数据驱动）、`frontend/src/components/chat/ChatPanel.tsx`（🌐 入口按钮 + 弹窗挂载）
