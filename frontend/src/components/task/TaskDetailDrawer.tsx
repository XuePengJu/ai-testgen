/**
 * M3 / M5-fix：任务详情居中弹窗（遮罩 + scale 弹出，恢复 V2.7 modal 形态）。
 * - 3 Tab：思维导图（默认）/ 用例列表（表格）/ 导出
 * - running 状态：显示步骤进度卡（复用 TaskStepsCard）+ 2s 详情轮询（taskStore.pollDrawer）
 *
 * V2.10 统一入口：详情页不再有任何输入表单（迭代输入已移除），
 * 底部固定操作栏提供「💬 继续优化」—— 跳回该任务所属会话并挂载迭代引用 chip，
 * 由会话输入框承载补充需求，保证「发起工作」入口全局唯一。
 */
import { useEffect, useState } from "react";
import { useTaskStore } from "../../store/taskStore";
import { statusBadge } from "../chat/TaskStepsCard";
import { buildChain } from "../../utils/taskChain";
import TaskStepsCard from "../chat/TaskStepsCard";
import CaseListTab from "./CaseListTab";
import MindMapTab from "./MindMapTab";
import ExportTab from "./ExportTab";

type TabKey = "mindmap" | "cases" | "export";

export default function TaskDetailDrawer() {
  const drawerTaskId = useTaskStore((s) => s.drawerTaskId);
  const detail = useTaskStore((s) => s.detail);
  const detailLoading = useTaskStore((s) => s.detailLoading);
  const closeDetail = useTaskStore((s) => s.closeDetail);
  const focusCaseId = useTaskStore((s) => s.focusCaseId);
  const focusCaseSeq = useTaskStore((s) => s.focusCaseSeq);
  const tasks = useTaskStore((s) => s.tasks);
  const openDetail = useTaskStore((s) => s.openDetail);
  const [tab, setTab] = useState<TabKey>("mindmap");
  const [verOpen, setVerOpen] = useState(false);

  // Esc 关闭
  useEffect(() => {
    if (!drawerTaskId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDetail();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerTaskId, closeDetail]);

  // 点击别处 / 切换任务时收起版本菜单
  useEffect(() => {
    setVerOpen(false);
  }, [drawerTaskId]);

  useEffect(() => {
    if (!verOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".ver-switch")) setVerOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [verOpen]);

  if (!drawerTaskId) return null;

  const open = !!drawerTaskId;
  const t = detail;
  const casesCount = t?.cases?.length || t?.cases_count || 0;
  /** 迭代版本链（v1…vN）；>1 才显示版本切换器 */
  const chain = t ? buildChain(tasks, t.id) : [];
  const curVer = chain.find((n) => n.id === t?.id)?.version ?? 0;

  /** 继续优化：关闭抽屉 → 跳回 AI 会话（V5.5：抽屉可从用例库页打开）→ 回到任务所属会话 → 挂载迭代引用 chip（输入框聚焦） */
  async function onContinueOptimize(): Promise<void> {
    if (!t) return;
    const { useChatStore } = await import("../../store/chatStore");
    closeDetail();
    window.dispatchEvent(new CustomEvent("nav-to", { detail: "main" }));
    await useChatStore.getState().openIterate(t);
  }

  // 迭代前置条件：任务已终态 + 已关联会话（后端对历史任务做了反查兜底，正常不会为空）
  const iterable = !!t && (t.status === "completed" || t.status === "failed") && !!t.conversation_id;
  const iterTitle = !t
    ? "加载中"
    : !(t.status === "completed" || t.status === "failed")
      ? "任务进行中，完成后可继续优化"
      : !t.conversation_id
        ? "该任务未关联会话，无法回到会话迭代"
        : "回到会话，基于本任务已有用例继续补充（不会从零生成）";

  return (
    <div className={`drawer-mask ${open ? "show" : ""}`} onClick={closeDetail}>
      <div className={`task-drawer ${open ? "show" : ""}`} data-task-id={t?.id || ""} onClick={(e) => e.stopPropagation()}>
        {detailLoading || !t ? (
          <div className="drawer-empty" style={{ margin: "auto" }}>加载任务详情…</div>
        ) : (
          <>
            <div className="drawer-head">
              <div className="drawer-title-row">
                <span className="drawer-title">{t.name}</span>
                {chain.length > 1 ? (
                  <div className="ver-switch">
                    <button
                      type="button"
                      className="ver-btn"
                      title="切换该任务的迭代版本"
                      aria-expanded={verOpen}
                      onClick={() => setVerOpen((v) => !v)}
                    >
                      {curVer ? `v${curVer}` : "版本"} ▾
                    </button>
                    {verOpen && (
                      <div className="ver-menu" role="menu">
                        {chain
                          .slice()
                          .reverse()
                          .map((n) => (
                            <button
                              key={n.id}
                              type="button"
                              role="menuitem"
                              className={`ver-item ${n.id === t.id ? "active" : ""}`}
                              onClick={() => {
                                setVerOpen(false);
                                if (n.id !== t.id) void openDetail(n.id);
                              }}
                            >
                              <span className="vi-ver">v{n.version}</span>
                              <span className="vi-name">{n.name}</span>
                              <span className="vi-meta">
                                {n.status === "completed"
                                  ? `${n.cases_count} 条`
                                  : n.status === "running"
                                    ? "生成中"
                                    : n.status === "failed"
                                      ? "失败"
                                      : "排队中"}
                              </span>
                            </button>
                          ))}
                      </div>
                    )}
                  </div>
                ) : (
                  t.parent_task_id && <span className="pill pill-sub" title={`迭代自 ${t.parent_task_id}`}>迭代</span>
                )}
                <span className={`pill pill-${statusBadge(t.status).cls}`}>{statusBadge(t.status).text}</span>
                <span className="dh-spacer" />
                <button type="button" className="drawer-close" onClick={closeDetail} title="关闭（Esc）">
                  ×
                </button>
              </div>
              <div className="drawer-meta">
                {t.status === "completed" && <span>{casesCount} 个用例</span>}
                {t.duration_ms > 0 && <span>耗时 {(t.duration_ms / 1000).toFixed(1)}s</span>}
                <span>{t.kind === "business" ? "业务用例" : "接口用例"}</span>
                <span>{t.source_type === "iterate" ? "迭代生成" : t.source_type === "chat" ? "对话生成" : t.source_type}</span>
              </div>
            </div>

            {(t.status === "running" || t.status === "pending") && (
              <div className="drawer-running">
                <TaskStepsCard task={t} />
              </div>
            )}

            <div className="drawer-tabs">
              <button
                type="button"
                className={`dtab ${tab === "mindmap" ? "active" : ""}`}
                onClick={() => setTab("mindmap")}
              >
                思维导图
              </button>
              <button
                type="button"
                className={`dtab ${tab === "cases" ? "active" : ""}`}
                onClick={() => setTab("cases")}
              >
                用例列表{t.cases?.length ? `（${t.cases.length}）` : ""}
              </button>
              <button
                type="button"
                className={`dtab ${tab === "export" ? "active" : ""}`}
                onClick={() => setTab("export")}
              >
                导出
              </button>
            </div>

            <div className="drawer-body">
              {tab === "mindmap" && <MindMapTab task={t} />}
              {tab === "cases" && <CaseListTab task={t} focusCaseId={focusCaseId} focusSeq={focusCaseSeq} />}
              {tab === "export" && <ExportTab task={t} />}
            </div>

            {/* 底部固定操作栏：三个 Tab 都可见，切 Tab 不消失 —— 迭代的唯一入口 */}
            <div className="drawer-footer">
              {/* 思维导图 Tab 的画布控件经 portal 挂到这里（存/全屏/缩放/百分比/居中） */}
              <div id="mm-ctrl-slot" className="mm-ctrl-slot" />
              <span className="df-hint">需要补充用例？</span>
              <button
                type="button"
                className="btn-primary btn-md"
                disabled={!iterable}
                title={iterTitle}
                onClick={() => void onContinueOptimize()}
              >
                💬 继续优化（回到会话）
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
