/**
 * M3：导出 Tab。
 * - 下载：GET /tasks/{id}/download?fmt=（FileResponse 二进制，中间件只加密 JSON → 透传）
 *
 * V2.10 统一入口改造：本 Tab 职责收敛为纯「拿结果」，不再保留任何输入表单。
 * 迭代补充统一走会话输入框 —— 由详情抽屉底部操作栏「💬 继续优化」
 * 跳回该任务所属会话并挂载迭代引用 chip，用户在输入框里直接补充需求。
 */
import { downloadTaskFile } from "../../api/client";
import type { Task } from "../../types";

const FMT_META: Record<string, { icon: string; label: string }> = {
  xlsx: { icon: "📊", label: "Excel 用例表" },
  json: { icon: "🧾", label: "JSON 结构化" },
  xmind: { icon: "🗺️", label: "XMind 思维导图" },
};

export default function ExportTab({ task }: { task: Task }) {
  const formats = (task.formats || "xlsx,json,xmind")
    .split(",")
    .map((f) => f.trim())
    .filter(Boolean);
  const downloadable = task.status === "completed";

  return (
    <div className="export-tab">
      <div className="export-sec-title">导出文件</div>
      {!downloadable ? (
        <div className="drawer-empty">任务完成后可下载导出文件</div>
      ) : (
        <div className="export-list">
          {formats.map((f) => {
            const meta = FMT_META[f] || { icon: "📄", label: f };
            return (
              <button
                key={f}
                type="button"
                className="export-item"
                onClick={() => void downloadTaskFile(task.id, f, task.name)}
              >
                <span className="ex-icon">{meta.icon}</span>
                <span className="ex-main">
                  <span className="ex-name">{task.name}.{f}</span>
                  <span className="ex-label">{meta.label}</span>
                </span>
                <span className="ex-dl">下载</span>
              </button>
            );
          })}
        </div>
      )}

      {/* 迭代路径引导：输入已统一到会话，避免用户在本页找不到补充入口 */}
      {downloadable && (
        <div className="export-tip">
          <span className="et-icon" aria-hidden="true">💡</span>
          <span>
            需要补充用例？点下方「💬 继续优化」回到会话，直接在输入框里说明补充内容即可 ——
            会基于本任务已有用例迭代合并，不会从零生成。
          </span>
        </div>
      )}
    </div>
  );
}
