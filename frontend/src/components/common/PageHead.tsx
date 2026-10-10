/**
 * 统一页头（V7.5 unified-layout）。
 *
 * 背景：一级页面原先「有的有标题、有的直接从卡片开始」，标题字号/行高也各不相同，
 * 切菜单时标题基线会跳。这里把页头收敛成一个组件 + 一套样式（.page-head），
 * 所有页面共用：图标色块 + 标题 + 可选副标题 + 右侧操作区。
 *
 * 用法：
 *   <PageHead icon={<BarChart3 size={18} />} title="使用统计" sub="时间口径为 UTC 日期"
 *             ops={<button className="btn btn-secondary btn-md">刷新</button>} />
 */
import type { ReactNode } from "react";

export default function PageHead({
  icon, title, sub, ops,
}: {
  /** 左侧图标（建议 size={18}，会被包进 34px 圆角色块） */
  icon?: ReactNode;
  title: string;
  /** 标题右侧的灰色说明文案 */
  sub?: ReactNode;
  /** 右侧操作区（按钮等），自动推到最右 */
  ops?: ReactNode;
}) {
  return (
    <header className="page-head">
      {icon ? <span className="ph-ico" aria-hidden="true">{icon}</span> : null}
      <h2>{title}</h2>
      {sub ? <span className="ph-sub">{sub}</span> : null}
      {ops ? <div className="ph-ops">{ops}</div> : null}
    </header>
  );
}
