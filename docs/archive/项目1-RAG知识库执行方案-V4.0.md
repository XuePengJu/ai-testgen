# 执行方案：RAG 知识库（V4.0 整合版 · 已确认）

| 文档信息 | |
| ---- | ---- |
| 文档版本 | V4.0-整合版（**已落地 V4.0**） |
| 状态 | 方案已确认：知识库权限（全局共享/私有）、图谱纳入主路线、Excel/XMind 同批支持 |
| 关联文档 | 《项目1-工作流平台-MVP执行方案.md》《PRD.md》《README.md》 |
| 更新日期 | 2026-09-18 |
| 借鉴来源 | WeKnora（腾讯开源，MIT，github.com/Tencent/WeKnora v0.8.0）：chunks/chunk_revisions/knowledges 表设计、知识图谱 Entity/Relationship 模型与 LLM 抽取流程、Wiki Mode 设计 |

---

## 1. 背景与目标

### 1.1 为什么做 RAG 知识库

ai-testflow 当前（V3.1）的"知识问答"是**伪 RAG**：

- 上传附件 → `doc_extract` 抽文本 → **一次性截断 6000 字符注入 prompt** → 回答，用完即弃
- 用例生成（Parser/Generator）同样是文档一次性注入，无历史知识可参考
- 无向量化、无检索、无知识沉淀；大文档截断丢内容；多文档无法联合查询

**核心目标**：知识库成为平台大脑——对话问答与用例生成都从知识库取上下文，文档入库一次、终身复用，且**全程可见、可审、可干预**（用户能看见文档、分块、状态，能发现问题并修正）。

### 1.2 三个功能概念（对齐 WeNote/WeKnora 产品形态）

| 概念 | 定义 | 价值 | 交付版本 |
| ---- | ---- | ---- | ---- |
| **文档** | 上传原始文档，解析→分块→向量化，可检索 | RAG 基础，核心必做 | V4.0 |
| **Wiki** | Agent 从文档自动蒸馏出结构化、互链的 Markdown 页面，支持手动编辑+修订历史+回滚 | 知识沉淀的"人读"形态 | V4.1 |
| **知识图谱** | LLM 从文档抽取实体与关系，形成节点-边网络，可视化 + 图增强检索 | 作品集亮点 | V4.1（已确认纳入主路线，与 Wiki 同批） |

### 1.3 已确认的决策（2026-09-18 用户拍板）

1. **知识库隔离**：创建知识库时设置权限——`private`（仅自己可见可检索）/ `global`（全平台共享，只读）
2. **知识图谱**：纳入主路线，与 Wiki 同批推进，不单独搁置
3. **文件格式**：V4.0 同批支持 Excel（.xlsx/.xls）与 XMind（.xmind）

---

## 2. 总体设计：两层架构 + 两个管道

```
┌────────────────────────────────────────────────────────────┐
│  ① 入库管道（写侧）  上传→解析→分块→后处理三路→状态机全程可见   │
│  ② 知识库管理台（人看） 文档列表/原文预览/分块编辑/检索测试台    │
│  ③ 查询管道（读侧）  提问→混合检索→图增强→重排→注入→LLM        │
└────────────────────────────────────────────────────────────┘
```

**核心原则：文档资产层（MySQL，人可见可管理）与向量索引层（Chroma，RAG 用）分离**。向量永远不脱离文档资产层单独存在；任何文档的状态、分块、错误信息都能在界面上看到。

---

## 3. 技术选型

| 环节 | 选型 | 理由 |
| ---- | ---- | ---- |
| 框架 | LangChain 1.4（已装） | 不引入 LlamaIndex，保持技术栈统一 |
| Embedding | 百炼 qwen3.7-text-embedding（API，0.5 元/百万 token，带免费额度） | 中文好、零部署、与现有百炼体系一致；本地 bge-m3 留作演进 |
| 向量库 | **ChromaDB 嵌入式起步** → 规模大了迁 Qdrant | 零新增服务、LangChain 集成最成熟；Qdrant 接口兼容，迁移几十行代码 |
| 关键词检索 | `rank_bm25`（纯 Python） | 混合检索一路召回 |
| 混合检索 | LangChain `EnsembleRetriever` | 不用自己写合并 |
| 重排 | FlagEmbedding `FlagReranker`（bge-reranker）或百炼 gte-rerank | 提升召回质量（V4.1） |
| 图谱存储 | MySQL 节点表+边表（不引入 Neo4j） | 个人规模（千级节点）MySQL 足够；WeKnora 已弃用 Neo4j 佐证 |
| **Excel 解析** | `openpyxl`（依赖已有） | 读全部 sheet 单元格按行拼接，表格保留为 Markdown 表 |
| **XMind 解析** | 标准库 `zipfile` + `json`（解析 content.json，零新依赖）；备选 `xmindparser` | xmind 本质是 zip 容器，直接解析成本最低 |
| 原文件 | 现有 `uploads/` 磁盘 | 已就位 |

---

## 4. 数据模型（借鉴 WeKnora，7 张新表）

### 4.1 knowledge_bases（知识库，含权限）

```sql
CREATE TABLE knowledge_bases (
  id VARCHAR(36) PRIMARY KEY,
  user_id INT NOT NULL,                -- 创建者
  name VARCHAR(255) NOT NULL,
  description TEXT,
  visibility VARCHAR(20) DEFAULT 'private',  -- private(仅自己) | global(全平台共享只读)
  type VARCHAR(20) DEFAULT 'document', -- document | wiki | faq（扩展）
  embedding_model_id VARCHAR(64),      -- 知识库级模型配置
  chunking_config JSON,                -- 分块配置（块大小/重叠/策略）
  extract_config JSON,                 -- 图谱抽取配置（开/关、并发、温度）
  created_at DATETIME, updated_at DATETIME, deleted_at DATETIME
);
```

**权限语义**：
- `private`：仅创建者（`user_id` 匹配）可见、可检索、可管理
- `global`：全平台用户可见、可检索（**只读**）；仅创建者与 admin 可管理（上传/编辑/删除）
- 检索过滤：`visibility='global' OR user_id=当前用户`；admin 全部可见

### 4.2 knowledges（文档条目 + 状态机）

```sql
CREATE TABLE knowledges (
  id VARCHAR(36) PRIMARY KEY,
  user_id INT NOT NULL,
  knowledge_base_id VARCHAR(36) NOT NULL,
  type VARCHAR(20) DEFAULT 'document',  -- document | wiki
  title VARCHAR(255), description TEXT,
  file_name VARCHAR(255), file_type VARCHAR(50), file_size BIGINT,
  file_path TEXT, file_hash VARCHAR(64),  -- 去重
  parse_status VARCHAR(20) DEFAULT 'unprocessed', -- 状态机，见 5.2
  error_message TEXT,                    -- 失败原因可见（关键）
  chunk_count INT DEFAULT 0,
  metadata JSON, custom_metadata JSON,
  created_at DATETIME, updated_at DATETIME, deleted_at DATETIME,
  processed_at DATETIME
);
```

### 4.3 chunks（分块，可编辑）

```sql
CREATE TABLE chunks (
  id VARCHAR(36) PRIMARY KEY,
  user_id INT NOT NULL,
  knowledge_base_id VARCHAR(36) NOT NULL,
  knowledge_id VARCHAR(36) NOT NULL,
  chunk_index INT NOT NULL,
  content TEXT NOT NULL,           -- 当前内容（可被用户编辑）
  source_content TEXT NOT NULL,    -- 解析原始内容（diff 基线）
  content_revision INT DEFAULT 0,  -- 版本号
  context_header TEXT,             -- 上下文标题（父级路径）
  parent_chunk_id VARCHAR(36),     -- 父子分块
  chunk_type VARCHAR(20) DEFAULT 'text', -- text | table | json
  start_at INT, end_at INT,        -- 原文定位
  relation_chunks JSON,            -- 图谱关联块（图增强检索）
  is_enabled BOOLEAN DEFAULT TRUE, -- 软删除
  index_status VARCHAR(16) DEFAULT 'ready',
  created_at DATETIME, updated_at DATETIME, deleted_at DATETIME
);
```

### 4.4 chunk_revisions（分块编辑修订历史）

```sql
CREATE TABLE chunk_revisions (
  id VARCHAR(36) PRIMARY KEY,
  user_id INT NOT NULL,
  knowledge_base_id VARCHAR(36) NOT NULL,
  knowledge_id VARCHAR(36) NOT NULL,
  chunk_id VARCHAR(36) NOT NULL,
  revision INT NOT NULL,
  content TEXT NOT NULL,
  edit_source VARCHAR(16) DEFAULT 'user', -- user | agent
  edited_at DATETIME,
  UNIQUE KEY uk_chunk_revision (chunk_id, revision)
);
```

### 4.5 wiki_pages + graph（V4.1 交付，表结构与 V4.0 同步建好）

```sql
-- Wiki 页面
CREATE TABLE wiki_pages (
  id VARCHAR(36) PRIMARY KEY,
  knowledge_base_id VARCHAR(36) NOT NULL,
  parent_id VARCHAR(36),           -- 页面树层级
  title VARCHAR(255), content MEDIUMTEXT,
  revision INT DEFAULT 0,
  status VARCHAR(20) DEFAULT 'draft', -- draft | published
  created_at DATETIME, updated_at DATETIME, deleted_at DATETIME
);

-- 图谱实体与关系（最小版不引入 Neo4j）
CREATE TABLE graph_entities (
  id VARCHAR(36) PRIMARY KEY,
  knowledge_base_id VARCHAR(36) NOT NULL,
  title VARCHAR(255) NOT NULL,
  entity_type VARCHAR(50), description TEXT,
  frequency INT, degree INT,
  UNIQUE KEY uk_entity_title (knowledge_base_id, title)
);
CREATE TABLE graph_relationships (
  id VARCHAR(36) PRIMARY KEY,
  knowledge_base_id VARCHAR(36) NOT NULL,
  source VARCHAR(36), target VARCHAR(36),
  relation VARCHAR(100), strength INT,  -- 1-10
  description TEXT,
  KEY idx_rel_src (source), KEY idx_rel_tgt (target)
);
```

---

## 5. 入库管道（写侧）

### 5.1 流程

```
上传文档 → 解析（doc_extract 扩展支持 docx/pdf/md/txt/xlsx/xmind）
        → 结构化分块 → 写入 chunks
        → 后处理三路并行：
            ① 向量化 → embedding（百炼 API）→ 写入 Chroma
            ② Wiki 生成（V4.1）：Agent 蒸馏分块为互链 Markdown 页面
            ③ 图谱抽取（V4.1）：LLM 抽实体/关系 → MySQL 表
```

**格式扩展明细（V4.0 同批）**：
- `.xlsx/.xls`：`openpyxl`（已有依赖）遍历所有 sheet，行内单元格按 ` | ` 拼接，表格以 Markdown 表形式进入分块（分块器识别为 `chunk_type='table'`）
- `.xmind`：标准库 `zipfile` 读 `content.json`（XMind 8+/Zen 格式），解析主题树 → 层级标题（`context_header` 天然可用）；`SUPPORTED_EXTS` 与前端 `accept` 同步扩展

### 5.2 文档状态机（用户可见）

```
unprocessed → parsing → chunking → processing → ready（已就绪 ✓）
                    ↘             ↘             ↘
                    failed ✗（error_message 记录原因，可一键重建索引）
```

任一步失败：状态变红、保留错误信息、页面提供"重建索引"重试，不静默丢弃。

### 5.3 分块策略

- 结构化分块：Markdown 标题切分 + 表格/JSON/代码块整块保留（测试文档核心信息）
- 块大小 300~800 token，重叠 10%
- 父子分块（`parent_chunk_id`）：小块检索、大块返回上下文

---

## 6. 查询管道（读侧）

```
用户提问 / 用例生成请求
  → 权限过滤（knowledge_bases.visibility：global 全可见 + 本人 private）
  → 向量检索 top-K（Chroma）+ BM25 关键词 top-K（rank_bm25）
  → EnsembleRetriever 合并去重
  → 图增强（V4.1）：relation_chunks 拉一阶/二阶邻居
  → Rerank（V4.1）：bge-reranker
  → 截断注入 prompt → LLM 回答 / 进入四 Agent 流水线
```

### 6.1 与现有功能集成点

| 接入点 | 改法 |
| ---- | ---- |
| 对话问答 `chat/stream` | `ChatIn` 增加知识库检索，命中片段注入（替换一次性附件注入）；附件上传可选"入库沉淀" |
| 用例生成 Parser | 生成前先检索相似历史需求/接口规范作 few-shot 参考 |
| 用例生成 Generator | 参考历史用例格式与风格 |
| 迭代补充 `iterate` | 补充要求携带知识库上下文 |
| **权限** | 检索与列表均按 `visibility` 过滤：global 知识库对全员可见可检索（只读），private 仅创建者；admin 不受限 |

---

## 7. API 设计（app/api/knowledge.py）

| 方法 | 路径 | 作用 |
| ---- | ---- | ---- |
| POST | `/api/knowledge/bases` | 创建知识库（body 含 `name` + `visibility: private/global`） |
| GET | `/api/knowledge/bases` | 知识库列表（按权限过滤） |
| PUT | `/api/knowledge/bases/{id}` | 修改知识库（含切换 visibility，仅创建者/admin） |
| POST | `/api/knowledge/documents` | 上传文档入库（触发管道，支持 docx/pdf/md/txt/xlsx/xmind） |
| GET | `/api/knowledge/documents` | 文档列表（状态/分块数/时间，按知识库过滤） |
| GET | `/api/knowledge/documents/{id}` | 文档详情 + 状态 + 错误信息 |
| **PUT** | `/api/knowledge/documents/{id}` | **手动更新：替换文件 → 自动重建索引** |
| DELETE | `/api/knowledge/documents/{id}` | 删除文档（同时清向量） |
| POST | `/api/knowledge/documents/{id}/reindex` | 重建索引（失败重试） |
| GET | `/api/knowledge/documents/{id}/chunks` | 分块列表 |
| PUT | `/api/knowledge/chunks/{id}` | 分块编辑（写 chunk_revisions，触发重向量化） |
| POST | `/api/knowledge/chunks/{id}/revert` | 分块回滚到指定版本 |
| POST | `/api/knowledge/search` | 检索测试台（query → 命中块+分数+来源，按权限过滤） |

---

## 8. 前端页面

**V4.0 知识库管理台（核心交付）**：
1. **文档列表页**：知识库 Tab（含权限徽标 私有/共享）+ 文档表格（文件名/类型/状态徽标/分块数/时间）+ 操作（预览/更新/重建/删除）
2. **文档详情页**：原文预览 + 分块可视化（每块文本/token/类型）+ 分块编辑入口 + 错误信息展示
3. **检索测试台**：输入 query → 显示命中块、相似度分数、来源文档 → 定位检索问题
4. **知识库创建/设置**：名称 + 权限选择（私有/全局共享）

**V4.1 追加**：Wiki 浏览器（页面树 + 修订历史抽屉）+ 图谱可视化页（AntV G6 / ECharts Graph，节点-边网络 + 全库概览统计）

前端新增 `frontend/src/pages/KnowledgePage.tsx` + 组件，复用现有 React + zustand 体系。

---

## 9. 落地路线（已确认）

| 版本 | 范围 | 预估 |
| ---- | ---- | ---- |
| **V4.0（基座）** | 7 张表（同步建齐）+ `doc_extract` 扩展 xlsx/xmind + 入库管道 + 知识库权限（private/global）+ 文档管理 API + 管理台（文档列表/分块可视化/分块编辑修订/检索测试台）+ 对话 RAG 接入 | 约 2 周 |
| **V4.1（Wiki+图谱 同批）** | Wiki：Agent 自动生成 + 页面树 + 修订历史/回滚；图谱：LLM 抽取 + 可视化 + 图增强检索；rerank；用例生成接入 RAG | 约 2~3 周 |

> 图谱不再单独搁置：V4.1 与 Wiki 同批推进，先由 V4.0 把分块与入库基座做稳（图谱依赖分块质量）。

---

## 10. 成本评估（自研）

| 项目 | 金额 |
| ---- | ---- |
| 软件授权 | ¥0 |
| 服务器 | 复用现有轻量机，零新增组件（Chroma 是本地目录） |
| Embedding | 百炼 API：按文档量估约 ¥2 一次性（带免费额度） |
| LLM | 复用魔搭免费 API（每日 2000 次） |
| Excel/XMind 解析 | ¥0（openpyxl 已有 + 标准库解析 xmind） |
| 对比 | RAGFlow 需 4C16G 服务器（约 ¥200-400/月）；WeKnora 全量部署更重 |

---

## 11. 风险与权衡

1. **图谱抽取质量**是最大不确定项——LLM 抽取的实体关系噪声大，需要清洗与人工确认；V4.1 先最小版验证（低温度 0.1、实体按标题去重、最少实体数门槛）
2. **知识图谱对任务型工具增益有限**——文档检索+问答已覆盖 80% 场景；图谱定位为作品集亮点，放在 V4.0 基座稳定之后
3. **分块编辑带来一致性成本**——编辑后需自动重向量化，注意与"重建索引"的并发
4. **global 知识库只读**——共享库被他人修改会造成信任问题；V4.0 先做只读共享，后续如需协作者再扩展角色
5. **Chroma→Qdrant 迁移**——提前约定通过 LangChain 统一接口访问，迁移成本可控

---

## 12. 决策记录

| 决策点 | 结论 | 状态 |
| ---- | ---- | ---- |
| Embedding | 百炼 API 起步 | ✅ 已确认 |
| 向量库 | Chroma 起步 → Qdrant 演进 | ✅ 已确认 |
| 知识库隔离 | 创建时设权限：private / global（共享只读） | ✅ 已确认（2026-09-18） |
| 知识图谱 | 纳入主路线，与 Wiki 同批（V4.1） | ✅ 已确认（2026-09-18） |
| 文件格式 | V4.0 同批支持 Excel/XMind | ✅ 已确认（2026-09-18） |

---

## 13. V4.0 实现状态（2026-09-18 实施完成）

### 已完成（全部端到端验证通过）

| # | 模块 | 说明 | 验证 |
| ---- | ---- | ---- | ---- |
| 1 | 建表 | 7 张表（knowledge_bases/knowledges/chunks/chunk_revisions/wiki_pages/graph_entities/graph_relationships） | 隔离 sqlite 建表 ✓ |
| 2 | 解析 | doc_extract 扩展 xlsx/xls/xmind，SUPPORTED_EXTS 共 8 种 | 真实 xlsx + 构造 xmind ✓ |
| 3 | 入库管道 | chunker（标题感知+表格整块）/ vectorstore（embedding+mock 兜底）/ ingest（状态机） | 16 项 API 全过 ✓ |
| 4 | 知识库管理 API | bases CRUD+权限 · 文档上传/列表/详情/更新/重建/删除 · 分块编辑/修订/回滚 · 检索测试台 | test_knowledge_api.py ✓ |
| 5 | 对话 RAG | /api/chat/stream 注入知识库检索片段（chat.py _build_rag_context） | test_chat_rag.py ✓ |
| 6 | 前端 | KnowledgePage.tsx（库列表/文档/分块编辑/检索测试）+ 导航入口 | npm run build ✓ |
| 7 | Embedding 页面配置 | 复用 LLMConfig 体系新增 slot=embedding，与 LLM 模型同格式卡片：设置页「我的模型配置」（用户配自己的 Key/模型/Base URL，厂商预设百炼/siliconflow/自定义，测试连通走 /embeddings）+ 管理页「平台默认模型」（admin，全员兜底）；解析优先级 personal>platform>env>mock，入库按库创建者、检索/对话按当前用户统一注入；**创建知识库后弹窗引导「去配置向量模型」**，知识库页常驻生效状态条 | test_embedding_config.py 9 项 ✓ + 回归全过 ✓ |

### 实施中确认的关键决策（新增）

1. **权限过滤定稿：MySQL 为准，向量只管 kb_id**。最初把 visibility 写进 Chroma metadata
   做 where 过滤，端到端测试发现「切 global 后检索仍 0 条」——向量 metadata 不同步。
   改为：检索前先查 `visible_kb_ids(db, user, admin)`（admin 全见；否则 visibility='global'
   OR user_id=me），向量检索用 `kb_id IN [...]` 过滤，权限切换即时生效。`ingest.visible_kb_ids`
   是唯一权限口径，列表/检索/对话共用。
2. **SQLAlchemy 保留名坑**：Declarative 模型列名不能用 `metadata`（与 Base.metadata 冲突），
   已改名 `doc_meta` / `custom_meta`。
3. **测试隔离强制**：.env 配的是线上 MySQL（生产库，地址见 `.env`，不写入文档），本地测试默认会写线上库！
   已清理一次误写数据。此后测试一律：
   `DATABASE_URL="sqlite:////tmp/kb_test/app.db" AITF_ROOT_DIR=/tmp/kb_test` 隔离运行。
   线上库的 7 张新表由 init_db create_all 自动建（幂等，保留）。
4. **requirements.txt**：已补 `chromadb>=0.5.5`、`rank_bm25>=0.2.2`（混合检索预留）。
5. **Embedding 配置用户级 + 平台兜底（2026-09-18 修订）**：与 LLM 模型一致，用户在
   设置页配自己的向量模型（personal），admin 在管理页配平台默认（platform）作兜底，
   解析优先级 personal > platform > env > mock。注意：向量维度由模型决定，不同用户配
   不同模型时跨用户检索（global 库）可能失效；同一用户自己的库入库/检索同模型正常匹配。
   mock（256 维）↔ 真实（text-embedding-v3 1024 维）切换后旧向量失效，需「重建索引」。

### 已知边界（诚实声明）

- **mock embedding 检索质量差**：未配置 Embedding 时用确定性哈希向量，流程全通但
  相似度排序参考价值有限。配置真实模型（设置页「我的模型配置 → Embedding 向量模型」，
  或管理页平台默认，或环境变量 EMBEDDING_API_KEY）后，对已入库文档「重建索引」即切换真实向量。
- 上传文档为同步入库（≤10MB），大文档会阻塞请求几秒；后续可迁 task_queue 异步。
- 知识图谱 / Wiki 为 V4.1 主线，V4.0 只建表不实现。
