/**
 * 管理页（M4，仅 admin）：统计卡 + 用户管理 + 访客清理。
 * 平台配置数据本地拉取（GET /llm/platform-config，admin-only）。
 * V4：统计卡加图标 + 左对齐；访客治理按钮规范化（secondary / outline-danger）。
 * 方案 A（2026-09-23）：质量看板迁出为独立「质量报告」页（全角色可见），本页回退纯概览。
 * V5.2（2026-09-25）：平台模型区整体迁出到「设置」页（admin 的「平台默认」Tab），本页不再含模型配置。
 */
import { useCallback, useEffect, useState } from "react";
import { Users, UserX, ClipboardList, Trash2 } from "lucide-react";
import { apiJson, API, toast } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import UserTable from "../components/admin/UserTable";
import type { AdminStats, AdminUserRow } from "../types";

export default function AdminPage() {
  const { me, role, ready } = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const [cleaning, setCleaning] = useState("");

  const loadAll = useCallback(async () => {
    const [st, us] = await Promise.all([
      apiJson<AdminStats>(API + "/admin/stats"),
      apiJson<AdminUserRow[]>(API + "/users"),
    ]);
    if (st) setStats(st);
    if (us) setUsers(us);
  }, []);

  useEffect(() => {
    if (ready && role === "admin") {
      void loadAll();
    }
  }, [ready, role, loadAll]);

  const resetSharedGuest = async () => {
    const msg =
      "⚠️ 将清空共享访客账号下的全部任务与文件（账号本身保留，所有人仍可继续使用），确定？";
    if (!window.confirm(msg)) return;
    setCleaning("reset");
    try {
      const r = await apiJson<{ deleted_tasks: number; deleted_files: number }>(
        API + "/admin/guest/shared/reset",
        { method: "POST" },
      );
      if (r) {
        toast(`已清空共享访客数据（${r.deleted_tasks} 个任务 / ${r.deleted_files} 个文件）`);
        void loadAll();
      }
    } finally {
      setCleaning("");
    }
  };

  if (ready && role !== "admin") {
    return <div className="page-empty">仅管理员可访问</div>;
  }
  if (!me) return <div className="page-empty">请先登录</div>;

  return (
    <div className="page-wrap admin-page" data-testid="admin-page">
      <section className="stats-row" data-testid="stats-cards">
        <div className="stat-card">
          <div className="stat-icon blue"><Users size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{stats?.registered_users ?? "—"}</div>
            <div className="s-label">注册用户</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon orange"><UserX size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{stats?.active_guests ?? "—"}</div>
            <div className="s-label">共享访客</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green"><ClipboardList size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{stats?.total_tasks ?? "—"}</div>
            <div className="s-label">总任务数</div>
          </div>
        </div>
      </section>

      <section className="set-card guest-clean-bar">
        <h3>访客数据</h3>
        <div className="sub">访客为全站唯一共享账号（免注册共用），不会过期，仅管理员可清空其数据。</div>
        <div className="llm-btnrow">
          <button
            className="btn-outline-danger btn-md"
            disabled={!!cleaning}
            onClick={() => void resetSharedGuest()}
            data-testid="clean-expired"
          >
            <Trash2 size={14} />
            {cleaning === "reset" ? "清空中…" : "清空共享访客数据"}
          </button>
        </div>
      </section>

      <UserTable users={users} myId={me.id > 0 ? me.id : -1} onChanged={() => void loadAll()} />
    </div>
  );
}
