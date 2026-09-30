/**
 * 模型配置页（V5.3，自设置页拆出的独立一级入口）：
 * 模型调度摘要 + 模型池配置（分段 Tab）。
 * - admin：「我的模型 | 平台默认」双 Tab（平台默认仅 admin 可见，V5.2 自管理后台迁入）
 * - user：无 Tab 控件，直接「我的模型」
 * - guest：模型配置接口 403 → 只显示模型调度摘要与提示（只读）
 * V5.4：单条配置下线，本页只渲染模型池卡片（个人池 / 平台池）。
 */
import { useEffect, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { useSettingsStore } from "../store/settingsStore";
import LLMSlotGroup from "../components/settings/LLMSlotGroup";
import EffectiveBar from "../components/settings/EffectiveBar";

export default function ModelConfigPage() {
  const { me, role } = useAuth();
  const loadEffective = useSettingsStore((s) => s.loadEffective);
  const [tab, setTab] = useState<"personal" | "platform">("personal");

  useEffect(() => {
    void loadEffective();
  }, [loadEffective]);

  if (!me) return <div className="page-empty">请先登录</div>;

  // 分段 Tab（仅 admin）：role=tablist + 左右方向键切换，焦点环可见
  const tabs =
    role === "admin" ? (
      <div
        className="seg-tabs"
        role="tablist"
        aria-label="模型配置分组"
        data-testid="seg-tabs"
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            const next = tab === "personal" ? "platform" : "personal";
            setTab(next);
            const btn = e.currentTarget.querySelector<HTMLButtonElement>(
              `[data-testid="tab-${next}"]`,
            );
            btn?.focus();
          }
        }}
      >
        <button
          role="tab"
          aria-selected={tab === "personal"}
          data-testid="tab-personal"
          className={tab === "personal" ? "seg-on" : ""}
          onClick={() => setTab("personal")}
        >
          我的模型
        </button>
        <button
          role="tab"
          aria-selected={tab === "platform"}
          data-testid="tab-platform"
          className={tab === "platform" ? "seg-on" : ""}
          onClick={() => setTab("platform")}
        >
          平台默认
        </button>
      </div>
    ) : null;

  return (
    <div className="page-wrap settings-page" data-testid="models-page">
      <EffectiveBar />
      {role !== "guest" ? (
        <>
          {tabs}
          {tab === "personal" ? (
            <LLMSlotGroup
              title="我的模型池"
              intro="个人模型池优先于平台默认；文本槽为必配项（免费厂商可不填 Key，由平台提供）；Embedding 向量模型用于知识库入库与检索，不配置时走 mock（流程可用、检索质量差）。池内多条候选按优先级调度，撞限流自动切换。"
              mode="personal"
            />
          ) : (
            <LLMSlotGroup
              title="平台默认模型池（兜底）"
              intro="未配置个人模型的用户（含访客）使用平台池调度；免费厂商不填 Key 时由服务器环境变量提供。个人池优先于此处。"
              mode="platform"
            />
          )}
        </>
      ) : (
        <section className="set-card">
          <h3>模型配置</h3>
          <div className="hint-line">
            共享访客使用平台默认模型。注册账号后可自定义模型池并长期保留数据。
          </div>
        </section>
      )}
    </div>
  );
}
