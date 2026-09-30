/**
 * 个人中心（V5.3，原「设置」页瘦身）：个人资料 + 修改密码。
 * - 模型配置已拆为独立一级入口（pages/ModelConfigPage.tsx）
 * - 入口：rail 底部用户名区域（V5.3 移除了「设置」按钮）
 * - guest：可查看账号信息（共享访客无密码）
 */
import { useAuth } from "../hooks/useAuth";
import ProfileCard from "../components/settings/ProfileCard";

export default function SettingsPage() {
  const { me } = useAuth();
  if (!me) return <div className="page-empty">请先登录</div>;
  return (
    <div className="page-wrap settings-page" data-testid="settings-page">
      <ProfileCard />
    </div>
  );
}
