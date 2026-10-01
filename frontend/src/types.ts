/** 与后端 UserOut 对齐的核心类型（V2.8 M1） */

export type Role = "guest" | "user" | "admin";

export interface Me {
  id: number;
  username: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  expires_at?: string | null;
  remaining_hours?: number | null;
}

/** 登录/注册/访客明文通道的响应体 */
export interface AuthResponse {
  access_token: string;
  token_type: string;
  username: string;
  role: Role;
  enc_key: string;
  remaining_hours?: number;
}

/* ===== V2.8 M2：对话驱动 + 任务列表（与后端 schemas 对齐） ===== */

/** 与 app/schemas/conversation.py:MessageOut 对齐 */
export interface ConvMessage {
  id: number;
  role: string;
  content: string;
  thinking: string;
  /** V4.5.2：持久化的 RAG 引用溯源（assistant 消息），与 CitationItem 对齐 */
  citations?: CitationItem[] | null;
  task_id?: string | null;
  created_at?: string | null;
  task?: TaskSummary | null;
}

/** 会话详情接口里消息内嵌的任务摘要 */
export interface TaskSummary {
  id: string;
  name: string;
  status: string;
  cases_count?: number;
  duration_ms?: number | null;
  created_at?: string | null;
  /** 节点步骤（含 input_summary / output_summary），与后端 _task_brief 对齐 */
  steps?: StepLog[];
  report?: { summary?: string };
}

export interface Conversation {
  id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
  message_count: number;
  task_count: number;
  messages?: ConvMessage[];
  mode?: string;            // workflow=首页工作流；kb_qa=已下线的知识库问答（仅历史数据）
  kb_id?: string | null;    // V4.1：知识库问答绑定的库
}

/** V4.1 引用溯源（SSE citations 事件 items 元素，与后端 _build_rag_context 对齐） */
export interface CitationItem {
  chunk_id: string;
  knowledge_id: string;
  doc_title: string;
  context_header: string;
  snippet: string;
  score: number | null;
  /** V6.0 命中来源为个人记忆库时回传，前端渲染「🧠 记忆」小标签 */
  personal?: boolean;
}

/** V6.0 知识库条目精简类型（/api/knowledge/bases items，个人记忆库标记 is_personal） */
export interface KbBaseItem {
  id: string;
  name: string;
  doc_count?: number;
  /** true = 用户个人记忆库（对话记忆沉淀，前端默认勾选且不可取消） */
  is_personal?: boolean;
}

/** V6.0 会话记忆整理状态（GET /api/conversations/{id}/memory/state） */
export interface MemoryState {
  status: "idle" | "running" | "done" | "failed";
  mem_at?: string | null;
  doc_id?: string | null;
  error?: string | null;
  msg_count?: number;
  attachments?: { total: number; done: number };
}

/** 与 app/schemas/task.py:StepLogOut 对齐 */
export interface StepLog {
  name: string;
  title: string;
  status: string;
  /** running 期间实时子进度（如"正在为第 2/5 个测试点生成用例…"） */
  progress?: string | null;
  started_at?: string | null;
  duration_ms?: number | null;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

/** M3：任务详情里的单条用例（详情接口 cases[] 注入） */
export interface CaseItem {
  case_id: string;
  title: string;
  module?: string;
  case_type?: string;
  priority?: string;
  pre_condition?: string;
  steps?: string[];
  step_expectations?: string[];
  expected?: string;
  test_data?: string;
}

/** 与 app/schemas/task.py:TaskOut 对齐（列表接口 cases 为 []） */
export interface Task {
  id: string;
  name: string;
  kind: string;
  source_type: string;
  status: string;
  /** V5.5 用例库资产化：评审状态 draft/reviewed（与生成过程状态 status 分离） */
  review_status?: string;
  cases_count: number;
  duration_ms: number;
  formats: string;
  category_id?: number | null;
  parent_task_id?: string | null;
  /** 所属会话 id：详情页「继续优化」据此跳回会话挂载迭代引用（后端对历史任务做反查兜底） */
  conversation_id?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
  steps: StepLog[];
  cases?: CaseItem[];
}

/** 聊天输入草稿（流式回复完成后「生成测试用例」消费） */
export interface ChatDraft {
  text: string;
  file?: File | null;
  kind: string;
  formats: string[];
  /** 「总是深度思考」开关：true=每轮都先推理；null/省略=按需（由后端判定是否值得推理） */
  thinking?: boolean | null;
  /** 多角色协作（V3.1）：参与生成的视角，如 ["pm","qa","dev"]，默认 ["qa"] */
  roles?: string[];
}

/* ===== V2.8 M4：设置页 / admin / 分类（与后端对齐） ===== */

/** 与 app/core/providers.py:PROVIDERS 对齐（键为厂商 id） */
export interface ProviderModel {
  id: string;
  label: string;
  vision: boolean;
}

export interface ProviderPreset {
  label: string;
  base_url: string;
  note: string;
  models: ProviderModel[];
}

export type ProviderMap = Record<string, ProviderPreset>;

/** 与 app/schemas/llm_pool.py:PoolItemOut 对齐（模型池一条） */
export interface LLMPoolItem {
  id: number;
  slot: string;
  provider: string;
  provider_label: string;
  base_url: string;
  model: string;
  /** 如 ****abcd；空 = 靠服务器环境变量兜底 Key */
  api_key_masked: string;
  priority: number;
  enabled: boolean;
  /** 付费模型（老板承担费用，界面给橙色徽标提示） */
  paid: boolean;
  note: string;
  /** Key 指纹（判重/同 Key 提示用，不泄露 Key） */
  key_fingerprint: string;
  cooldown_until: string | null;
  cooling: boolean;
  last_error: string | null;
  success_count: number;
  fail_count: number;
  /** 是否为当前实际生效的池（用户池非空时平台池为 false） */
  effective: boolean;
}

/** 池内一条的新增 / 修改入参（api_key 缺省 = 保留原 Key） */
export interface LLMPoolItemIn {
  provider: string;
  base_url: string;
  model: string;
  api_key?: string | null;
  paid?: boolean;
  note?: string;
  enabled?: boolean;
  priority?: number | null;
}

/** GET /api/llm/pool/{slot}/health */
export interface LLMPoolHealth {
  slot: string;
  total: number;
  enabled: number;
  available: number;
  items: Array<{
    id: number;
    model: string;
    enabled: boolean;
    cooling: boolean;
    cooldown_until: string | null;
    last_error: string | null;
    success_count: number;
    fail_count: number;
  }>;
}

/** /api/llm/effective 的 pools[slot]：该槽位池的现状（V5.1 P1） */
export interface LLMPoolStats {
  /** 有可用候选才算池真的接管 */
  active: boolean;
  /** 池归属：personal = 我的池 / platform = 平台池 / null = 未启用池 */
  owner: "personal" | "platform" | null;
  /** 池内全部条数（含已停用），与池卡卡头同口径 */
  total: number;
  enabled: number;
  available: number;
  cooling: number;
  /** 优先序号（1-based）；0 = 无可用候选 */
  hit: number;
}

/** /api/llm/effective 响应（public_view 形态，不含 Key）；source 增加 pool = 模型池生效 */
export interface LLMEffective {
  source: string;
  text: { provider: string; provider_label: string; base_url: string; model: string } | null;
  vision: { provider: string; provider_label: string; base_url: string; model: string } | null;
  embedding?: { provider: string; provider_label: string; base_url: string; model: string } | null;
  embedding_source?: string;
  /** V5.1 P1：三槽池现状（effective 走 get_current_user，访客也能拿到条数） */
  pools?: Partial<Record<"text" | "vision" | "embedding", LLMPoolStats>>;
}

/** /api/llm/test-default/{slot} 响应 */
export interface LLMTestResult {
  ok: boolean;
  err_type?: string;
  error_label?: string;
  error?: string;
  model?: string | null;
  provider_label?: string;
  latency_ms?: number;
}

/** 与 app/api/users.py:UserRow 对齐 */
export interface AdminUserRow {
  id: number;
  username: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  expires_at?: string | null;
  tasks: number;
  created_at?: string | null;
}

/** /api/admin/stats 响应 */
export interface AdminStats {
  registered_users: number;
  active_guests: number;
  cleaned_24h: number;
  total_tasks: number;
}

/** /api/categories 响应（扁平列表，parent_id 组树） */
export interface CategoryNode {
  id: number;
  name: string;
  parent_id: number | null;
  task_count: number;
  /** 页面 2 级自动派生标记（历史数据；后端按任务自动生成的一级分类） */
  is_auto?: boolean;
}
