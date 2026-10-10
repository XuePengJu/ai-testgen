/**
 * 使用统计页（V5.12 可观测性，仅 admin）：
 * - 概览卡：今日 / 累计的会话、消息、任务、LLM 调用、HTTP 请求、用户
 * - 趋势图：近 14 天业务量折线（echarts）+ HTTP 请求量折线
 * - 明细表：最近 LLM 调用（时间/模型/动作/成败/耗时/错误），点行看请求/返回详情（V5.14）
 * 数据源：/api/stats/overview、/api/stats/daily、/api/stats/llm/recent、
 *         /api/stats/llm/{id}（均 admin-only）
 */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactECharts from "echarts-for-react";
import {
  MessageSquare, Library, Cpu, Activity, Users, RefreshCw, BarChart3, X,
} from "lucide-react";
import { apiJson, API } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import PageHead from "../components/common/PageHead";
import { fmtCnFull } from "../utils/time";

interface OverviewResp {
  today: {
    conversations: number; messages: number; tasks: number;
    llm_calls: number; requests: number;
  };
  total: {
    users: number; guests: number; conversations: number; messages: number;
    tasks: number; llm_calls: number; llm_fail: number;
    llm_avg_ms: number | null; requests: number;
  };
  llm_by_model: { model: string; calls: number }[];
}

interface DailyResp {
  days: string[];
  conversations: number[];
  messages: number[];
  tasks: number[];
  llm_calls: number[];
  requests: number[];
}

interface UsageRow {
  id: number; created_at: string | null; model: string; slot: string;
  action: string; ok: boolean; latency_ms: number;
  prompt_chars: number; completion_chars: number; user_id: number; error: string;
}

interface UsageDetail extends UsageRow {
  prompt_preview: string;
  completion_preview: string;
}

function StatCard({ icon, num, label, cls = "blue" }: {
  icon: React.ReactNode; num?: number | string | null; label: string; cls?: string;
}) {
  return (
    <div className="stat-card">
      <div className={"stat-icon " + cls}>{icon}</div>
      <div className="stat-body">
        <div className="s-num">{num ?? "—"}</div>
        <div className="s-label">{label}</div>
      </div>
    </div>
  );
}

const LINE_COLORS = { conversations: "#3b82f6", messages: "#8b5cf6", tasks: "#10b981", llm_calls: "#f59e0b" };

/** LLM 调用详情弹窗（V5.14）：展示请求 / 返回内容快照。 */
function LlmDetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const [detail, setDetail] = useState<UsageDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setDetail(null); setErr("");
    apiJson<UsageDetail>(`${API}/stats/llm/${id}`)
      .then((d) => { if (alive) setDetail(d); })
      .catch((e) => { if (alive) setErr(String(e?.message || e)); });
    return () => { alive = false; };
  }, [id]);

  // createPortal 挂到 body：避免受页面容器动画/滚动上下文影响，
  // 保证 position:fixed 恒以视口定位（弹窗居中在当前窗口中间）
  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000, background: "rgba(31,35,41,.45)",
        display: "grid", placeItems: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card"
        data-testid="llm-detail-modal"
        style={{ width: "min(860px, 100%)", maxHeight: "80vh", overflowY: "auto", position: "relative" }}
      >
        <button
          onClick={onClose}
          aria-label="关闭详情"
          style={{
            position: "absolute", top: 12, right: 12, border: "none", background: "none",
            cursor: "pointer", color: "#646a73", padding: 4,
          }}
        >
          <X size={18} />
        </button>
        {err && <div style={{ color: "#ef4444", fontSize: 13 }}>加载失败：{err}</div>}
        {!detail && !err && <div style={{ color: "#888", fontSize: 13 }}>加载中…</div>}
        {detail && (
          <>
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 10 }}>
              调用详情 #{detail.id}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px", fontSize: 12.5, color: "#646a73", marginBottom: 14 }}>
              <span>{fmtCnFull(detail.created_at)}（北京时间）</span>
              <span>模型：<b style={{ color: "#1f2329" }}>{detail.model}</b></span>
              <span>槽位：{detail.slot}</span>
              <span>动作：{detail.action}</span>
              <span style={{ color: detail.ok ? "#10b981" : "#ef4444" }}>{detail.ok ? "成功" : "失败"}</span>
              <span>耗时 {detail.latency_ms} ms</span>
              <span>入/出 {detail.prompt_chars} / {detail.completion_chars} 字符</span>
              <span>用户 ID：{detail.user_id || "平台级"}</span>
            </div>
            {!detail.ok && detail.error && (
              <div style={{
                background: "#ffece8", color: "#d83931", borderRadius: 8,
                padding: "8px 12px", fontSize: 12.5, marginBottom: 12,
              }}>
                错误：{detail.error}
              </div>
            )}
            <div style={{ fontWeight: 600, fontSize: 13, margin: "10px 0 6px" }}>请求内容（快照）</div>
            <pre style={{
              margin: 0, padding: 12, background: "#f5f6f7", borderRadius: 8,
              fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word",
              maxHeight: 240, overflowY: "auto",
            }}>
              {detail.prompt_preview || "未记录快照（该条调用发生在快照功能上线前）"}
            </pre>
            <div style={{ fontWeight: 600, fontSize: 13, margin: "12px 0 6px" }}>模型返回（快照）</div>
            <pre style={{
              margin: 0, padding: 12, background: "#171a1f", color: "#d7e3f4", borderRadius: 8,
              fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word",
              maxHeight: 300, overflowY: "auto",
            }}>
              {detail.completion_preview || (detail.ok ? "未记录快照（该条调用发生在快照功能上线前）" : "（失败调用无返回内容）")}
            </pre>
            <div style={{ fontSize: 11.5, color: "#8f959e", marginTop: 10 }}>
              快照最长保留 4000 字符，仅用于排查；图片附件不入快照。
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}

export default function StatsPage() {
  const { ready, role } = useAuth();
  const [ov, setOv] = useState<OverviewResp | null>(null);
  const [daily, setDaily] = useState<DailyResp | null>(null);
  const [rows, setRows] = useState<UsageRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [o, d, r] = await Promise.all([
        apiJson<OverviewResp>(API + "/stats/overview"),
        apiJson<DailyResp>(API + "/stats/daily?days=14"),
        apiJson<UsageRow[]>(API + "/stats/llm/recent?limit=50"),
      ]);
      if (o) setOv(o);
      if (d) setDaily(d);
      if (r) setRows(r);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (ready && role === "admin") void loadAll();
  }, [ready, role, loadAll]);

  if (ready && role !== "admin") {
    return <div className="page-empty">仅管理员可访问</div>;
  }

  const shortDay = (iso: string) => (iso || "").slice(5); // 2026-10-01 → 10-01
  const trendOption = daily && {
    color: Object.values(LINE_COLORS),
    tooltip: { trigger: "axis" as const },
    legend: { data: ["会话", "消息", "任务", "LLM 调用"], top: 0 },
    grid: { left: 40, right: 20, top: 36, bottom: 28 },
    xAxis: { type: "category" as const, data: daily.days.map(shortDay) },
    yAxis: { type: "value" as const, minInterval: 1 },
    series: [
      { name: "会话", type: "line", smooth: true, data: daily.conversations },
      { name: "消息", type: "line", smooth: true, data: daily.messages },
      { name: "任务", type: "line", smooth: true, data: daily.tasks },
      { name: "LLM 调用", type: "line", smooth: true, data: daily.llm_calls },
    ],
  };
  const reqOption = daily && {
    color: ["#06b6d4"],
    tooltip: { trigger: "axis" as const },
    grid: { left: 48, right: 20, top: 16, bottom: 28 },
    xAxis: { type: "category" as const, data: daily.days.map(shortDay) },
    yAxis: { type: "value" as const, minInterval: 1 },
    series: [{ name: "HTTP 请求", type: "bar", data: daily.requests }],
  };

  return (
    <div className="page-wrap admin-page" data-testid="stats-page">
      <PageHead
        icon={<BarChart3 size={18} />}
        title="使用统计"
        sub="时间口径为北京时间"
        ops={
          <button
            className="btn btn-secondary btn-md"
            onClick={() => void loadAll()}
            disabled={loading}
            aria-label="刷新统计"
          >
            <RefreshCw size={14} className={loading ? "spin" : ""} /> 刷新
          </button>
        }
      />

      {/* 今日 */}
      <section className="stats-row" data-testid="stats-today">
        <StatCard icon={<MessageSquare size={22} />} num={ov?.today.conversations} label="今日新会话" />
        <StatCard icon={<Cpu size={22} />} num={ov?.today.messages} label="今日消息" cls="purple" />
        <StatCard icon={<Library size={22} />} num={ov?.today.tasks} label="今日任务" cls="green" />
        <StatCard icon={<Activity size={22} />} num={ov?.today.llm_calls} label="今日 LLM 调用" cls="orange" />
        <StatCard icon={<BarChart3 size={22} />} num={ov?.today.requests} label="今日 API 请求" cls="cyan" />
      </section>

      {/* 累计 */}
      <section className="stats-row" data-testid="stats-total">
        <StatCard icon={<Users size={22} />} num={ov?.total.users} label="累计用户" />
        <StatCard icon={<MessageSquare size={22} />} num={ov?.total.conversations} label="累计会话" />
        <StatCard icon={<Library size={22} />} num={ov?.total.tasks} label="累计任务" cls="green" />
        <StatCard
          icon={<Activity size={22} />} num={ov?.total.llm_calls} label={`累计 LLM 调用${ov ? `（失败 ${ov.total.llm_fail}）` : ""}`}
          cls="orange"
        />
        <StatCard icon={<BarChart3 size={22} />} num={ov?.total.requests} label="累计 API 请求" cls="cyan" />
      </section>

      {/* 趋势图（间距交给 .page-wrap 的 --shell-gap 统一控制） */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.8fr) minmax(0, 1fr)", gap: "var(--shell-gap)" }}>
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          {trendOption ? (
            <ReactECharts option={trendOption} style={{ height: 320, width: "100%" }} />
          ) : <div className="page-empty">加载中…</div>}
        </div>
        <div className="card" style={{ gridColumn: "1 / 2" }}>
          {reqOption && <ReactECharts option={reqOption} style={{ height: 220, width: "100%" }} />}
          <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>HTTP 请求量（/api，含轮询）</div>
        </div>
        <div className="card" style={{ gridColumn: "2 / 3" }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>模型调用 TOP5（累计）</div>
          {(ov?.llm_by_model?.length ?? 0) === 0 && <div style={{ color: "#888", fontSize: 13 }}>暂无 LLM 调用记录</div>}
          {ov?.llm_by_model.map((m) => (
            <div key={m.model} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 13, padding: "3px 0" }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={m.model}>{m.model}</span>
              <b style={{ flex: "none" }}>{m.calls}</b>
            </div>
          ))}
          {ov?.total.llm_avg_ms != null && (
            <div style={{ fontSize: 12, color: "#888", marginTop: 8 }}>成功调用平均耗时 {ov.total.llm_avg_ms} ms</div>
          )}
        </div>
      </div>

      {/* 最近 LLM 调用明细（点行查看请求 / 返回快照） */}
      <div className="card" style={{ overflowX: "auto" }} data-testid="stats-llm-recent">
        <div style={{ fontWeight: 600, marginBottom: 8 }}>
          最近 LLM 调用（最新 50 条，点击行查看详情）
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "#888" }}>
              <th style={{ padding: "4px 8px" }}>时间（北京时间）</th>
              <th style={{ padding: "4px 8px" }}>模型</th>
              <th style={{ padding: "4px 8px" }}>槽位</th>
              <th style={{ padding: "4px 8px" }}>动作</th>
              <th style={{ padding: "4px 8px" }}>结果</th>
              <th style={{ padding: "4px 8px" }}>耗时</th>
              <th style={{ padding: "4px 8px" }}>入/出 字符</th>
              <th style={{ padding: "4px 8px" }}>错误</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={8} style={{ padding: 10, color: "#888" }}>暂无记录（埋点自本版本起生效）</td></tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => setDetailId(r.id)}
                data-testid={`llm-row-${r.id}`}
                style={{
                  borderTop: "1px solid var(--border, #eee)", cursor: "pointer",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "#f5f7fa"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = ""; }}
                title="点击查看请求与返回内容"
              >
                <td style={{ padding: "4px 8px", whiteSpace: "nowrap" }}>{fmtCnFull(r.created_at)}</td>
                <td style={{ padding: "4px 8px", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.model}</td>
                <td style={{ padding: "4px 8px" }}>{r.slot}</td>
                <td style={{ padding: "4px 8px" }}>{r.action}</td>
                <td style={{ padding: "4px 8px", color: r.ok ? "#10b981" : "#ef4444" }}>{r.ok ? "成功" : "失败"}</td>
                <td style={{ padding: "4px 8px" }}>{r.latency_ms} ms</td>
                <td style={{ padding: "4px 8px" }}>{r.prompt_chars} / {r.completion_chars}</td>
                <td style={{ padding: "4px 8px", color: "#ef4444", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.error || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detailId !== null && <LlmDetailModal id={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
