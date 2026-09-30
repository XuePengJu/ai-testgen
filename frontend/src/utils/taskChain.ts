/**
 * 版本链工具：把「迭代链」(parent_task_id 串起来的 v1 → v2 → v3) 解析成有序版本列表。
 *
 * 数据来源只有任务列表（Task.parent_task_id），后端无需改动。
 * 用途：① 抽屉版本切换器 ② 会话流旧版本卡折叠 ③ 任务列表同链聚合。
 */
import type { Task } from "../types";

export interface ChainNode {
  id: string;
  name: string;
  /** 版本号：链根 = 1，每迭代一代 +1 */
  version: number;
  status: string;
  cases_count: number;
  created_at: string | null;
}

function ts(t: Task): string {
  return t.created_at || "";
}

/**
 * 从任意节点出发：先回溯到链根，再按创建时间收集全部后代，返回 v1…vN 有序链。
 * 任务不在列表里（如会话内轻量对象）时返回空数组。
 */
export function buildChain(tasks: Task[], taskId: string): ChainNode[] {
  if (!tasks.length || !taskId) return [];
  const byId = new Map<string, Task>();
  for (const t of tasks) byId.set(t.id, t);
  if (!byId.has(taskId)) return [];

  // ① 回溯到根（带环保护）
  let cur = byId.get(taskId) as Task;
  const seen = new Set<string>([cur.id]);
  while (cur.parent_task_id && byId.has(cur.parent_task_id) && !seen.has(cur.parent_task_id)) {
    seen.add(cur.parent_task_id);
    cur = byId.get(cur.parent_task_id) as Task;
  }
  const root = cur;

  // ② 子节点索引（按创建时间升序，保证 v1 → vN）
  const children = new Map<string, Task[]>();
  for (const t of tasks) {
    if (!t.parent_task_id) continue;
    const arr = children.get(t.parent_task_id) || [];
    arr.push(t);
    children.set(t.parent_task_id, arr);
  }
  for (const [, arr] of children) arr.sort((a, b) => ts(a).localeCompare(ts(b)));

  // ③ 深度优先展开成线性链
  const ordered: Task[] = [];
  const walk = (t: Task) => {
    ordered.push(t);
    (children.get(t.id) || []).forEach(walk);
  };
  walk(root);

  return ordered.map((t, i) => ({
    id: t.id,
    name: t.name,
    version: i + 1,
    status: t.status,
    cases_count: t.cases_count || 0,
    created_at: t.created_at || null,
  }));
}

/** 链上最新版本节点（null 表示不在链里 / 列表未加载） */
export function latestOfChain(tasks: Task[], taskId: string): ChainNode | null {
  const chain = buildChain(tasks, taskId);
  return chain.length ? chain[chain.length - 1] : null;
}

/** 该任务是否是本链最新版本；链未加载（空）时保守返回 true，避免误折叠 */
export function isLatestOfChain(tasks: Task[], taskId: string): boolean {
  const chain = buildChain(tasks, taskId);
  if (chain.length <= 1) return true;
  return chain[chain.length - 1].id === taskId;
}

/** 任务列表按链聚合：只保留每链最新版本一行，其余作为该行的历史版本 */
export interface ChainGroup {
  latest: Task;
  history: Task[]; // 旧 → 新（不含 latest）
  versions: number;
}

export function groupByChain(tasks: Task[]): ChainGroup[] {
  const byId = new Map<string, Task>();
  for (const t of tasks) byId.set(t.id, t);
  const groups: ChainGroup[] = [];
  const claimed = new Set<string>();

  for (const t of tasks) {
    if (claimed.has(t.id)) continue;
    const chain = buildChain(tasks, t.id);
    if (chain.length <= 1) {
      claimed.add(t.id);
      groups.push({ latest: t, history: [], versions: 1 });
      continue;
    }
    const latestNode = chain[chain.length - 1];
    const latest = byId.get(latestNode.id) as Task;
    const history = chain
      .slice(0, -1)
      .map((n) => byId.get(n.id))
      .filter((x): x is Task => !!x);
    chain.forEach((n) => claimed.add(n.id));
    groups.push({ latest, history, versions: chain.length });
  }
  return groups;
}
