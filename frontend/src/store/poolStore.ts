/**
 * 模型池 store（V5.0 P2）：个人池 / 平台池共用一套接口，按 `${mode}:${slot}` 缓存列表。
 *
 * 与 settingsStore 的分工：settingsStore 管「单条配置」（池为空时的回落），
 * 本 store 管「池」。二者都有时以池为准（后端 build_client 同口径）。
 *
 * 端点映射（app/api/llm_pool.py）：
 *   personal → /api/llm/pool/{slot}
 *   platform → /api/llm/platform-pool/{slot}
 * 写操作后一律重拉列表（服务端会重算 priority / cooling / effective，本地拼不准）。
 */
import { create } from "zustand";
import { api, apiJson, API, toast } from "../api/client";
import { getAuthSnapshot } from "../contexts/authState";
import type { LLMPoolHealth, LLMPoolItem, LLMPoolItemIn } from "../types";

export type PoolMode = "personal" | "platform";
export type PoolSlot = "text" | "vision" | "embedding";

export interface PoolTestResult {
  ok: boolean;
  error?: string;
  error_label?: string;
  err_type?: string;
  latency_ms?: number;
  model?: string | null;
}

/** 批量添加结果（V5.12）：成功 / 重复跳过 / 失败明细 */
export interface PoolBatchAddResult {
  ok: number;
  dup: number;
  fail: string[];
}

const keyOf = (mode: PoolMode, slot: PoolSlot) => `${mode}:${slot}`;
const basePath = (mode: PoolMode) => (mode === "platform" ? "/llm/platform-pool" : "/llm/pool");

interface PoolState {
  items: Record<string, LLMPoolItem[]>;
  loaded: Record<string, boolean>;

  /** force=false 时若已加载则跳过（进入页面用）；写操作后用 force=true 重拉 */
  load: (mode: PoolMode, slot: PoolSlot, force?: boolean) => Promise<void>;
  add: (mode: PoolMode, slot: PoolSlot, body: LLMPoolItemIn) => Promise<boolean>;
  /** V5.12 批量添加：逐条 POST，409 记为重复跳过；只发一次汇总 toast，最后统一重拉 */
  addBatch: (mode: PoolMode, slot: PoolSlot, bodies: LLMPoolItemIn[]) => Promise<PoolBatchAddResult | null>;
  update: (mode: PoolMode, slot: PoolSlot, id: number, body: LLMPoolItemIn) => Promise<boolean>;
  remove: (mode: PoolMode, slot: PoolSlot, id: number) => Promise<boolean>;
  toggle: (mode: PoolMode, slot: PoolSlot, id: number, enabled: boolean) => Promise<boolean>;
  reorder: (mode: PoolMode, slot: PoolSlot, ids: number[]) => Promise<boolean>;
  test: (mode: PoolMode, slot: PoolSlot, id: number) => Promise<PoolTestResult | null>;
  health: (mode: PoolMode, slot: PoolSlot) => Promise<LLMPoolHealth | null>;
  reset: () => void;
}

export const usePoolStore = create<PoolState>((set, get) => ({
  items: {},
  loaded: {},

  async load(mode, slot, force = false) {
    const k = keyOf(mode, slot);
    if (!force && get().loaded[k]) return;
    const snap = getAuthSnapshot();
    if (!snap.token) return;
    const rows = await apiJson<LLMPoolItem[]>(`${API}${basePath(mode)}/${slot}`);
    if (rows === null) return; // 请求失败（403/网络）→ 不置 loaded，下次进入可重试
    set((s) => ({
      items: { ...s.items, [k]: Array.isArray(rows) ? rows : [] },
      loaded: { ...s.loaded, [k]: true },
    }));
  },

  async add(mode, slot, body) {
    const row = await apiJson<LLMPoolItem>(`${API}${basePath(mode)}/${slot}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!row) return false;
    toast("已添加到模型池");
    await get().load(mode, slot, true);
    return true;
  },

  async addBatch(mode, slot, bodies) {
    const out: PoolBatchAddResult = { ok: 0, dup: 0, fail: [] };
    for (const body of bodies) {
      let r: Response;
      try {
        r = await api(`${API}${basePath(mode)}/${slot}`, {
          method: "POST",
          body: JSON.stringify(body),
        });
      } catch {
        // 401 会在 api() 内走登出流程后 throw；网络错误兜底计入失败
        out.fail.push(`${body.model}: 网络错误`);
        continue;
      }
      if (r.ok) {
        out.ok++;
        continue;
      }
      let detail = `HTTP ${r.status}`;
      try {
        const d = (await r.json()) as { detail?: unknown };
        if (typeof d?.detail === "string") detail = d.detail;
      } catch { /* 非 JSON 错误体 */ }
      if (r.status === 409) {
        out.dup++;                 // 同端点 + 同模型 + 同 Key：静默跳过
      } else {
        out.fail.push(`${body.model}: ${detail}`);
      }
    }
    if (out.ok > 0) await get().load(mode, slot, true);
    const parts = [`成功 ${out.ok} 条`];
    if (out.dup) parts.push(`跳过重复 ${out.dup} 条`);
    if (out.fail.length) parts.push(`失败 ${out.fail.length} 条`);
    if (out.ok > 0 || out.dup > 0) {
      toast(parts.join(" · "));
      if (out.fail.length) toast(`失败明细：${out.fail[0]}${out.fail.length > 1 ? ` 等 ${out.fail.length} 条` : ""}`);
    } else if (out.fail.length) {
      toast(out.fail[0]);
    }
    return out;
  },

  async update(mode, slot, id, body) {
    const row = await apiJson<LLMPoolItem>(`${API}${basePath(mode)}/${slot}/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    if (!row) return false;
    toast("已保存");
    await get().load(mode, slot, true);
    return true;
  },

  async remove(mode, slot, id) {
    let r: Response;
    try {
      r = await api(`${API}${basePath(mode)}/${slot}/${id}`, { method: "DELETE" });
    } catch {
      toast("删除失败");
      return false;
    }
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const d = (await r.json()) as { detail?: string | Array<{ msg?: string }> };
        if (typeof d?.detail === "string") detail = d.detail;
      } catch { /* 非 JSON 错误体 */ }
      toast(detail);
      return false;
    }
    toast("已删除");
    await get().load(mode, slot, true);
    return true;
  },

  async toggle(mode, slot, id, enabled) {
    const row = await apiJson<LLMPoolItem>(`${API}${basePath(mode)}/${slot}/${id}/enabled`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
    if (!row) return false;
    await get().load(mode, slot, true);
    return true;
  },

  async reorder(mode, slot, ids) {
    const rows = await apiJson<LLMPoolItem[]>(`${API}${basePath(mode)}/${slot}/reorder`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    if (!rows) return false;
    // 排序接口直接返回新顺序，就地更新即可（省一次往返）
    set((s) => ({ items: { ...s.items, [keyOf(mode, slot)]: rows } }));
    return true;
  },

  async test(mode, slot, id) {
    return apiJson<PoolTestResult>(`${API}${basePath(mode)}/${slot}/${id}/test`, {
      method: "POST",
    });
  },

  async health(mode, slot) {
    return apiJson<LLMPoolHealth>(`${API}${basePath(mode)}/${slot}/health`);
  },

  reset() {
    set({ items: {}, loaded: {} });
  },
}));
