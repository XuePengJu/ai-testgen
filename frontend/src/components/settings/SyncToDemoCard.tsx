/**
 * 正式站 → 演示站模型配置同步卡片（V9.6）。
 *
 * 只在正式站管理员「平台默认」Tab 显示（演示站自身隐藏，避免自己同步自己）。
 * 点击把当前平台池（text/vision/embedding/rerank，含 Key）推到演示站；
 * 演示站另有每 30 分钟自动拉取兜底（见 app/jobs/model_sync.py）。
 */
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiJson, toast } from "../../api/client";

interface SyncResult {
  ok: boolean;
  counts: Record<string, number>;
  cleared_personal: number;
  embedding_changed: boolean;
  embedding_before?: { model?: string } | null;
  embedding_after?: { model?: string } | null;
}

const SLOT_LABEL: Record<string, string> = {
  text: "文本",
  vision: "图像",
  embedding: "向量",
  rerank: "重排",
};

export default function SyncToDemoCard() {
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const res = await apiJson<SyncResult>("/api/llm/platform-pool/sync-demo", {
        method: "POST",
      });
      if (!res) return;
      const parts = Object.entries(SLOT_LABEL)
        .map(([slot, label]) => `${label} ${res.counts?.[slot] ?? 0} 条`)
        .join("，");
      const warn = res.embedding_changed
        ? "；embedding 模型已变更，演示知识库快照需用新模型重建"
        : "";
      toast(`已同步到演示站：${parts}${warn}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="set-card" data-testid="sync-to-demo-card">
      <h3>同步到演示站</h3>
      <div className="sub">
        把当前平台模型池（含 API Key）推送到演示站；演示站每 30 分钟也会自动拉取一次。
        只同步平台池，不同步正式站 admin 的个人池。
      </div>
      <button
        type="button"
        className="btn-primary btn-md"
        disabled={busy}
        onClick={() => void sync()}
        data-testid="sync-to-demo-btn"
      >
        <RefreshCw size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
        {busy ? "同步中…" : "同步到演示站"}
      </button>
      <div className="hint-line">
        同步 embedding 后，演示知识库快照若仍用旧向量模型，检索会退化；换模型后需重导快照。
      </div>
    </section>
  );
}