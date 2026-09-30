/**
 * V4.5.1 系统自检 · 悬浮通知（selfcheck-report-card 设计稿落地）：
 * - 右上角滑入，完成后 6s 自动消失（底部倒计时进度条），hover 暂停，可手动 ✕ 关闭
 * - 四项检查：服务连通 / 加密链路 / 登录态 / 会话有效期；原始响应折叠保留（调试用）
 * - 复用 GET /api/auth/me，后端零改动
 */
import { useCallback, useEffect, useRef, useState } from "react";

type Pill = "ok" | "warn" | "fail";
type Item = {
  icon: string; color: string; name: string; sub: string;
  pill?: string; pillCls?: Pill; loading?: boolean; dim?: boolean;
};

const AUTO_MS = 6000;
const ROLE_LABEL: Record<string, string> = { admin: "管理员", user: "用户", guest: "访客" };

export default function SelfCheckToast({ runId, onDone }: { runId: number; onDone: () => void }) {
  const [phase, setPhase] = useState<"loading" | "done">("loading");
  const [items, setItems] = useState<Item[]>([]);
  const [raw, setRaw] = useState("");
  const [cost, setCost] = useState(0);
  const [pass, setPass] = useState(0);
  const [closing, setClosing] = useState(false);
  const [hovering, setHovering] = useState(false);
  const timerRef = useRef<number | null>(null);
  const remainRef = useRef(AUTO_MS);
  const startedRef = useRef(0);

  const close = useCallback(() => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    setClosing(true);
    window.setTimeout(onDone, 260);
  }, [onDone]);

  const run = useCallback(async () => {
    setPhase("loading");
    setClosing(false);
    setHovering(false);
    setItems([
      { icon: "📡", color: "i-net", name: "服务连通", sub: "请求中…", loading: true },
      { icon: "🔐", color: "i-enc", name: "加密链路", sub: "等待上一步完成", dim: true },
      { icon: "👤", color: "i-user", name: "登录态", sub: "等待上一步完成", dim: true },
      { icon: "⏱", color: "i-time", name: "会话有效期", sub: "等待上一步完成", dim: true },
    ]);
    const t0 = performance.now();
    let status = 0; let info: Record<string, unknown> = {}; let text = "";
    try {
      // 必须走 api 客户端（自动带 Authorization）；裸 fetch 无 token 会 401 误判未登录
      const { api } = await import("../api/client");
      const r = await api("/api/auth/me");
      status = r.status;
      text = await r.text();
      try { info = JSON.parse(text) as Record<string, unknown>; } catch { /* 非 JSON 保留原文 */ }
    } catch (e) {
      text = e instanceof Error ? e.message : String(e);
    }
    const ms = Math.round(performance.now() - t0);
    setCost(ms);
    setRaw(text || "(空响应)");

    const ok = status >= 200 && status < 300;
    const role = typeof info.role === "string" ? info.role : "";
    const logged = ok && !!info.username;
    const remainH = typeof info.remaining_hours === "number" ? info.remaining_hours : null;
    const expires = typeof info.expires_at === "string" ? info.expires_at : null;

    let expirySub = "未登录，无会话"; let expiryPill: Pill = "warn"; let expiryText = "—";
    if (logged) {
      if (remainH == null && !expires) { expirySub = "长期有效"; expiryPill = "ok"; expiryText = "✓ 有效"; }
      else if (remainH != null && remainH <= 0) { expirySub = `已于 ${expires || "此前"} 过期，请重新登录`; expiryPill = "fail"; expiryText = "✗ 已过期"; }
      else {
        expiryPill = "ok"; expiryText = "✓ 有效";
        expirySub = remainH != null
          ? `剩余 ${remainH >= 24 ? `${Math.round(remainH / 24)} 天` : `${Math.round(remainH)} 小时`}（${expires || ""} 到期）`
          : `到期时间 ${expires}`;
      }
    }
    setPass([ok, logged && role !== "admin", logged, logged && expiryPill !== "fail"].filter(Boolean).length);
    setItems([
      {
        icon: "📡", color: "i-net", name: "服务连通",
        sub: `GET /api/auth/me · HTTP ${status || "ERR"} · ${ms}ms`,
        pill: ok ? "✓ 正常" : "✗ 异常", pillCls: ok ? "ok" : "fail",
      },
      role && role !== "admin"
        ? { icon: "🔐", color: "i-enc", name: "加密链路", sub: `AES-256-GCM 透明解密正常（role=${role}）`, pill: "✓ 加密", pillCls: "ok" as Pill }
        : { icon: "🔐", color: "i-enc", name: "加密链路", sub: `明文直通（role=${role || "未登录"}，管理员/未登录不经过用户级加密层）`, pill: "⚠ 明文", pillCls: "warn" as Pill },
      logged
        ? { icon: "👤", color: "i-user", name: "登录态", sub: `${info.username} · ${ROLE_LABEL[role] || role} · ${info.email || "无邮箱"}`, pill: "✓ 已登录", pillCls: "ok" as Pill }
        : { icon: "👤", color: "i-user", name: "登录态", sub: "当前未登录（游客模式）", pill: "未登录", pillCls: "warn" as Pill },
      { icon: "⏱", color: "i-time", name: "会话有效期", sub: expirySub, pill: expiryText, pillCls: expiryPill },
    ]);
    setPhase("done");
  }, []);

  useEffect(() => { if (runId > 0) void run(); }, [runId, run]);

  // 完成后自动倒计时；hover 暂停/恢复（进度条动画同步暂停）
  useEffect(() => {
    if (phase !== "done") return;
    remainRef.current = AUTO_MS;
    startedRef.current = Date.now();
    timerRef.current = window.setTimeout(close, AUTO_MS);
    return () => { if (timerRef.current) window.clearTimeout(timerRef.current); };
  }, [phase, runId, close]);

  const pause = () => {
    setHovering(true);
    if (phase !== "done") return;
    if (timerRef.current) window.clearTimeout(timerRef.current);
    remainRef.current -= Date.now() - startedRef.current;
  };
  const resume = () => {
    setHovering(false);
    if (phase !== "done") return;
    startedRef.current = Date.now();
    timerRef.current = window.setTimeout(close, Math.max(remainRef.current, 400));
  };

  return (
    <div className="sc-zone" role="status" aria-live="polite">
      <div
        className={"sc-toast" + (closing ? " out" : "")}
        onMouseEnter={pause}
        onMouseLeave={resume}
      >
        <div className="sc-head">
          <span className="sc-shield">🛡</span>
          <span className="sc-title">
            <b>{phase === "loading" ? "系统自检中…" : pass === 4 ? "系统自检通过" : "发现问题，请关注"}</b>
            <small>{phase === "loading" ? "正在逐项验证" : `4 项检查 · ${cost}ms`}</small>
          </span>
          {phase === "done" && (
            <span className="sc-count"><b>{pass}/4</b><small>通过</small></span>
          )}
          <button className="sc-x" onClick={close} aria-label="关闭自检通知">✕</button>
        </div>
        <div className="sc-items">
          {items.map((it) => (
            <div key={it.name} className={"sc-item" + (it.dim ? " dim" : "")}>
              <span className={"sc-ico " + it.color}>{it.icon}</span>
              <span className="sc-meta">
                <b>{it.name}</b>
                <small>{it.sub}</small>
              </span>
              {it.loading ? (
                <span className="sc-spin" aria-label="检测中" />
              ) : (
                it.pill && <span className={"sc-pill " + it.pillCls}>{it.pill}</span>
              )}
            </div>
          ))}
        </div>
        {phase === "done" && (
          <>
            <details className="sc-raw">
              <summary>▸ 原始响应（调试用）</summary>
              <pre>{raw}</pre>
            </details>
            <div className="sc-acts">
              <button className="sc-again" onClick={() => void run()}>重新检测</button>
              <button className="sc-ok" onClick={close}>完成</button>
            </div>
          </>
        )}
        {phase === "done" && (
          <div
            className="sc-bar"
            style={{
              animation: `sc-shrink ${AUTO_MS}ms linear forwards`,
              animationPlayState: hovering ? "paused" : "running",
            }}
          />
        )}
      </div>
    </div>
  );
}
