/**
 * 认证状态的模块级快照（非 React 存储）。
 *
 * 为什么需要：api/client.ts 在 fetch 时要读当前 token/encKey（含加密开关判断），
 * 而 client 被 React 组件调用——如果 client 反过来 import Context 会形成依赖环。
 * 方案：AuthContext 每次状态变化时同步写这个快照，client 只读快照。
 */
import type { Me } from "../types";

export interface AuthSnapshot {
  token: string;
  encKey: string;
  me: Me | null;
}

let snapshot: AuthSnapshot = { token: "", encKey: "", me: null };

export function setAuthSnapshot(s: AuthSnapshot): void {
  snapshot = s;
}

export function getAuthSnapshot(): AuthSnapshot {
  return snapshot;
}

/* ===== localStorage 持久化（key 与旧前端完全一致，无缝衔接存量登录态） ===== */
export const TOKEN_KEY = "aitf_token";
export const USER_KEY = "aitf_user";
export const ENCKEY_KEY = "aitf_enc_key";

export function readPersisted(): AuthSnapshot {
  let me: Me | null = null;
  try {
    me = JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    me = null;
  }
  return {
    token: localStorage.getItem(TOKEN_KEY) || "",
    encKey: localStorage.getItem(ENCKEY_KEY) || "",
    me,
  };
}

export function persist(s: AuthSnapshot): void {
  if (s.token) localStorage.setItem(TOKEN_KEY, s.token);
  else localStorage.removeItem(TOKEN_KEY);
  if (s.me) localStorage.setItem(USER_KEY, JSON.stringify(s.me));
  else localStorage.removeItem(USER_KEY);
  // enc_key：登录响应里带就更新；否则保留既有（访客转正 user_id 不变，密钥无缝延续）
  if (s.me && s.me.role !== undefined) {
    const encFromAuth = (s.me as Me & { enc_key?: string }).enc_key;
    if (encFromAuth) localStorage.setItem(ENCKEY_KEY, encFromAuth);
  }
  if (!s.token) localStorage.removeItem(ENCKEY_KEY);
}

export function clearPersisted(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ENCKEY_KEY);
}
