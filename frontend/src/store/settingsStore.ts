/**
 * 设置页 store（zustand）：LLM 厂商预设 + 生效模型（M4）。
 * 无轮询——进入页面拉一次，操作后定向刷新。
 * V5.4：单条配置（myConfigs / saveConfig / deleteConfig）已随功能下线移除，
 * 模型池数据由 poolStore 管理（见 poolStore.ts）。
 */
import { create } from "zustand";
import { apiJson, API } from "../api/client";
import type { LLMEffective, ProviderMap } from "../types";

interface SettingsState {
  providers: ProviderMap;
  providersLoaded: boolean;
  effective: LLMEffective | null;

  loadProviders: () => Promise<void>;
  loadEffective: () => Promise<void>;
  /** 连通测试（表单值，不落库）；kind=chat|embedding（V4.0 向量模型走 /embeddings） */
  testConfig: (body: { provider: string; base_url: string; model: string; api_key: string; kind?: string }) => Promise<{
    ok: boolean;
    error?: string;
    latency_ms?: number;
  } | null>;
  /** 测当前生效模型（服务端解析，无需 Key） */
  testDefault: (slot: string) => Promise<{
    ok: boolean;
    error_label?: string;
    model?: string | null;
    latency_ms?: number;
  } | null>;
  reset: () => void;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  providers: {},
  providersLoaded: false,
  effective: null,

  async loadProviders() {
    if (get().providersLoaded) return;
    const p = await apiJson<ProviderMap>(API + "/llm/providers");
    if (p) set({ providers: p, providersLoaded: true });
  },

  async loadEffective() {
    const eff = await apiJson<LLMEffective>(API + "/llm/effective");
    if (eff) set({ effective: eff });
  },

  async testConfig(body) {
    return apiJson<{ ok: boolean; error?: string; latency_ms?: number }>(API + "/llm/test", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async testDefault(slot) {
    return apiJson<{ ok: boolean; error_label?: string; model?: string | null; latency_ms?: number }>(
      API + `/llm/test-default/${slot}`,
      { method: "POST" },
    );
  },

  reset() {
    set({ providers: {}, providersLoaded: false, effective: null });
  },
}));
