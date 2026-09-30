/**
 * 槽位分组（V5.0 P2；V5.4 起仅模型池）：每槽一张池卡片，设置页与管理页共用。
 *
 * V5.4：单条配置（LLMConfigCard + 池空兜底折叠）已下线，模型池是唯一配置入口；
 * 池空时后端解析链回落 env 兜底 / mock（见 llm_service.resolve_effective）。
 */
import type { PoolMode, PoolSlot } from "../../store/poolStore";
import LLMPoolCard from "./LLMPoolCard";

const SLOTS: PoolSlot[] = ["text", "vision", "embedding"];

interface Props {
  title: string;
  intro: string;
  mode: PoolMode;
  onSaved?: () => void;
}

export default function LLMSlotGroup({ title, intro, mode, onSaved }: Props) {
  return (
    <section className="set-card" data-testid={`slot-group-${mode}`}>
      <h3>{title}</h3>
      <div className="sub">{intro}</div>

      <div className="llm-grid">
        {SLOTS.map((s) => (
          <LLMPoolCard key={s} slot={s} mode={mode} onChanged={onSaved} />
        ))}
      </div>
    </section>
  );
}
