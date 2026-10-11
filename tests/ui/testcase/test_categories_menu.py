"""归类菜单交互（自 frontend/scripts/e2e-v513-menu-probe.mjs 迁移，逐条对齐）：

1. 点归类按钮 → 菜单打开（多次采样，覆盖同步 flush 时序）
2. 点菜单标题（内部）→ 仍打开（菜单内部点击不关闭）
3. Esc → 关闭
4. 再次点开 → 点页面标题（外部）→ 关闭
5. toggle 语义：关闭态点归类按钮 → 打开；打开态再点同一按钮 → 关闭；收尾 Esc 清场

起始态 = **test 账户**（由 conftest::_start_state 自动注入，用例内不写登录；
JS 版开头的「退出 → admin 登录」在 Python 版由夹具等价承担）。用例库无任务行时 `pytest.skip`。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e_ui


@pytest.mark.title("用例归类菜单交互验证")
def test_categories_menu(page, hud, server):
    """归类菜单的开合 / 内部点击 / 外部点击 / Esc / toggle 五种交互。"""
    base = server

    hud.step("归类菜单：进入用例库")
    page.goto(base, wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.locator('button[aria-label^="用例库"]').click()
    page.wait_for_timeout(1500)

    menu_btn = page.locator('[data-testid^="cat-menu-"]').first
    if not menu_btn.is_visible() and page.locator("tbody tr").count() == 0:
        pytest.skip("用例库无任务行，无法验证归类菜单")

    def is_open() -> bool:
        return page.locator(".cl-move-menu").is_visible()

    # 1. 打开（多次采样，覆盖同步 flush 时序）
    menu_btn.click()
    opened = False
    for _ in range(12):
        if is_open():
            opened = True
            break
        page.wait_for_timeout(50)
    assert hud.check("点归类按钮 → 菜单打开", opened)

    # 2. 菜单内部点击不关闭
    page.locator(".cl-move-menu .cmm-title").click()
    page.wait_for_timeout(300)
    assert hud.check("点菜单标题（内部）→ 仍打开", is_open())

    # 3. Esc 关闭
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert hud.check("Esc → 关闭", not is_open())

    # 4. 再打开 → 点外部关闭
    menu_btn.click()
    page.wait_for_timeout(400)
    assert hud.check("再次点归类按钮 → 打开", is_open())
    page.locator(".cl-title").click()
    page.wait_for_timeout(300)
    assert hud.check("点页面标题（外部）→ 关闭", not is_open())

    # 5. toggle 语义（上文「点外部关闭」后菜单已关：此处首点是"打开"）
    menu_btn.click()
    page.wait_for_timeout(400)
    assert hud.check("关闭状态点归类按钮 → 打开", is_open())
    menu_btn.click()
    page.wait_for_timeout(400)
    assert hud.check("打开状态再点同一归类按钮 → 关闭", not is_open())
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert hud.check("收尾 Esc 清场 → 关闭", not is_open())

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
