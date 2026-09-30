/**
 * LLM 厂商预设与槽位文案（设置页 / 管理页共用）。
 *
 * 历史来源：原从 LLMConfigCard.tsx（单条配置，V5.4 已下线）抽出共用；
 * 现由模型池卡片（LLMPoolCard.tsx）使用。
 *
 * 文本 / 视觉厂商来自后端 GET /api/llm/providers（settingsStore.providers）；
 * Embedding 厂商前端内置（后端不提供 /embeddings 预设）。
 */

/** Embedding 专用厂商预设（V4.0）：与文本厂商分开维护，走 /embeddings 端点 */
export const EMBED_PROVIDERS: Record<string, {
  label: string;
  base_url: string;
  note: string;
  models: { id: string; label: string }[];
}> = {
  bailian: {
    label: "阿里百炼 · 通义文本向量",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    note: "text-embedding-v3 中文效果好（1024 维）；需在百炼控制台开通模型服务",
    models: [
      { id: "text-embedding-v3", label: "text-embedding-v3 · 1024 维（推荐）" },
      { id: "text-embedding-v4", label: "text-embedding-v4 · 新模型" },
    ],
  },
  siliconflow: {
    label: "硅基流动 · SiliconCloud",
    base_url: "https://api.siliconflow.cn/v1",
    note: "BGE-M3 有免费额度，中文好；注册后到控制台创建 API Key",
    models: [
      { id: "BAAI/bge-m3", label: "BAAI/bge-m3 · 1024 维（免费额度）" },
      { id: "BAAI/bge-large-zh-v1.5", label: "BAAI/bge-large-zh-v1.5 · 1024 维" },
    ],
  },
  custom: {
    label: "自定义（OpenAI 兼容 /embeddings）",
    base_url: "",
    note: "填写任意支持 /embeddings 的 OpenAI 兼容端点",
    models: [],
  },
  ollama: {
    label: "Ollama · 本地部署",
    base_url: "http://localhost:11434/v1",
    note: "本地跑 embedding 模型，零 API 费用；先 ollama pull <模型>，远端部署时把地址换成 http://<服务器IP>:11434/v1",
    models: [
      { id: "nomic-embed-text", label: "nomic-embed-text · 768 维（轻量推荐）" },
      { id: "bge-m3", label: "bge-m3 · 1024 维（中文好）" },
      { id: "mxbai-embed-large", label: "mxbai-embed-large · 1024 维" },
    ],
  },
};

/** 槽位标题与说明 */
export const SLOT_TITLE: Record<string, { title: string; sub: string }> = {
  text: { title: "默认文本模型", sub: "需求拆解 / 用例生成 / 对话" },
  vision: { title: "图像识别模型", sub: "原型图 / 截图转用例（可选）" },
  embedding: { title: "Embedding 向量模型", sub: "知识库入库与检索（向量化）" },
};

/**
 * 中文圈号序号（① ② ③…）：展示「池内优先第几条」。
 * 序号即优先级，与池卡列表行号一一对应；超过 20 退化为 #n。
 */
const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
export const circledIndex = (n: number): string => CIRCLED[n - 1] ?? `#${n}`;
