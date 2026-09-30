/**
 * 任务列表 store（zustand）。
 * - 列表：5s 轮询 GET /api/tasks（走 api() 加密透明解密）
 * - 活跃任务：创建后 2s 轮询 GET /api/tasks/{id} 直到终态，回写 chatStore 消息步骤卡
 *   （平移旧版 startMsgPoll，TTL 3 分钟）
 */
import { create } from "zustand";
import { api, API, toast } from "../api/client";
import { getAuthSnapshot } from "../contexts/authState";
import type { Task } from "../types";

const LIST_INTERVAL_MS = 5000;
const ACTIVE_INTERVAL_MS = 2000;
// 真实模型任务可达 10 分钟以上（N 个测试点 × 每次 30-90s）；TTL 放宽到 20 分钟
const ACTIVE_TTL_ROUNDS = 600; // 2s × 600 = 20 分钟

interface TaskState {
  tasks: Task[];
  listLoaded: boolean;
  /** 详情轮询中的任务（chat 步骤卡实时刷新） */
  activeIds: Set<string>;
  refreshing: boolean;
  /** M3：详情抽屉 */
  drawerTaskId: string | null;
  detail: Task | null;
  detailLoading: boolean;
  /** 导图节点 → 用例 Tab 跳转信号（每次 +1 触发 useEffect） */
  focusCaseSeq: number;
  focusCaseId: string | null;

  refresh: () => Promise<void>;
  startPolling: (taskId: string) => void;
  stopPolling: (taskId: string) => void;
  deleteTask: (taskId: string) => Promise<void>;
  retryTask: (taskId: string) => Promise<void>;
  openDetail: (taskId: string) => Promise<void>;
  closeDetail: () => void;
  /** 抽屉内活跃任务轮询（running 状态时 2s 刷新详情直至终态） */
  pollDrawer: (taskId: string) => void;
  focusCase: (caseId: string) => void;
}

/** 单个活跃任务的轮询循环 */
async function pollTask(taskId: string, onEnd: () => void): Promise<void> {
  for (let round = 0; round < ACTIVE_TTL_ROUNDS; round++) {
    await new Promise((r) => setTimeout(r, ACTIVE_INTERVAL_MS));
    try {
      const r = await api(API + "/tasks/" + taskId);
      if (!r.ok) continue;
      const t = (await r.json()) as Task;
      const { useChatStore } = await import("./chatStore");
      useChatStore.getState().updateMsgTask(taskId, t);
      if (t.status === "completed" || t.status === "failed") {
        toast(t.status === "completed" ? `任务完成：${t.name}（${t.cases_count} 个用例）已入用例库` : `任务失败：${t.name}`);
        onEnd();
        return;
      }
    } catch {
      /* 单次失败继续轮询 */
    }
  }
  onEnd(); // TTL 到期停止
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  listLoaded: false,
  activeIds: new Set<string>(),
  refreshing: false,
  drawerTaskId: null,
  detail: null,
  detailLoading: false,
  focusCaseSeq: 0,
  focusCaseId: null,

  async refresh() {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      set({ tasks: [], listLoaded: false });
      return;
    }
    if (get().refreshing) return;
    set({ refreshing: true });
    try {
      const r = await api(API + "/tasks");
      if (r.ok) {
        const list = (await r.json()) as Task[];
        set({ tasks: Array.isArray(list) ? list : [], listLoaded: true });
      }
    } catch {
      /* 静默：轮询下轮再试 */
    } finally {
      set({ refreshing: false });
    }
  },

  startPolling(taskId) {
    const active = new Set(get().activeIds);
    if (active.has(taskId)) return;
    active.add(taskId);
    set({ activeIds: active });
    void pollTask(taskId, () => {
      const cur = new Set(useTaskStore.getState().activeIds);
      cur.delete(taskId);
      set({ activeIds: cur });
      // 终态后刷一次列表
      void get().refresh();
      // 任务名/会话名在解析步骤由后端回写，同步刷新侧栏会话列表
      void import("./chatStore").then(({ useChatStore }) =>
        useChatStore.getState().refreshConversations(),
      );
    });
  },

  stopPolling(taskId) {
    const cur = new Set(get().activeIds);
    cur.delete(taskId);
    set({ activeIds: cur });
  },

  async deleteTask(taskId) {
    const r = await api(API + "/tasks/" + taskId, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) {
      toast("删除任务失败");
      return;
    }
    toast("任务已删除");
    get().stopPolling(taskId);
    if (get().drawerTaskId === taskId) get().closeDetail();
    await get().refresh();
    const { useChatStore } = await import("./chatStore");
    useChatStore.getState().refreshConversations();
  },

  async retryTask(taskId) {
    const r = await api(API + "/tasks/" + taskId + "/retry", { method: "POST" }).catch(() => null);
    if (!r || !r.ok) {
      toast("重试失败，请稍后再试");
      return;
    }
    const t = (await r.json()) as Task;
    // 更新本地任务状态为 pending，触发轮询
    set({
      tasks: get().tasks.map((x) => (x.id === taskId ? t : x)),
    });
    get().startPolling(taskId);
    toast("已重新排队，从断点继续执行");
    await get().refresh();
  },

  async openDetail(taskId) {
    set({ drawerTaskId: taskId, detailLoading: true, detail: null });
    try {
      const r = await api(API + "/tasks/" + taskId);
      if (!r.ok) {
        toast("加载任务详情失败");
        set({ detailLoading: false, drawerTaskId: null });
        return;
      }
      const t = (await r.json()) as Task;
      // 抽屉打开期间任务可能已变化（或正在 running）→ 仅在仍是当前抽屉任务时写入
      if (get().drawerTaskId !== taskId) return;
      set({ detail: t, detailLoading: false });
      if (t.status === "running" || t.status === "pending") get().pollDrawer(taskId);
    } catch {
      set({ detailLoading: false });
      toast("加载任务详情失败");
    }
  },

  closeDetail() {
    set({ drawerTaskId: null, detail: null, detailLoading: false, focusCaseId: null });
  },

  pollDrawer(taskId) {
    const timer = window.setInterval(async () => {
      // 抽屉已关 / 已切到别的任务 → 停
      if (get().drawerTaskId !== taskId) {
        window.clearInterval(timer);
        return;
      }
      try {
        const r = await api(API + "/tasks/" + taskId);
        if (!r.ok) return;
        const t = (await r.json()) as Task;
        if (get().drawerTaskId !== taskId) {
          window.clearInterval(timer);
          return;
        }
        set({ detail: t });
        if (t.status === "completed" || t.status === "failed") {
          window.clearInterval(timer);
          toast(t.status === "completed" ? `任务完成：${t.cases_count} 个用例` : "任务失败");
          void get().refresh();
        }
      } catch {
        /* 单次失败继续 */
      }
    }, 2000);
    // 兜底 5 分钟自动停
    window.setTimeout(() => window.clearInterval(timer), 300_000);
  },

  focusCase(caseId) {
    set((s) => ({ focusCaseId: caseId, focusCaseSeq: s.focusCaseSeq + 1 }));
  },
}));

/** 列表轮询的启动/停止由 ChatLayout 组件管理（随登录态生命周期） */
export function startListPolling(): () => void {
  void useTaskStore.getState().refresh();
  const timer = window.setInterval(() => void useTaskStore.getState().refresh(), LIST_INTERVAL_MS);
  return () => window.clearInterval(timer);
}
