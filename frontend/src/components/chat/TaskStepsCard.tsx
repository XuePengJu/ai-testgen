/**
 * 任务步骤卡：四 Agent 编排节点实时进度（平移旧版 renderLiveSteps）。
 * 数据源：chatStore 消息上的 task（taskStore 轮询回写）。
 * 方案 B：每个步骤可折叠，默认展开，展开区显示该步思考详情（输入/输出/错误）。
 */
import { useEffect, useState } from "react";
import type { Task } from "../../types";
import { useTaskStore } from "../../store/taskStore";
import { isLatestOfChain } from "../../utils/taskChain";
import { parseServerTime } from "../../utils/time";

/** 毫秒 → 人类可读：<1s 显示 ms，<60s 显示 Xs，否则 mm:ss */
function fmtDuration(ms: number): string {
  if (ms <= 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toString().padStart(2, "0")}`;
}

/** 默认步骤标题集（数据驱动渲染的兜底）：普通任务四步 / e2e 全链路六步。
 *  实际渲染以任务返回的 steps[].title 为准（老任务四步显示不变，向后兼容）。 */
const BASE_STEP_TITLES = ["解析规格", "AI 生成用例", "质量校验", "导出文件"];
const E2E_STEP_TITLES = ["抓取页面", "解析需求", "AI生成用例", "质量校验", "脚本生成", "导出文件"];
/** M5 探索式测试三步（与 engine.STEPS_EXPLORE 对齐） */
const EXPLORE_STEP_TITLES = ["探索式测试", "脚本生成", "导出文件"];
const STEP_ICONS: Record<string, string> = {
  解析规格: "🔍",
  "AI 生成用例": "✨",
  质量校验: "🛡️",
  导出文件: "📦",
  抓取页面: "🌐",
  解析需求: "🔍",
  AI生成用例: "✨",
  脚本生成: "📜",
  探索式测试: "🧭",
  生成探索计划: "🗺️",
};
const RUNNING_HINTS: Record<string, string> = {
  解析规格: "⏳ 正在拆解需求...",
  "AI 生成用例": "✨ 正在生成用例...",
  质量校验: "🛡️ 正在质量校验...",
  导出文件: "📦 正在导出文件...",
  抓取页面: "🌐 正在抓取页面...",
  解析需求: "⏳ 正在解析页面功能...",
  AI生成用例: "✨ 正在生成用例...",
  脚本生成: "📜 正在生成脚本...",
  探索式测试: "🧭 正在探索页面（ReAct 循环）...",
  生成探索计划: "🗺️ 正在生成探索计划...",
};
/** M3 计划先行：explore 任务探索步骤等待确认时的提示文案 */
const AWAITING_CONFIRM_HINT = "⏸ 探索计划已生成，等待确认后开始";

/** V1.3：生成中预览最多展示几条（其余折叠成一行提示） */
const PREVIEW_MAX = 6;

/** 从后端进度文案里取子任务计数（形如「正在为第 4/7 个子任务生成用例」）。
 *  取不到返回 null（老任务/未开始），此时不渲染进度条。文案来源见后端 engine.py 的 _progress。 */
function parseSubProgress(progress?: string | null): { done: number; total: number } | null {
  const m = /第\s*(\d+)\s*\/\s*(\d+)\s*个子任务/.exec(progress || "");
  if (!m) return null;
  return { done: Number(m[1]), total: Number(m[2]) };
}

export function statusBadge(status: string): { text: string; cls: string } {
  if (status === "completed") return { text: "✓ 已完成", cls: "ok" };
  if (status === "running") return { text: "⏳ 生成中", cls: "run" };
  if (status === "failed") return { text: "✗ 失败", cls: "fail" };
  return { text: "排队中", cls: "sub" };
}

interface StepInfo {
  status: string;
  /** running 期间实时子进度（后端 StepLog.progress，轮询回写） */
  progress?: string | null;
  started_at?: string | null;
  duration_ms?: number | null;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

/** showIterate：会话内任务卡传 true（挂「继续优化」→ 挂 chip）；详情页 running 卡不传（避免重复入口） */
export default function TaskStepsCard({ task, showIterate = false }: { task: Task; showIterate?: boolean }) {
  const tasks = useTaskStore((s) => s.tasks);
  const openDetail = useTaskStore((s) => s.openDetail);
  const retryTask = useTaskStore((s) => s.retryTask);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggle = (title: string) => setExpanded((prev) => ({ ...prev, [title]: !prev[title] }));
  // 会话流里同一条迭代链的旧版本卡折叠成一行细条：入口只留最新一张完整卡，避免翻页找入口。
  // 抽屉内的卡（showIterate=false）不做折叠，那里由版本切换器导航。
  const stale = showIterate && !isLatestOfChain(tasks, task.id);
  // 步骤卡优先用列表轮询里的最新任务：列表 5s 轮询永不停，完成后的 steps 全量都在；
  // 活跃任务轮询（pollTask）有 TTL，到期后卡片不能停在旧 running 状态。
  const latest = tasks.find((t) => t.id === task.id);
  const live = latest && latest.steps && latest.steps.length > 0 ? latest : task;
  // 实时计时：进行中的任务/步骤每秒刷新一次时间戳，刷新页面后从 started_at 继续计时
  const [now, setNow] = useState(Date.now());
  const isLive = live.status === "running" || live.status === "pending";
  useEffect(() => {
    if (!isLive) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isLive]);
  const badge = statusBadge(live.status);
  const stepMap: Record<string, StepInfo & { started_at?: string | null }> = {};
  (live.steps || []).forEach((s) => {
    if (s.title)
      stepMap[s.title] = {
        status: s.status,
        progress: s.progress,
        started_at: s.started_at,
        input_summary: s.input_summary,
        output_summary: s.output_summary,
        error: s.error,
      };
  });

  // 数据驱动步骤标题：默认集按 kind 兜底（pending 时 steps 为空也能显示骨架），
  // 后端实际返回的 steps[].title 按出现顺序渲染在默认集之后（向后兼容老任务四步）。
  const defaultTitles =
    live.kind === "e2e" ? E2E_STEP_TITLES
    : live.kind === "explore" ? EXPLORE_STEP_TITLES
    : BASE_STEP_TITLES;
  const titles = [...defaultTitles];
  (live.steps || []).forEach((s) => {
    if (s.title && !titles.includes(s.title)) titles.push(s.title);
  });
  // M3 计划先行：「生成探索计划」是 explore 任务的首步骤，后端返回后插到最前
  const planTitleIdx = titles.indexOf("生成探索计划");
  if (planTitleIdx > 0) {
    titles.splice(planTitleIdx, 1);
    titles.unshift("生成探索计划");
  }

  // 未配置可用模型：解析/生成步骤的摘要会带「未配置可用模型」提示 → 卡片顶部展示醒目提示条
  const mockNotice = (live.steps || []).some((s) =>
    `${s.output_summary || ""}${s.input_summary || ""}${s.error || ""}`.includes("未配置可用模型")
  );

  // V1.3 生成过程可见性：过程态用例只在详情接口返回（列表轮询不带，避免大 payload）。
  // 消息内的 task 由 taskStore 的 2s 详情轮询回写，所以优先取它；抽屉内则两者同源。
  const preview = task.partial_cases?.length
    ? task.partial_cases
    : (live.partial_cases || []);
  const genProgress = parseSubProgress(stepMap["AI 生成用例"]?.progress);

  // 旧版本折叠态：一行细条，点击直接开抽屉看该版本（抽屉内可切回最新版）
  if (stale) {
    return (
      <div className="task-card-stale" data-task-card={task.id}>
        <span className="tcs-icon" aria-hidden="true">📜</span>
        <span className="tcs-name">{task.name}</span>
        <span className="tcs-meta">
          {task.status === "completed" ? `${task.cases_count || 0} 条用例 · ` : ""}已被新版本取代
        </span>
        <button
          type="button"
          className="tcs-open"
          title="在详情里查看该版本（可切换其他版本）"
          onClick={(e) => {
            e.stopPropagation();
            void openDetail(task.id);
          }}
        >
          查看 ▸
        </button>
      </div>
    );
  }

  return (
    <div className="task-steps-card" data-task-card={task.id}>
      <div className="tsc-head">
        <span className="tsc-name">{live.name}</span>
        {live.status === "running" && live.created_at && (
          <span className="tsc-timer">
            ⏱ {fmtDuration(now - (parseServerTime(live.created_at)?.getTime() ?? now))}
          </span>
        )}
        {live.status === "completed" && live.duration_ms > 0 && (
          <span className="tsc-timer">⏱ {fmtDuration(live.duration_ms)}</span>
        )}
        <span className={`pill pill-${badge.cls}`}>{badge.text}</span>
        {live.status === "completed" && <span className="tsc-cnt">共 {live.cases_count || 0} 个用例</span>}
        {live.status === "failed" && (
          <button
            type="button"
            className="tsc-retry-btn"
            title="从断点重试（保留已完成的解析步骤）"
            onClick={(e) => {
              e.stopPropagation();
              void retryTask(live.id);
            }}
          >
            🔄 重试
          </button>
        )}
      </div>
      {mockNotice && (
        <div className="tsc-mock-tip">⚠️ 未配置可用模型，当前为模拟生成。请到「模型设置」配置真实模型后重新生成。</div>
      )}
      <div className="tsc-steps">
        {titles.map((title) => {
          const s = stepMap[title];
          const st = s ? s.status : "pending";
          const ring =
            st === "completed" ? "✓"
            : st === "failed" ? "!"
            : st === "awaiting_confirm" ? "⏸"
            : STEP_ICONS[title] || "•";
          const open = expanded[title] !== false;
          const hasDetail = !!(s?.input_summary || s?.output_summary || s?.error);
          return (
            <div key={title} className={`tsc-step tsc-${st}`}>
              <button type="button" className="tsc-step-head" onClick={() => toggle(title)}>
                <span className="tsc-ring">{ring}</span>
                <span className="tsc-title">{title}</span>
                {st === "running" && s?.started_at && (
                  <span className="tsc-step-time">
                    {fmtDuration(now - (parseServerTime(s.started_at)?.getTime() ?? now))}
                  </span>
                )}
                {st === "completed" && s?.duration_ms != null && s.duration_ms > 0 && (
                  <span className="tsc-step-time">{fmtDuration(s.duration_ms)}</span>
                )}
                <span className="tsc-hint">
                  {st === "awaiting_confirm"
                    ? AWAITING_CONFIRM_HINT
                    : st === "running"
                    ? (live.status === "failed" ? "已中断" : (s?.progress || RUNNING_HINTS[title]))
                    : s?.error ? "失败" : ""}
                </span>
                <span className={`tsc-toggle ${open ? "open" : ""}`}>{open ? "▾" : "▸"}</span>
              </button>
              {open && (
                <div className="tsc-step-detail">
                  {st === "running" && live.status !== "failed" && (
                    <div className="tsc-io-text tsc-muted">{s?.progress || RUNNING_HINTS[title]}</div>
                  )}
                  {st === "running" && live.status === "failed" && (
                    <div className="tsc-io-text tsc-muted">任务已中断，可点击上方「🔄 重试」从断点继续</div>
                  )}
                  {s?.input_summary && (
                    <div className="tsc-io">
                      <span className="tsc-io-label">📥 输入</span>
                      <div className="tsc-io-text">{s.input_summary}</div>
                    </div>
                  )}
                  {s?.output_summary && (
                    <div className="tsc-io">
                      <span className="tsc-io-label">📤 输出</span>
                      <div className="tsc-io-text">{s.output_summary}</div>
                    </div>
                  )}
                  {s?.error && (
                    <div className="tsc-io tsc-io-err">
                      <span className="tsc-io-label">✗ 错误</span>
                      <div className="tsc-io-text">{s.error}</div>
                    </div>
                  )}
                  {st === "completed" && !hasDetail && (
                    <div className="tsc-io-text tsc-muted">（暂无思考记录）</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {/* V1.3 生成过程可见性：并发生成时子任务的「完成顺序」不等于「顺序」，
          所以这里用「计数 + 进度条」，不给逐点落位那种会误导顺序的暗示。 */}
      {isLive && genProgress && (
        <div className="tsc-subprog" data-testid="gen-subprog">
          <div className="tsc-subprog-head">
            <span className="tsc-subprog-text">
              子任务 {genProgress.done}/{genProgress.total} 个已完成
            </span>
            <span className="tsc-subprog-cnt">已产出 {preview.length} 条用例</span>
          </div>
          <div className="tsc-bar">
            <div
              className="tsc-bar-fill"
              style={{ width: `${Math.round((genProgress.done / genProgress.total) * 100)}%` }}
            />
          </div>
        </div>
      )}
      {isLive && preview.length > 0 && (
        <div className="tsc-preview" data-testid="gen-preview">
          <div className="tsc-preview-head">
            <span>已产出的用例（实时）</span>
            <span className="tsc-preview-note">编号待收尾统一排定</span>
          </div>
          <ul className="tsc-preview-list">
            {preview.slice(0, PREVIEW_MAX).map((c, i) => (
              <li key={`${c.title}-${i}`} className="tsc-preview-item">
                <span className="tsc-preview-title">{c.title}</span>
                <span className="tsc-preview-tags">
                  {c.module && <span className="tsc-tag">{c.module}</span>}
                  {c.case_type && <span className="tsc-tag">{c.case_type}</span>}
                  {c.priority && <span className="tsc-tag tsc-tag-pri">{c.priority}</span>}
                </span>
              </li>
            ))}
          </ul>
          {preview.length > PREVIEW_MAX && (
            <div className="tsc-preview-more">
              还有 {preview.length - PREVIEW_MAX} 条，完成后可在详情页查看全部
            </div>
          )}
        </div>
      )}
      {live.status === "completed" && (
        <div className="tsc-actions">
          <button
            type="button"
            className="qtag confirm-btn"
            onClick={(e) => {
              e.stopPropagation();
              void import("../../store/taskStore").then((m) => m.useTaskStore.getState().openDetail(task.id));
            }}
          >
            查看用例 / 思维导图 →
          </button>
          {/* V5.5 用例库资产化：完成即入库，引导去用例库管理（筛选/评审/迭代） */}
          <button
            type="button"
            className="qtag"
            title="用例已自动入用例库（草稿），去库中评审管理"
            onClick={(e) => {
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent("nav-to", { detail: "cases" }));
            }}
          >
            📚 用例库
          </button>
          {/* 会话内闭环：直接挂载迭代引用 chip，用户无需先开详情抽屉 */}
          {showIterate && (
            <button
              type="button"
              className="qtag"
              title="基于该任务继续补充用例（在输入框中说明补充内容后发送）"
              onClick={(e) => {
                e.stopPropagation();
                void import("../../store/chatStore").then((m) => {
                  void m.useChatStore.getState().openIterate(task);
                });
              }}
            >
              💬 继续优化
            </button>
          )}
        </div>
      )}
    </div>
  );
}
