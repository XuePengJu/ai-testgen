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
            {/* V6.0/V7.2：命中个人记忆库或记忆条目（memory_item_id）时挂「🧠 记忆」小标签 */}
            {(c.personal || c.memory_item_id) && (
              <span
                style={{
                  fontSize: 11, color: "#7c3aed", background: "#f5f3ff",
                  borderRadius: 8, padding: "1px 6px", flex: "none", whiteSpace: "nowrap",
                }}
              >
                🧠 记忆
              </span>
            )}
            {/* V7.4.1：score = 真实向量余弦相似度。仅关键词命中时后端给 null，
                改标「关键词」——混合检索的 RRF 名次分满分只有 1.6%，当相似度
                百分比展示会塌成 1~2% 造成「没匹配上」的误判，故不再外传 */}
            {typeof c.score === "number" ? (
              <span className="cc-score">{(c.score * 100).toFixed(0)}%</span>
            ) : c.hit_channel === "keyword" ? (
              <span
                className="cc-score cc-kw"
                title="仅靠关键词(BM25)命中，向量通道未召回该分块"
              >
                关键词
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}
