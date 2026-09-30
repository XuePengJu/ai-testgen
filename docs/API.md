# API 接口手册

> 任务类接口均需 `Authorization: Bearer <token>`（guest / user / admin 皆可）。
> 交互式文档（Swagger）：服务启动后访问 <http://127.0.0.1:8000/docs>。

## 认证

| 方法   | 路径                          | 说明                |
| ---- | --------------------------- | ----------------- |
| POST | `/api/auth/register`        | 注册（首个用户自动成 admin） |
| POST | `/api/auth/login`           | 登录，返回 JWT + 加密密钥  |
| GET  | `/api/auth/me`              | 当前身份（guest 含剩余时长） |
| POST | `/api/auth/change-password` | 改密                |

## 访客

| 方法   | 路径                   | 说明                 |
| ---- | -------------------- | ------------------ |
| POST | `/api/guest/token`   | 签发/复用全站共享 guest token |
| POST | `/api/guest/upgrade` | 访客转注册用户（数据迁移）      |

## 任务

| 方法   | 路径                              | 说明                                        |
| ---- | ------------------------------- | ----------------------------------------- |
| POST | `/api/tasks`                    | 提交任务：`file` 或 `text` + `kind` + `formats` + `roles`（可选，pm/qa/dev 视角数组，默认仅 qa） |
| GET  | `/api/tasks`                    | 任务列表（admin 加 `?all=true` 看全部）             |
| GET  | `/api/tasks/{id}`               | 任务详情 + 四步骤日志 + 用例列表（含 `conversation_id`、`parent_task_id`、`roles`） |
| PATCH | `/api/tasks/{id}`              | 更新任务：`name` 重命名 / `review_status` 评审切换（仅本人） |
| DELETE | `/api/tasks/{id}`             | 删除任务（仅本人/admin）                           |
| POST | `/api/tasks/{id}/iterate`      | 迭代补充：`instruction` + 可选 `file`（用例导入）+ 可选 `conversation_id`，生成新版本子任务（自动继承角色，同步落会话消息） |
| GET  | `/api/tasks/{id}/download?fmt=` | 下载导出文件（xlsx/json/xmind）                   |

## 会话

| 方法     | 路径                              | 说明                    |
| ------ | ------------------------------- | --------------------- |
| GET    | `/api/conversations`           | 会话列表（按更新时间倒序，含 mode/kb_id） |
| POST   | `/api/conversations`           | 新建会话（可带 `mode=workflow\|kb_qa` + `kb_id`） |
| GET    | `/api/conversations/{id}`       | 会话详情（含全部消息，含思考过程）     |
| PATCH  | `/api/conversations/{id}`       | 手动重命名会话（仅本人）     |
| POST   | `/api/conversations/{id}/ai-title` | AI 总结会话内容生成标题并覆盖（走生效模型池） |
| POST   | `/api/conversations/{id}/messages` | 追加消息（前端断线恢复用）     |
| DELETE | `/api/conversations/{id}`       | 删除会话（连带任务/消息级联清理）  |

## 对话

| 方法   | 路径                | 说明                          |
| ---- | ----------------- | --------------------------- |
| POST | `/api/chat/stream` | 流式对话（SSE）：支持注入任务摘要上下文（`task_id`）+ 深度思考 + 文档附件（`file_id`）+ 多库知识库检索（`kb_ids`）；命中知识库先发 `citations` 事件（引用溯源） |
| POST | `/api/chat`        | 非流式对话（兜底，同渲染管线）              |

## 文件

| 方法   | 路径           | 说明                                  |
| ---- | ------------ | ----------------------------------- |
| POST | `/api/files` | 上传对话附件（docx/pdf/md/markdown/txt/xlsx/xls），返回 file_id 供对话引用 |

## 知识库

| 方法     | 路径                                       | 说明                              |
| ------ | ---------------------------------------- | ------------------------------- |
| GET    | `/api/knowledge/bases`                   | 知识库列表（按可见性过滤，含文档/分块统计）          |
| POST   | `/api/knowledge/bases`                   | 新建库（`name` / `visibility` 私有或共享）    |
| PATCH  | `/api/knowledge/bases/{id}`              | 改名 / 改可见性                      |
| DELETE | `/api/knowledge/bases/{id}`             | 删库（级联文档）                        |
| POST   | `/api/knowledge/bases/{id}/documents`    | 上传文档入库（docx/pdf/md/xlsx…）         |
| POST   | `/api/knowledge/bases/{id}/documents/text` | 新建文本文档                          |
| GET    | `/api/knowledge/bases/{id}/documents`    | 文档列表                            |
| GET    | `/api/knowledge/documents/{id}`          | 文档详情                            |
| PUT    | `/api/knowledge/documents/{id}`          | 改正文                             |
| PUT    | `/api/knowledge/documents/{id}/meta`     | 改元数据                            |
| POST   | `/api/knowledge/documents/{id}/summary`  | 重新生成 wiki 摘要                    |
| POST   | `/api/knowledge/documents/{id}/reindex`  | 重建索引                            |
| DELETE | `/api/knowledge/documents/{id}`          | 删文档                             |
| GET    | `/api/knowledge/documents/{id}/chunks`   | 分块列表                            |
| PUT    | `/api/knowledge/chunks/{id}`             | 编辑分块                            |
| GET    | `/api/knowledge/chunks/{id}/revisions`   | 分块修订历史                          |
| POST   | `/api/knowledge/chunks/{id}/rollback`    | 回滚分块                            |
| GET    | `/api/knowledge/search`                  | 混合检索（`kb_id` 单库 / `query` / `top_k`） |
| POST   | `/api/knowledge/bases/{id}/wiki/index`   | 整库 wiki 索引                       |

## 质量看板（admin only）

| 方法 | 路径                        | 说明                                     |
| --- | ------------------------- | -------------------------------------- |
| GET  | `/api/quality/summary`   | 最新质量汇总（pytest 通过率 / 覆盖率 / e2e 结果；响应层只含核心接口 + e2e 展示口径） |
| POST | `/api/quality/run`       | 后台触发 pytest + e2e + 聚合（running 时 409） |
| GET  | `/api/quality/run/status` | 当次运行状态与各阶段进度                       |
| GET  | `/api/quality/history`   | 历史趋势数组（近 30 次）                     |

## 被测系统与自动化执行

| 方法 | 路径                                        | 说明                                |
| --- | ----------------------------------------- | --------------------------------- |
| POST/GET | `/api/targets`                       | 创建 / 列出被测系统（密码加密，永不回传）        |
| GET/DELETE | `/api/targets/{id}`                | 详情 / 删除（校验属主）                   |
| POST | `/api/tasks/{task_id}/run-auto`         | 触发自动化执行（入执行队列；`auto_heal` 参数默认开自愈；已有进行中 run 返回 409） |
| GET  | `/api/tasks/{task_id}/executions`        | 执行记录列表（新→旧）                    |
| GET  | `/api/executions/{run_id}`               | 执行详情（含结构化报告；running 带实时进度）      |
| POST | `/api/executions/{run_id}/retry`         | 复制新 run 重跑                         |
| GET  | `/api/executions/{run_id}/files/{path}`  | 报告附属文件（截图 / trace，路径白名单）        |
| GET  | `/api/tasks/{task_id}/pages`             | 抓取页面清单（探索可视化）                    |
| GET  | `/api/tasks/{task_id}/pages/screenshot/{name}` | 页面截图（文件名白名单防穿越）            |
| GET  | `/api/tasks/{task_id}/video`             | 探索过程录屏                            |
| GET  | `/api/tasks/{task_id}/live-shot`         | 探索/执行实时画面（最新 step 截图） |
| GET  | `/api/tasks/{id}/explore-plan`           | 探索计划查询（plan-first）           |
| POST | `/api/tasks/{id}/explore-plan/confirm`   | 确认探索计划继续执行       |

## 分类

| 方法     | 路径                                       | 说明                  |
| ------ | ---------------------------------------- | ------------------- |
| GET    | `/api/categories`                        | 获取分类树               |
| POST   | `/api/categories`                        | 新建分类（可选 parent\_id） |
| PATCH  | `/api/categories/{id}`                   | 重命名分类               |
| DELETE | `/api/categories/{id}`                   | 删除分类（子分类级联，任务回落未分类） |
| POST   | `/api/categories/{id}/move`              | 移动分类到新父节点（防环校验）     |
| POST   | `/api/tasks/{task_id}/category/{cat_id}` | 任务归入分类              |
| DELETE | `/api/tasks/{task_id}/category`          | 任务移出分类              |

## 模型池与配置

| 方法     | 路径                              | 说明                 |
| ------ | --------------------------- | ------------------ |
| GET  | `/api/llm/providers`         | 获取厂商预设列表（含 Embedding 预设） |
| GET  | `/api/llm/effective`         | 当前生效配置（含各槽位池现状与调度摘要） |
| GET/POST | `/api/llm/pool/{slot}`   | 个人池列表 / 新增候选（Key AES 加密，指纹判重 409） |
| PUT/DELETE | `/api/llm/pool/{slot}/{id}` | 更新候选（未传 Key 保留旧 Key）/ 删除 |
| POST | `/api/llm/pool/{slot}/reorder` | 池内优先级排序（全量 ids）    |
| PATCH | `/api/llm/pool/{slot}/{id}/enabled` | 启用/停用某条候选    |
| POST | `/api/llm/pool/{slot}/{id}/test` | 测试单条连通（enable_thinking=False） |
| GET  | `/api/llm/pool/{slot}/health` | 池健康汇总（可用/冷却/错误计数）  |
| GET/POST | `/api/llm/platform-pool/{slot}` | 平台池（GET 登录只读；POST admin） |
| PUT/DELETE/PATCH | `/api/llm/platform-pool/{slot}/{id}/...` | 平台池更新/删除/排序/启停/测连通（写操作 admin） |
| POST | `/api/llm/test`              | 测试连通（用已保存池条目或传入的配置，Key 缺失自动复用同厂商池条目） |
| POST | `/api/llm/test-default/{slot}` | （admin）测试平台池当前生效候选（slot 含 embedding） |

## 管理后台（admin）

| 方法     | 路径                             | 说明                     |
| ------ | ------------------------------ | ---------------------- |
| GET    | `/api/users`                   | 用户/访客列表                |
| PATCH  | `/api/users/{id}`              | 启用/禁用用户                |
| DELETE | `/api/users/{id}`              | 删除并级联清理                |
| POST   | `/api/admin/guests/clean`      | 手动清理过期访客（历史动态访客遗留）     |
| POST   | `/api/admin/guests/clean-all`  | 清空全部历史访客               |
| POST   | `/api/admin/guests/{id}/clean` | 定向清理单个访客               |
| POST   | `/api/admin/shared-guest/reset` | 清空共享 guest 的任务与文件（保留账号） |
| GET    | `/api/admin/stats`             | 注册用户/活跃访客/24h 清理数/任务总数 |

## 其他

| 方法  | 路径           | 说明           |
| --- | ------------ | ------------ |
| GET | `/config.js` | 前端配置（仅旧版单文件前端需要；React 版走相对路径） |
| GET | `/health`    | 健康检查（返回 `{"status":"ok","db_dialect":"mysql"}`） |
