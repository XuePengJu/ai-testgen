/**
 * 演示站顶部提示条（V9.1 轻量化改版）。
 *
 * 显示条件：window.__AITF_DEMO__ 由后端在 index.html 注入（仅演示实例）。
 * 形态：28px 中性浅底细条（琥珀点 + 一句核心提示 + 次级链接 + 可关闭），
 * 替代 V9.0 的 40px 高饱和品牌蓝横幅，降低首屏视觉抢占。
 * 关闭后信息降级：侧栏 logo 旁渲染「演示站」徽章（DemoEnvBadge）兜底环境标识。
 * 关闭状态存 localStorage，跨标签页同步。
 */
import { useEffect, useState } from "react";
import { X } from "lucide-react";

export interface DemoMeta {
  is_demo: boolean;
  guide_url: string;
  reset_cron: string;
  /** false = 已关闭每日自动清空（旧后端不注入该字段，按 true 处理） */
  reset_enabled?: boolean;
}

/** 读取后端注入的演示元信息（无则视为非演示站） */
export function getDemoMeta(): DemoMeta | null {
  const w = window as unknown as { __AITF_DEMO__?: DemoMeta };
  return w.__AITF_DEMO__ || null;
}

/** 是否处于演示模式 */
export function isDemo(): boolean {
  return !!getDemoMeta()?.is_demo;
}

// ============ 关闭状态（banner 与侧栏徽章共享） ============
const DISMISS_KEY = "aitf_demo_banner_dismissed__";
const DISMISS_EVENT = "aitf-demo-banner-dismiss";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

/** 订阅提示条关闭状态；dismiss() 关闭并持久化 */
function useDemoDismissed(): [boolean, () => void] {
  const [dismissed, setDismissed] = useState(readDismissed);
  useEffect(() => {
    const sync = () => setDismissed(readDismissed());
    // 自定义事件同步本标签页；storage 事件同步其他标签页
    window.addEventListener(DISMISS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(DISMISS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* 隐私模式等写入失败时仅本次会话内隐藏 */
    }
    setDismissed(true);
    window.dispatchEvent(new Event(DISMISS_EVENT));
  };
  return [dismissed, dismiss];
}

/** 重置状态前缀：关闭自动清空时不再展示这段，只保留「请勿存放真实数据」 */
function resetTip(meta: DemoMeta): string {
  if (meta.reset_enabled === false) return "";
  const cron = meta.reset_cron || "02:00";
  return `数据每日 ${cron} 自动清空，`;
}

// ============ 顶部提示条 ============
export function DemoBanner() {
  const meta = getDemoMeta();
  const [dismissed, dismiss] = useDemoDismissed();
  if (!meta?.is_demo || dismissed) return null;

  // guide_url 为空 = 没有正式站可跳（单站演示模式）→ 不渲染链接
  const guide = meta.guide_url;

  return (
    <div className="demo-banner" role="banner">
      {/* 文案组绝对居中，不随右侧链接/关闭按钮宽度偏移 */}
      <span className="demo-banner-center">
        <span className="demo-banner-dot" aria-hidden="true" />
        <span className="demo-banner-text">
          演示环境 · {resetTip(meta)}请勿存放真实数据
        </span>
      </span>
      {guide ? (
        <a className="demo-banner-link" href={guide} target="_blank" rel="noopener noreferrer">
          前往正式站 →
        </a>
      ) : null}
      <button
        type="button"
        className="demo-banner-close"
        onClick={dismiss}
        aria-label="关闭提示（关闭后在侧栏保留演示站标识）"
        title="关闭提示"
      >
        <X size={12} />
      </button>
    </div>
  );
}

// ============ 侧栏徽章兜底（提示条被关闭后显示） ============
export function DemoEnvBadge() {
  const meta = getDemoMeta();
  const [dismissed] = useDemoDismissed();
  if (!meta?.is_demo || !dismissed) return null;

  return (
    <span
      className="demo-env-badge"
      title={`演示站：${resetTip(meta)}请勿存放真实数据`}
    >
      演示站
    </span>
  );
}
