/**
 * SSE 流式请求（平移自旧前端 sendChat 内嵌逻辑）。
 *
 * 契约：POST /api/chat/stream 是后端中间件 _PLAINTEXT_PATHS 豁免的明文通道
 * （响应 text/event-stream 无法加密），因此这里直接 fetch，不走 api() 加密封装。
 *
 * 事件协议（app/api/chat.py）：
 *   event: delta  data: {"content": "..."}          增量文本
 *   event: error  data: {"message": "..."}          错误
 *   event: done   data: {"full": "...", "source": "mock|deepseek", "had_error"?: true}
 *   事件以空行（\n\n）分隔。
 */

export interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

/** 平移旧版 parseSSE：解析一段（不含 \n\n 分隔符的）SSE 原文 */
export function parseSSE(raw: string): SseEvent | null {
  let event = "message";
  let dataStr = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataStr += (dataStr ? "\n" : "") + line.slice(5).trim();
  }
  if (!dataStr) return null;
  try {
    return { event, data: JSON.parse(dataStr) as Record<string, unknown> };
  } catch {
    return null;
  }
}

export interface StreamCallbacks {
  onEvent: (ev: SseEvent) => void;
}

/**
 * 发起明文 SSE 流式请求并逐事件回调。
 * 返回的 Promise 在流结束（done/连接关闭/abort）后 resolve。
 */
export async function sseStream(
  path: string,
  body: Record<string, unknown>,
  signal: AbortSignal,
  { onEvent }: StreamCallbacks,
): Promise<void> {
  const snap = await import("../contexts/authState").then((m) => m.getAuthSnapshot());
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (snap.token) headers["Authorization"] = "Bearer " + snap.token;

  const r = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error("请求失败：" + t.slice(0, 200));
  }
  if (!r.body) throw new Error("浏览器不支持流式响应（请升级到 Chrome/Edge/Firefox）");

  const reader = r.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseSSE(raw);
      if (ev) onEvent(ev);
    }
  }
}
