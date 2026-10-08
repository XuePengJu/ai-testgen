/**
 * V7.0-V7.1 记忆条目面板（知识库页「记忆」Tab，仅个人记忆库渲染）。
 *
 * 数据源：/api/memory/*（用户维度，与 kb.id 无关——后端按 current_user 硬过滤）。
 * 能力：
 * - 列表：kind 徽标 + subject + 置信度条 + 重要性 + 状态徽标 + content + 溯源行
 * - 过滤：kind 下拉 / status 下拉 / 关键词搜索（q，350ms 防抖）
 * - 冲突：红边高亮，可展开对照被冲突的 active 条目（GET /items/{id} 的 conflict_with_item）
 * - 操作：编辑（行内 textarea → PATCH）/ 删除（软删）/ 彻底删除（二次确认）/ 采纳新的 / 恢复
 * - 聚合：顶部右侧展示 /api/memory/stats（总数/冲突/过期等）
 *
 * 铁律：独立文件（KnowledgePage.tsx 已 1100+ 行不再加码）；不引新依赖，fetch 走既有 apiJson。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Brain, Check, ChevronDown, ChevronRight, Pencil, RefreshCw,
  Search, Trash2, Undo2,
} from "lucide-react";
import { apiJson, toast } from "../../api/client";
import type { MemoryItem, MemoryItemDetail, MemoryStats } from "../../types";

/* ---------------- 常量字典（与 app/models/memory.py 枚举同口径） ---------------- */

/** kind → 中文标签 + 主题色（沿用 doc-type-ic 的色块视觉语言） */
const KIND_META: Record<string, { label: string; color: string }> = {
  fact: { label: "事实", color: "#2563eb" },
  preference: { label: "偏好", color: "#7c3aed" },
  rule: { label: "规则", color: "#dc2626" },
  todo: { label: "待办", color: "#d97706" },
  profile: { label: "画像", color: "#0891b2" },
};
const kindMeta = (k?: string) =>
  KIND_META[k ?? ""] ?? { label: k || "未知", color: "#64748b" };

/** status → 中文标签 + 徽标色调（active 绿 / superseded 灰 / conflict 红 / expired 黄 / deleted 灰） */
const STATUS_META: Record<string, { label: string; cls: string }> = {
  active: { label: "生效中", cls: "active" },
  superseded: { label: "已被取代", cls: "superseded" },
  conflict: { label: "冲突待裁决", cls: "conflict" },
  expired: { label: "已过期", cls: "expired" },
  deleted: { label: "已删除", cls: "deleted" },
};

/** source → 来源标签 */
const SOURCE_META: Record<string, string> = {
  conversation: "对话抽取",
  user: "手动写入",
  file: "文档提取",
};

const fmtPct = (v: number | null | undefined) =>
  `${Math.round(Math.max(0, Math.min(1, v ?? 0)) * 100)}%`;

const fmtDay = (iso?: string | null) =>
  iso ? iso.slice(0, 10) : "—";

export default function MemoryPanel({
  kb,
}: {
  kb: { id: string; name: string; is_personal?: boolean };
}) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  /* 过滤条件（kind/status 立即生效；q 走 350ms 防抖） */
  const [kindF, setKindF] = useState("");
  const [statusF, setStatusF] = useState("");
  const [kw, setKw] = useState("");
  const [kwDeb, setKwDeb] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setKwDeb(kw), 350);
    return () => clearTimeout(t);
  }, [kw]);

  /* 冲突对照展开态：条目 id → 被冲突的 active 条目（懒加载缓存） */
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [conflictRefs, setConflictRefs] = useState<Record<string, MemoryItem | null>>({});

  /** 行内编辑态：仅一条可同时编辑（content） */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    const params = new URLSearchParams();
    if (kindF) params.set("kind", kindF);
    if (statusF) params.set("status", statusF);
    if (kwDeb.trim()) params.set("q", kwDeb.trim());
    params.set("limit", "200");
    const [list, st] = await Promise.all([
      apiJson<{ items: MemoryItem[]; total: number }>(`/api/memory/items?${params.toString()}`),
      apiJson<MemoryStats>("/api/memory/stats"),
    ]);
    setItems(list?.items ?? []);
    setStats(st ?? null);
    setLoading(false);
    setRefreshing(false);
  }, [kindF, statusF, kwDeb]);
  useEffect(() => { void load(); }, [load]);

  /** 展开冲突对照：拉详情取 conflict_with_item（被冲突的 active 条目） */
  const toggleConflict = async (it: MemoryItem) => {
    if (expandedId === it.id) { setExpandedId(null); return; }
    setExpandedId(it.id);
    if (conflictRefs[it.id] !== undefined) return; // 已缓存
    const d = await apiJson<MemoryItemDetail>(`/api/memory/items/${it.id}`);
    setConflictRefs((prev) => ({ ...prev, [it.id]: d?.conflict_with_item ?? null }));
  };

  /* ---------------- 行操作 ---------------- */

  const saveEdit = async (it: MemoryItem) => {
    const text = editText.trim();
    if (!text) { toast("内容不能为空"); return; }
    if (text === it.content) { setEditingId(null); return; }
    setBusyId(it.id);
    const r = await apiJson<{ ok: boolean }>(`/api/memory/items/${it.id}`, {
      method: "PATCH",
      body: JSON.stringify({ content: text }),
    });
    setBusyId(null);
    if (r) { setEditingId(null); toast("已保存（旧版本入档，可追溯）"); void load(); }
  };

  /** 软删：进宽限期（30 天后物理清），confirm 一次 */
  const delSoft = async (it: MemoryItem) => {
    if (!window.confirm(`删除记忆条目「${it.subject || it.content.slice(0, 20)}」？\n删除后进入宽限期，到期由系统自动清理。`)) return;
    setBusyId(it.id);
    const r = await apiJson<{ ok: boolean }>(`/api/memory/items/${it.id}`, { method: "DELETE" });
    setBusyId(null);
    if (r) { toast("已删除（宽限期内可由系统清理）"); void load(); }
  };

  /** 硬删：立即物理删，二次 confirm 且文案明确「不可恢复」 */
  const delHard = async (it: MemoryItem) => {
    const title = it.subject || it.content.slice(0, 20);
    if (!window.confirm(`彻底删除「${title}」？\n该操作立即物理删除条目，不可恢复！`)) return;
    if (!window.confirm("再次确认：彻底删除后无法找回，确定继续吗？")) return;
    setBusyId(it.id);
    const r = await apiJson<{ ok: boolean }>(`/api/memory/items/${it.id}?hard=true`, { method: "DELETE" });
    setBusyId(null);
    if (r) { toast("已彻底删除"); void load(); }
  };

  /** 冲突条目一键采纳：升级 active 并取代被冲突条目 */
  const adopt = async (it: MemoryItem) => {
    if (!window.confirm("采纳这条新记忆？它将成为生效版本并取代旧条目。")) return;
    setBusyId(it.id);
    const r = await apiJson<{ ok: boolean }>(`/api/memory/items/${it.id}/adopt`, { method: "POST" });
    setBusyId(null);
    if (r) { toast("已采纳：新记忆生效"); setExpandedId(null); void load(); }
  };

  /** 恢复 superseded 旧版本（按置信度竞争，不足时后端 400 拒绝并 toast） */
  const restore = async (it: MemoryItem) => {
    setBusyId(it.id);
    const r = await apiJson<{ ok: boolean }>(`/api/memory/items/${it.id}/restore`, { method: "POST" });
    setBusyId(null);
    if (r) { toast("已恢复为生效版本"); void load(); }
  };

  /* ---------------- 渲染 ---------------- */

  const filteredCount = items.length;
  const isFiltered = !!(kindF || statusF || kwDeb.trim());

  return (
    <div className="kb-panel mem-panel">
      {/* 库头：🧠 标识 + 面板说明（与 DocsTab 库头同视觉） */}
      <header className="kb-lib-head">
        <h2 className="kb-lib-name">🧠 记忆条目</h2>
        <span className="kb-vis private">{kb.name}</span>
        <span className="kb-lib-stats muted">
          {stats ? `${stats.total} 条 · 平均置信度 ${fmtPct(stats.avg_confidence_active)}` : "加载中…"}
        </span>
      </header>

      {/* 工具栏：kind/status 过滤 + 关键词搜索 + 右侧健康度聚合 */}
      <div className="kb-toolbar mem-toolbar">
        <select className="input" style={{ width: 120 }} value={kindF} aria-label="按种类筛选"
          onChange={(e) => setKindF(e.target.value)}>
          <option value="">全部种类</option>
          {Object.entries(KIND_META).map(([k, m]) => (
            <option key={k} value={k}>{m.label}</option>
          ))}
        </select>
        <select className="input" style={{ width: 130 }} value={statusF} aria-label="按状态筛选"
          onChange={(e) => setStatusF(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_META).map(([s, m]) => (
            <option key={s} value={s}>{m.label}</option>
          ))}
        </select>
        <div className="mem-search-wrap">
          <Search size={13} className="mem-search-ic" />
          <input className="input" style={{ paddingLeft: 26, width: 200 }} value={kw}
            placeholder="搜索主题或内容…" onChange={(e) => setKw(e.target.value)} />
        </div>
        <button className="btn ghost btn-sm" onClick={() => void load()} disabled={refreshing} title="刷新列表">
          <RefreshCw size={13} className={refreshing ? "spin" : ""} /> 刷新
        </button>
        <span className="mem-stats">
          <span>生效 <b>{stats?.active ?? "—"}</b></span>
          <span className={stats?.conflict ? "mem-stat-warn" : ""}>冲突 <b>{stats?.conflict ?? "—"}</b></span>
          <span className={stats?.expired ? "mem-stat-warn" : ""}>过期 <b>{stats?.expired ?? "—"}</b></span>
          <span>已取代 <b>{stats?.superseded ?? "—"}</b></span>
        </span>
        {isFiltered && <span className="muted" style={{ fontSize: 12 }}>筛出 {filteredCount} 条</span>}
      </div>

      {/* 条目列表 */}
      <div className="mem-list">
        {loading && <div className="muted" style={{ padding: 32, textAlign: "center" }}>加载中…</div>}
        {!loading && items.length === 0 && !isFiltered && (
          <div className="muted" style={{ padding: 40, textAlign: "center" }}>
            暂无记忆条目，AI 对话后夜间自动沉淀，或点对话输入区 🧠 立即整理
          </div>
        )}
        {!loading && items.length === 0 && isFiltered && (
          <div className="muted" style={{ padding: 40, textAlign: "center" }}>
            没有匹配「{[kindF && kindMeta(kindF).label, statusF && STATUS_META[statusF]?.label, kwDeb.trim()].filter(Boolean).join(" / ")}」的记忆条目
          </div>
        )}
        {items.map((it) => {
          const km = kindMeta(it.kind);
          const sm = STATUS_META[it.status] ?? { label: it.status, cls: "superseded" };
          const isConflict = it.status === "conflict" && !!it.conflict_with;
          const isEditing = editingId === it.id;
          const busy = busyId === it.id;
          const ref = conflictRefs[it.id];
          const expanded = expandedId === it.id;
          return (
            <article key={it.id} className={"mem-item" + (isConflict ? " mem-conflict" : "")}>
              {/* 头行：kind 徽标 + subject + 置信度条 + 重要性 + 状态徽标 */}
              <div className="mem-item-head">
                <span className="mem-kind" style={{ background: km.color + "1a", color: km.color }}>
                  {km.label}
                </span>
                <span className="mem-subject" title={it.subject}>{it.subject || "（无主题）"}</span>
                <span className="mem-conf" title={`置信度 ${fmtPct(it.confidence)}`}>
                  <span className="mem-conf-bar"><span style={{ width: fmtPct(it.confidence) }} /></span>
                  <span className="mem-conf-num">{fmtPct(it.confidence)}</span>
                </span>
                <span className="mem-imp" title="重要性（影响检索排序）">重要 {fmtPct(it.importance)}</span>
                <span className={"mem-badge mem-badge-" + sm.cls}>{sm.label}</span>
                <span className="mem-ver muted" title={`版本链第 ${it.version} 版`}>v{it.version}</span>
              </div>

              {/* 正文：查看态 / 行内编辑态 */}
              {isEditing ? (
                <div className="mem-edit">
                  <textarea className="input" rows={3} value={editText} disabled={busy}
                    onChange={(e) => setEditText(e.target.value)} maxLength={500} />
                  <div className="mem-ops-row">
                    <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void saveEdit(it)}>
                      <Check size={13} /> 保存
                    </button>
                    <button className="btn btn-sm ghost" disabled={busy} onClick={() => setEditingId(null)}>取消</button>
                    <span className="muted" style={{ fontSize: 11.5 }}>保存后旧版本自动入档（走版本链取代）</span>
                  </div>
                </div>
              ) : (
                <div className="mem-content">{it.content || "（空）"}</div>
              )}

              {/* 溯源行：来源 · 会话 · 消息 · evidence 片段 · 命中次数 · 到期 */}
              <div className="mem-prov">
                <span>{SOURCE_META[it.source] ?? it.source}</span>
                {it.conversation_id && <span title={it.conversation_id}>会话 {it.conversation_id.slice(0, 8)}</span>}
                {typeof it.message_id === "number" && it.message_id > 0 && <span>消息 #{it.message_id}</span>}
                <span title={it.evidence || ""}>
                  {it.evidence ? `“${it.evidence.length > 40 ? it.evidence.slice(0, 40) + "…" : it.evidence}”` : "无原文摘录"}
                </span>
                <span title={it.last_hit_at ? `最近命中 ${fmtDay(it.last_hit_at)}` : "尚未被检索命中"}>
                  命中 {it.hit_count} 次
                </span>
                {it.expires_at && <span title={`到期后自动转为过期`}>{fmtDay(it.expires_at)} 到期</span>}
              </div>

              {/* 操作行：编辑 / 采纳新的（conflict）/ 恢复（superseded）/ 删除 / 彻底删除 */}
              <div className="mem-ops-row">
                {it.status === "active" && !isEditing && (
                  <button className="btn ghost btn-sm" disabled={busy}
                    onClick={() => { setEditingId(it.id); setEditText(it.content); }}>
                    <Pencil size={13} /> 编辑
                  </button>
                )}
                {isConflict && (
                  <button className="btn btn-sm mem-adopt-btn" disabled={busy} onClick={() => void adopt(it)}>
                    <Check size={13} /> 采纳新的
                  </button>
                )}
                {it.status === "superseded" && (
                  <button className="btn ghost btn-sm" disabled={busy} onClick={() => void restore(it)}>
                    <Undo2 size={13} /> 恢复
                  </button>
                )}
                {it.status !== "deleted" && (
                  <>
                    <button className="btn ghost btn-sm danger" disabled={busy} onClick={() => void delSoft(it)}>
                      <Trash2 size={13} /> 删除
                    </button>
                    <button className="btn ghost btn-sm danger" disabled={busy} onClick={() => void delHard(it)}
                      title="立即物理删除，不可恢复">
                      彻底删除
                    </button>
                  </>
                )}
                {isConflict && (
                  <button className="btn ghost btn-sm" onClick={() => void toggleConflict(it)}>
                    {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    对照旧条目
                  </button>
                )}
              </div>

              {/* 冲突对照：展开展示被冲突的 active 条目 */}
              {isConflict && expanded && (
                <div className="mem-conf-box">
                  {!conflictRefs[it.id] ? (
                    <div className="muted" style={{ fontSize: 12, padding: "4px 0" }}>加载对照条目…</div>
                  ) : ref ? (
                    <>
                      <div className="mem-conf-title">与以下生效条目冲突（AI 挂起待裁决）：</div>
                      <div className="mem-conf-item">
                        <div className="mem-item-head">
                          <span className="mem-kind" style={{ background: km.color + "1a", color: km.color }}>
                            {kindMeta(ref.kind).label}
                          </span>
                          <span className="mem-subject" title={ref.subject}>{ref.subject || "（无主题）"}</span>
                          <span className="mem-conf" title={`置信度 ${fmtPct(ref.confidence)}`}>
                            <span className="mem-conf-bar"><span style={{ width: fmtPct(ref.confidence) }} /></span>
                            <span className="mem-conf-num">{fmtPct(ref.confidence)}</span>
                          </span>
                          <span className="mem-badge mem-badge-active">生效中</span>
                          <span className="mem-ver muted">v{ref.version}</span>
                        </div>
                        <div className="mem-content">{ref.content}</div>
                        <div className="mem-prov">
                          <span>{SOURCE_META[ref.source] ?? ref.source}</span>
                          {ref.conversation_id && <span>会话 {ref.conversation_id.slice(0, 8)}</span>}
                          <span>命中 {ref.hit_count} 次</span>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="muted" style={{ fontSize: 12, padding: "4px 0" }}>
                      被冲突的旧条目不存在（可能已被删除）
                    </div>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>

      {/* 底部说明：命中次数与 brain 图标呼应（不可点击，纯说明） */}
      {items.length > 0 && (
        <div className="mem-foot muted">
          <Brain size={13} />
          命中次数 = 该条记忆在 AI 对话检索中被采用的次数，越高说明越常被引用；
          删除走软删宽限期，「彻底删除」立即物理删除且不可恢复。
        </div>
      )}
    </div>
  );
}
