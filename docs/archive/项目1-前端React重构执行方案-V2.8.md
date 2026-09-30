# 项目1 · 前端 React + TS 重构执行方案（V2.8）

> 状态：**已完成**（2026-09-12 M1~M5 五里程碑全部通过验收，详见 §11 验收记录）
> 起草日期：2026-09-12
> 前置变更：任务详情独立页（`?task=` 模式）已于 2026-09-12 移除，详情入口统一收敛为会话内抽屉，迁移面已缩小

---

## 1. 背景与目标

### 1.1 为什么要重构
当前前端为**原生 HTML 单文件**（`frontend/index.html`，3143 行 / 165KB），原生 JS + 全局变量做状态 + 手写 DOM。功能持续膨胀后（V2.2 个人中心 → V2.6 对话驱动 → V2.7 迭代导入），单文件已到可维护性临界点：
- 全局变量做状态（`kind / pendingDraft / chatMessages / token...`），改一处难评估影响面
- 6 个 `<script>` 块混杂 HTML，无模块边界
- 无类型约束，接口字段全靠记忆

### 1.2 目标
1. 重构为 **React + TypeScript + Vite** 工程，按组件拆分，复杂度下降（全局变量 → 组件 state）
2. **生产架构零改动**：构建产物仍是纯静态文件，继续由 FastAPI 同源托管（同源无 CORS、cpolar 隧道、systemd 单服务全部不变）
3. **作品集价值**：React+TS 是目标岗位（AI 测试/测开）高频技术栈，重构过程本身可写入简历与面试话术

### 1.3 非目标（本次不做）
- 不引入状态管理库以外的重框架（不上 Next.js / SSR）
- 不做移动端原生适配重构（现有响应式样式平移）
- 不改后端任何接口（前端重构期间 API 契约冻结）

---

## 2. 现状盘点（实测数据）

| 项 | 数据 |
|---|---|
| 前端总量 | 单文件 `frontend/index.html` 3143 行 / 165KB + `config.js` |
| 外部依赖 | 仅 mind-elixir（原生 ESM，124KB，`frontend/vendor/mind-elixir/`）→ **Vite 可直接 npm import，零迁移阻力** |
| 与后端交互 | `fetch` + `apiFetch` 封装、SSE 流式（`fetch + getReader` + AbortController）、AES-256-GCM 流量加密（V2.1） |
| 功能面 | 三级角色认证 / SSE 对话 / 会话持久化 / 任务列表+分类拖拽 / 详情抽屉 3Tab / 迭代导入 / 个人中心 / LLM 配置 / admin 用户管理 |

---

## 3. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript（strict） | 求职栈对齐；TS 强约束替代"记忆接口字段" |
| 构建 | Vite 5 | dev 热更新 + build 产物纯静态；`npm run build` 后与现状同构 |
| 路由 | React Router 6 | 仅 3-4 个顶层视图，够用 |
| 状态 | 组件 state + Context（认证/加密两个全局上下文） | 规模不需要 Redux/Zustand，保持简单 |
| 拖拽 | @dnd-kit/core | 任务分类拖拽，禁止手写 drag 事件 |
| 思维导图 | mind-elixir（npm 包） | 替换 vendor 文件，React 组件内 `useRef` 挂载 |
| HTTP | 平移现有 `apiFetch` → `src/api/client.ts` | 含加密层，逻辑不变只换语言 |
| 样式 | 保留现有 CSS，按组件拆分进模块 | 不引 Tailwind/CSS-in-JS，减少迁移变量 |

---

## 4. 目标目录骨架（monorepo，不拆仓库）

```
ai-testflow/
├─ frontend/                  # React+TS+Vite 工程（重构落点）
│  ├─ src/
│  │  ├─ api/                 # client.ts(apiFetch+加密) / tasks.ts / chat.ts / llm.ts
│  │  ├─ contexts/            # AuthContext / CryptoContext
│  │  ├─ hooks/               # useChatStream(SSE) / useTaskPolling
│  │  ├─ components/
│  │  │  ├─ chat/             # 首页对话驱动（气泡/草稿确认/流式）
│  │  │  ├─ tasks/            # 任务列表 + 分类拖拽
│  │  │  ├─ detail/           # 详情抽屉 3Tab（用例表/导图/时间线）
│  │  │  ├─ settings/         # LLM 配置 / 个人中心 / admin
│  │  │  └─ common/           # Toast / Modal / 模型状态胶囊
│  │  ├─ App.tsx / main.tsx
│  │  └─ styles/              # 平移现有 CSS
│  ├─ dist/                   # 构建产物（入库，见 §6）
│  └─ package.json
├─ frontend-legacy/           # 旧单文件前端改名保留（切换期回退用）
├─ app/ / generator_core/ / tests/ / main.py   # 后端不动
```

> 旧前端改名为 `frontend-legacy/` 而非删除：main.py 的 mount 指向可一键切回。

---

## 5. 组件映射清单（现有功能 → 新组件）

| 现有功能（index.html 内） | 新组件 | 难度 |
|---|---|---|
| 认证 + 三级角色 + token 管理 | AuthContext + LoginModal | ★★ |
| **AES-256-GCM 加密层** | CryptoContext + api/client.ts | ★★★ |
| SSE 流式对话 + 停止按钮 + 草稿确认 + 补充模式 | chat/（useChatStream hook） | ★★★ |
| 会话持久化（列表/切换/删除） | chat/SessionList | ★ |
| 任务列表 + 分类拖拽 | tasks/TaskList + @dnd-kit | ★★ |
| 详情抽屉 3Tab | detail/Drawer + CaseTable / MindMap / Timeline | ★★ |
| 迭代 + 导入（xmind/xlsx/json） | detail/IteratePanel | ★ |
| LLM 配置（厂商预设/连通测试） | settings/LlmConfig | ★ |
| 个人中心 / admin 用户管理 | settings/Profile / AdminUsers | ★ |
| Toast / 模型状态胶囊 / 拖拽 FAB | common/ | ★ |

---

## 6. 关键决策点（已定 + 待确认）

### 6.1 【已定】生产不需要 Node
- Node 只存在于开发机（Mac）：dev server + `npm run build`
- 服务器只收 `dist/` 静态产物，FastAPI mount 托管——**服务器不装 Node**

### 6.2 【建议，待拍板】dist/ 是否入 git
| 方案 | 做法 | 取舍 |
|---|---|---|
| **A. dist 入库（推荐）** | `frontend/dist` 提交，服务器 pull 即得 | 部署流程与现在完全一致（pull→restart）；代价是 diff 混入构建产物 |
| B. 本地构建 + scp | dist 不入库 | 仓库干净但部署多一步手工，易漏 |

### 6.3 【已定】切换策略
- React 版开发期间 `main.py` mount 仍指旧前端，**不阻塞线上**
- 稳定后 mount 切到 `frontend/dist`，旧前端改名 `frontend-legacy/` 保留一个版本周期后删除

---

## 7. 里程碑（我主导写、老板验收的口径）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **M1 脚手架 + 认证加密**（Day1） | Vite 工程、路由、api client、AuthContext、**加密层** | 本地登录三级角色全流程跑通；加密请求后端解密成功（TestClient 端到端） |
| **M2 对话驱动 + 任务列表**（Day2-3） | SSE 流式（useChatStream）、会话持久化、任务列表+拖拽 | 与旧版行为逐项对照；SSE 中断/停止/恢复正常 |
| **M3 详情抽屉 3Tab**（Day4） | 用例表/思维导图/时间线 + 迭代导入 | mind-elixir 渲染正常；迭代合并、三格式导入可用 |
| **M4 配置页 + admin**（Day5 上午） | LLM 配置 / 个人中心 / 用户管理 | 表单行为与旧版一致 |
| **M5 回归 + 切换**（Day5 下午） | Playwright 全流程截图对比；mount 切换；136 条 pytest 回归 | 截图 ≥3 张落档；pytest 全过；/health 验证 |

> 总量预估：源码约 4000-5000 行（TS 类型与组件壳占增量）。按铁律，**每个里程碑完成先给老板验收，不一次性交付**。

---

## 8. 风险清单

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | **加密层静默失败**：AES-GCM 与后端不对齐时不报错、只是解密失败 | 高 | M1 第一优先实现；配 TestClient 端到端用例；加密层测试不过不进入 M2 |
| 2 | SSE 流式行为差异（缓冲、断线、AbortController 时序） | 中 | useChatStream 逐行对照旧实现；对话场景手动回归清单 |
| 3 | mind-elixir 在 React 严格模式/重渲染下重复初始化 | 中 | 组件内 `useRef` 挂载 + 严格卸载清理；必要时关 StrictMode 双调用 |
| 4 | dist 入库导致仓库体积增长 | 低 | .gitignore 排除 node_modules；每次 build 覆盖式提交 |
| 5 | 旧链接 `?task=` 书签失效 | 低 | 已在独立页移除时确认，无存量依赖 |

---

## 9. 验收标准（整体）

1. 功能对照：现有功能清单逐项可用，无回归（Playwright 截图 + 手动关键路径）
2. `pytest` 136 条全过（后端未动，回归性质）
3. 生产链路验证：本地 build → mount 切换 → curl /health → cpolar 线上可访问
4. 旧前端保留可一键切回
5. README/PRD/执行方案同步 V2.8 变更（沿用文档同步惯例）

---

## 10. 拍板记录

**2026-09-12 老板已拍板：**

1. **dist/ 入库**（采用建议 A：构建产物提交 git，服务器 pull 即生效、免装 Node）
2. **执行口径：Buddy 主导**（3~5 个工作日强度，老板负责关键节点验收）
3. **M1 暂不启动**，等老板发话后按 M1 → M5 顺序执行，每阶段验收后再进下一阶段。

---

## 11. 验收记录（2026-09-12 全部完成）

| 里程碑 | 验收内容 | 结果 |
|---|---|---|
| M1 脚手架 + 认证加密 | Vite 工程、AuthContext、AES-256-GCM 加密层、三级角色 | ✅ e2e 通过 |
| M2 对话驱动 + 任务列表 | SSE 流式、会话持久化、任务列表 + 5s 轮询、任务定位 | ✅ e2e 10 项通过 |
| M3 详情抽屉 3Tab | 用例表 / 思维导图（mind-elixir）/ 时间线 + 迭代导入 + 导出 | ✅ e2e 12 项通过 |
| M4 配置页 + admin | 个人中心 / LLM 双槽配置 / 生效模型 / 用户管理 / 分类树 | ✅ e2e 18 项通过 |
| M5 回归 + 切换 | mount 切换（AITF_FRONTEND 开关）、三套 e2e 对 :8000 生产同源架构全过、真实 ModelScope Key 冒烟（思考模型 `<think>` 流式输出 + 测连通 1881ms）、pytest 136/136 | ✅ 全绿 |

补充说明：
- 路由未引入 React Router（3 个顶层视图用 state 切换，比原方案更轻）；分类树用 🏷 归类菜单替代 @dnd-kit 拖拽（交互更清晰、可测性更好）
- 生产链路：`config.py` `AITF_FRONTEND=react(默认)/legacy` 决定 STATIC_DIR；`main.py` `/assets`、`/vendor` 条件挂载，同一份代码兼容新旧前端
- 回归口径：M2/M3/M4 三套 e2e 脚本均支持 `M*_URL` 环境变量，本轮全部跑在 `:8000`（生产同源 + 加密链路端到端）
- 遗留：线上部署（git push + 服务器 pull + restart）待老板发话，按协作铁律不擅自执行

---

## 12. 实际落地差异（与初稿方案偏差）

> 作为收尾记录，列出与 §3 技术选型、§4 目录骨架的偏差——这些偏差已在 M1~M5 验收中确认可接受。

| 初稿方案 | 实际落地 | 偏差说明 |
| --- | --- | --- |
| 路由：React Router 6 | 3 个顶层视图用 state 切换（main\|settings\|admin），未引入 React Router | 视图少、state 切更轻，避免额外依赖 |
| 拖拽：@dnd-kit/core | 分类树用 🏷 归类菜单（任务「🏷」一键归入/移出），未做节点拖拽 | 交互更清晰、可测性更好、规避 dnd 在 React 严格模式下的重渲染坑 |
| 状态：组件 state + Context | 引入 zustand 管理 chat/task/settings/category 多 store | 多模块共享状态，zustand 比 Context 更可控 |
| 思维导图：npm 包 mind-elixir | 保留 vendor ESM 引入（未改 npm 包） | 功能等价，零迁移风险 |
| 部署：待发话 | 仍待老板发话（git push + 服务器 pull + restart） | 按协作铁律，本地完成不擅自部署 |

说明：上述偏差不影响 V2.8 验收结论（M1~M5 全绿）；文档指针已通过《项目1-功能增强执行方案-V2.9.md》同步至 V2.9。
