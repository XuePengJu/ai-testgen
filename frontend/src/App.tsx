/**
 * V4.2 应用壳 · 全量对齐设计稿（knowledge-hub-redesign）：
 * - 布局：左侧深色图标 rail（64px）+ 右侧内容区（.app-body）
 *   rail = 对话 / 知识库 / 质量报告 / 模型配置 / 用户管理(admin) | spacer | 自检 / GitHub / 用户名→个人中心
 * - main：对话驱动三栏（左历史会话 | 中对话流 | 右任务列表+分类树）
 * - knowledge / models / quality / settings(个人中心) / admin(用户管理)：整页视图
 * 原 V2.8 顶部 header 全部功能保留，仅形态迁移到 rail（图标 + title 提示）。
 * V5.3（2026-09-26）：信息架构重组 —— 模型配置拆为一级入口；管理后台改名用户管理；
 * 「设置」按钮移除，改为点击 rail 底部用户名区域打开（个人中心）。
 * V5.5（2026-09-28）：用例库资产化 —— 新增一级「用例库」页（测试用例集管理）；
 * 「用例设计」改名「AI 会话」；会话页移除右侧「我的任务」栏（与用例库重复），
 * 任务列表轮询收归 App 层（会话任务卡实时刷新依赖同一份数据）。
 * W3（2026-09-29）：M8 导航收敛 + M9 测试中心 ——
 * - 一级导航收敛 4 项：AI 会话 / 用例库 / 测试中心（原「全链路测试」，并入质量报告）/ 知识库；
 * - 模型配置 / 用户管理 / 被测系统 / 源码 / 系统自检 全部沉到底部「设置」组
 *   （可见性规则不变：用户管理仍仅管理员可见）；
 * - 旧 nav-to detail 值兼容重定向：quality → e2e（测试中心），避免死链接；
 * - 左栏漂浮贴纸（.rail-sticker）移除；「DBERP 进销存」改名「被测系统 · DBERP」。
 */
import { useEffect, useState } from "react";
import {
  Shield, ShieldCheck, Loader2, LogOut, Cpu,
  BookOpen, MessageSquare, UserPlus, ChevronsLeft, ChevronsRight,
  Boxes, ChevronDown, ExternalLink, Library,
} from "lucide-react";
import { useAuth } from "./hooks/useAuth";
import { useChatStore } from "./store/chatStore";
import { useTaskStore, startListPolling } from "./store/taskStore";
import { useSettingsStore } from "./store/settingsStore";
import { useCategoryStore } from "./store/categoryStore";
import ChatPanel from "./components/chat/ChatPanel";
import ConversationPicker from "./components/chat/ConversationPicker";
import SelfCheckToast from "./components/SelfCheckToast";
import CaseLibraryPage from "./pages/CaseLibraryPage";
import TaskDetailDrawer from "./components/task/TaskDetailDrawer";
import SettingsPage from "./pages/SettingsPage";
import ModelConfigPage from "./pages/ModelConfigPage";
import AdminPage from "./pages/AdminPage";
import KnowledgePage from "./pages/KnowledgePage";

type View = "main" | "cases" | "settings" | "models" | "admin" | "knowledge";

/** 合法视图 id */
const VIEWS: View[] = ["main", "cases", "settings", "models", "admin", "knowledge"];

/** 未知/历史残留视图标识一律回落主视图 */
function normalizeView(v: string | null): View {
  return (VIEWS as string[]).includes(v || "") ? (v as View) : "main";
}

const GITHUB_REPO = "https://github.com/XuePengJu/ai-testflow";

/** GitHub 官方 mark（单色，随 currentColor） */
function GithubMark({ size = 18 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

export default function App() {
  const { me, role, ready, token, showLogin, logout } = useAuth();
  const refreshConversations = useChatStore((s) => s.refreshConversations);
  const newConversation = useChatStore((s) => s.newConversation);
  const refreshTasks = useTaskStore((s) => s.refresh);
  const [view, setView] = useState<View>(() => normalizeView(localStorage.getItem("aitf_view")));
  useEffect(() => { localStorage.setItem("aitf_view", view); }, [view]);

  // V4.3 侧边栏折叠态（默认展开，记忆用户选择）
  const [railCollapsed, setRailCollapsed] = useState(
    () => localStorage.getItem("aitf_rail_collapsed") === "1"
  );
  useEffect(() => { localStorage.setItem("aitf_rail_collapsed", railCollapsed ? "1" : "0"); }, [railCollapsed]);

  // V4.5.3 被测系统子菜单：数据源数组，后续加链接只需在 TARGETS 里追加一行
  // W3 M8：显示名规范为「被测系统 · DBERP」
  const TARGETS: { name: string; url: string }[] = [
    { name: "被测系统 · DBERP", url: "https://erp.agentest.vip/" },
  ];
  const [tOpen, setTOpen] = useState(
    () => localStorage.getItem("aitf_target_open") !== "0"
  );
  useEffect(() => { localStorage.setItem("aitf_target_open", tOpen ? "1" : "0"); }, [tOpen]);

  // 登录态变化：拉会话/任务；登出清对话并回主视图
  useEffect(() => {
    if (token) {
      void refreshConversations();
      void refreshTasks();
    } else {
      newConversation();
      useSettingsStore.getState().reset();
      useCategoryStore.getState().reset();
      setView("main");
    }
  }, [token, refreshConversations, refreshTasks, newConversation]);

  // V5.5 任务列表轮询收归 App 层：会话页任务卡实时刷新与用例库页共用同一份数据
  useEffect(() => {
    if (!token) return;
    const stop = startListPolling();
    return stop;
  }, [token]);

  // 非 admin 切到 admin 视图时踢回 main（登出/角色变化兜底）
  useEffect(() => {
    if (view === "admin" && ready && role !== "admin") setView("main");
    if (view === "settings" && ready && !me) setView("main");
    // V4.2：guest 也可进知识库（只读共享库，写操作后端 403 + 前端隐藏按钮）
    if (view === "knowledge" && ready && !me) setView("main");
    // V5.3：模型配置对所有登录角色开放（访客只读调度摘要，配置接口后端 403）
    if (view === "models" && ready && !me) setView("main");
  }, [view, ready, role, me]);

  // V4.0：跨页面跳转事件（如知识库创建后引导去设置页配置向量模型）
  // 未知目标（历史残留 e2e/quality 等）经 normalizeView 安全回落主视图
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent<string>).detail;
      setView(normalizeView(d));
    };
    window.addEventListener("nav-to", h);
    return () => window.removeEventListener("nav-to", h);
  }, []);

  // V4.5.1 系统自检：改为右上角悬浮通知（自动消失 / hover 暂停 / 手动关）
  const [scRun, setScRun] = useState(0);      // 递增触发一次检测
  const [scShow, setScShow] = useState(false);
  const runSelfCheck = () => { setScShow(true); setScRun((n) => n + 1); };

  if (!ready) {
    return <div className="m1-loading">加载中…</div>;
  }

  /**
   * V4.3 rail 导航按钮：图标 + 文字（展开态一目了然）；
   * 收起态隐藏文字，hover 弹出 CSS 浮层标签（.rl-tip）替代系统 title。
   */
  const railBtn = (
    v: View | "selfcheck" | "github" | "logout",
    icon: React.ReactNode,
    label: string,
    show: boolean,
    opts: { active?: boolean; onClick?: () => void; cls?: string; aria?: string; ico?: string } = {}
  ) =>
    show ? (
      <button
        key={v}
        className={"rail-btn " + (opts.active ? "active " : "") + (opts.cls || "")}
        onClick={opts.onClick}
        aria-label={opts.aria || label}
      >
        <span className={"rl-ico " + (opts.ico || "")}>{icon}</span>
        <span className="rl-txt">{label}</span>
        <span className="rl-tip" aria-hidden="true">{opts.aria || label}</span>
      </button>
    ) : null;

  const canKb = !!me;

  return (
    <div className="app-shell">
      {/* V4.3 左侧可折叠侧边栏：默认展开（图标+文字），可收起为 64px 图标态 */}
      <nav className={"rail" + (railCollapsed ? " collapsed" : "")} aria-label="主导航">
        <div className="rail-head">
          <div className="rl-logo" title="AgentTest · AI 测试智能体平台" onClick={() => setView("main")} style={{ cursor: "pointer" }}>
            <Shield size={19} />
          </div>
          <div className="rl-title" onClick={() => setView("main")} style={{ cursor: "pointer" }}>
            AgentTest
          </div>
        </div>
        <button
          className="rail-collapse"
          onClick={() => setRailCollapsed((c) => !c)}
          aria-label={railCollapsed ? "展开导航" : "收起导航"}
        >
          {railCollapsed ? <ChevronsRight size={13} /> : <ChevronsLeft size={13} />}
        </button>

        <div className="rl-group">工作区</div>
        {railBtn("main", <MessageSquare />, "AI 会话", true, { active: view === "main", onClick: () => setView("main"), aria: "AI 会话 · 测试工作台", ico: "i-chat" })}
        {/* V5.5：用例库升为一级入口（原会话页右栏「我的任务」的资产化形态），全角色可见 */}
        {railBtn("cases", <Library />, "用例库", true, { active: view === "cases", onClick: () => setView("cases"), aria: "用例库 · 测试用例资产管理", ico: "i-cases" })}
        {railBtn("knowledge", <BookOpen />, "知识库", canKb, { active: view === "knowledge", onClick: () => setView("knowledge"), aria: role === "guest" ? "知识库（访客 · 只读共享库）" : "知识库 · 文档与问答", ico: "i-kb" })}

        {/* W3 M8：贴纸残留（含绿色铅笔/烧杯 emoji）移除，spacer 仅作弹性占位 */}
        <div className="rail-spacer" />

        {/* W3 M8：模型配置/用户管理/被测系统/源码/系统自检 沉到「设置」组（可见性规则不变） */}
        <div className="rl-group">设置</div>
        {/* V5.3：模型配置（原一级入口下沉）；全角色可见，访客只读调度摘要 */}
        {railBtn("models", <Cpu />, "模型配置", canKb, { active: view === "models", onClick: () => setView("models"), aria: "模型配置 · 模型池与调度", ico: "i-models" })}
        {/* V5.3：管理后台改名「用户管理」（页面现已只含用户管理与访客治理）；仅管理员可见 */}
        {railBtn("admin", <Shield />, "用户管理", role === "admin", { active: view === "admin", onClick: () => setView("admin"), aria: "用户管理（仅管理员）", ico: "i-admin" })}
        {/* V4.5.3 被测系统：可折叠父项 + 子项外链（TARGETS 数组，后续追加即可） */}
        <div className={"rail-sub" + (tOpen ? " open" : "")}>
          <button
            className="rail-btn"
            onClick={() => setTOpen((o) => !o)}
            aria-expanded={tOpen}
            aria-label="被测系统菜单"
          >
            <span className="rl-ico i-target"><Boxes /></span>
            <span className="rl-txt">被测系统</span>
            <ChevronDown className="rail-chev" />
            <span className="rl-tip" aria-hidden="true">被测系统</span>
          </button>
          <div className="rail-submenu">
            {TARGETS.map((t) => (
              <a
                key={t.url}
                className="rail-subitem"
                href={t.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink />
                <span className="rs-name">{t.name}</span>
              </a>
            ))}
          </div>
        </div>
        <a
          className="rail-btn"
          href={GITHUB_REPO}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="在 GitHub 上查看源码"
        >
          <span className="rl-ico i-git"><GithubMark /></span>
          <span className="rl-txt">源码</span>
          <span className="rl-tip" aria-hidden="true">在 GitHub 上查看源码</span>
        </a>
        {railBtn("selfcheck", scShow ? <Loader2 className="spin" /> : <ShieldCheck />, "系统自检", true, { onClick: runSelfCheck, aria: "系统自检（验证加密链路）", ico: "i-check" })}
        {/* V5.3：「设置」rail 按钮移除 —— 个人中心改由 rail 底部用户名区域进入 */}
        {!me || !role ? (
          <button className="rail-login" onClick={() => showLogin("login")} aria-label="登录 / 注册">
            <span className="rl-txt">登录 / 注册</span>
            <span className="rl-tip" aria-hidden="true">登录 / 注册</span>
          </button>
        ) : (
          <>
            <div className="rail-user">
              <button
                className="rail-avatar"
                onClick={() => setView("settings")}
                aria-label={`${role === "guest" ? "访客" : role === "admin" ? "管理员" : "用户"} · ${me.username}（进个人中心）`}
              >
                {role === "guest" ? <UserPlus size={16} /> : me.username.slice(0, 1).toUpperCase()}
              </button>
              <div className="rl-user-info" onClick={() => setView("settings")} role="button" tabIndex={0}>
                <div className="rl-user-name">{me.username}</div>
                <div className="rl-user-role">{role === "guest" ? "访客" : role === "admin" ? "管理员" : "用户"}</div>
              </div>
            </div>
            {railBtn("logout", <LogOut />, "退出登录", true, { onClick: logout, cls: "rail-danger", ico: "i-out" })}
          </>
        )}
      </nav>

      {/* 右侧内容区 */}
      <div className="app-body">
        {view === "main" && (
          <main className="app-main app-main-chat">
            <ConversationPicker />
            <ChatPanel showCitations />
          </main>
        )}
        {view === "cases" && <CaseLibraryPage />}
        {view === "knowledge" && <KnowledgePage />}
        {view === "models" && <ModelConfigPage />}
        {view === "settings" && <SettingsPage />}
        {view === "admin" && <AdminPage />}
      </div>

      <TaskDetailDrawer />
      {scShow && <SelfCheckToast runId={scRun} onDone={() => setScShow(false)} />}
    </div>
  );
}
