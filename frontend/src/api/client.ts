/**
 * api client：平移自旧前端 api() 封装（frontend-legacy/index.html）。
 *
 * 行为契约（V2.1 分级加密，逐条对齐旧实现）：
 * 1. 有 token → Authorization: Bearer
 * 2. cryptoOn（token+encKey+非 admin）→ JSON 请求体加密为 {enc:...}；FormData 保持明文
 * 3. 401 → 默认清登录态 + toast + 1.2s 后重载；keep401=true 时原样返回由调用方处理
 * 4. 响应 {"enc":...}（单键）→ 透明解密重建 Response；无密钥遇密文 → 重新登录
 */
import { aesGcmEncrypt, aesGcmDecrypt } from "../crypto/aesGcm";
import { getAuthSnapshot, type AuthSnapshot } from "../contexts/authState";

export const API = "/api";

function cryptoOn(snap: AuthSnapshot): boolean {
  return !!(snap.token && snap.encKey && snap.me && snap.me.role !== "admin");
}

export interface ApiOptions extends Omit<RequestInit, "body"> {
  body?: BodyInit | null;
  /** true：401 不强制登出，由调用方处理（如改密"旧密码错误"） */
  keep401?: boolean;
}

export async function api(path: string, opts: ApiOptions = {}): Promise<Response> {
  const snap = getAuthSnapshot();
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  if (snap.token) headers["Authorization"] = "Bearer " + snap.token;

  let o: RequestInit & { keep401?: boolean } = { ...opts };
  const keep401 = !!opts.keep401;
  delete (o as Record<string, unknown>).keep401;

  // 字符串 body 一律声明 JSON（admin 不加密时同样需要，否则 text/plain → 422）；
  // 加密开启时再把明文 JSON 包成 {enc:...}
  if (opts.body && typeof opts.body === "string") {
    headers["Content-Type"] = "application/json";
    if (cryptoOn(snap)) {
      o = { ...o, body: JSON.stringify({ enc: aesGcmEncrypt(snap.encKey!, opts.body) }) };
    }
  }
  o = { ...o, headers };

  const r = await fetch(path, o);

  if (r.status === 401) {
    if (keep401) {
      // keep401：原样交给调用方，但密文体先透明解密（如改密码"旧密码错误"detail）
      try {
        const d = await r.clone().json();
        if (cryptoOn(snap) && d && d.enc && typeof d.enc === "string") {
          return new Response(aesGcmDecrypt(snap.encKey!, d.enc), {
            status: r.status,
            statusText: r.statusText,
            headers: r.headers,
          });
        }
      } catch {
        /* 非 JSON 响应体，原样返回 */
      }
      return r;
    }
    let detail = "";
    try {
      let d = await r.clone().json();
      if (cryptoOn(snap) && d && d.enc) {
        try {
          d = JSON.parse(aesGcmDecrypt(snap.encKey!, d.enc));
        } catch {
          /* 解不开就当没有 detail */
        }
      }
      detail = (d && d.detail) || "";
    } catch {
      /* 非 JSON 响应体 */
    }
    window.dispatchEvent(new CustomEvent("aitf-logout-invalid"));
    toast(detail || "登录已失效，请重新登录");
    setTimeout(() => location.reload(), 1200);
    throw new Error(detail || "401");
  }

  // 加密响应 {"enc": ...} 统一处理：有密钥→透明解密；无密钥（版本升级等边界）→重新登录
  if ((r.headers.get("content-type") || "").includes("application/json")) {
    try {
      const d = await r.clone().json();
      if (d && typeof d === "object" && "enc" in d && Object.keys(d).length === 1) {
        if (!cryptoOn(snap)) {
          window.dispatchEvent(new CustomEvent("aitf-logout-invalid"));
          toast("登录状态需要刷新，请重新登录");
          setTimeout(() => location.reload(), 1200);
          throw new Error("no-key");
        }
        return new Response(aesGcmDecrypt(snap.encKey!, d.enc), {
          status: r.status,
          statusText: r.statusText,
          headers: r.headers,
        });
      }
    } catch (e) {
      if (e instanceof Error && e.message === "no-key") throw e;
    }
  }
  return r;
}

/** 轻量 toast（事件驱动，避免为 client 引入 React 依赖环） */
export function toast(msg: string): void {
  window.dispatchEvent(new CustomEvent("aitf-toast", { detail: msg }));
}

/**
 * M4：JSON 便捷封装。ok → 解析返回；非 ok → toast 错误 detail 并返回 null。
 * 统一处理 422 数组校验信息（避免显示 [object Object]）。
 */
export async function apiJson<T>(path: string, opts: ApiOptions = {}): Promise<T | null> {
  let r: Response;
  try {
    r = await api(path, opts);
  } catch {
    return null;
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const d = (await r.json()) as { detail?: unknown };
      if (typeof d.detail === "string") detail = d.detail;
      else if (Array.isArray(d.detail)) {
        const first = d.detail[0] as { msg?: string } | undefined;
        detail = first?.msg || detail;
      }
    } catch {
      /* 非 JSON 错误体 */
    }
    toast(detail);
    return null;
  }
  if (r.status === 204) return null;
  try {
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

/**
 * M3：任务导出文件下载。GET /api/tasks/{id}/download?fmt=xlsx|json|xmind
 * - FileResponse 二进制流，中间件只加密 JSON → 响应天然明文透传
 * - GET 无请求体，不受请求侧加密影响；仅需 Bearer
 */
export async function downloadTaskFile(taskId: string, fmt: string, taskName: string): Promise<void> {
  const snap = getAuthSnapshot();
  const headers: Record<string, string> = {};
  if (snap.token) headers["Authorization"] = "Bearer " + snap.token;
  const r = await fetch(`${API}/tasks/${taskId}/download?fmt=${encodeURIComponent(fmt)}`, { headers });
  if (!r.ok) {
    let detail = `下载失败（HTTP ${r.status}）`;
    try {
      const d = await r.json();
      if (d && typeof d.detail === "string") detail = d.detail;
    } catch { /* 非 JSON 错误体 */ }
    toast(detail);
    return;
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${taskName || taskId}.${fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
