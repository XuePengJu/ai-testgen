# ai-testgen 移动端兼容落地方案（完整版）

> 版本：v1.0 · 2026-10-13 · 作者：Buddy  
> 范围：仅前端（React + base.css），后端 / API / 部署零改动。  
> 原则：**渐进式响应式适配**——一套代码，桌面端表现完全不变，窄屏逐级降级。

---

## 0. 实测基线（改造前）

取证方式：Playwright 390×844（iPhone 14 逻辑分辨率）访问本地 8005，  
脚本 `scripts/mobile_audit.py`，截图 `reports/mobile-audit/{main,cases,knowledge,models}.png`。

| 指标                    | 实测值                                                                       | 结论                                 |
| --------------------- | ------------------------------------------------------------------------- | ---------------------------------- |
| 视口宽 vs 文档 scrollWidth | 390px vs **635px**                                                        | 全站横向溢出 ~245px，需左右拖动                |
| 四个核心页溢出元素数            | 每页 12+ 个                                                                  | 同一批壳层元素（rail 尾部、role-picker）+ 各页自身 |
| rail（≤768 顶部横条态）      | 自检/用户/退出按钮排到 472~512px 外，被 `overflow:hidden` 裁掉                           | **部分导航入口手机上不可达（P0）**               |
| hover-only 操作钮        | 7 处（会话删除/重命名/AI标题、用例行操作、分类删除、文档卡操作、文档行操作）                                 | 触屏无 hover，**功能在移动端等于消失（P0）**       |
| 会话列表                  | `.app-main` grid 固定 250/200px 列，窄屏不折叠                                     | 聊天区被挤成一条缝（P0）                      |
| 知识库左栏                 | `.kb-page` grid 固定 260px 列                                                | 同上（P1）                             |
| 用例库表格                 | 状态列 150px + 时间列 100px + 操作列 170px 固定宽                                     | 表格溢出（P1）                           |
| 弹窗/抽屉                 | `.auth-modal` 360px、`.kb-drawer` 460px、`.pe-modal` 94vw/88vh（两栏 300px 列表） | 窄屏贴边或溢出（P1）                        |
| 输入框字号                 | textarea 14px                                                             | iOS 聚焦自动放大页面（P2 体验项）               |

现有媒体查询断点混乱：575 / 640 / 720 / 768 / 860 / 1080 六种混用（历史逐页补丁）。

---

## 1. 断点与总体策略

### 1.1 统一断点（新增规则一律只用这两档）

| 档位      | 媒体条件                         | 定位                      | 典型行为                           |
| ------- | ---------------------------- | ----------------------- | ------------------------------ |
| **平板档** | `@media (max-width: 1024px)` | 窄窗 / 小笔记本 / 平板横屏        | 侧栏收窄、次要面板隐藏（沿用现有 1080 逻辑，逐步归并） |
| **手机档** | `@media (max-width: 768px)`  | 手机竖屏（360~430px 主流宽度全覆盖） | 单栏、抽屉化、rail 横条、表格降级            |

- 既有 575/640/720/860 断点**不动**（它们各自工作正常，重写是无谓风险），只约束新增代码。
- 触屏交互修正单独用能力查询 `@media (hover: none)`，与宽度断点正交（触屏平板也能命中）。

### 1.2 手机档信息架构

```
┌─────────────────────────┐
│  内容区（单栏，独占全宽）  │ ← 会话列表/知识库左栏 → ☰ 唤出的覆盖式抽屉
│                         │
│  (100dvh - rail)        │
├─────────────────────────┤
│ ◉ ☰ ⌂  ⚙ …  ←横滑→    │ ← rail 顶部横条（现状保留），改可横向滚动
└─────────────────────────┘
```

桌面端（>1024px）渲染路径**零变化**：所有新规则都在媒体查询内，或只加默认不可见的元素（☰ 按钮桌面端 `display:none`）。

---

## 2. 逐页改造设计

### 2.1 应用壳（P0）

**① rail 横条溢出 → 横向滚动**

现状（base.css:1991、2082 附近）：`≤768` 时 `.rail` 变 `flex-direction: row` + `overflow: hidden`，按钮总宽 ~510px 被裁。

```css
@media (max-width: 768px) {
  .rail { overflow-x: auto; overflow-y: hidden; scrollbar-width: none;
          -webkit-overflow-scrolling: touch; }
  .rail::-webkit-scrollbar { display: none; }
  .rail-spacer { display: none; }          /* 横条态弹性占位无意义，还撑开间距 */
  .rail-btn, .rail-user, .rail-login { flex: none; }  /* 防压缩 */
}
```

- 效果：所有入口可达（滑动查看），改动纯 CSS、~10 行。
- 不做「更多」收纳弹层：rail-sub 已有 fixed 面板机制，再加一层收纳反而增加交互复杂度；横滑是手机底部工具栏的成熟惯例。

**② 视口高度：100% → 100dvh（手机浏览器地址栏遮挡）**

```css
@media (max-width: 768px) {
  html, body, #root { height: 100dvh; }   /* 不支持 dvh 的旧内核回退 100vh */
  .app-shell { height: 100vh; height: 100dvh; }
  body:has(.demo-banner) .app-shell { height: calc(100dvh - 32px); }
}
```

**③ 安全区（刘海/底部横条）**

```css
@media (max-width: 768px) {
  .rail { padding-bottom: env(safe-area-inset-bottom); }
  .chat-input-wrap { padding-bottom: calc(14px + env(safe-area-inset-bottom)); }
}
```

`index.html` 的 viewport meta 追加 `viewport-fit=cover`（env() 生效前提）。

### 2.2 AI 会话页（P0）

**① 会话列表 → 覆盖式抽屉**

组件结构：`main.app-main > aside.side-panel.conv-panel + ChatPanel`。  
采用「DOM 顺序无关」的 `:has()` 方案，JS 改动最小：

- `ChatPanel.tsx`：`input-toolbar` 最左侧新增 ☰ 按钮（仅手机档显示），点击 `document.querySelector('.app-main').classList.toggle('conv-open')`；
- `ConversationPicker.tsx`：`aside` 内尾部追加一个 `<div className="conv-backdrop" onClick={close}/>`（桌面端 CSS 隐藏，不渲染成本≈0）；选中会话的 `onClick` 里顺带移除 `conv-open`；
- CSS：

```css
.gh-menu { display: none; }                 /* ☰ 桌面端不出现 */
@media (max-width: 768px) {
  .app-main { grid-template-columns: minmax(0, 1fr); }
  .conv-panel {
    position: fixed; left: 0; top: 0; bottom: 0; z-index: 90;
    width: min(320px, 86vw);
    transform: translateX(-100%); transition: transform .25s ease;
    box-shadow: 4px 0 24px rgba(0,0,0,.15);
  }
  .app-main:has(.conv-backdrop):not(:has(.conv-open)) { /* 兜底，见下 */ }
  .app-main.conv-open .conv-panel { transform: none; }
  .conv-backdrop { display: none; }
  .app-main.conv-open .conv-backdrop {
    display: block; position: fixed; inset: 0; z-index: 89;
    background: rgba(15,23,42,.4);
  }
  .gh-menu { display: inline-flex; }
}
```

（`conv-open` 类挂在 `.app-main` 上由 JS toggle，backdrop 常驻 DOM、CSS 控制显隐。）

**② 输入区**

```css
@media (max-width: 768px) {
  .chat-input-wrap { padding: 10px 12px 14px; }
  .chat-input-row .input-body textarea { font-size: 16px; min-height: 76px; }
  /* ↑ 16px 防 iOS 聚焦缩放 */
  .input-toolbar { flex-wrap: wrap; row-gap: 6px; }
  .role-picker { margin-left: 0; }          /* 换行后不再需要左距 */
  .kb-pop { width: min(292px, calc(100vw - 24px)); }
}
```

**③ 消息流**

`.chat-stream { padding: 24px 28px }` → 手机档 `padding: 14px 12px`；气泡/卡片本身是流式宽度，无需改。

### 2.3 触屏交互修正（P0，`@media (hover: none)`）

所有 hover-only 元素在触屏常显（降透明度保持视觉轻量）：

```css
@media (hover: none) {
  .h-del, .h-op { opacity: .6; }                    /* 会话行：重命名/AI标题/删除 */
  .cl-col-ops .cl-op { opacity: .85; }              /* 用例行操作 */
  .cat-row .h-del { opacity: .6; } .cat-row .cat-count { /* hover 隐藏计数逻辑撤销 */ }
  .doc-card .dc-ops, .doc-row-ops { opacity: .85; } /* 知识库文档操作 */
}
```

注意 `.cat-row:hover .cat-count { opacity: 0 }` 这类「hover 互换」在触屏上保持原样即可（不 hover 就永远显示计数，删除钮常显，两者共存：给 `.cat-row` 触屏档加 `padding-right` 让位）。

触控目标尺寸：手机档把 `.icon-btn`/`.h-del`/`.h-op` 热区撑到 ≥36px（`padding` 补偿，不改视觉尺寸）。

### 2.4 用例库页（P1）

```css
@media (max-width: 768px) {
  .cases-page { padding: 10px; }
  .cl-head { gap: 8px; } .cl-toolbar { margin-left: 0; width: 100%; }
  .cl-cards { gap: 6px; } .cl-cards > * { flex: 1 1 45%; min-width: 0; } /* 概览卡 2×2 */
  .cl-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .cl-table { min-width: 640px; }                   /* 表内横滚，不再撑破页面 */
  .cl-search input { width: 100%; } .cl-search { flex: 1; }
}
```

表格选择「横滚」而非「卡片化重构」：卡片化要动渲染层、风险高收益低；横滚 + 名称列 `position: sticky; left: 0` 是成熟折中（sticky 名列一并加上）。

### 2.5 知识库页（P1）

```css
@media (max-width: 768px) {
  .kb-page { grid-template-columns: minmax(0, 1fr); padding: 12px 12px 32px; }
  .kb-side { width: auto; }                    /* 库列表收成顶部横滑 chip 条 */
  .kb-list { flex-direction: row; overflow-x: auto; }
  .kb-item { flex: none; width: auto; }
  .wiki-layout, .doc-layout { grid-template-columns: 1fr; }
  .wiki-cats, .doc-tree { max-height: 160px; } /* 目录树收成可折叠/矮面板 */
  .kb-drawer { width: 100vw; }                 /* 右侧抽屉 → 全屏 */
  .kb-drawer-resize { display: none; }         /* 触屏不需要拖宽手柄 */
  .kb-edit-pop { width: min(260px, calc(100vw - 24px)); }
}
```

### 2.6 弹窗 / 抽屉 / 浮层（P1）

| 对象                                                    | 现状                    | 手机档                                                                                                                                                                                |
| ----------------------------------------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.auth-modal`                                         | 360px 固定              | `width: min(360px, calc(100vw - 24px))`                                                                                                                                            |
| `.task-drawer`                                        | 96vw/90vh             | `width: 100vw; height: 100dvh; max-height: 100dvh; border-radius: 0`（全屏任务详情）                                                                                                       |
| `.pe-modal`（提示词定制）                                    | 两栏 920px              | `width: 100vw; height: 100dvh; border-radius: 0`；`.pe-body` 改 `flex-direction: column`，`.pe-list` 改 `width: 100%; max-height: 140px; border-right: none; border-bottom: 1px solid` |
| `.kb-mask/.kb-dialog`                                 | 已有 max-width 兜底       | 不动                                                                                                                                                                                 |
| `.sc-zone`（自检 toast）                                  | 已有 `calc(100vw-32px)` | 不动                                                                                                                                                                                 |
| 各 `position:absolute` 下拉（`.cl-move-menu`、`.cite-*` 等） | 240~292px             | 统一 `max-width: calc(100vw - 24px)` 兜底                                                                                                                                              |

### 2.7 外围页收尾（P2）

- **模型配置 / 用户管理 / 使用统计 / 个人中心**：均为 `.page-wrap` 流式布局，主体已适配（llm-grid 860px 已单列）。逐页跑 390px 截图，按审计脚本输出修残余溢出（预计：统计卡 `minmax(196px,1fr)` 网格、表格类行）。
- **统计卡**：`grid-template-columns: repeat(auto-fit, minmax(196px,1fr))` 在 390px 下自动单列，验证即可，预计零改动。
- 演示站 `/demo/` 与主站同一份前端代码，**自动继承全部修复**，无需单独处理。

---

## 3. 代码改动清单（文件级）

| 文件                                                    | 改动                                                                                                                              | 量级    |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `frontend/src/styles/base.css`                        | ① 末尾新增统一「移动端适配」区块（约 150~200 行，全部在 `@media (max-width:768px)` / `(hover:none)` 内）；② 微调既有 768 断点中 rail 两处（overflow 改横滑、隐藏 spacer） | 主要工作量 |
| `frontend/src/components/chat/ChatPanel.tsx`          | `input-toolbar` 头部加 ☰ 按钮（toggle `.app-main.conv-open`）                                                                          | ~10 行 |
| `frontend/src/components/chat/ConversationPicker.tsx` | 加 `.conv-backdrop` 元素 + 选中会话/点遮罩时关抽屉                                                                                            | ~15 行 |
| `frontend/index.html`                                 | viewport meta 加 `viewport-fit=cover`                                                                                            | 1 行   |
| `scripts/mobile_audit.py`                             | 升级为 pytest UI 用例（见 §4）                                                                                                          | ~60 行 |

**不改**：路由/状态管理/API 层/后端/桌面端任何既有规则。  
新增 CSS 全部收敛在文件末尾单一区块 + 注释分节，方便整体回滚（删一个区块 = 全恢复）。

## 4. 验收方案

1. **移动门禁（新增）**：`tests/ui/mobile/test_mobile_fit.py`
   - 复用 `scripts/mobile_audit.py` 的检测逻辑，参数化 4 个核心视图 × 视口 390×844；
   - 断言 `document.documentElement.scrollWidth <= clientWidth`（零横向溢出）；
   - 断言 rail 内所有 `.rail-btn` 可通过横滚到达（`scrollWidth` 有限、无 `overflow:hidden` 裁切）；
   - 每视图截图存档 `reports/e2e-artifacts/mobile/`；
   - 纳入 `-m e2e_ui` 标签体系，本地跑仍须 `--tracing=off`。
2. **桌面回归（既有门禁不破）**：`.venv/bin/python -m pytest tests/ui -m "not visual and not demo" --tracing=off -p no:cacheprovider` → 基线 11P/1S 必须保持。
3. **构建**：`npm run build` EXIT=0。
4. **真机抽查**：本地起服务后手机同 Wi-Fi 访问 `http://<mac-ip>:8005`（访客自动登录可直接看壳层），老板过目后再谈部署。
5. **UI 铁律**：改动 push 前 Playwright 截图 ≥3（桌面 2 + 移动 1 组）。

## 5. 分期计划与工作量

| 期      | 内容                                                       | 交付物                     | 预估 |
| ------ | -------------------------------------------------------- | ----------------------- | -- |
| **P0** | 壳层三件套（rail 横滑 / dvh / 安全区）+ 会话页抽屉 + 输入区 + hover→touch 修正 | 4 核心页零溢出、导航全可达、删除/重命名可用 | 半天 |
| **P1** | 用例库表格横滚、知识库单栏+全屏抽屉、全部弹窗/浮层兜底                             | 全部页面零溢出                 | 半天 |
| **P2** | 外围页逐页清尾 + 移动 UI 门禁入库 + 全量回归                              | 门禁脚本 + 截图报告             | 半天 |

P0 结束即出一轮 390px 截图给老板过目，方向确认后再推进 P1/P2（避免整包做完返工）。

## 6. 风险与对策

| 风险                    | 等级 | 对策                                                                                              |
| --------------------- | -- | ----------------------------------------------------------------------------------------------- |
| `:has()` / `dvh` 兼容性  | 低  | 目标 iOS Safari ≥15.4、Chrome ≥108（2022+），作品集访客基本覆盖；`:has` 仅用于抽屉兜底选择器，主逻辑走 JS class toggle，不依赖也能工作 |
| rail 横滑后 active 指示不可见 | 低  | 进入页面时 JS `scrollIntoView` 当前项（P0 顺手加，2 行）                                                       |
| 触屏常显操作钮导致行内拥挤         | 中  | 只降 opacity 不改布局；会话行右侧已预留 64px padding，够用                                                        |
| base.css 单文件冲突/误伤桌面   | 中  | 新规则 100% 在媒体查询内；结束前 grep 校验「区块外无裸选择器」；桌面 UI 门禁回归兜底                                              |
| 演示站同步生效               | —  | 同代码库自动继承，属收益非风险；但部署仍走「老板明确说部署才 push」铁律                                                          |

## 7. 明确不做（本期范围外）

- 底部 Tab Bar 式原生导航重构（方案 B，过度设计）
- 用例/文档的滑动手势操作（左滑删除等）
- 移动端专属功能裁剪（如隐藏使用统计图表细节）——先保证「能看能点」，体验精修另立项
- 独立 m 站 / 二次打包 H5

---

**决策点（需老板拍板）：**

1. 整体路线按 §5 三期走？还是先只做 P0 看效果？
2. 会话列表交互：☰ 覆盖式抽屉（本方案）vs 顶部下拉面板？
3. 用例库表格：横滚 + sticky 名列（本方案）vs 窄屏卡片化（工作量 +1 天）？
