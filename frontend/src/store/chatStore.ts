/**
 * 对话驱动核心 store（zustand）。
 * 平移旧前端全局变量范式：pendingDraft / chatMessages / currentConversationId /
 * chatStreamAbort / supplementTaskId → 单一 store 收口。
 *
 * 流式状态机：send() → user 消息 + streaming assistant 消息 → SSE delta 累积
 * → done/error/abort → done 后 AI 消息挂 draft（「生成测试用例」按钮消费）。
 */
import { create } from "zustand";
import { api, API, toast } from "../api/client";
import { sseStream } from "../api/sse";
import { getAuthSnapshot } from "../contexts/authState";
import type { ChatDraft, CitationItem, Conversation, Task } from "../types";

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  /** 用户消息携带的附件名（气泡上单独显示 📎 徽标，不混进正文） */
  fileName?: string;
  /** AI 消息回复完成后的生成草稿（生成用例按钮消费，消费后置 null） */
  draft?: ChatDraft | null;
  /** 消息升级为任务后挂载（步骤卡渲染 + TaskList 定位） */
  task?: Task | null;
  state: "streaming" | "done" | "error" | "stopped";
  error?: string;
  /** 非终态提示（如「真实模型失败，已切换演示模式」），以黄色提示条展示，不改变消息状态 */
  notice?: string;
  /** LLM 来源：mock / deepseek 等 */
  source?: string;
  /** AI 回复身份（qa测试/pm产品/dev开发），决定消息头部标签显示 */
  persona?: string;
  /** V4.1 引用溯源：SSE citations 事件回传的知识库命中条目（showCitations 时渲染） */
  citations?: CitationItem[];
}

export interface HistoryItem {
  role: string;
  content: string;
}

let seq = 0;
const nextId = (): string => `m${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** 去掉流式中残留在正文末尾的未闭合标签前缀（如 "<thi" / "</thinkin"），避免闪现半截标签 */
function stripPartialTag(s: string): string {
  const m = s.match(/<[^>]*$/);
  if (m && m.index !== undefined) {
    const frag = m[0].toLowerCase();
    const cands = ["<think>", "<thinking>", "</think>", "</thinking>"];
    if (cands.some((c) => c.startsWith(frag))) return s.slice(0, m.index).trimEnd();
  }
  return s;
}

/** 合并两路思考：上游独立字段（reasoning_content）+ 正文里切出的标签内容 */
export function mergeThink(streamThink: string, tagThink: string): string {
  return [streamThink.trim(), tagThink.trim()].filter(Boolean).join("\n\n");
}

/** 切分思考块：支持 <think>/<thinking> 标签、中文 思考...思考、英文 thinking 列表块 */
export function splitThink(text: string): { thinking: string; reply: string } {
  // 1) XML 标签 <think> / <thinking>；流式中可能尚未闭合 → 开标签之后的内容全归思考面板
  const tag = text.match(/<think(?:ing)?>([\s\S]*?)(<\/think(?:ing)?>|$)/i);
  if (tag) {
    const start = tag.index!;
    const closed = tag[2].slice(0, 2) === "</";
    const rest = text.slice(0, start) + (closed ? text.slice(start + tag[0].length) : "");
    return { thinking: tag[1].trim(), reply: stripPartialTag(rest.trim()) };
  }
  // 2) 中文标记： 思考 ... 思考
  let m = text.match(/ 思考([\s\S]*?)思考/);
  if (m) {
    return {
      thinking: m[1].trim(),
      reply: (text.slice(0, m.index!) + text.slice(m.index! + m[0].length)).trim(),
    };
  }
  // 3) 英文 thinking 独占一行 + 后续列表块（直到空行/结尾）
  m = text.match(/(?:^|\n)thinking\s*\n((?:[ \t]*[-*][^\n]*\n?)+)(?:\n\s*\n|$)/i);
  if (m) {
    return {
      thinking: m[1].trim(),
      reply: (text.slice(0, m.index!) + text.slice(m.index! + m[0].length)).trim(),
    };
  }
  return { thinking: "", reply: text };
}

interface ChatState {
  conversationId: string | null;
  conversations: Conversation[];
  messages: ChatMsg[];
  /** 按会话隔离的流式状态：一个会话输出中不影响其他会话发消息 */
  streamingByConversation: Record<string, boolean>;
  /** 消息流滚动定位：递增序号触发 ChatPanel scrollIntoView */
  focusSeq: number;
  focusTaskId: string | null;

  /** 迭代引用：非空 = 迭代沟通模式（AI 带旧任务上下文对话）；点「生成用例」才真正跑 iterate */
  iterTaskId: string | null;
  iterTaskName: string;
  /** 迭代沟通期累积的用户输入，点「生成用例」时拼成 instruction */
  iterNotes: string[];
  /** 迭代沟通期最后附加的文档，随 instruction 一起提交 */
  iterFile: File | null;
  /** 迭代任务生成中：防重复点击「生成用例」 */
  iterGenerating: boolean;
  /** 输入框聚焦定位：递增序号触发 ChatPanel 自动聚焦 textarea */
  inputFocusSeq: number;

  /** V5.8 知识库多选检索：勾选的库 id 列表（空 = 不检索）；跨会话保留，由 ChatPanel 知识库选择器驱动 */
  kbIds: string[];
  toggleKb: (id: string) => void;
  clearKbs: () => void;

  refreshConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  deleteConversation: (id: string) => Promise<void>;
  /** V5.9 手动重命名会话（侧栏行内编辑） */
  renameConversation: (id: string, title: string) => Promise<boolean>;
  /** V5.9 AI 总结会话内容生成标题；返回 null=失败（toast 已提示），string=新标题 */
  aiRenameConversation: (id: string) => Promise<string | null>;
  /** 打开任务所属会话并挂载迭代引用（详情页「继续优化」入口） */
  openIterate: (task: Task) => Promise<void>;
  clearIterRef: () => void;
  /** 迭代沟通模式下手动触发：把累积的补充要求作为 instruction 真正跑 iterate */
  requestIterate: () => Promise<void>;

  send: (text: string, draft: ChatDraft) => Promise<void>;
  stop: () => void;

  confirmCreateTask: (msgId: string) => Promise<void>;
  updateMsgTask: (taskId: string, task: Task) => void;
  focusTask: (taskId: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversationId: null,
  conversations: [],
  messages: [],
  streamingByConversation: {},
  focusSeq: 0,
  focusTaskId: null,
  iterTaskId: null,
  iterTaskName: "",
  iterNotes: [],
  iterFile: null,
  iterGenerating: false,
  inputFocusSeq: 0,
  kbIds: [],

  toggleKb(id) {
    const cur = get().kbIds;
    set({ kbIds: cur.includes(id) ? cur.filter((k) => k !== id) : [...cur, id] });
  },

  clearKbs() {
    set({ kbIds: [] });
  },

  async refreshConversations() {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      set({ conversations: [] });
      return;
    }
    try {
      const r = await api(API + "/conversations");
      if (!r.ok) return;
      const list = (await r.json()) as Conversation[];
      const all = Array.isArray(list) ? list : [];
      // V5.8：kb_qa 历史会话（已下线的知识库问答）继续按 mode 隔离，不在首页列表出现
      set({ conversations: all.filter((c) => (c.mode || "workflow") !== "kb_qa").slice(0, 50) });
    } catch {
      /* 网络异常静默，侧栏下次轮询再刷 */
    }
  },

  async loadConversation(id) {
    // 切换会话自动清除迭代引用，避免跨会话误迭代（chip 只在当前会话有效）
    set({ conversationId: id, iterTaskId: null, iterTaskName: "", iterNotes: [], iterFile: null });
    try {
      const r = await api(API + "/conversations/" + id);
      if (!r.ok) return;
      const conv = (await r.json()) as Conversation;
      const messages: ChatMsg[] = (conv.messages || []).map((m) => ({
        id: "h" + m.id,
        role: m.role === "user" ? "user" : "assistant",
        content: m.content || m.task?.report?.summary || "",        thinking: m.thinking || "",
        // V4.5.2：恢复持久化的引用溯源，切会话/刷新后引用不丢
        citations: m.citations?.length ? m.citations : undefined,
        // 历史消息带任务摘要时直接挂轻量任务对象（步骤卡渲染 cases_count/status）
        task: m.task
          ? ({
              id: m.task.id,
              name: m.task.name,
              kind: "",
              source_type: "",
              status: m.task.status,
              cases_count: m.task.cases_count || 0,
              duration_ms: 0,
              formats: "",
              steps: m.task.steps || [],
            } as Task)
          : null,
        state: "done",
      }));
      // 历史消息恢复：最后一条无任务的 assistant 消息补 draft，触发「✨ 生成测试用例」按钮
      // （confirmCreateTask 会自动拼接所有用户消息作为任务文本，draft 本身无需带 text）
      const lastAiNoTask = [...messages].reverse().find((m) => m.role === "assistant" && !m.task);
      if (lastAiNoTask) {
        const idx = messages.indexOf(lastAiNoTask);
        messages[idx] = {
          ...lastAiNoTask,
          draft: { text: "", kind: "business", formats: ["xlsx", "json", "xmind"], roles: ["qa"] },
        };
      }
      set({ messages });
    } catch {
      /* 加载失败保持空消息，用户可重试切换 */
    }
  },

  newConversation() {
    set({
      conversationId: null, messages: [], focusTaskId: null,
      iterTaskId: null, iterTaskName: "", iterNotes: [], iterFile: null,
    });
  },

  // 迭代引用入口：详情页「继续优化」与会话内任务卡「继续优化」共用。
  // - 带 conversation_id 且非当前会话 → 先加载该会话再挂 chip（loadConversation 会清 chip，故在其后挂载）
  // - 无 conversation_id（会话内轻量任务对象）或已是当前会话 → 只挂 chip + 聚焦，不重复加载
  async openIterate(task) {
    const convId = task.conversation_id;
    if (convId && convId !== get().conversationId) {
      await get().loadConversation(convId);
    }
    set({
      ...(convId ? { conversationId: convId } : {}),
      iterTaskId: task.id,
      iterTaskName: task.name,
      iterNotes: [],
      iterFile: null,
      inputFocusSeq: get().inputFocusSeq + 1,
    });
    get().refreshConversations();
  },

  clearIterRef() {
    set({ iterTaskId: null, iterTaskName: "", iterNotes: [], iterFile: null });
  },

  // 迭代沟通模式下的手动触发：把沟通期累积的用户补充要求拼成 instruction，真正跑 iterate。
  // 沟通本身走聊天流（带 task_id 让 AI 知道讨论的是哪个任务），不生成任务 —— 生成只由本方法触发。
  async requestIterate() {
    const iterId = get().iterTaskId;
    if (!iterId) return;
    if (get().iterGenerating || get().streamingByConversation[get().conversationId || ""]) {
      toast("请等待当前生成结束");
      return;
    }
    const iterName = get().iterTaskName;
    const instruction = get().iterNotes.map((s) => s.trim()).filter(Boolean).join("\n");
    if (!instruction && !get().iterFile) {
      toast("请先描述要补充的内容，再点「生成用例」");
      return;
    }
    set({ iterGenerating: true });
    try {
      const fd = new FormData();
      const f = get().iterFile;
      if (f) fd.append("file", f);
      fd.append("instruction", instruction);
      fd.append("conversation_id", get().conversationId || "");
      const r = await api(`${API}/tasks/${iterId}/iterate`, { method: "POST", body: fd });
      if (!r.ok) {
        const e = await r.text();
        toast("迭代失败：" + e.slice(0, 200));
        set({ iterGenerating: false });
        return;
      }
      const child = (await r.json()) as Task;
      set({
        messages: [
          ...get().messages,
          {
            id: nextId(), role: "assistant", content: "", thinking: "",
            state: "done", task: child,
          },
        ],
        // chip 指向新子任务：连续补充时版本链 v2 → v3 继续递增；清空累积，重新沟通
        iterTaskId: child.id,
        iterTaskName: child.name || iterName,
        iterNotes: [],
        iterFile: null,
        iterGenerating: false,
      });
      toast(`已开始生成：基于《${iterName}》补充用例`);

      const { useTaskStore } = await import("./taskStore");
      useTaskStore.getState().refresh();
      get().refreshConversations();

      void (async () => {
        for (let i = 0; i < 600; i++) {
          await new Promise((res) => setTimeout(res, 2000));
          const rr = await api(API + "/tasks/" + child.id).catch(() => null);
          if (!rr || !rr.ok) continue;
          const t = (await rr.json()) as Task;
          get().updateMsgTask(child.id, t);
          if (t.status === "completed" || t.status === "failed") {
            await get().loadConversation(get().conversationId || "");
            const { useTaskStore: ts } = await import("./taskStore");
            ts.getState().refresh();
            return;
          }
        }
      })();
    } catch (e) {
      toast("网络错误：" + (e instanceof Error ? e.message : String(e)));
      set({ iterGenerating: false });
    }
  },

  async deleteConversation(id) {
    const r = await api(API + "/conversations/" + id, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) {
      toast("删除会话失败");
      return;
    }
    if (get().conversationId === id) get().newConversation();
    await get().refreshConversations();
    const { useTaskStore } = await import("./taskStore");
    useTaskStore.getState().refresh();
  },

  async renameConversation(id, title) {
    const t = title.trim();
    if (!t) {
      toast("标题不能为空");
      return false;
    }
    const r = await api(API + "/conversations/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: t.slice(0, 80) }),
    }).catch(() => null);
    if (!r || !r.ok) {
      toast("重命名失败");
      return false;
    }
    const c = (await r.json()) as Conversation;
    // 本地直接更新，避免整表刷新闪烁
    set({
      conversations: get().conversations.map((x) => (x.id === id ? { ...x, title: c.title } : x)),
    });
    return true;
  },

  async aiRenameConversation(id) {
    const r = await api(API + "/conversations/" + id + "/ai-title", { method: "POST" }).catch(() => null);
    if (!r || !r.ok) {
      // 403/400 带业务提示（未配模型 / 空会话），读 detail 给准确反馈
      let msg = "AI 生成标题失败";
      try {
        if (r) {
          const j = (await r.json()) as { detail?: string };
          if (j?.detail) msg = j.detail;
        }
      } catch { /* 忽略解析失败，用默认提示 */ }
      toast(msg);
      return null;
    }
    const j = (await r.json()) as { title?: string };
    const title = j.title || "";
    if (!title) {
      toast("AI 未返回有效标题");
      return null;
    }
    set({
      conversations: get().conversations.map((x) => (x.id === id ? { ...x, title } : x)),
    });
    return title;
  },

  async send(text, draft) {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      toast("请先登录或使用游客体验");
      return;
    }
    if (get().streamingByConversation[get().conversationId || ""]) {
      toast("请先停止当前生成");
      return;
    }

    // 首次发送：先建会话，后续消息挂同一会话（平移旧版行为）
    if (!get().conversationId) {
      try {
        const r = await api(API + "/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: (text || "新对话").slice(0, 40),
          }),
        });
        if (r.ok) {
          const c = (await r.json()) as Conversation;
          set({ conversationId: c.id });
          // 立即刷新左侧会话列表：发消息就建会话，不等 AI 回复完成，避免切换会话后"丢失"
          void get().refreshConversations();
        }
      } catch {
        /* 会话创建失败不阻断发送（消息不落会话，仍可生成） */
      }
    }

    // ── 迭代沟通模式：chip 非空时只沟通，不生成任务 ──
    // 本条作为补充要求累积起来，由用户点「生成用例」（requestIterate）时才真正跑 iterate。
    if (get().iterTaskId) {
      set({
        iterNotes: text.trim() ? [...get().iterNotes, text.trim()] : get().iterNotes,
        ...(draft.file ? { iterFile: draft.file } : {}),
      });
    }

    const userMsg: ChatMsg = {
      id: nextId(), role: "user", content: text, thinking: "", state: "done",
      fileName: draft.file?.name,
    };
    const aiMsg: ChatMsg = {
      id: nextId(), role: "assistant", content: "", thinking: "", state: "streaming",
      // 回复身份取第一个选中角色（多选时第一个生效）
      persona: draft.roles?.[0] || "qa",
    };
    const convId = get().conversationId || "";
    set({
      messages: [...get().messages, userMsg, aiMsg],
      streamingByConversation: { ...get().streamingByConversation, [convId]: true },
    });

    // history：done 消息去掉刚追加的 user（与旧版 chatMessages.slice(0,-1) 对齐；
    // streaming 的 aiMsg 因 state 未 done 被 filter 排除）；只传附件没打字的消息正文为空，
    // 滤掉避免把空串塞进模型上下文
    const history: HistoryItem[] = get()
      .messages.filter((m) => m.state === "done")
      .slice(0, -1)
      .filter((m) => m.content.trim())
      .map((m) => ({ role: m.role, content: m.content }));

    const aborter = new AbortController();
    const aborters = (get as unknown as { _aborters?: Record<string, AbortController> })._aborters || {};
    aborters[convId] = aborter;
    (get as unknown as { _aborters?: Record<string, AbortController> })._aborters = aborters;

    const patchAi = (patch: Partial<ChatMsg>) => {
      const msgs = get().messages;
      const i = msgs.findIndex((m) => m.id === aiMsg.id);
      if (i === -1) return; // 流式中切换会话：消息已被替换，丢弃
      const next = msgs.slice();
      next[i] = { ...next[i], ...patch };
      set({ messages: next });
    };

    let fullText = "";
    let streamThink = ""; // 上游独立字段（reasoning_content）累积的思考内容
    try {
      // 有附件时先单独上传拿 file_id（SSE 流没法带 multipart），
      // 后端抽取文档文本后在对话时注入，AI 才能读到文档内容
      let fileId: string | undefined;
      if (draft.file) {
        const fd = new FormData();
        fd.append("file", draft.file);
        const up = await api(API + "/files", { method: "POST", body: fd });
        if (!up.ok) {
          const e = await up.text();
          patchAi({ state: "error", error: "附件上传失败：" + e.slice(0, 160) });
          return;
        }
        const meta = (await up.json()) as { file_id?: string };
        fileId = meta.file_id;
      }

      await sseStream(
        API + "/chat/stream",
        {
          message: text,
          history,
          conversation_id: get().conversationId,
          // 迭代沟通模式：带上任务 id，后端把该任务用例摘要注入上下文，AI 才知道在讨论哪个任务
          task_id: get().iterTaskId || undefined,
          file_id: fileId,
          // 思考三态透传：true=总是深度思考；false=强制关；null/省略=按需（后端 should_deep_think 判定）。
          // 注意不能写成 `draft.thinking !== false`——那会把 null 误转成 true，等于强制开启思考。
          thinking: draft.thinking ?? null,
          // 角色选择：决定 AI 回复身份（qa测试/pm产品/dev开发），取第一个选中的角色
          roles: draft.roles?.length ? draft.roles : undefined,
          // V5.8：勾选的知识库多选检索（空 = 不检索，后端跳过 RAG）
          kb_ids: get().kbIds.length ? get().kbIds : undefined,
        },
        aborter.signal,
        {
          onEvent(ev) {
            if (ev.event === "delta") {
              fullText += String(ev.data.content || "");
              const parsed = splitThink(fullText);
              patchAi({ content: parsed.reply, thinking: mergeThink(streamThink, parsed.thinking) });
            } else if (ev.event === "think") {
              // 上游独立字段的思考增量：直接进思考面板，不混进正文
              streamThink += String(ev.data.content || "");
              patchAi({ thinking: streamThink });
            } else if (ev.event === "notice") {
              // 降级/中断提示：只挂提示条，流继续（state 仍为 streaming，等 done 收尾）
              patchAi({ notice: String(ev.data.message || "") });
            } else if (ev.event === "citations") {
              // V4.1 引用溯源：正文前回传的知识库命中条目（是否渲染由 showCitations 决定）
              const items = Array.isArray(ev.data.items) ? (ev.data.items as CitationItem[]) : [];
              patchAi({ citations: items });
            } else if (ev.event === "error") {
              patchAi({ state: "error", error: String(ev.data.message || "未知错误") });
            } else if (ev.event === "done") {
              const full = typeof ev.data.full === "string" ? ev.data.full : "";
              // 后端自动创建的会话 id（新会话首轮）：仅在当前无会话时兜底设置，
              // 避免用户在输出中切换会话后被 done 事件强制切回原会话
              if (ev.data.conversation_id && !get().conversationId) {
                set({ conversationId: String(ev.data.conversation_id) });
              }
              // done 里的 thinking 与流式 think 事件同源，取其一避免重复拼接
              const doneThink = typeof ev.data.thinking === "string" ? ev.data.thinking : "";
              const thinkBase = streamThink || doneThink;
              if (full && !fullText) fullText = full;
              const parsed = splitThink(fullText);
              if (thinkBase || parsed.thinking) {
                patchAi({ thinking: mergeThink(thinkBase, parsed.thinking) });
              }
              if (parsed.reply) patchAi({ content: parsed.reply });
              patchAi({
                state: "done",
                source: typeof ev.data.source === "string" ? ev.data.source : undefined,
                draft: { ...draft, text: draft.text || text },
              });
            }
          },
        },
      );
      // 流正常结束但没收到 done 事件（连接中断兜底）
      const cur = get().messages.find((m) => m.id === aiMsg.id);
      if (cur && cur.state === "streaming") {
        patchAi(cur.content ? { state: "stopped" } : { state: "error", error: "连接中断" });
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        patchAi({ state: "stopped" });
      } else {
        patchAi({ state: "error", error: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      const aborters = (get as unknown as { _aborters?: Record<string, AbortController> })._aborters || {};
      delete aborters[convId];
      const nextStreaming = { ...get().streamingByConversation };
      delete nextStreaming[convId];
      set({ streamingByConversation: nextStreaming });
      get().refreshConversations(); // 后端已落库，刷新 message_count/排序
    }
  },

  stop() {
    const convId = get().conversationId || "";
    const aborters = (get as unknown as { _aborters?: Record<string, AbortController> })._aborters || {};
    const aborter = aborters[convId];
    if (aborter) aborter.abort();
  },

  async confirmCreateTask(msgId) {
    const { useTaskStore } = await import("./taskStore");
    const msg = get().messages.find((m) => m.id === msgId);
    if (!msg) return;
    const draft = msg.draft;
    if (!draft) {
      toast("没有待生成的需求，请重新输入");
      return;
    }
    // 平移旧版 confirmAndCreateTask：把完整对话作为任务文本，上下文更充分
    const conversationText = get()
      .messages.filter((m) => m.role === "user")
      .map((m) => `用户：${m.content}`)
      .join("\n\n");
    const text = (draft.text || conversationText).trim();
    if (!text && !draft.file) {
      toast("请上传文件或粘贴规格文本");
      return;
    }

    const fd = new FormData();
    if (draft.file) fd.append("file", draft.file);
    fd.append("text", text);
    fd.append("kind", draft.kind || "business");
    fd.append("formats", (draft.formats.length ? draft.formats : ["xlsx", "json", "xmind"]).join(","));
    fd.append("roles", (draft.roles?.length ? draft.roles : ["qa"]).join(","));
    fd.append("name", text.slice(0, 40) || "未命名任务");
    fd.append("conversation_id", get().conversationId || "");

    try {
      const r = await api(API + "/tasks", { method: "POST", body: fd });
      if (!r.ok) {
        const e = await r.text();
        toast("提交失败：" + e.slice(0, 200));
        return;
      }
      const task = (await r.json()) as Task;
      // 消息升级：草稿已消费 + 挂载任务（步骤卡轮询渲染）
      const msgs = get().messages.slice();
      const i = msgs.findIndex((m) => m.id === msgId);
      if (i !== -1) {
        msgs[i] = { ...msgs[i], draft: null, task };
        set({ messages: msgs });
      }
      toast("任务已提交，正在编排生成");
      useTaskStore.getState().startPolling(task.id);
      get().refreshConversations();
      useTaskStore.getState().refresh();
    } catch (e) {
      toast("网络错误：" + (e instanceof Error ? e.message : String(e)));
    }
  },

  updateMsgTask(taskId, task) {
    const msgs = get().messages;
    const i = msgs.findIndex((m) => m.task && m.task.id === taskId);
    if (i === -1) return;
    const next = msgs.slice();
    next[i] = { ...next[i], task };
    set({ messages: next });
  },

  focusTask(taskId) {
    set({ focusTaskId: taskId, focusSeq: get().focusSeq + 1 });
  },
}));
