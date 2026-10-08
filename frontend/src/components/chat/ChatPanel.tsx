/**
 * 对话驱动主面板：消息流 + 输入区。
 * 平移旧版 chatStream/chatText/chatSendBtn 行为：
 * - Enter 发送 / Shift+Enter 换行 / textarea 自适应高度
 * - 流式中发送按钮变「⏹ 停止生成」
 * - 文件附加 chip + 移除
 * - 新消息/流式增量自动滚底（用户上滚时暂停跟随）
 * V4：图标改用 lucide-react（纸飞机/灯泡/附件/停止）。
 * V2.10：输入框为唯一入口 —— 挂载「迭代引用 chip」时本次发送走 iterate（基于旧任务合并用例），
 *        无 chip 时为新建任务；chip 由详情页「继续优化」或会话内任务卡挂载。
 * V6.0：新增「存入记忆库」手动按钮（Brain，整理中转圈）；个人记忆库强制勾选（🧠 徽章 + 不可取消）；
 *       附件格式改由 /knowledge/supported-formats 动态下发（失败回落硬编码兜底值）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Brain, Paperclip, Lightbulb, Send, Square, Library, PenLine } from "lucide-react";
import { useChatStore } from "../../store/chatStore";
import { useTaskStore } from "../../store/taskStore";
import { api, API, toast } from "../../api/client";
import type { ChatDraft, KbBaseItem } from "../../types";
import { useAuth } from "../../hooks/useAuth";
import MessageView from "./MessageView";
import PromptEditorModal from "./PromptEditorModal";
import { groupByChain } from "../../utils/taskChain";

function fmtSize(b: number): string {
  return b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(2) + " MB";
}

/** 允许上传的文档格式兜底值（/knowledge/supported-formats 拉取失败时回落，与后端 doc_extract 对齐）；
 *  不设 input accept 属性：macOS Chrome 对 .md 等动态 UTI 扩展名会整体置灰（间歇性），格式交给 ACCEPT_RE 校验 */
const ACCEPT_HINT_FALLBACK = "docx / pdf / md / txt";
const ACCEPT_RE_FALLBACK = /\.(docx|pdf|md|markdown|txt)$/i;

/** 动态格式解析结果：提示文案 + 校验正则（exts + images 合并） */
interface AcceptFormats { hint: string; re: RegExp }

/** V6.0 模块级单例 Promise：附件格式以后端 /knowledge/supported-formats 为唯一真源，
 *  整个页面生命周期只拉一次；接口失败/超时回落上面的硬编码兜底值 */
let acceptPromise: Promise<AcceptFormats> | null = null;
function loadAcceptFormats(): Promise<AcceptFormats> {
  if (!acceptPromise) {
    acceptPromise = (async (): Promise<AcceptFormats> => {
      try {
        const r = await api(API + "/knowledge/supported-formats");
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = (await r.json()) as { exts?: string[]; images?: string[]; hint?: string };
        const exts = Array.isArray(d?.exts) ? d.exts.filter((s) => typeof s === "string" && s.trim()) : [];
        const imgs = Array.isArray(d?.images) ? d.images.filter((s) => typeof s === "string" && s.trim()) : [];
        const all = [...exts, ...imgs];
        if (!all.length) throw new Error("empty formats");
        // 扩展名转正则安全转义（如 c++ 等奇异扩展），合并生成 /\.（docx|pdf|png…）$/i
        const esc = all.map((e) => e.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
        return {
          hint: typeof d?.hint === "string" && d.hint.trim() ? d.hint : all.join(" / "),
          re: new RegExp(`\\.(${esc})$`, "i"),
        };
      } catch {
        // 接口失败回落现值；不缓存失败结果，下次挂载可重试
        acceptPromise = null;
        return { hint: ACCEPT_HINT_FALLBACK, re: ACCEPT_RE_FALLBACK };
      }
    })();
  }
  return acceptPromise;
}

/** 空态示例 chips 的示例需求（点选直接填入输入框） */
const SAMPLE_ECOM =
  "电商订单流程。\n功能点：下单、支付、取消订单、申请退款、订单状态流转、按订单号/状态查询。\n业务规则：超时未支付自动取消；已发货订单不可取消；退款需审核。";
const SAMPLE_LOGIN =
  "用户登录注册。\n功能点：注册、登录、找回密码、验证码、记住登录态、退出登录。\n业务规则：密码强度校验；连续 5 次错误锁定 10 分钟；验证码 5 分钟有效。";
const SAMPLE_DBERP =
  "DBERP 采购入库。\n功能点：创建采购入库单、关联采购订单、质检、上架、库存更新、单据查询。\n业务规则：入库数量不可超采购数量；质检不合格可退货；库存实时扣减。";

/** 「总是深度思考」开关的本地记忆键（默认关＝按需：简单问题直接答，复杂问题由后端自动推理） */
const THINK_KEY = "aitf_deep_think";

/** 多角色协作（V3.1）：参与生成用例的视角（与后端 src/generator.case_generator 对齐） */
const ROLE_OPTIONS: { id: string; label: string; title: string }[] = [
  { id: "pm", label: "产品", title: "产品视角：业务价值/需求覆盖/验收标准" },
  { id: "qa", label: "测试", title: "测试视角：正向/异常/边界/场景组合" },
  { id: "dev", label: "开发", title: "开发视角：契约/幂等/并发/数据一致性" },
];

export default function ChatPanel({
  showCitations = false,
  onCiteClick,
}: {
  /** V5.8：AI 会话勾选知识库后渲染引用溯源 chips（不选库不检索、无 citations） */
  showCitations?: boolean;
  /** 引用 chip 点击回调（V7.4.2：跳知识库对应文档并定位到命中分块） */
  onCiteClick?: (knowledgeId: string, chunkId?: string) => void;
}) {
  const messages = useChatStore((s) => s.messages);
  const conversationId = useChatStore((s) => s.conversationId);
  // V6.0 访客判定：role 由 AuthContext 响应式下发（登录/访客切换即时生效）
  const { role } = useAuth();
  const isGuest = role === "guest";
  // W3 M8 首页驾驶舱摘要：复用 taskStore 任务列表（App 层统一 5s 轮询），客户端聚合最近 2 条
  const tasks = useTaskStore((s) => s.tasks);
  /** 最近用例：任务按迭代链聚合（一行 = 一条用例集，展示最新版），取最近 2 条 */
  const recentCases = useMemo(() => groupByChain(tasks).slice(0, 2), [tasks]);
  const streamingByConversation = useChatStore((s) => s.streamingByConversation);
  // 按会话隔离的流式状态：当前会话在输出中才禁用输入框，其他会话不受影响
  const streaming = conversationId ? (streamingByConversation[conversationId] ?? false) : false;
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const focusSeq = useChatStore((s) => s.focusSeq);
  const focusTaskId = useChatStore((s) => s.focusTaskId);
  /** 迭代引用：非空 → 迭代沟通模式（先沟通，点「生成用例」才生成） */
  const iterTaskId = useChatStore((s) => s.iterTaskId);
  const iterTaskName = useChatStore((s) => s.iterTaskName);
  const iterNotes = useChatStore((s) => s.iterNotes);
  const iterGenerating = useChatStore((s) => s.iterGenerating);
  const requestIterate = useChatStore((s) => s.requestIterate);
  const clearIterRef = useChatStore((s) => s.clearIterRef);
  const inputFocusSeq = useChatStore((s) => s.inputFocusSeq);
  // V5.8 知识库多选检索
  const kbIds = useChatStore((s) => s.kbIds);
  const toggleKb = useChatStore((s) => s.toggleKb);
  const clearKbs = useChatStore((s) => s.clearKbs);
  // V6.0 手动「存入记忆库」
  const memoryBusy = useChatStore((s) => s.memoryBusy);
  const digestMemory = useChatStore((s) => s.digestMemory);
  const ensureDefaultKbs = useChatStore((s) => s.ensureDefaultKbs);
  const [kbOpen, setKbOpen] = useState(false);
  const [kbList, setKbList] = useState<KbBaseItem[]>([]);
  /** 附件格式（动态下发 + 兜底）：挂载后异步解析为后端真源 */
  const [accept, setAccept] = useState<AcceptFormats>({ hint: ACCEPT_HINT_FALLBACK, re: ACCEPT_RE_FALLBACK });
  /** kb 弹层容器：点击外部 / Escape 关闭 */
  const kbPickerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!kbOpen) return;
    const onDown = (e: MouseEvent) => {
      if (kbPickerRef.current && !kbPickerRef.current.contains(e.target as Node)) setKbOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setKbOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [kbOpen]);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [kind] = useState("business");
  const [formats] = useState<string[]>(["xlsx", "json", "xmind"]);
  /** 多角色协作（V3.1）：参与生成用例的视角（pm/qa/dev），默认仅测试 */
  const [roles, setRoles] = useState<string[]>(["qa"]);
  /** V5.10 提示词定制弹窗（角色 pill 旁 ✎ 入口） */
  const [promptOpen, setPromptOpen] = useState(false);
  /** 「总是深度思考」：默认关＝按需 —— 简单问题直接答，复杂问题（排查/分析/报错等）
   *  由后端自动判定是否推理。开启后每轮都先推理再作答。本地记忆，未表态时后端走自动判定。 */
  const [alwaysThink, setAlwaysThink] = useState<boolean>(() => {
    try {
      return localStorage.getItem(THINK_KEY) === "1";
    } catch {
      return false;
    }
  });
  const streamRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /** 用户上滚后暂停自动跟随，回到底部恢复 */
  const stickBottom = useRef(true);

  // 自动滚底：消息变化 + 流式增量
  useEffect(() => {
    const el = streamRef.current;
    if (el && stickBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  // 任务定位：TaskList 点击 → 滚动到对应任务卡并高亮
  useEffect(() => {
    if (!focusSeq) return;
    const el = streamRef.current;
    if (!el) return;
    const target = focusTaskId ? el.querySelector(`[data-task-card="${focusTaskId}"]`) : null;
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("flash");
      setTimeout(() => target.classList.remove("flash"), 1600);
    } else if (focusTaskId) {
      // 当前消息流没有该任务的卡（如已切到新会话）→ 提示而非静默
      toast("该任务不在当前会话，可切换到对应会话查看");
    }
  }, [focusSeq, focusTaskId]);

  // 迭代入口聚焦：详情页「继续优化」跳回会话后自动聚焦输入框（inputFocusSeq 递增触发）
  useEffect(() => {
    if (!inputFocusSeq) return;
    inputRef.current?.focus();
  }, [inputFocusSeq]);

  // V5.8：知识库选择 popover 打开时拉取可见库列表（后端按权限过滤）
  const loadKbList = useCallback(() => {
    void api(API + "/knowledge/bases")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d) => setKbList(Array.isArray(d?.items) ? d.items : []))
      .catch(() => setKbList([]));
  }, []);
  useEffect(() => {
    if (!kbOpen) return;
    loadKbList();
  }, [kbOpen, loadKbList]);

  // V6.0/V7.4.1：挂载即幂等并入默认勾选（个人记忆库 + 全部共享业务库，徽章计数同步）
  useEffect(() => {
    void ensureDefaultKbs();
  }, [ensureDefaultKbs]);

  // V6.0：记忆整理完成等场景触发的库列表刷新（chatStore dispatch "kb-refresh"）
  useEffect(() => {
    const onRefresh = () => {
      void ensureDefaultKbs();
      loadKbList();
    };
    window.addEventListener("kb-refresh", onRefresh);
    return () => window.removeEventListener("kb-refresh", onRefresh);
  }, [ensureDefaultKbs, loadKbList]);

  // V6.0：附件格式动态化（模块级单例 Promise，只拉一次；失败回落兜底值）
  useEffect(() => {
    let alive = true;
    void loadAcceptFormats().then((f) => {
      if (alive) setAccept(f);
    });
    return () => {
      alive = false;
    };
  }, []);

  function onScroll(): void {
    const el = streamRef.current;
    if (!el) return;
    stickBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  function autoGrow(el: HTMLTextAreaElement): void {
    el.style.height = "auto";
    el.style.height = Math.min(320, el.scrollHeight) + "px";
  }

  function doSend(): void {
    const txt = text.trim();
    if (!txt && !file) return;
    if (streaming) return;
    // thinking：true=总是深度思考；null=未表态 → 后端按需自动判定（llm_service.should_deep_think）
    const draft: ChatDraft = {
      text: txt,
      file,
      kind,
      formats,
      thinking: alwaysThink ? true : null,
      roles,
    };
    // 只传附件不打字时正文保持为空（气泡显示 📎 文件名徽标），不再写「(仅附加文档)」占位符
    void send(txt, draft);
    setText("");
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
    stickBottom.current = true;
  }

  /** 选择附件：前端先按白名单拦一道（格式以后端 supported-formats 下发为准） */
  function onPickFile(f: File | null): void {
    if (f && !accept.re.test(f.name)) {
      toast(`暂不支持该格式，请上传 ${accept.hint}`);
      if (fileRef.current) fileRef.current.value = "";
      setFile(null);
      return;
    }
    setFile(f);
  }

  /** V6.0 手动「存入记忆库」入口：按钮已做态控制，这里只透传当前会话 id */
  function onDigestMemory(): void {
    if (conversationId) void digestMemory(conversationId);
  }

  /** 切换「总是深度思考」：写本地记忆，下一次发送即生效 */
  function toggleThink(): void {
    const next = !alwaysThink;
    setAlwaysThink(next);
    try {
      localStorage.setItem(THINK_KEY, next ? "1" : "0");
    } catch {
      /* 隐私模式 / 存储禁用：仅本次会话生效 */
    }
  }

  /** 多角色协作：切换某角色参与生成（至少保留一个角色） */
  function toggleRole(id: string): void {
    setRoles((prev) => {
      const next = prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id];
      return next.length ? next : ["qa"];
    });
  }

  return (
    <div className="chat-panel">
      <div className="chat-stream" ref={streamRef} onScroll={onScroll}>
        {messages.length === 0 ? (
          <div className="welcome">
            {/* W3 M8 首页驾驶舱：一句话主线 + 副文案 + 示例 chips + 最近用例/最近测试运行摘要卡。
                单输入框 = 下方现有聊天输入（功能与附加按钮全保留），不再摆 4 张功能卡分流。 */}
            <h2>
              <Bot size={24} style={{ verticalAlign: "-4px", marginRight: 6 }} />
              输入需求，生成用例，一键全链路测试
            </h2>
            <p className="welcome-sub">
              把需求告诉试飞员（TestPilot）：自动拆解测试点、生成用例并沉淀到用例库。
            </p>

            <div className="sample-chips">
              <span className="sc-label">试试这些示例：</span>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_ECOM)}>电商订单流程</button>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_LOGIN)}>用户登录注册</button>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_DBERP)}>DBERP 采购入库</button>
            </div>

            {/* 摘要卡：数据全部来自 taskStore 现有列表（App 层 5s 轮询），无新后端 */}
            <div className="home-cards">
              <div className="home-card" data-testid="home-recent-cases">
                <div className="hc-title">
                  <Library size={14} />
                  最近用例
                  <button
                    type="button"
                    className="hc-more"
                    onClick={() => window.dispatchEvent(new CustomEvent("nav-to", { detail: "cases" }))}
                  >
                    查看全部
                  </button>
                </div>
                {recentCases.length === 0 ? (
                  <div className="hc-empty">暂无用例，输入需求即可生成</div>
                ) : (
                  recentCases.map((g) => (
                    <div key={g.latest.id} className="hc-row">
                      <span className="hc-name" title={g.latest.name}>{g.latest.name}</span>
                      <span className="pill pill-sub">v{g.versions}</span>
                      <span className={`pill ${(g.latest.review_status || "draft") === "reviewed" ? "rv-ok" : "rv-draft"}`}>
                        {(g.latest.review_status || "draft") === "reviewed" ? "已评审" : "草稿"}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : (
          messages.map((m) => (
            <MessageView key={m.id} msg={m} showCitations={showCitations} onCiteClick={onCiteClick} />
          ))
        )}
      </div>

      <div className={`chat-input-wrap ${streaming ? "streaming" : ""} ${iterTaskId ? "iter-mode" : ""}`}>
        {/* 迭代引用 chip：常驻可见，明确告知本次发送是「迭代旧任务」而非新建；✕ 一键回到新建模式 */}
        {iterTaskId && (
          <div className="iter-ref-chip">
            <span className="irc-icon" aria-hidden="true">🔁</span>
            <span className="irc-text">基于《{iterTaskName}》迭代</span>
            <span className="irc-hint">
              {iterNotes.length ? `已记录 ${iterNotes.length} 条补充要求` : "先沟通补充方向"}
            </span>
            <button
              type="button"
              className="irc-gen"
              disabled={iterGenerating || streaming}
              title="按沟通确认的补充要求生成用例（合并原有用例，版本号 +1）"
              onClick={() => void requestIterate()}
            >
              {iterGenerating ? "生成中…" : "⚡ 生成用例"}
            </button>
            <button
              type="button"
              className="irc-close"
              title="取消迭代，恢复为新建任务模式"
              aria-label="取消迭代引用"
              onClick={clearIterRef}
            >
              ✕
            </button>
          </div>
        )}
        {file && (
          <div className="file-chip">
            <Paperclip size={14} /> {file.name} <span className="fc-size">({fmtSize(file.size)})</span>
            <button type="button" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>
              移除
            </button>
          </div>
        )}
        <div className="chat-input-row">
          <div className="input-toolbar">
            <input
              ref={fileRef}
              type="file"
              hidden
              onChange={(e) => onPickFile(e.target.files?.[0] || null)}
            />
            {/* V5.8 知识库多选检索：不选 = 不检索；按钮徽章显示已选数量 */}
            <div className="kb-picker" ref={kbPickerRef}>
              <button
                className={`icon-btn ${kbIds.length ? "kb-active" : ""}`}
                type="button"
                title={
                  kbIds.length
                    ? `已选 ${kbIds.length} 个知识库参与检索（含个人记忆库，点击调整）`
                    : "选择知识库参与检索（个人库始终参与；附加库不选则不额外检索）"
                }
                aria-expanded={kbOpen}
                onClick={() => setKbOpen((v) => !v)}
              >
                <Library size={20} />
                {kbIds.length > 0 && <span className="kb-badge">{kbIds.length}</span>}
              </button>
              {kbOpen && (
                <div className="kb-pop" role="dialog" aria-label="选择检索知识库">
                  <div className="kb-pop-head">
                    <span>检索知识库</span>
                    {kbIds.length > 0 && (
                      <button type="button" className="kb-pop-clear" title="清空附加库（个人记忆库保留）" onClick={clearKbs}>清空</button>
                    )}
                  </div>
                  <p className="kb-pop-hint">个人库始终参与检索；附加库不选则不额外检索</p>
                  <div className="kb-pop-list">
                    {kbList.length === 0 && (
                      <div className="kb-pop-empty">暂无可选知识库（可在「知识库」页创建）</div>
                    )}
                    {kbList.map((k) => {
                      const personal = !!k.is_personal;
                      // 个人库强制勾选：kbIds 是否包含不影响 on 值（渲染恒亮）
                      const on = personal || kbIds.includes(k.id);
                      return (
                        <button
                          key={k.id}
                          type="button"
                          className={`kb-pop-row ${on ? "on" : ""}`}
                          title={personal ? "个人记忆库：默认始终参与检索，不可取消" : undefined}
                          aria-disabled={personal}
                          style={personal ? { cursor: "default", opacity: 0.85 } : undefined}
                          onClick={() => toggleKb(k.id)} // 个人库时 toggleKb 为 no-op（chatStore 拦截）
                        >
                          <span className={`kb-check ${on ? "on" : ""}`} aria-hidden="true">{on ? "✓" : ""}</span>
                          <span className="kb-pop-name">
                            {k.name}
                            {personal && (
                              <span aria-hidden="true" style={{ marginLeft: 4 }} title="个人记忆库">🧠</span>
                            )}
                          </span>
                          <span className="kb-pop-count">{k.doc_count ?? 0} 篇</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
            <button
              className="icon-btn"
              type="button"
              title={`附加文档（${accept.hint}），AI 会读取文档内容`}
              disabled={streaming}
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip size={20} />
            </button>
            {/* V6.0 手动「存入记忆库」：本会话对话记录+附件 → 个人知识库（后端异步整理，按钮全程置灰+转圈） */}
            <button
              className="icon-btn"
              type="button"
              aria-label="存入记忆库"
              title={
                isGuest
                  ? "注册后即可使用记忆库"
                  : "把本会话的对话记录与附件存入个人知识库"
              }
              disabled={streaming || !conversationId || isGuest || memoryBusy || messages.length === 0}
              onClick={onDigestMemory}
            >
              <Brain
                size={20}
                // 复用 base.css 既有 @keyframes spin（不动白名单外样式文件）
                style={memoryBusy ? { animation: "spin 1s linear infinite" } : undefined}
              />
            </button>
            <button
              className={`think-toggle ${alwaysThink ? "active" : ""}`}
              type="button"
              aria-pressed={alwaysThink}
              title={
                alwaysThink
                  ? "总是深度思考：已开启 —— 每轮都会先推理再作答（点击改为按需）"
                  : "按需深度思考：简单问题直接作答，复杂问题（排查/分析/报错等）自动推理（点击改为每轮都思考）"
              }
              disabled={streaming}
              onClick={toggleThink}
            >
              <Lightbulb size={16} />
              <span className="tt-text">总是深度思考</span>
            </button>
            {/* 角色选择器：生成用例的视角（多选合并去重） */}
            <span className="role-picker" title="多角色协作：以多个视角分别生成用例后合并去重">
              {ROLE_OPTIONS.map((r) => (
                <button
                  key={r.id}
                  className={`role-chip ${roles.includes(r.id) ? "active" : ""}`}
                  type="button"
                  title={r.title}
                  disabled={streaming}
                  onClick={() => toggleRole(r.id)}
                >
                  {r.label}
                </button>
              ))}
            </span>
            {/* V5.10 提示词定制入口：编辑角色口吻与生成模板的系统提示词 */}
            <button
              className="prompt-btn"
              type="button"
              title="定制提示词：修改 AI 角色口吻与用例生成模板"
              aria-label="定制提示词"
              disabled={streaming}
              onClick={() => setPromptOpen(true)}
            >
              <PenLine size={14} />
            </button>
          </div>
          <div className="input-body">
            <textarea
              ref={inputRef}
              value={text}
              placeholder={
                streaming
                  ? "生成中…"
                  : iterTaskId
                    ? `和试飞员沟通《${iterTaskName}》要补充什么…（确认后点「⚡ 生成用例」）`
                    : "把你的测试需求告诉试飞员…"
              }
              disabled={streaming}
              onChange={(e) => {
                setText(e.target.value);
                autoGrow(e.target);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  doSend();
                }
              }}
            />
            {streaming ? (
              <button className="send-btn streaming" type="button" onClick={stop} title="停止生成">
                <Square size={16} fill="currentColor" />
              </button>
            ) : (
              <button className="send-btn" type="button" onClick={doSend} disabled={!text.trim() && !file} title="发送">
                <Send size={18} />
              </button>
            )}
          </div>
        </div>
      </div>
      {/* V5.10 提示词定制弹窗（访客打开时后端 403，弹窗内提示注册） */}
      <PromptEditorModal open={promptOpen} onClose={() => setPromptOpen(false)} />
    </div>
  );
}
