"""知识库服务包（V4.0 RAG）。

- chunker.py     结构化分块（标题感知 + 表格整块 + 大小控制）
- vectorstore.py Embedding + Chroma 向量存储（含 mock 兜底）
- ingest.py      入库管道编排（分块 → 写库 → 向量化，状态机推进）
"""
