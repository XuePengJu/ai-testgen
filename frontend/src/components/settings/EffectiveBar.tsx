/**
 * 模型调度摘要（V5.1）：只读，全角色可见（含访客）。
 *
 * 池不是「多条同时生效」，而是「一条生效梯队」：任一时刻只有一个模型在干活，
 * 其余是后备，它限流 / 报错就自动顺延下一条。所以本卡只回答三件事：
 * 这一槽由谁接管、梯队多大、当前优先用第几条。
 *
 * 取数口径（V5.1 P1）：改走后端 /llm/effective 的 pools 字段，每槽给
 * owner / total / enabled / available / cooling / hit，判据与 build_client（真实调度）同源。
 * 相比 V5.0 的三点收益：
 *   ① 三槽都准确 —— 旧口径后端 source 只反映 text 槽，vision / embedding 一律误报「未启用池」；
 *   ② 访客也能看到条数 —— effective 走 get_current_user、不是 require_user，
 *      而池列表接口对访客是 403，旧版只能显示「模型池接管」不猜数字；
 *   ③ 不再为算条数而额外拉 3 次池列表，少一层耦合。
 *
 * 文案用「优先 ①」而非「最近命中」：后端给的是「按优先级预期会命中」，
 * 不是「上次实际命中」。等池表加 last_used_at 再升级口径 —— 宁保守，不说谎。
 */
import { useSettingsStore } from "../../store/settingsStore";
import { circledIndex } from "./llmPresets";
import type { LLMPoolStats } from "../../types";

const SLOTS = ["text", "vision", "embedding"] as const;
type Slot = (typeof SLOTS)[number];

const SLOT_LABEL: Record<Slot, string> = { text: "文本", vision: "图像", embedding: "向量" };

const NONE_MODEL: Record<Slot, string> = {
  text: "未配置",
  vision: "未配置（可选）",
  embedding: "未配置（知识库不可用）",
};

type Kind = "mine" | "platform" | "single";

/**
 * 池现状 → 状态徽标。
 *   owner 为 null       该槽没有池（未配置时后端走 env 兜底 / mock）
 *   hit 为 0            有池但一条可用候选都没有（全冷却 / 全停用 / Key 全不可解）
 *   pools 字段缺失       后端旧版本，按老的 source 兜底，不显示错误结论
 */
function describe(st: LLMPoolStats | undefined, source: string): { kind: Kind; badge: string } {
  if (!st) {
    return source === "pool"
      ? { kind: "mine", badge: "模型池接管" }
      : { kind: "single", badge: "未启用池" };
  }
  if (!st.owner) return { kind: "single", badge: "未启用池" };

  const kind: Kind = st.owner === "personal" ? "mine" : "platform";
  const prefix = st.owner === "platform" ? "平台 " : "";
  if (st.hit <= 0) return { kind, badge: `${prefix}${st.total} 条 · 全部不可用` };
  const avail = st.available < st.total ? ` · 可用 ${st.available}` : "";
  return { kind, badge: `${prefix}${st.total} 条${avail} · 优先 ${circledIndex(st.hit)}` };
}

export default function EffectiveBar() {
  const effective = useSettingsStore((s) => s.effective);

  if (!effective) return null;

  const rows = SLOTS.map((slot) => {
    const cfg = effective[slot] ?? null;
    const st = effective.pools?.[slot];
    const { kind, badge } = describe(st, effective.source);
    return {
      slot,
      kind,
      badge,
      model: cfg ? `${cfg.provider_label} · ${cfg.model}` : null,
      /** 池真的在接管（有空闲候选），而不只是「归属是我的」 */
      takingOver: kind !== "single" && (st?.active ?? false),
    };
  });

  const mine = rows.filter((r) => r.kind === "mine" && r.takingOver).length;
  const plat = rows.filter((r) => r.kind === "platform" && r.takingOver).length;
  const head = mine > 0
    ? `${mine} 个槽位由我的模型池接管`
    : plat > 0
      ? "由平台模型池接管"
      : "未启用模型池（池空时走平台兜底 / mock）";

  return (
    <section className="set-card effective-bar" data-testid="effective-bar">
      <div className="sched-head">
        <h3>模型调度</h3>
        <span className={`sched-badge${mine > 0 ? " on" : ""}`} data-testid="sched-head-badge">
          {head}
        </span>
      </div>

      <dl className="sched-list">
        {rows.map((r) => (
          <div className="sched-row" key={r.slot} data-testid={`sched-row-${r.slot}`}>
            <dt className="sched-slot">{SLOT_LABEL[r.slot]}</dt>
            <dd className="sched-state">
              <span className={`sched-tag ${r.kind}`} data-testid={`sched-tag-${r.slot}`}>
                {r.badge}
              </span>
            </dd>
            <dd className={`sched-model${r.model ? "" : " none"}`} title={r.model ?? undefined}>
              {r.model ?? NONE_MODEL[r.slot]}
            </dd>
          </div>
        ))}
      </dl>

      {effective.embedding_source === "mock" && (
        <div className="sched-warn" data-testid="sched-mock-warn">
          当前为 mock 向量，知识库检索质量差 —— 配置 Embedding 后需重建索引。
        </div>
      )}

      <div className="sched-foot">只读摘要；单个模型的连通测试在各槽位卡片内</div>
    </section>
  );
}
