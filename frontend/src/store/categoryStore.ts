/**
 * 分类 store（zustand，M4）：多级分类树 + 任务归类 + 列表过滤。
 * 后端语义：
 * - 每用户独立树；删除分类 = 级联删子树，其下任务回落「未分类」
 * - move-task：category_id=null 表示移回未分类
 */
import { create } from "zustand";
import { api, apiJson, API, toast } from "../api/client";
import { getAuthSnapshot } from "../contexts/authState";
import type { CategoryNode } from "../types";

export type CategoryFilter = number | "all" | "none";

interface CategoryState {
  categories: CategoryNode[];
  loaded: boolean;
  /** 任务列表过滤条件 */
  filter: CategoryFilter;
  /** 折叠状态（分类 id → 是否折叠） */
  collapsed: Record<number, boolean>;

  refresh: () => Promise<void>;
  setFilter: (f: CategoryFilter) => void;
  toggleCollapse: (id: number) => void;
  /** 创建分类（顶级或子分类） */
  create: (name: string, parentId: number | null) => Promise<boolean>;
  rename: (id: number, name: string) => Promise<boolean>;
  move: (id: number, parentId: number | null) => Promise<boolean>;
  remove: (id: number) => Promise<boolean>;
  moveTask: (taskId: string, categoryId: number | null) => Promise<boolean>;
  reset: () => void;
}

export const useCategoryStore = create<CategoryState>((set, get) => ({
  categories: [],
  loaded: false,
  filter: "all",
  collapsed: {},

  async refresh() {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      set({ categories: [], loaded: false });
      return;
    }
    const rows = await apiJson<CategoryNode[]>(API + "/categories");
    if (rows) set({ categories: Array.isArray(rows) ? rows : [], loaded: true });
  },

  setFilter(f) {
    set({ filter: f });
  },

  toggleCollapse(id) {
    set((s) => ({ collapsed: { ...s.collapsed, [id]: !s.collapsed[id] } }));
  },

  async create(name, parentId) {
    const row = await apiJson<CategoryNode>(API + "/categories", {
      method: "POST",
      body: JSON.stringify({ name, parent_id: parentId }),
    });
    if (!row) return false;
    toast("分类已创建");
    await get().refresh();
    return true;
  },

  async rename(id, name) {
    const r = await apiJson<{ ok: boolean }>(API + "/categories/" + id, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    if (!r) return false;
    await get().refresh();
    return true;
  },

  async move(id, parentId) {
    const r = await apiJson<{ ok: boolean }>(API + "/categories/" + id, {
      method: "PATCH",
      body: JSON.stringify({ parent_id: parentId }),
    });
    if (!r) return false;
    await get().refresh();
    return true;
  },

  async remove(id) {
    const cat = get().categories.find((c) => c.id === id);
    const msg = cat
      ? `删除分类「${cat.name}」将级联删除其子分类，其下任务回落「未分类」，确定？`
      : "确定删除该分类？";
    if (!window.confirm(msg)) return false;
    let r: Response;
    try {
      r = await api(API + "/categories/" + id, { method: "DELETE" });
    } catch {
      toast("删除失败");
      return false;
    }
    if (!r.ok) {
      toast(`删除失败（HTTP ${r.status}）`);
      return false;
    }
    const d = (await r.json().catch(() => null)) as
      | { deleted_categories?: number; tasks_to_uncategorized?: number }
      | null;
    toast(`已删除 ${d?.deleted_categories ?? 1} 个分类，${d?.tasks_to_uncategorized ?? 0} 个任务回落未分类`);
    if (typeof get().filter === "number" && get().filter === id) set({ filter: "all" });
    await get().refresh();
    return true;
  },

  async moveTask(taskId, categoryId) {
    const r = await apiJson<{ ok: boolean }>(API + `/categories/move-task/${taskId}`, {
      method: "PUT",
      body: JSON.stringify({ category_id: categoryId }),
    });
    if (!r) return false;
    await get().refresh();
    return true;
  },

  reset() {
    set({ categories: [], loaded: false, filter: "all", collapsed: {} });
  },
}));

/** 由扁平列表构建两级树（后端未限层级，但 UI 按缩进渲染任意层级） */
export function buildTree(
  cats: CategoryNode[],
  parentId: number | null = null,
  depth = 0,
): (CategoryNode & { depth: number; children: CategoryNode[] })[] {
  return cats
    .filter((c) => c.parent_id === parentId)
    .map((c) => ({ ...c, depth, children: cats.filter((x) => x.parent_id === c.id) }));
}
