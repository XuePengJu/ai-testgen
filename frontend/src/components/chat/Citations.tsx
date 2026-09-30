/**
 * V4.1 引用溯源 chips：AI 回答下方展示 RAG 命中的知识库条目，点击跳转定位。
 * 数据来自 SSE citations 事件（后端 _build_rag_context 回传的命中元数据）。
 * 纯展示组件：跳转行为由父级 onCiteClick 决定（知识库页切 tab + 高亮定位）。
 */
import { BookOpen } from "lucide-react";
import type { CitationItem } from "../../types";

export default function Citations({
  items,
  onCiteClick,
}: {
  items: CitationItem[];
  onCiteClick?: (knowledgeId: string) => void;
}) {
  if (!items?.length) return null;
  return (
    <div className="msg-citations">
      <span className="mc-label">📚 引用 {items.length} 条</span>
      <div className="mc-chips">
        {items.map((c, i) => (
          <button
            key={c.chunk_id || i}
            type="button"
            className="cite-chip"
            title={c.snippet || c.doc_title}
            onClick={() => c.knowledge_id && onCiteClick?.(c.knowledge_id)}
          >
            <BookOpen size={12} />
            <span className="cc-idx">[{i + 1}]</span>
            <span className="cc-title">{c.doc_title || "未命名条目"}</span>
            {typeof c.score === "number" && (
              <span className="cc-score">{(c.score * 100).toFixed(0)}%</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
