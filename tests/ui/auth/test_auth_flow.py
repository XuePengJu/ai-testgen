"""认证与权限全链路（源：V2.8 M1 里程碑验收脚本 e2e-m1-browser.mjs，7 步逐条对齐）：

① 首开静默进访客（rail 用户区显示「访客」）
② guest 加密链路：登录态业务接口返回 enc 密文 + 前端透明解密出访客身份
③ 退出 → 弹登录框
④ admin 登录（明文直通角色）→ rail 显示「管理员」
⑤ 退出 → 注册新 user（注册后业务接口返回 enc 密文）→ rail 显示「用户」
⑥ 清理钩子：admin API 删除本次注册的测试账号（防 e2e 残留累积）
⑦ 全程 0 JS 错误

起始态 = **未登录**（由 tests/ui/conftest.py::_start_state 依 ui/auth 路径规则自动推导，用例内不写登录）；
BASE URL 走 conftest 的 `server` 夹具（单一真源 AITF_BASE_URL）；「0 JS 错误」用 conftest 的 `js_errors`。
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_ui

# 与 JS 版一致：admin 起始凭据（登录 ④ 与清理 ⑥ 复用）
ADMIN_USER = "admin"
ADMIN_PWD = "Admin@123"

GUEST_ROLE = "访客"
ADMIN_ROLE = "管理员"
USER_ROLE = "用户"


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent。

    §9.1：`page.text_content()` / `locator.inner_text()` 在元素缺失时会按默认超时（30s）隐式空等，
    开录屏后会把「画面僵住」录进去；这里改用 `page.evaluate` 同步读取，零等待。
    """
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; }",
        selector,
    )


def _rail_identity(page: Page, timeout: int = 10_000) -> dict:
    """rail 底部用户区身份断言（对应 JS 版 railIdentity）。"""
    expect(page.locator(".rail-user")).to_be_visible(timeout=timeout)
    return {
        "name": _text(page, ".rail-user .rl-user-name"),
        "role": _text(page, ".rail-user .rl-user-role"),
    }


@pytest.mark.title("登录 / 注册 / 权限全链路验证")
def test_auth_flow(page: Page, hud, server, js_errors):
    """完整 7 步链路；任一 `hud.check(...)` 返回 False 即断言失败（HUD 同步记录该步）。"""
    base = server

    # 收集应用发起的 JSON 接口响应体（AES enc 密文断言用；排除 SSE 流与 guest/token 密钥分发通道）
    api_bodies: list[dict] = []

    def _collect(resp) -> None:
        url = resp.url
        if "/api/" not in url:
            return
        if "/chat/stream" in url or "/guest/token" in url:
            return
        ctype = (resp.headers or {}).get("content-type", "") or ""
        if "json" not in ctype:
            return
        try:
            api_bodies.append({"url": url, "body": resp.text()})
        except Exception:      # noqa: BLE001 —— 响应体偶发不可读时静默跳过（与 JS 版一致）
            pass

    page.on("response", _collect)

    # ① 首开 → 静默访客
    hud.step("① 首开静默进访客")
    page.goto(base, wait_until="networkidle")
    ident = _rail_identity(page)
    assert hud.check(
        f"① 首开静默进访客（{ident['name']} · {ident['role']}）",
        ident["role"] == GUEST_ROLE,
    ), ident

    # ② guest 加密链路：登录态业务接口返回 enc 密文，前端透明解密
    hud.step("② guest 加密链路")
    page.wait_for_timeout(1200)     # 等首屏业务接口（tasks/conversations 等）落地
    enc_hit = next((b for b in api_bodies if b["body"].lstrip().startswith('{"enc"')), None)
    assert hud.check(
        "② 业务接口返回 enc 密文 + 前端透明解密（AES 链路）",
        enc_hit is not None,
    ), f"捕获 {len(api_bodies)} 个响应，无 enc 密文"

    # ③ 退出 → 弹登录框（logout 会主动打开登录模态）
    hud.step("③ 退出后弹登录框")
    page.locator('button[aria-label="退出登录"]').click()
    expect(page.locator(".auth-modal")).to_be_visible(timeout=8_000)
    assert hud.check("③ 退出后弹登录框", True)

    # ④ admin 登录（明文直通角色）
    hud.step("④ admin 登录")
    page.locator('.auth-modal input[placeholder="用户名"]').fill(ADMIN_USER)
    page.locator('.auth-modal input[placeholder="密码"]').fill(ADMIN_PWD)
    page.locator(".auth-btn").click()
    page.wait_for_function(
        '() => document.querySelector(".rail-user .rl-user-role")?.textContent?.trim() === "管理员"',
        timeout=10_000,
    )
    id_admin = _rail_identity(page)
    assert hud.check(
        f"④ admin 登录成功（rail 显示 {id_admin['name']} · {id_admin['role']}）",
        id_admin["role"] == ADMIN_ROLE,
    ), id_admin

    # ⑤ 退出 → 注册新 user（加密链路 + 三级角色最后一环）
    hud.step("⑤ 注册新用户")
    page.locator('button[aria-label="退出登录"]').click()
    expect(page.locator(".auth-modal")).to_be_visible(timeout=8_000)
    api_before = len(api_bodies)
    page.locator(".auth-tabs button").nth(1).click()          # 注册 tab
    uname = "m1verify" + uuid.uuid4().hex[:10]                 # 本次注册的测试账号名（⑥ 清理钩子用）
    page.locator('.auth-modal input[placeholder="用户名"]').fill(uname)
    page.locator('.auth-modal input[placeholder="邮箱"]').fill(f"{uname}@163.com")
    page.locator('.auth-modal input[placeholder="密码"]').fill("Passw0rd123")
    page.locator(".auth-btn").click()
    try:
        page.wait_for_function(
            '(u) => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === u',
            arg=uname,
            timeout=12_000,
        )
    except Exception as exc:                                   # noqa: BLE001
        err = _text(page, ".auth-error") or "(无错误提示)"
        hud.check(f"⑤ 注册失败：{err}", False)
        raise AssertionError(f"register failed: {err}") from exc
    id_user = _rail_identity(page)
    assert hud.check(
        f"⑤ 注册 user 登录成功（{id_user['name']} · {id_user['role']}）",
        id_user["role"] == USER_ROLE,
    ), id_user
    page.wait_for_timeout(1200)                                # 等 user 身份的业务接口落地
    enc_user = next(
        (b for b in api_bodies[api_before:] if b["body"].lstrip().startswith('{"enc"')), None)
    assert hud.check("⑤ user 登录态业务接口返回 enc 密文（加密链路）", enc_user is not None)

    # ⑥ 清理钩子：admin API 删除本次注册的测试账号（注册 → 测完 → 删号，不残留）
    hud.step("⑥ 清理测试账号")
    lr = httpx.post(f"{base}/api/auth/login",
                    data={"username": ADMIN_USER, "password": ADMIN_PWD}, timeout=10)
    lr.raise_for_status()
    auth = "Bearer " + lr.json()["access_token"]
    users = httpx.get(f"{base}/api/users",
                      headers={"Authorization": auth}, timeout=10).json()
    target = next((u for u in users if u.get("username") == uname), None)
    assert target, f"未找到本次注册的账号 {uname}"
    dr = httpx.delete(f"{base}/api/users/{target['id']}",
                      headers={"Authorization": auth}, timeout=10)
    assert hud.check(
        f"⑥ 清理钩子：测试账号 {uname}（id={target['id']}）已删除",
        dr.status_code == 200,
    ), dr.text

    # ⑦ 全程 0 JS 错误
    hud.step("⑦ 全程 0 JS 错误")
    ok7 = hud.check("⑦ 全程 0 JS 错误", not js_errors)
    assert ok7, f"JS 错误：{js_errors[:3]}"

    # HUD 注入校验（G6）：确认浮层已在页面 DOM 中（headless 下同样录进视频）
    assert page.evaluate("() => !!document.getElementById('__hud')"), "HUD 未注入页面"

    # 7 步结果落 stdout（对应 JS 版逐条 console.log；配合 `-s` 可读，便于 CI 留痕）
    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
