/**
 * V5.10 提示词编辑弹窗（FR-AK）：查看 / 修改 / 恢复默认。
 *
 * - 左侧：条目列表（对话提示词 8 项 / 用例生成模板 6 项，自定义中的带徽章）
 * - 右侧：默认 vs 当前对照 + textarea 编辑 + 保存 / 恢复默认
 * - 生成模板保存时后端强校验 {name} 等占位符（400 带原因）
 * - 入口：会话输入区角色 pill 旁 ✎ 图标（ChatPanel）
 */
import { useCallback, useEffect, useState } from "react";
import { X, RotateCcw, Save } from "lucide-react";
import { api, API, toast } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";

type Item = { key: string; group: string; name: string; description: string; has_override: boolean };
type Detail = { key: string; name: string; description: string; default: string; override: string | null; content: string };

export default function PromptEditorModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { token } = useAuth();
  const [items, setItems] = useState<Item[]>([]);
  const [selKey, setSelKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadList = useCallback(async (): Promise<Item[]> => {
    const r = await api(API + "/prompts").catch(() => null);
    if (!r || !r.ok) return [];
    const j = (await r.json()) as { items: Item[] };
    setItems(j.items);
    return j.items;
  }, []);

  const loadDetail = useCallback(async (key: string) => {
    const r = await api(API + "/prompts/" + key).catch(() => null);
    if (!r || !r.ok) return;
    const d = (await r.json()) as Detail;
    setDetail(d);
    setDraft(d.content);
    setDirty(false);
    setError("");
  }, []);

  useEffect(() => {
    if (!open || !token) return;
    void (async () => {
      const list = await loadList();
      if (list.length) {
        setSelKey((prev) => prev && list.some((i) => i.key === prev) ? prev : list[0].key);
      }
    })();
  }, [open, token, loadList]);

  useEffect(() => {
    if (selKey) void loadDetail(selKey);
  }, [selKey, loadDetail]);

  if (!open) return null;

  const save = async () => {
    if (!selKey || !detail) return;
    if (draft === detail.content) {
      toast("内容未变化");
      return;
    }
    setSaving(true);
    setError("");
    const r = await api(API + "/prompts/" + selKey, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: draft }),
    }).catch(() => null);
    setSaving(false);
    if (!r || !r.ok) {
      let msg = "保存失败";
      try {
        if (r) {
          const j = (await r.json()) as { detail?: string };
          if (j?.detail) msg = j.detail;
        }
      } catch { /* ignore */ }
      setError(msg);
      return;
    }
    toast("已保存，下一条消息 / 下次生成即生效");
    setDirty(false);
    await loadDetail(selKey);
    await loadList();
  };

  const reset = async () => {
    if (!selKey || !detail) return;
    if (!detail.override) {
      toast("当前已是默认提示词");
      return;
    }
    if (!window.confirm(`确定恢复「${detail.name}」为默认提示词？自定义内容将被删除。`)) return;
    const r = await api(API + "/prompts/" + selKey, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) {
      toast("恢复默认失败");
      return;
    }
    toast("已恢复默认");
    await loadDetail(selKey);
    await loadList();
  };

  const groups: { label: string; match: (g: string) => boolean }[] = [
    { label: "对话提示词（回复口吻与行为）", match: (g) => g === "chat" },
    { label: "用例生成模板（生成什么样的用例）", match: (g) => g === "gen" },
  ];

  return (
    <div className="pe-overlay" onClick={onClose}>
      <div className="pe-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pe-head">
          <div>
            <h3>提示词定制</h3>
            <p className="pe-sub">自定义 AI 角色与生成模板的系统提示词；留空恢复默认即可回退</p>
          </div>
          <button type="button" className="pe-close" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="pe-body">
          <aside className="pe-list">
            {groups.map((g) => (
              <div key={g.label}>
                <div className="pe-group">{g.label}</div>
                {items.filter((i) => g.match(i.group)).map((i) => (
                  <button
                    key={i.key}
                    type="button"
                    className={`pe-item ${i.key === selKey ? "on" : ""}`}
                    onClick={() => {
                      if (dirty && !window.confirm("有未保存的修改，切换将丢弃，确定？")) return;
                      setSelKey(i.key);
                    }}
                  >
                    <span className="pe-item-name">{i.name}</span>
                    {i.has_override && <span className="pe-badge">自定义</span>}
                  </button>
                ))}
              </div>
            ))}
          </aside>
          <section className="pe-editor">
            {detail ? (
              <>
                <div className="pe-meta">
                  <span className={`pe-state ${detail.override ? "custom" : "default"}`}>
                    {detail.override ? "自定义中" : "默认提示词"}
                  </span>
                  <span className="pe-desc">{detail.description}</span>
                </div>
                <textarea
                  className="pe-text"
                  value={draft}
                  onChange={(e) => { setDraft(e.target.value); setDirty(true); }}
                  spellCheck={false}
                />
                {error ? <div className="pe-error">{error}</div> : null}
                <div className="pe-actions">
                  <button type="button" className="btn out" onClick={reset} title="删除自定义，回到内置默认">
                    <RotateCcw size={14} /> 恢复默认
                  </button>
                  <span className="pe-flex" />
                  {dirty ? <span className="pe-dirty">未保存</span> : null}
                  <button type="button" className="btn primary" disabled={saving || !dirty} onClick={() => void save()}>
                    <Save size={14} /> {saving ? "保存中…" : "保存"}
                  </button>
                </div>
              </>
            ) : (
              <div className="pe-loading">加载中…</div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
