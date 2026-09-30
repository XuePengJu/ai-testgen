# scripts/archive — 历史一次性脚本归档

> 各里程碑交付时的一次性验收/清理/迁移脚本，对应功能已由 pytest 全量用例与 e2e 套件覆盖，**不再维护**。
> 保留作为交付过程证据；重演历史迁移时仍可参考。

## 归档清单（2026-09-29）

| 文件 | 用途 | 所属里程碑 |
|---|---|---|
| verify_ai_parse.py | AI 解析验收 | V2.x 需求解析 |
| verify_parser_only.py | Parser 单环节验收 | V2.x |
| verify_delete.py / verify_delete_ui.js | 删除链路验收（API/UI） | V2.x |
| verify_end_to_end.py | 端到端验收 | V3 LangChain 迁移 |
| verify_langchain_stage1.py | LangChain 迁移阶段 1 验收 | V3 |
| verify_step_expected.py / verify_step_expected_ui.js | 步骤级预期结果验收（API/UI） | V2.9 |
| verify_pool_ui.py | 模型池 UI 验收 | V5.1–V5.4 |
| clear_legacy_data.py | 存量脏数据清理 | V2.x |
| migrate_sqlite_to_mysql.py | 本地 SQLite → MySQL 迁移 | 数据层切换（本地/服务器均已完成） |

**仍在 `scripts/` 的活跃脚本**：start_local.sh（后台守护启动）、db_local.sh（本地 Docker MySQL）、migrate_v2.py（幂等迁移，start.sh 每次启动执行）、migrate_case_library.py（V5.5 用例库迁移，服务器部署时执行）、migrate_kb_summary.py（M5 摘要迁移，同上）、quality/（质量看板采集聚合）、deploy_frontend.sh（前端发布，不入库）。
