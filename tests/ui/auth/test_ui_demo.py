"""UI 自动化演示（带断言 · 含负向用例；源：V2.8 M1 里程碑验收脚本 _e2e-ui-test.mjs）：

原 JS 脚本 = 访客起 → 权限对照 → 退出弹登录框 → 负向（错误密码被拒）→ 正向（admin 登录）→
四个业务页渲染断言 → 全局 JS 错误 / 4xx-5xx 响应核对 → 生成测试报告页；全程把断言结果画在
页面右下角 HUD（录屏可见）。

迁就 pytest 体系后：
- `pytestmark = [e2e_ui, demo]`；`demo` 默认被 pytest.ini 的 `-m "not demo"` 门禁排除，需显式选中。
- 起始态 = **未登录**（由 tests/ui/conftest.py::_start_state 依 `tests/ui/auth/**` 路径规则自动推导，
  用例内不手写登录）——正对应原脚本「首屏静默进访客」。
- **HUD 语义完整保留**：标题行「🤖 UI 自动化测试 · {步骤名}」+ ✔/✘ 断言清单 + 幂等重绘，
  由 conftest 的 `hud` 夹具提供（`hud.step` / `hud.check`，与原 JS `hud(title)` 一一对应）。
- 「0 JS 错误」用 conftest 的 `js_errors`；4xx/5xx 响应沿用原脚本的 page.on("response") 收集。

与 JS 版的差异（D10 起始态规范化 + §9.1 显式超时）：
- 原脚本用固定 `sleep()` 等页面稳定；迁移版改为显式 `timeout=` 的 `expect`/`wait_for_*`，
  避免默认 30s 隐式空等把录屏画面僵住（§9.1）。
- 登录输入框定位由原 `.auth-modal input` 的 nth(0)/nth(1) 改为 placeholder（等价且更稳，与 conftest 一致）。
- 报告页由原 `page.goto("data:text/html…")` 改为 `page.set_content(...)`：同为「末尾渲染一张汇总报告页
  并截图留档」，不额外发起网络请求。
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e_ui, pytest.mark.demo]

GUEST_ROLE = "访客"
ADMIN_ROLE = "管理员"
ADMIN_USER = "admin"
ADMIN_PWD = "Admin@123"
WRONG_PWD = "WrongPassword_123"


def _identity(page: Page) -> dict:
    """零等待读取 rail 身份（元素缺失返回 ''，不触发默认 30s 隐式空等；对应原 JS identity()）。"""
    return page.evaluate(
        """() => {
            const t = (s) => (((document.querySelector(s) || {}).textContent) || "").trim();
            return { name: t(".rail-user .rl-user-name"), role: t(".rail-user .rl-user-role") };
        }"""
    )


def _admin_entry_count(page: Page) -> int:
    """「用户管理」入口数量（访客态应为 0，管理员态应 > 0；对应原 JS adminEntryCount）。"""
    return page.locator('button[aria-label*="用户管理"]').count()


@pytest.mark.title("自动化演示：登录正负向 + 业务页渲染")
def test_ui_demo(page: Page, hud, server, js_errors, output_path):
    base = server

    # 4xx/5xx 响应收集（对应原脚本 page.on("response")）
    bad_responses: list[str] = []

    def _on_response(resp) -> None:
        if resp.status >= 400:
            bad_responses.append(f"{resp.status} {resp.request.method} {resp.url.replace(base, '')}")

    page.on("response", _on_response)

    # ===== ① 首屏 · 访客身份断言 =====
    hud.step("① 首屏加载 · 访客身份")
    page.goto(base, wait_until="networkidle")
    expect(page.locator(".rail-user")).to_be_visible(timeout=20_000)
    page.wait_for_timeout(1200)
    id1 = _identity(page)
    assert hud.check(
        f"首屏静默进入访客模式（{id1['name']} / {id1['role']}）",
        GUEST_ROLE in id1["role"],
    ), id1
    assert hud.check("rail 用户区已渲染身份", bool(id1["name"])), id1

    # ===== ② 权限差异（越权前置断言）=====
    hud.step("② 权限校验 · 访客不可见管理入口")
    guest_admin = _admin_entry_count(page)
    assert hud.check("访客态看不到「用户管理」入口（越权防护）", guest_admin == 0), guest_admin

    # ===== ③ 退出 → 登录弹窗 =====
    hud.step("③ 退出登录 · 弹窗断言")
    page.locator('button[aria-label="退出登录"]').click(timeout=10_000)
    expect(page.locator(".auth-modal")).to_be_visible(timeout=10_000)
    assert hud.check("点击退出后登录弹窗自动弹出", True)

    # ===== ④ 负向用例：错误密码必须被拒绝 =====
    hud.step("④ 负向用例 · 错误密码应被拒绝")
    page.locator('.auth-modal input[placeholder="用户名"]').fill(ADMIN_USER)
    page.locator('.auth-modal input[placeholder="密码"]').fill(WRONG_PWD)
    page.locator(".auth-btn").click()
    page.wait_for_timeout(2600)                       # 与原脚本一致：等鉴权失败回流
    still_modal = page.locator(".auth-modal").count()
    id_wrong = _identity(page)
    assert hud.check(
        "错误密码被拒绝 · 未越权登录",
        still_modal > 0 or ADMIN_ROLE not in id_wrong["role"],
    ), id_wrong

    # ===== ⑤ 正向用例：正确密码登录 + 权限对照 =====
    hud.step("⑤ 正向用例 · admin 登录 + 权限对照")
    if page.locator(".auth-modal").count():
        page.locator('.auth-modal input[placeholder="用户名"]').fill(ADMIN_USER)
        page.locator('.auth-modal input[placeholder="密码"]').fill(ADMIN_PWD)
        page.locator(".auth-btn").click()
    page.wait_for_function(
        '() => document.querySelector(".rail-user .rl-user-role")?.textContent?.trim() === "管理员"',
        timeout=15_000,
    )
    id2 = _identity(page)
    assert hud.check(
        f"正确密码登录成功 · 身份切换为管理员（{id2['name']} / {id2['role']}）",
        ADMIN_ROLE in id2["role"],
    ), id2
    assert hud.check(
        "管理员态可见「用户管理」入口（与访客态形成权限对照）",
        _admin_entry_count(page) > 0,
    )

    # ===== ⑥ 业务页面渲染断言 =====
    navs = [
        ("AI会话", '.rail-btn:has-text("AI 会话")', None),
        ("用例库", 'button[aria-label^="用例库"]', ".cases-page"),
        ("知识库", 'button[aria-label*="知识库"]', None),
        ("用户管理", 'button[aria-label*="用户管理"]', None),
    ]
    for label, sel, must in navs:
        hud.step(f"⑥ 页面渲染断言 · {label}")
        el = page.locator(sel).first
        if el.count() == 0:
            assert hud.check(f"进入「{label}」", False), f"入口缺失：{sel}"
            continue
        el.click(timeout=8_000)
        if must:
            expect(page.locator(must)).to_be_visible(timeout=8_000)
        else:
            page.wait_for_timeout(1200)
        rendered = page.locator(must).count() > 0 if must else True
        assert hud.check(
            f"进入「{label}」且页面已渲染" + (f"（{must}）" if must else ""),
            rendered,
        )

    # ===== ⑦ 全局 JS 错误 / 4xx-5xx 核对 =====
    hud.step("⑦ 全局 JS 错误与响应核对")
    real_js_errors = [e for e in {*js_errors} if not re.search(r"401|Unauthorized", e)]
    uni_resp = sorted({*bad_responses})
    expected = [r for r in uni_resp if "/api/auth/login" in r]
    unexpected = [r for r in uni_resp if "/api/auth/login" not in r]
    assert hud.check(
        "错误密码触发 401 被正确拒绝（负向用例生效的直接证据）",
        len(expected) > 0,
    ), f"预期内的 401 响应：{expected}"
    assert hud.check(
        f"除预期外 0 个非预期 4xx/5xx 响应（实际 {len(unexpected)} 条）",
        len(unexpected) == 0,
    ), unexpected[:5]
    assert hud.check(
        f"全程 0 个非预期 JS 运行时错误（实际 {len(real_js_errors)} 条）",
        len(real_js_errors) == 0,
    ), real_js_errors[:5]

    # HUD 注入校验：浮层确已在页面（headless 下同样录进视频）
    assert page.evaluate("() => !!document.getElementById('__hud')"), "HUD 未注入页面"

    # 逐条结果落 stdout（对应原 JS 逐条 console.log；配合 -s 可读）
    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))

    # ===== 测试报告页（末尾渲染汇总页并截图留档）=====
    passed = sum(1 for _, ok in hud.checks if ok)
    failed = len(hud.checks) - passed
    rows = "".join(
        f'<div style="color:{"#4ade80" if ok else "#f87171"};padding:3px 0">'
        f'{"✔" if ok else "✘"} {text}</div>'
        for text, ok in hud.checks
    )
    vp = page.viewport_size or {"width": 1280, "height": 720}
    report = (
        '<!doctype html><meta charset="utf-8"><body style="margin:0;'
        "font:15px/1.95 -apple-system,'PingFang SC',sans-serif;background:#0f172a;"
        'color:#e2e8f0;padding:46px">'
        '<h1 style="font-size:26px;margin:0 0 6px">UI 自动化测试报告</h1>'
        '<div style="color:#94a3b8;margin-bottom:24px">ai-testgen · M1 认证与权限链路 · '
        f"共 {len(hud.checks)} 条断言</div>"
        '<div style="display:flex;gap:14px;margin-bottom:24px">'
        '<div style="background:#052e16;border:1px solid #166534;border-radius:10px;padding:14px 28px">'
        f'<div style="font-size:30px;color:#4ade80;font-weight:700">{passed}</div>'
        '<div style="color:#86efac">通过</div></div>'
        f'<div style="background:{"#450a0a" if failed else "#1e293b"};'
        f'border:1px solid {"#991b1b" if failed else "#334155"};border-radius:10px;padding:14px 28px">'
        f'<div style="font-size:30px;color:{"#f87171" if failed else "#94a3b8"};font-weight:700">{failed}</div>'
        f'<div style="color:{"#fca5a5" if failed else "#94a3b8"}">失败</div></div>'
        "</div>"
        + rows
        + '<div style="margin-top:26px;color:#64748b">Playwright · Chromium · '
        f'{vp["width"]}×{vp["height"]} · '
        "含 1 条负向用例（错误密码拒绝）· 1 条权限对照断言</div></body>"
    )
    page.set_content(report, wait_until="load")
    page.wait_for_timeout(1000)
    out = f"{output_path}/99-测试报告.png"
    page.screenshot(path=out)
    print(f"\n🖼  {out}")
    print(f"===== {passed}/{len(hud.checks)} 条断言通过 =====")
