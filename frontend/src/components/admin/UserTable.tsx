/**
 * 用户管理表（M4 admin）：启停 / 删除 / 访客清理。
 * - guest 禁用 = 立即清理其数据（后端语义，前端 confirm 提示）
 * - admin 不能操作自己（后端 400，前端按钮禁用）
 * V4：角色 badge 去红 + 图标；状态圆点 + 文字；操作列图标按钮。
 */
import { useState } from "react";
import { Shield, User, UserX, Ban, Check, Broom, Trash2 } from "lucide-react";
import { api, API, toast } from "../../api/client";
import type { AdminUserRow } from "../../types";
import { parseServerTime } from "../../utils/time";

interface Props {
  users: AdminUserRow[];
  myId: number;
  onChanged: () => void;
}

const ROLE_LABEL: Record<string, string> = { guest: "访客", user: "用户", admin: "管理员" };

function fmtDate(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function RoleBadge({ role }: { role: string }) {
  const cls = role === "admin" ? "admin" : role === "guest" ? "guest" : "user";
  const icon = role === "admin" ? <Shield size={12} /> : role === "guest" ? <UserX size={12} /> : <User size={12} />;
  return (
    <span className={"role-badge " + cls}>
      {icon}
      {ROLE_LABEL[role] || role}
    </span>
  );
}

export default function UserTable({ users, myId, onChanged }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const patchUser = async (u: AdminUserRow, is_active: boolean) => {
    const msg = u.role === "guest"
      ? `清空共享访客「${u.username}」的全部数据（${u.tasks} 个任务），确定？`
      : `确定${is_active ? "启用" : "禁用"}用户「${u.username}」？`;
    if (!window.confirm(msg)) return;
    setBusyId(u.id);
    try {
      const r = await api(API + "/users/" + u.id, {
        method: "PATCH",
        body: JSON.stringify({ is_active }),
      });
      if (r.ok) {
        toast(u.role === "guest" && !is_active ? "共享访客数据已清空" : "已更新");
        onChanged();
      } else {
        const d = (await r.json().catch(() => null)) as { detail?: string } | null;
        toast(d?.detail || `HTTP ${r.status}`);
      }
    } catch {
      toast("网络错误");
    } finally {
      setBusyId(null);
    }
  };

  const deleteUser = async (u: AdminUserRow) => {
    if (
      !window.confirm(
        `⚠️ 删除用户「${u.username}」将级联删除其 ${u.tasks} 个任务与全部文件，不可恢复，确定？`,
      )
    )
      return;
    setBusyId(u.id);
    try {
      const r = await api(API + "/users/" + u.id, { method: "DELETE" });
      if (r.ok) {
        toast("用户已删除");
        onChanged();
      } else {
        const d = (await r.json().catch(() => null)) as { detail?: string } | null;
        toast(d?.detail || `HTTP ${r.status}`);
      }
    } catch {
      toast("网络错误");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="set-card" data-testid="user-table">
      <h3>用户管理（{users.length}）</h3>
      <div className="tbl-wrap">
        <table className="admin-tbl">
          <thead>
            <tr>
              <th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>任务</th><th>注册/到期</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} data-user-id={u.id}>
                <td>{u.id}</td>
                <td className="t-username">
                  {u.username}
                  {u.id === myId && <span className="me-tag">（我）</span>}
                </td>
                <td><RoleBadge role={u.role} /></td>
                <td>
                  {u.is_active ? (
                    <span><span className="status-dot ok" />活跃</span>
                  ) : (
                    <span><span className="status-dot warn" />已禁用</span>
                  )}
                </td>
                <td>{u.tasks}</td>
                <td className="t-date">
                  {u.role === "guest" ? `到期 ${fmtDate(u.expires_at)}` : fmtDate(u.created_at)}
                </td>
                <td className="t-actions">
                  {u.id !== myId && (
                    <>
                      <button
                        className="icon-btn"
                        disabled={busyId === u.id}
                        title={u.role === "guest" ? "清空共享访客数据" : u.is_active ? "禁用" : "启用"}
                        aria-label={u.role === "guest" ? "清空共享访客数据" : u.is_active ? "禁用" : "启用"}
                        onClick={() => void patchUser(u, !u.is_active)}
                      >
                        {u.role === "guest" ? <Broom size={15} /> : u.is_active ? <Ban size={15} /> : <Check size={15} />}
                      </button>
                      <button
                        className="icon-btn danger"
                        disabled={busyId === u.id}
                        title="删除"
                        aria-label="删除"
                        onClick={() => void deleteUser(u)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
