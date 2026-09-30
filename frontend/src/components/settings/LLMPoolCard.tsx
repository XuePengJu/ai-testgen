/**
 * 模型池卡片（V5.1；V5.4 起单条配置下线，本卡是该槽位唯一的模型配置入口）。
 *
 * 调度行为（见 app/services/llm_pool.py）：
 *   池非空 → 实际调度走池（池内撞限流 / 额度尽自动换下一条）
 *   池为空 → 后端解析链回落 env 兜底 / mock
 *
 * V5.1 把「梯队」讲明白（此前只有灯色，看不出谁在工作、谁在待命）：
 *   卡头  池 N 条 · 可用 M · 冷却 K +「优先 ①」= 下一次调用会命中的那条
 *   行内  状态词（使用中 / 待命 / 冷却中 / 已停用）—— 颜色不单独承载语义
 *   脚注  说明「从上到下依次尝试」，把「顺序即优先级」讲清楚
 *
 * 交互取舍：
 *   - 排序用 ↑↓ 按钮而非拖拽：不引第三方 dnd 依赖，后端 reorder 接口本来就是全量 ids
 *   - 新增 / 编辑用卡内联表单而非右侧抽屉：样式与既有卡片一致、无层级与滚动逻辑、
 *     便于浏览器截图验证
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { toast } from "../../api/client";
import { usePoolStore, type PoolMode, type PoolSlot } from "../../store/poolStore";
import { useSettingsStore } from "../../store/settingsStore";
import type { LLMPoolItem, LLMPoolItemIn } from "../../types";
import { EMBED_PROVIDERS, SLOT_TITLE, circledIndex } from "./llmPresets";

/** 稳定的空数组引用：zustand selector 返回新数组会导致无限重渲染 */
const EMPTY: LLMPoolItem[] = [];

/** 行状态词：颜色不单独承载语义，始终有文字 */
const STATE_TEXT: Record<string, string> = {
  hit: "使用中",
  standby: "待命",
  cool: "冷却中",
  off: "已停用",
};

/** 厂商预设的最小结构（文本槽来自 store.providers，Embedding 槽来自内置常量） */
interface PresetLike {
  label: string;
  base_url: string;
  note?: string;
  models: { id: string; label: string }[];
}
type PresetMap = Record<string, PresetLike>;

/** 后端 cooldown_until 是 naive UTC ISO 串 → 补 Z 后按本地时区展示 */
function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

/* ============ 内联新增 / 编辑表单 ============ */

interface FormProps {
  mode: PoolMode;
  slot: PoolSlot;
  /** null = 新增 */
  initial: LLMPoolItem | null;
  source: PresetMap;
  onCancel: () => void;
  onDone: () => void;
}

function PoolItemForm({ mode, slot, initial, source, onCancel, onDone }: FormProps) {
  const add = usePoolStore((s) => s.add);
  const update = usePoolStore((s) => s.update);

  const [provider, setProvider] = useState(initial?.provider || "bailian");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url || "");
  const [model, setModel] = useState(initial?.model || "");
  const [apiKey, setApiKey] = useState("");
  const [paid, setPaid] = useState(initial?.paid ?? false);
  const [note, setNote] = useState(initial?.note || "");
  const [busy, setBusy] = useState(false);

  const preset = source[provider];
  const models = useMemo(() => preset?.models || [], [preset]);

  const onProviderChange = (p: string) => {
    setProvider(p);
    const pre = source[p];
    if (pre?.base_url) setBaseUrl(pre.base_url);
  };

  const save = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      toast("Base URL 与模型不能为空");
      return;
    }
    setBusy(true);
    const body: LLMPoolItemIn = {
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      paid,
      note: note.trim(),
      // 编辑时留空 = 保留已存 Key（传 null）；新增时留空 = 空 Key（仅免费厂商可留空）
      api_key: apiKey.trim() || (initial ? null : ""),
    };
    const ok = initial
      ? await update(mode, slot, initial.id, body)
      : await add(mode, slot, body);
    setBusy(false);
    if (ok) onDone();
  };

  const fid = initial ? `edit-${initial.id}` : "new";

  return (
    <div className="pool-form" data-testid={`pool-form-${mode}-${slot}-${fid}`}>
      <label className="f-label">厂商预设</label>
      <div className="select-wrap">
        <select
          value={provider}
          onChange={(e) => onProviderChange(e.target.value)}
          data-testid={`pool-provider-${mode}-${slot}`}
        >
          {Object.entries(source).map(([id, p]) => (
            <option key={id} value={id}>{p.label}</option>
          ))}
          {!source[provider] && <option value={provider}>{provider}（当前值）</option>}
        </select>
        <ChevronDown className="chevron" size={16} />
      </div>

      <label className="f-label">Base URL</label>
      <input
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
        autoComplete="off"
        placeholder="https://…（选预设自动带出，可改）"
        data-testid={`pool-baseurl-${mode}-${slot}`}
      />

      <label className="f-label">模型</label>
      <input
        value={model}
        onChange={(e) => setModel(e.target.value)}
        autoComplete="off"
        placeholder={
          slot === "embedding" ? "如 text-embedding-v3 / BAAI/bge-m3"
            : slot === "vision" ? "如 qwen-vl-plus（需支持图像）" : "如 qwen-plus / ep-xxx"
        }
        list={`pool-model-suggest-${mode}-${slot}`}
        data-testid={`pool-model-${mode}-${slot}`}
      />
      <datalist id={`pool-model-suggest-${mode}-${slot}`}>
        {models.map((m) => (
          <option key={m.id} value={m.id}>{m.label}</option>
        ))}
      </datalist>

      <label className="f-label">
        API Key
        {initial?.api_key_masked && (
          <span className="key-saved">已保存 {initial.api_key_masked} · 留空=保留</span>
        )}
      </label>
      <input
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        autoComplete="new-password"
        placeholder={
          initial?.api_key_masked ? "留空保留已存 Key，输入新值覆盖"
            : "sk-…（免费厂商可留空，由平台提供）"
        }
        data-testid={`pool-apikey-${mode}-${slot}`}
      />
      {preset?.note && <div className="hint-line">{preset.note}</div>}

      <label className="f-label">备注</label>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        autoComplete="off"
        maxLength={64}
        placeholder="如：主用 / 限流备用（仅自己可见）"
        data-testid={`pool-note-${mode}-${slot}`}
      />

      <label className="pool-paid-row">
        <input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} />
        <span>付费模型（会产生费用，列表显示橙色徽标）</span>
      </label>

      <div className="llm-btnrow">
        <button className="btn-primary btn-md" disabled={busy} onClick={save}
          data-testid={`pool-save-${mode}-${slot}-${fid}`}>
          {busy ? "处理中…" : "保存"}
        </button>
        <button className="btn-secondary btn-md" disabled={busy} onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

/* ============ 池卡片 ============ */

interface Props {
  slot: PoolSlot;
  mode: PoolMode;
  /** 写操作成功后通知父级（如 EffectiveBar 需要刷新调度摘要） */
  onChanged?: () => void;
}

export default function LLMPoolCard({ slot, mode, onChanged }: Props) {
  const providers = useSettingsStore((s) => s.providers);
  const loadProviders = useSettingsStore((s) => s.loadProviders);
  const loadEffective = useSettingsStore((s) => s.loadEffective);

  const k = `${mode}:${slot}`;
  const items = usePoolStore((s) => s.items[k] ?? EMPTY);
  const load = usePoolStore((s) => s.load);
  const remove = usePoolStore((s) => s.remove);
  const toggle = usePoolStore((s) => s.toggle);
  const reorder = usePoolStore((s) => s.reorder);
  const testOne = usePoolStore((s) => s.test);

  const [editing, setEditing] = useState<"new" | number | null>(null);
  const [testing, setTesting] = useState<Record<number, boolean>>({});
  const [testMsg, setTestMsg] = useState<Record<number, { ok: boolean; text: string }>>({});

  useEffect(() => {
    void loadProviders();
    void load(mode, slot);
  }, [loadProviders, load, mode, slot]);

  const source: PresetMap = slot === "embedding" ? EMBED_PROVIDERS : providers;
  const meta = SLOT_TITLE[slot] ?? { title: slot, sub: "" };
  const available = items.filter((i) => i.enabled && !i.cooling).length;
  const coolingCount = items.filter((i) => i.enabled && i.cooling).length;
  const isEffective = items.length > 0 && items[0].effective;
  /** 本池生效时，第一个可用候选才是「使用中」；池不生效则整池都是后备（-1） */
  const hitIdx = isEffective ? items.findIndex((i) => i.enabled && !i.cooling) : -1;

  const afterWrite = () => {
    void loadEffective();
    onChanged?.();
  };

  const doTest = async (it: LLMPoolItem) => {
    setTesting((m) => ({ ...m, [it.id]: true }));
    try {
      const r = await testOne(mode, slot, it.id);
      if (!r) {
        setTestMsg((m) => ({ ...m, [it.id]: { ok: false, text: "请求失败" } }));
        return;
      }
      setTestMsg((m) => ({
        ...m,
        [it.id]: r.ok
          ? { ok: true, text: `✓ 连通正常${r.latency_ms != null ? ` · ${r.latency_ms}ms` : ""}` }
          : { ok: false, text: `✗ ${r.error_label || r.error || "调用失败"}` },
      }));
    } finally {
      setTesting((m) => ({ ...m, [it.id]: false }));
    }
  };

  const doDelete = async (it: LLMPoolItem) => {
    if (!window.confirm(`确定从模型池删除「${it.provider_label} · ${it.model}」？`)) return;
    if (await remove(mode, slot, it.id)) afterWrite();
  };

  const doToggle = async (it: LLMPoolItem, enabled: boolean) => {
    if (await toggle(mode, slot, it.id, enabled)) afterWrite();
  };

  const doMove = async (idx: number, delta: -1 | 1) => {
    const target = idx + delta;
    if (target < 0 || target >= items.length) return;
    const ids = items.map((i) => i.id);
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    if (await reorder(mode, slot, ids)) afterWrite();
  };

  return (
    <section
      className={`llm-slot-card pool-card${mode === "platform" ? " platform" : ""}`}
      data-testid={`pool-card-${mode}-${slot}`}
    >
      <h4>
        {meta.title}
        {items.length > 0 && (
          <span className="slot-badge on">
            模型池 {items.length} 条 · 可用 {available}
            {coolingCount > 0 ? ` · 冷却 ${coolingCount}` : ""}
          </span>
        )}
        {items.length === 0 && <span className="slot-badge">未配置</span>}
        {items.length > 0 && (
          isEffective ? (
            <span className="slot-badge pool-eff" data-testid={`pool-hit-${mode}-${slot}`}>
              {hitIdx >= 0 ? `优先 ${circledIndex(hitIdx + 1)}` : "全部冷却"}
            </span>
          ) : (
            <span className="slot-badge">未生效</span>
          )
        )}
      </h4>
      <div className="sub">
        {meta.sub}
        {mode === "platform" ? "（平台池：未配个人池的用户生效）" : ""}
      </div>

      {items.length === 0 && (
        <div className="pool-empty" data-testid={`pool-empty-${mode}-${slot}`}>
          还没有候选模型。池为空时该槽位走平台兜底（服务器环境变量），未配置则走 mock
          —— 添加第 1 条后即按池调度，某条撞限流或额度用尽时自动换下一条，任务不中断。
        </div>
      )}

      {items.length > 0 && (
        <ul className="pool-list">
          {items.map((it, idx) => {
            const state = !it.enabled ? "off" : it.cooling ? "cool" : idx === hitIdx ? "hit" : "standby";
            const dot = !it.enabled ? "off" : it.cooling ? "cool" : "ok";
            const dotTitle = !it.enabled ? "已停用"
              : it.cooling ? `冷却中至 ${fmtTime(it.cooldown_until)}`
                : "可用";
            return (
              <li
                key={it.id}
                className={
                  "pool-row" +
                  (it.enabled ? "" : " off") +
                  (state === "hit" ? " hit" : "") +
                  (state === "cool" ? " cooling" : "")
                }
                data-testid={`pool-row-${mode}-${slot}-${it.id}`}
              >
                <div className="pool-row-head">
                  <span className="pool-idx">{idx + 1}</span>
                  <span className={`pool-dot ${dot}`} title={dotTitle} aria-hidden="true" />
                  <div className="pool-main">
                    <div className="pool-name">
                      <span className={`pool-state ${state}`}>{STATE_TEXT[state]}</span>
                      {it.provider_label} · {it.model}
                      {it.paid && <span className="pool-tag paid">付费</span>}
                      {!it.api_key_masked && <span className="pool-tag">平台 Key</span>}
                      {it.note && <span className="pool-note">{it.note}</span>}
                    </div>
                    <div className="pool-sub">
                      {it.api_key_masked || "无 Key（由平台环境变量提供）"}
                      {" · "}成功 {it.success_count} / 失败 {it.fail_count}
                    </div>
                    {/* URL 独占一行 + 省略号：长端点走 break-all 会断在词中间，很难看 */}
                    <div className="pool-url" title={it.base_url}>{it.base_url}</div>
                    {it.cooling && (
                      <div className="pool-warn">
                        冷却中至 {fmtTime(it.cooldown_until)}
                        {it.last_error ? `（${it.last_error}）` : ""}
                      </div>
                    )}
                  </div>
                </div>

                <div className="pool-acts">
                  <button className="btn-ghost-icon" title="上移" disabled={idx === 0}
                    aria-label={`上移「${it.provider_label} · ${it.model}」`}
                    onClick={() => void doMove(idx, -1)}
                    data-testid={`pool-up-${mode}-${slot}-${it.id}`}>↑</button>
                  <button className="btn-ghost-icon" title="下移" disabled={idx === items.length - 1}
                    aria-label={`下移「${it.provider_label} · ${it.model}」`}
                    onClick={() => void doMove(idx, 1)}
                    data-testid={`pool-down-${mode}-${slot}-${it.id}`}>↓</button>
                  <button className="btn-secondary btn-sm" disabled={!!testing[it.id]}
                    onClick={() => void doTest(it)}
                    data-testid={`pool-test-${mode}-${slot}-${it.id}`}>
                    {testing[it.id] ? "测试中…" : "测试"}
                  </button>
                  <button className="btn-secondary btn-sm"
                    onClick={() => setEditing(editing === it.id ? null : it.id)}
                    data-testid={`pool-edit-${mode}-${slot}-${it.id}`}>
                    {editing === it.id ? "收起" : "编辑"}
                  </button>
                  <label className="pool-switch" title="启用 / 停用（停用后不参与调度）">
                    <input
                      type="checkbox"
                      checked={it.enabled}
                      onChange={(e) => void doToggle(it, e.target.checked)}
                      data-testid={`pool-enabled-${mode}-${slot}-${it.id}`}
                    />
                    <span>启用</span>
                  </label>
                  <button className="btn-outline-danger btn-sm" title="从池中删除"
                    aria-label={`删除「${it.provider_label} · ${it.model}」`}
                    onClick={() => void doDelete(it)}
                    data-testid={`pool-del-${mode}-${slot}-${it.id}`}>
                    <Trash2 size={13} />
                  </button>
                </div>

                {testMsg[it.id] && (
                  <div className={"test-msg " + (testMsg[it.id].ok ? "test-ok" : "test-err")}>
                    {testMsg[it.id].text}
                  </div>
                )}

                {editing === it.id && (
                  <PoolItemForm
                    mode={mode}
                    slot={slot}
                    initial={it}
                    source={source}
                    onCancel={() => setEditing(null)}
                    onDone={() => { setEditing(null); afterWrite(); }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {items.length > 1 && (
        <div className="pool-hint">
          从上到下依次尝试 —— 第 1 条限流或报错时自动换下一条，本行顺序即优先级
        </div>
      )}

      {editing === "new" ? (
        <PoolItemForm
          mode={mode}
          slot={slot}
          initial={null}
          source={source}
          onCancel={() => setEditing(null)}
          onDone={() => { setEditing(null); afterWrite(); }}
        />
      ) : (
        <div className="llm-btnrow">
          <button className="btn-secondary btn-md" onClick={() => setEditing("new")}
            data-testid={`pool-add-${mode}-${slot}`}>
            <Plus size={14} />
            添加模型
          </button>
        </div>
      )}
    </section>
  );
}
