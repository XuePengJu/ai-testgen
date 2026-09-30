/**
 * V4.4 知识库下拉选择器（对齐 kb-selector-dropdown 设计稿）。
 * 取代原左侧知识库列表：顶栏一控件完成「看当前库 + 切库 + 搜索 + 新建」，
 * 左侧区让位给会话列表（AI 问答）/ 内容区（文档·Wiki·图谱·检索）。
 * 交互：点击展开、点击外部或 Esc 关闭；当前库 ✓ 高亮；访客隐藏「新建」。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Search, Plus, Check } from "lucide-react";

export interface KbLite {
  id: string;
  name: string;
  description?: string;
  visibility: string;
  doc_count: number;
  chunk_count: number;
}

export default function KbSelector<T extends KbLite>({
  bases,
  sel,
  onSelect,
  onNew,
  readOnly = false,
}: {
  bases: T[];
  sel: T | null;
  onSelect: (kb: T) => void;
  onNew: () => void;
  readOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭 + Esc 关闭（keydown 用 capture 保证优先于页面其他快捷键）
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc, true);
    };
  }, [open]);

  const list = useMemo(() => {
    const k = kw.trim().toLowerCase();
    if (!k) return bases;
    return bases.filter((b) => (b.name || "").toLowerCase().includes(k));
  }, [bases, kw]);

  const pick = (b: T) => {
    onSelect(b);
    setOpen(false);
    setKw("");
  };

  return (
    <div className={"kb-select" + (open ? " open" : "")} ref={boxRef}>
      <button
        type="button"
        className="kb-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={sel?.description || sel?.name || "选择知识库"}
      >
        <span className="kb-dot" />
        <span className="kb-trigger-name">{sel?.name ?? "选择知识库"}</span>
        {sel && (
          <span className={"kb-badge " + (sel.visibility === "global" ? "shared" : "private")}>
            {sel.visibility === "global" ? "共享" : "私有"}
          </span>
        )}
        <ChevronDown size={14} className="kb-chev" />
      </button>

      {open && (
        <div className="kb-dropdown" role="listbox" aria-label="知识库列表">
          <div className="dd-search">
            <Search size={13} />
            <input
              autoFocus
              value={kw}
              placeholder="搜索知识库…"
              onChange={(e) => setKw(e.target.value)}
              aria-label="搜索知识库"
            />
          </div>
          <div className="dd-list">
            {list.length === 0 && <div className="dd-empty muted">没有匹配的知识库</div>}
            {list.map((b) => (
              <button
                key={b.id}
                type="button"
                role="option"
                aria-selected={sel?.id === b.id}
                className={"dd-item" + (sel?.id === b.id ? " on" : "")}
                onClick={() => pick(b)}
              >
                <span className="dd-ico">{b.name.slice(0, 1).toUpperCase()}</span>
                <span className="dd-meta">
                  <span className="dd-name">{b.name}</span>
                  <span className="dd-sub">
                    {b.doc_count} 文档 · {b.chunk_count} 块 · {b.visibility === "global" ? "共享" : "私有"}
                  </span>
                </span>
                {sel?.id === b.id && <Check size={14} className="dd-check" />}
              </button>
            ))}
          </div>
          {!readOnly && (
            <div className="dd-foot">
              <button type="button" className="dd-new" onClick={() => { setOpen(false); setKw(""); onNew(); }}>
                <Plus size={14} /> 新建知识库
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
