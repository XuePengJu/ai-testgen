/**
 * 个人中心卡：账号信息 + 修改密码（M4）。
 * - 旧密码错误 → 后端 401 detail「旧密码错误」，keep401 原样返回不强制登出
 * - V4：角色 badge 改用主色/灰/警告（管理员不再用红色）+ 图标。
 */
import { useState } from "react";
import { Shield, User, UserX } from "lucide-react";
import { api, API, toast } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import type { Role } from "../../types";

const ROLE_LABEL: Record<Role, string> = { guest: "访客", user: "用户", admin: "管理员" };

function RoleIcon({ role }: { role: Role }) {
  if (role === "admin") return <Shield size={12} />;
  if (role === "guest") return <UserX size={12} />;
  return <User size={12} />;
}

export default function ProfileCard() {
  const { me, role } = useAuth();
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (!me) return null;

  const submit = async () => {
    setErr("");
    if (!oldPwd || !newPwd) {
      setErr("请填写旧密码和新密码");
      return;
    }
    if (newPwd !== confirm) {
      setErr("两次输入的新密码不一致");
      return;
    }
    if (newPwd.length < 8) {
      setErr("新密码至少 8 位");
      return;
    }
    setBusy(true);
    try {
      // keep401：旧密码错误时不触发全局登出
      const r = await api(API + "/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
        keep401: true,
      });
      if (r.ok) {
        toast("密码已修改");
        setOldPwd("");
        setNewPwd("");
        setConfirm("");
      } else {
        let detail = `HTTP ${r.status}`;
        try {
          const d = (await r.json()) as { detail?: string };
          if (d?.detail) detail = d.detail;
        } catch { /* ignore */ }
        setErr(detail);
      }
    } catch {
      setErr("网络错误，请稍后再试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="set-card" data-testid="profile-card">
      <h3>个人中心</h3>
      <div className="profile-grid">
        <div className="p-row"><span className="p-label">用户名</span><span>{me.username}</span></div>
        <div className="p-row">
          <span className="p-label">角色</span>
          <span className={"role-badge " + (role || "user")}>
            <RoleIcon role={me.role} />
            {ROLE_LABEL[me.role]}
          </span>
        </div>
        {me.email && <div className="p-row"><span className="p-label">邮箱</span><span>{me.email}</span></div>}
        {me.role === "guest" && (
          <div className="p-row">
            <span className="p-label">账号类型</span>
            <span>全站共享演示账号（不过期）</span>
          </div>
        )}
      </div>

      {me.role !== "guest" ? (
        <div className="pwd-form">
          <h4>修改密码</h4>
          <input
            type="password"
            placeholder="旧密码"
            value={oldPwd}
            onChange={(e) => setOldPwd(e.target.value)}
            data-testid="old-pwd"
          />
          <input
            type="password"
            placeholder="新密码（至少 8 位）"
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
            data-testid="new-pwd"
          />
          <input
            type="password"
            placeholder="确认新密码"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {err && <div className="form-err" data-testid="pwd-err">{err}</div>}
          <button className="btn-primary btn-md" disabled={busy} onClick={submit} data-testid="pwd-submit">
            {busy ? "提交中…" : "修改密码"}
          </button>
        </div>
      ) : (
        <div className="hint-line">当前为共享访客账号（免注册、不过期）。注册账号后可自定义模型配置并长期保留数据。</div>
      )}
    </section>
  );
}
