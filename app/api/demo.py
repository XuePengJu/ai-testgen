"""演示站 API（V9.0）：免登录 admin token + 演示状态。

端点：
- POST /api/demo/token：返回固定演示管理员（admin）的 JWT + 加密会话密钥。
  前端 AuthContext 检测到演示模式时自动调用，免登录直接进入。
- GET  /api/demo/meta：演示站元信息（引导链接、重置时间文案），
  前端 Banner 渲染用（避免把链接写死在构建产物里）。

设计要点：
- /api/demo/token 必须加入 ApiCryptoMiddleware 的 _PLAINTEXT_PATHS
  （明文分发通道，与 /api/guest/token 同款），否则客户端在拿到密钥前
  无法解密响应。
- 演示模式是**进程级**开关：正式站实例 AITF_DEMO_ENABLED=0 时，这些
  端点一律 403，绝不可能被误调。
- admin 走明文（API_ENCRYPT 分级策略本就如此），所以即使请求/响应
  不加密，前端 api() 也能正常解析。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto, demo_config, security
from app.core.db import get_db
from app.models.user import User

router = APIRouter(prefix="/demo", tags=["演示站"])

logger = logging.getLogger("api.demo")


def _require_demo_enabled() -> None:
    """非演示实例直接 403（正式站永远调不通这些端点）。

    运行时读取 demo_config 属性（而非 import 时绑定常量），
    便于测试/运维在同一进程内切换实例语义。
    """
    if not demo_config.AITF_DEMO_ENABLED:
        raise HTTPException(status_code=403, detail="演示模式未开启")


def _get_or_create_admin(db: Session) -> User:
    """取演示管理员；不存在则按配置幂等创建（演示站专用账号）。"""
    admin = db.execute(
        select(User).where(
            User.username == demo_config.AITF_DEMO_ADMIN_USERNAME,
            User.role == "admin",
        )
    ).scalar_one_or_none()
    if admin:
        return admin
    user = User(
        username=demo_config.AITF_DEMO_ADMIN_USERNAME,
        password_hash=security.hash_password(demo_config.AITF_DEMO_ADMIN_PASSWORD),
        role="admin",
        data_dir="",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user.data_dir = f"u_{user.id}"
    db.commit()
    # 新管理员播种示例分类/任务（与注册链路一致；失败不影响 token 下发）
    try:
        from app.services.sample_seeder import seed_sample_tasks
        seed_sample_tasks(db, user)
    except Exception:  # noqa: BLE001
        logger.warning("demo admin seed sample tasks failed", exc_info=True)
    return user


@router.post("/token")
def demo_token(db: Session = Depends(get_db)):
    """演示站免登录：返回 admin 的 JWT + 会话密钥。

    前端调用时机：检测到演示模式（location 前缀 /demo 或
    window.__AITF_DEMO__ 注入）且本地无 token 时。
    """
    _require_demo_enabled()
    admin = _get_or_create_admin(db)
    token = security.create_token(
        admin.id, admin.username, "admin",
        ttl_hours=demo_config.AITF_DEMO_TOKEN_TTL_HOURS,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "admin",
        "username": admin.username,
        "enc_key": crypto.derive_key(admin.id),
        "is_demo": True,
    }


@router.get("/meta")
def demo_meta():
    """演示站元信息（Banner 文案 / 引导链接 / 重置时间）。"""
    _require_demo_enabled()
    return {
        "is_demo": True,
        "guide_url": demo_config.AITF_DEMO_GUIDE_URL,
        "reset_cron": f"{demo_config.AITF_DEMO_RESET_HOUR:02d}:{demo_config.AITF_DEMO_RESET_MINUTE:02d}",
        "notice": "这是演示站：免登录、数据每日自动清空，请勿存放真实数据。",
    }
