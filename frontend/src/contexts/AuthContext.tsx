/**
 * AuthContext：三级角色认证（guest / user / admin）。
 *
 * 行为对齐旧前端（frontend-legacy）：
 * - 首次打开（无 token 且非手动退出）→ 静默进访客模式
 * - 手动退出 → sessionStorage 标记 atf_manual_logout，重载后弹登录框、不再自动进访客
 * - /auth/me 刷新用户信息
 * - 登录/注册/访客均走明文通道，成功后由各视图自行响应 me 变化
 *
 * V2 简化：访客是**全站唯一固定共享账号**（后端 username='guest'），
 * 所有人共用、不过期、不转正，因此没有 remaining_hours 倒计时与「转正」入口。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { AuthResponse, Me, Role } from "../types";
import {
  clearPersisted,
  getAuthSnapshot,
  persist,
  readPersisted,
  setAuthSnapshot,
  type AuthSnapshot,
} from "./authState";
import { api } from "../api/client";

type AuthMode = "login" | "register";

/** 后端错误 detail 提取：字符串直取；422 校验数组取首条 msg（避免显示 [object Object]） */
function detailOf(d: { detail?: unknown } | null | undefined): string {
  if (!d) return "";
  if (typeof d.detail === "string") return d.detail;
  if (Array.isArray(d.detail)) {
    const first = d.detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    const field = Array.isArray(first?.loc) ? first.loc.filter((x) => typeof x === "string").join(".") : "";
    return (field ? field + ": " : "") + (first?.msg || "参数校验失败");
  }
  return "";
}

interface AuthContextValue {
  me: Me | null;
  token: string;
  role: Role | null;
  ready: boolean; // 初始化完成（含静默访客进入 / me 刷新）
  showLogin: (mode?: AuthMode) => void;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  guest: () => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

function commit(s: AuthSnapshot): void {
  setAuthSnapshot(s);
  persist(s);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthSnapshot>(() => {
    const s = readPersisted();
    setAuthSnapshot(s);
    return s;
  });
  const [ready, setReady] = useState(false);
  const [loginModal, setLoginModal] = useState<{ open: boolean; mode: AuthMode }>({
    open: false,
    mode: "login",
  });
  const startedRef = useRef(false);

  const apply = useCallback((s: AuthSnapshot) => {
    commit(s);
    setState(s);
  }, []);

  /** 登录/注册成功后补拉 /auth/me：登录响应无 id（-1 占位），靠这里补齐真实 id/邮箱等 */
  const refreshMe = useCallback(async () => {
    const snap = getAuthSnapshot();
    if (!snap.token) return;
    try {
      const r = await api("/api/auth/me", { keep401: true });
      if (r.ok) {
        const me = (await r.json()) as Me;
        apply({ ...snap, me });
      }
    } catch {
      /* 网络错误：保留登录响应里的 me */
    }
  }, [apply]);

  /* 初始化：refreshMe + 首开静默访客（旧版 refreshMe/enterGuestSilently 行为） */
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    (async () => {
      if (!state.token) {
        let manual = false;
        try {
          manual = sessionStorage.getItem("atf_manual_logout") === "1";
          sessionStorage.removeItem("atf_manual_logout");
        } catch {
          /* 隐私模式等边界 */
        }
        if (manual) {
          setLoginModal({ open: true, mode: "login" });
          setReady(true);
          return;
        }
        // 静默进访客（固定共享账号）
        try {
          const r = await fetch("/api/guest/token", { method: "POST" });
          const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse>;
          if (r.ok && d.access_token) {
            apply({
              token: d.access_token,
              encKey: d.enc_key || "",
              me: {
                id: -1,
                username: d.username || "guest",
                email: null,
                role: "guest",
                is_active: true,
                remaining_hours: null, // 共享访客不过期，无倒计时
              },
            });
            setReady(true);
            return;
          }
        } catch {
          /* 后端不可达：保持未登录，弹登录框 */
        }
        setLoginModal({ open: true, mode: "login" });
        setReady(true);
        return;
      }
      // 有 token：刷新 me（走 api()：非 admin 响应为 {enc:...} 密文，需透明解密；
      // keep401 由本处自行处理失效清态，避免 api() 内部的强制登出+reload）
      try {
        const r = await api("/api/auth/me", { keep401: true });
        if (r.ok) {
          const me = (await r.json()) as Me;
          apply({ ...state, me });
        } else if (r.status === 401) {
          clearPersisted();
          setAuthSnapshot({ token: "", encKey: "", me: null });
          setState({ token: "", encKey: "", me: null });
          setLoginModal({ open: true, mode: "login" });
        }
      } catch {
        /* 网络错误：沿用 localStorage 缓存的 me */
      }
      setReady(true);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* client.ts 发出的 401 失效事件 → 立即清态弹登录（与旧版 reload 效果一致但更快） */
  useEffect(() => {
    const onInvalid = () => {
      clearPersisted();
      setAuthSnapshot({ token: "", encKey: "", me: null });
      setState({ token: "", encKey: "", me: null });
      setLoginModal({ open: true, mode: "login" });
    };
    window.addEventListener("aitf-logout-invalid", onInvalid);
    return () => window.removeEventListener("aitf-logout-invalid", onInvalid);
  }, []);

  /* ===== 明文通道认证动作（与旧版 doAuth/doGuest 一致） ===== */

  const login = useCallback(
    async (username: string, password: string) => {
      const fd = new URLSearchParams();
      fd.append("username", username);
      fd.append("password", password);
      const r = await fetch("/api/auth/login", { method: "POST", body: fd });
      const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
    if (!r.ok) throw new Error(detailOf(d) || "失败：" + r.status);
    const me: Me & { enc_key?: string } = {
      id: -1, // 登录响应无 id，refreshMe 补齐
      username: d.username || username,
      email: null,
      role: d.role || "user",
      is_active: true,
      enc_key: d.enc_key,
    };
    apply({ token: d.access_token || "", encKey: d.enc_key || "", me });
    void refreshMe();
    setLoginModal({ open: false, mode: "login" });
  },
    [apply, refreshMe],
  );

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      const r = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, email, password }),
      });
      const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
    if (!r.ok) throw new Error(detailOf(d) || "失败：" + r.status);
    const me: Me & { enc_key?: string } = {
      id: -1, // 登录响应无 id，refreshMe 补齐
      username: d.username || username,
      email: email || null,
      role: d.role || "user",
      is_active: true,
      enc_key: d.enc_key,
    };
    apply({ token: d.access_token || "", encKey: d.enc_key || "", me });
    void refreshMe();
    setLoginModal({ open: false, mode: "login" });
  },
    [apply, refreshMe],
  );

  const guest = useCallback(async () => {
    const r = await fetch("/api/guest/token", { method: "POST" });
    const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
    if (!r.ok) throw new Error(detailOf(d) || "获取访客身份失败");
    apply({
      token: d.access_token || "",
      encKey: d.enc_key || "",
      me: {
        id: -1,
        username: d.username || "guest",
        email: null,
        role: "guest",
        is_active: true,
        remaining_hours: null, // 共享访客不过期
      },
    });
    setLoginModal({ open: false, mode: "login" });
  }, [apply]);

  const logout = useCallback(() => {
    try {
      sessionStorage.setItem("atf_manual_logout", "1");
    } catch {
      /* ignore */
    }
    clearPersisted();
    setAuthSnapshot({ token: "", encKey: "", me: null });
    setState({ token: "", encKey: "", me: null });
    setLoginModal({ open: true, mode: "login" });
  }, []);

  const showLogin = useCallback((mode: AuthMode = "login") => {
    setLoginModal({ open: true, mode });
  }, []);

  /* 登录态变化（login/register/guest 成功）→ 由各视图自行响应 me 变化 */
  const authJustChanged = useRef(false);
  useEffect(() => {
    if (!ready) return;
    if (authJustChanged.current) {
      authJustChanged.current = false;
      return;
    }
    // noop：React 版不再无脑 reload
  }, [state.token, ready]);

  const value = useMemo<AuthContextValue>(
    () => ({
      me: state.me,
      token: state.token,
      role: state.me?.role ?? null,
      ready,
      showLogin,
      login: async (...args) => {
        authJustChanged.current = true;
        await login(...args);
      },
      register: async (...args) => {
        authJustChanged.current = true;
        await register(...args);
      },
      guest: async (...args) => {
        authJustChanged.current = true;
        await guest(...args);
      },
      logout,
    }),
    [state, ready, showLogin, login, register, guest, logout],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {loginModal.open && (
        <LoginModal
          mode={loginModal.mode}
          onClose={() => setLoginModal({ open: false, mode: "login" })}
        />
      )}
    </AuthContext.Provider>
  );
}

/* 登录弹窗（登录 / 注册 / 游客体验 三入口；共享访客无「转正」入口） */
function LoginModal({ mode, onClose }: { mode: AuthMode; onClose: () => void }) {
  const ctx = useContext(AuthContext)!;
  const [tab, setTab] = useState<AuthMode>(mode === "register" ? "register" : "login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!username || !password) {
      setError("请填写用户名和密码");
      return;
    }
    if (tab === "register" && !email) {
      setError("请填写邮箱");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (tab === "login") await ctx.login(username, password);
      else await ctx.register(username, email, password);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="auth-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="auth-modal">
        <h2>{tab === "register" ? "注册账号" : "登录"}</h2>
        <div className="auth-tabs">
          <button className={tab === "login" ? "on" : ""} onClick={() => setTab("login")}>
            登录
          </button>
          <button className={tab === "register" ? "on" : ""} onClick={() => setTab("register")}>
            注册
          </button>
        </div>
        <input
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        {tab === "register" && (
          <input
            placeholder="邮箱"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        )}
        <input
          placeholder="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        {error && <div className="auth-error">{error}</div>}
        <button className="auth-btn" disabled={busy} onClick={submit}>
          {busy ? "请稍候…" : tab === "register" ? "注册" : "登录"}
        </button>
        <button
          className="guest-btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await ctx.guest();
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          👤 游客体验（免注册 · 共享演示账号）
        </button>
      </div>
    </div>
  );
}
