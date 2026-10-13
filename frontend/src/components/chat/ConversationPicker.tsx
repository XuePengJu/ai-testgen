/**
 * 会话历史侧栏：列表 / 切换回放 / 删除 / 新建会话。
 * V4：按 updated_at 分组今天/昨天/更早；删除按钮用图标；新建用 + 图标。
 * M5 历史会话治理（纯展示层，不改 API/store 数据结构）：
 * - 标题兜底：空标题 / 寒暄（"你好"类）/ 裸「任务:<id>」→ 取首条用户消息摘要
 *   （仅当前已加载会话有消息缓存）→ 语义化兜底名；
 * - 简短会话（总消息数 < 4，不足 2 轮）自动折叠进底部「已归档 · N 个简短会话」
 *   分组，可点击展开；当前正在使用的会话不归档，避免丢失入口。
 *   V5.11 豁免：①今天有更新的会话（刚发起的探索/任务自动会话立刻可见，过夜自动沉底）；
 *   ②有 running/pending 任务的会话（生成中永不折叠，任务数据源 = taskStore 5s 轮询，前端 join）。
 *   V5.13 收敛：仅当会话总数超过 RECENT_KEEP（9）条才启动折叠——按 updated_at 排序的
 *   最近 9 条永不归档，总数 ≤9 时侧栏全部平铺展示，避免整体被折叠成一条归档区。
 * V5.9：hover 操作组扩为 3 个——✏️ 重命名（行内编辑）/ ✨ AI 总结标题 / 🗑 删除。
 */
import { useMemo, useRef, useState } from "react";
import { useChatStore } from "../../store/chatStore";
import { useTaskStore } from "../../store/taskStore";
import { useAuth } from "../../hooks/useAuth";
import { Trash2, Plus, ChevronDown, ChevronRight, Pencil, Sparkles } from "lucide-react";
import { toast } from "../../api/client";
import { parseServerTime } from "../../utils/time";

function fmtTime(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

type Conv = {
  id: string;
  title: string;
  message_count?: number;
  task_count?: number;
  updated_at?: string | null;
  created_at?: string | null;
};
type Group = "今天" | "昨天" | "更早";

/** 总消息数不足该值（不足 2 轮对话）视为简短会话，折叠进归档分组 */
const SHORT_CONV_LIMIT = 4;

/** V5.13：会话总数超过该值才启动归档折叠；按更新时间排序的前该值条永不折叠 */
const RECENT_KEEP = 9;

/** 寒暄类标题：整条命中才算（"你好！"、"hi?" 等），正常长标题不受影响 */
const GREETING_RE =
  /^(你好|您好|你好啊|您好啊|哈喽|哈罗|嗨|嗨嗨|在吗|在么|hi+|hello+|hey|测试|test|good\s*(morning|afternoon|evening))[\s!！。~～?？]*$/i;

/** 裸任务 ID 形态：「任务:07a3ee5a45a2」（冒号中英文均可，ID 十六进制/带连字符） */
const BARE_TASK_RE = /^任务[:：]\s*[0-9a-f][0-9a-f-]{5,}$/i;

/** 标题是否属于"无信息量"形态（空 / 寒暄 / 裸任务 ID） */
function isLowInfoTitle(title: string | null | undefined): boolean {
  const t = (title || "").trim();
  return !t || GREETING_RE.test(t) || BARE_TASK_RE.test(t);
}

/** 首条用户消息 → 15 字摘要标题 */
function briefFromFirstMsg(msg: string | null | undefined): string {
  const s = (msg || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  return s.length > 15 ? s.slice(0, 15) + "…" : s;
}

function groupByDate(list: Conv[]): Record<Group, Conv[]> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  const out: Record<Group, Conv[]> = { 今天: [], 昨天: [], 更早: [] };
  list.forEach((c) => {
    const d = parseServerTime(c.updated_at || c.created_at);
    // parseServerTime 返回 Date | null：空值直接归到「更早」
    if (!d || isNaN(d.getTime())) {
      out["更早"].push(c);
      return;
    }
    const dd = new Date(d);
    dd.setHours(0, 0, 0, 0);
    if (dd.getTime() === today.getTime()) out["今天"].push(c);
    else if (dd.getTime() === yest.getTime()) out["昨天"].push(c);
    else out["更早"].push(c);
  });
  return out;
}

/** V5.11：今天（本地时区 0 点起）有更新的会话视为「活跃」，豁免归档折叠 */
function isFreshToday(c: Conv): boolean {
  const d = parseServerTime(c.updated_at || c.created_at);
  if (!d || isNaN(d.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return d.getTime() >= today.getTime();
}

/**
 * 移动端抽屉：关闭会话列表（移除 .app-main 的 conv-open 类）。
 * 选中会话 / 点击遮罩 / 新对话 都走它，桌面端为 no-op（类不存在时 remove 无副作用）。
 */
function closeConvDrawer(): void {
  document.querySelector(".app-main")?.classList.remove("conv-open");
}

export default function ConversationPicker() {
  const conversations = useChatStore((s) => s.conversations);
  const currentId = useChatStore((s) => s.conversationId);
  const messages = useChatStore((s) => s.messages);
  const loadConversation = useChatStore((s) => s.loadConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const aiRenameConversation = useChatStore((s) => s.aiRenameConversation);
  const newConversation = useChatStore((s) => s.newConversation);
  /** 移动端：新建会话后同样收起抽屉，露出空输入框（桌面端 closeConvDrawer 为 no-op） */
  const onNewConversation = () => {
    newConversation();
    closeConvDrawer();
  };
  const { token } = useAuth();
  const [archivedOpen, setArchivedOpen] = useState(false);
  /** V5.9 行内重命名的会话 id 与草稿值 */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  /** V5.9 AI 生成标题中的会话 id（按钮转圈防重复点击） */
  const [aiBusyId, setAiBusyId] = useState<string | null>(null);

  // V5.11 进行中任务所属会话集合：taskStore 5s 轮询已有全量任务（含 conversation_id/status），前端 join 即可
  // 注意：下方所有 hook（runningConvIds / recentKeepIds）都必须在 `if (!token) return`
  // 早退之前调用，否则登出/登录切换时 hook 数量不一致会崩（React #300，V5.13 踩坑实录）
  const tasks = useTaskStore((s) => s.tasks);
  const runningConvIds = useMemo(
    () =>
      new Set(
        tasks
          .filter((t) => t.status === "running" || t.status === "pending")
          .map((t) => t.conversation_id)
          .filter(Boolean) as string[],
      ),
    [tasks],
  );

  // 简短会话归档：当前会话不归档（保证正在用的会话永远可见可切回）
  // V5.11 豁免：今天有更新（刚发起的探索/任务会话立刻可见，过夜自动沉底）、有进行中任务（生成中永不折叠）
  // V5.13 收敛：仅当总数超过 RECENT_KEEP 条才启动折叠，最近更新的前 RECENT_KEEP 条
  // 始终平铺可见——防止会话不多时整栏被折叠成一条「已归档」。
  // ⚠️ 必须保持在早退之前：hook 顺序约束同上（V5.13 回归教训）
  const recentKeepIds = useMemo(
    () =>
      new Set(
        [...conversations]
          .sort((a, b) => {
            const ta = parseServerTime(a.updated_at || a.created_at)?.getTime() ?? 0;
            const tb = parseServerTime(b.updated_at || b.created_at)?.getTime() ?? 0;
            return tb - ta;
          })
          .slice(0, RECENT_KEEP)
          .map((c) => c.id),
      ),
    [conversations],
  );

  const startEdit = (c: Conv) => {
    // 编辑草稿取「展示标题」对应的原标题：低信息量标题时直接用原标题改
    cancelRef.current = false;
    setEditingId(c.id);
    setEditTitle(c.title || "");
  };

  /** 防双提交锁：Enter 提交进行中又触发 blur 时不再重复 PATCH */
  const savingRef = useRef(false);
  /** Esc 取消标记：输入框卸载瞬间若仍触发 blur，跳过提交 */
  const cancelRef = useRef(false);

  const saveEdit = async () => {
    const id = editingId;
    if (!id || savingRef.current) return;
    savingRef.current = true;
    try {
      const ok = await renameConversation(id, editTitle);
      if (ok) toast("已重命名");
      setEditingId(null);
    } finally {
      savingRef.current = false;
    }
  };

  /**
   * V5.9.2 blur 即提交：点击行外空白 / 切换会话 = 确认修改（同 Enter）。
   * 空标题静默取消（不保留编辑态、不打扰）；Esc 取消走 onKeyDown 分支不经此。
   */
  const commitOnBlur = () => {
    if (cancelRef.current) {
      cancelRef.current = false;
      return;
    }
    if (!editingId || savingRef.current) return;
    if (!editTitle.trim()) {
      setEditingId(null);
      return;
    }
    void saveEdit();
  };

  const aiRename = async (c: Conv) => {
    if (aiBusyId) return;
    if (!c.message_count) {
      toast("会话还没有内容，先发条消息再生成标题");
      return;
    }
    setAiBusyId(c.id);
    const t = await aiRenameConversation(c.id);
    setAiBusyId(null);
    if (t) toast(`已生成标题：${t}`);
  };

  /**
   * 展示标题：低信息量标题时优先取首条用户消息摘要（当前会话有消息缓存时
   * 可得；历史列表 API 不含首条消息，纯展示层拿不到则退语义化兜底名）。
   */
  const displayTitle = (c: Conv): string => {
    const t = (c.title || "").trim();
    if (!isLowInfoTitle(t)) return t;
    if (c.id === currentId) {
      const firstUser = messages.find((m) => m.role === "user");
      const brief = briefFromFirstMsg(firstUser?.content);
      if (brief) return brief;
    }
    if (BARE_TASK_RE.test(t)) return "测试任务会话";
    return "未命名会话";
  };

  const renderItem = (c: Conv) => {
    const shownTitle = displayTitle(c);
    const editing = editingId === c.id;
    return (
      <div
        key={c.id}
        className={`hist-item ${c.id === currentId ? "active" : ""}`}
        onClick={() => {
          void loadConversation(c.id);
          // 移动端：选中会话后收起抽屉，露出对话流
          closeConvDrawer();
        }}
      >
        {editing ? (
          <input
            className="h-edit"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") void saveEdit();
              if (e.key === "Escape") {
                cancelRef.current = true;
                setEditingId(null);
              }
            }}
            onBlur={commitOnBlur}
            onClick={(e) => e.stopPropagation()}
            maxLength={80}
            autoFocus
            aria-label="重命名会话"
          />
        ) : (
          <div className="h-name" title={c.title && c.title !== shownTitle ? `原标题：${c.title}` : undefined}>
            {shownTitle}
          </div>
        )}
        <button
          className="h-op h-ren"
          title="重命名"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            startEdit(c);
          }}
        >
          <Pencil size={13} />
        </button>
        <button
          className={`h-op h-ai ${aiBusyId === c.id ? "busy" : ""}`}
          title="AI 总结生成标题"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            void aiRename(c);
          }}
        >
          <Sparkles size={13} />
        </button>
        <button
          className="h-del"
          title="删除会话"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            const n = c.task_count || 0;
            const tip = n
              ? `该会话关联 ${n} 个任务，删除会话不影响任务。确定删除「${shownTitle}」？`
              : `确定删除「${shownTitle}」？`;
            if (window.confirm(tip)) void deleteConversation(c.id);
          }}
        >
          <Trash2 size={14} />
        </button>
        <div className="h-meta">
          <span>{c.message_count || 0} 条消息</span>
          {/* 分隔点粘进后一段（V5.12.1）：h-meta 是 flex-wrap，点若是独立元素会被孤零零留在上一行行尾 */}
          {c.task_count ? <span>· {c.task_count} 个任务</span> : null}
          <span>· {fmtTime(c.updated_at || c.created_at)}</span>
        </div>
      </div>
    );
  };

  if (!token) {
    return (
      <aside className="side-panel conv-panel">
        <div className="side-head">
          <span>历史会话</span>
          <button className="qtag" type="button" onClick={onNewConversation}>
            <Plus size={14} /> 新对话
          </button>
        </div>
        <div className="hist-empty">登录或游客体验后查看历史会话</div>
        {/* 移动端全屏遮罩（aside 内部尾部，fixed inset:0 不受父级约束；桌面端 CSS 隐藏） */}
        <div className="conv-backdrop" onClick={closeConvDrawer} />
      </aside>
    );
  }

  // 简短会话归档：当前会话不归档（保证正在用的会话永远可见可切回）
  // V5.11 豁免：今天有更新（刚发起的探索/任务会话立刻可见，过夜自动沉底）、有进行中任务（生成中永不折叠）
  // V5.13 收敛：仅当总数超过 RECENT_KEEP 条才启动折叠，最近更新的前 RECENT_KEEP 条
  // 始终平铺可见——防止会话不多时整栏被折叠成一条「已归档」。
  const isShort = (c: Conv) =>
    c.id !== currentId &&
    (c.message_count ?? 0) < SHORT_CONV_LIMIT &&
    !isFreshToday(c) &&
    !runningConvIds.has(c.id) &&
    !recentKeepIds.has(c.id);
  const active = conversations.filter((c) => !isShort(c));
  const short = conversations.filter(isShort);
  const groups = groupByDate(active);

  return (
    <aside className="side-panel conv-panel">
      <div className="side-head">
        <span>历史会话</span>
        <button className="qtag" type="button" onClick={onNewConversation}>
          <Plus size={14} /> 新对话
        </button>
      </div>
      <div className="hist-list">
        {conversations.length === 0 ? (
          <div className="hist-empty">
            还没有会话
            <br />
            右侧发条消息开始
          </div>
        ) : (
          <>
            {active.length === 0 && short.length > 0 ? (
              <div className="hist-empty">正式会话都归档在下方</div>
            ) : null}
            {(["今天", "昨天", "更早"] as Group[]).map((label) =>
              groups[label].length === 0 ? null : (
                <div key={label}>
                  <div className="group-label">{label}</div>
                  {groups[label].map((c) => renderItem(c))}
                </div>
              )
            )}
            {short.length > 0 && (
              <div className="hist-archived">
                <button
                  type="button"
                  className="group-label hist-archived-toggle"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    width: "100%",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    padding: "8px 12px 4px",
                    textAlign: "left",
                    opacity: 0.75,
                  }}
                  onClick={() => setArchivedOpen((v) => !v)}
                  title={archivedOpen ? "收起简短会话" : "展开简短会话"}
                >
                  {archivedOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  已归档 · {short.length} 个简短会话
                </button>
                {archivedOpen && short.map((c) => renderItem(c))}
              </div>
            )}
          </>
        )}
      </div>
      {/* 移动端全屏遮罩（aside 内部尾部，fixed inset:0 不受父级约束；桌面端 CSS 隐藏） */}
      <div className="conv-backdrop" onClick={closeConvDrawer} />
    </aside>
  );
}
