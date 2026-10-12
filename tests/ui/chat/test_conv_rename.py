"""V5.9 会话重命名 + AI 总结标题（自 frontend/scripts/e2e-v59-conv-rename.mjs 迁移，步骤逐条对齐）：

① test 起始态 → 会话列表渲染
② hover 会话行出现 3 个操作钮（✏️ 重命名 / ✨ AI 标题 / 🗑 删除）
③ 行内重命名：Enter 提交 → 标题更新
④ ✨ AI 总结标题 → 等待返回 → 标题被 AI 重写
⑤ 全程 0 JS 错误

与 JS 版差异（**仅起始态**）：JS 版自带「访客 → 退出 → test 登录」，Python 版起始态由
tests/ui/conftest.py::_start_state 按模块（chat → test）自动注入，用例内不写登录。

共享态提示：本用例沿用 JS 语义操作「第一条历史会话」并改名（写入固定名），对 `test` 账户
共享；断言只针对「这条会话的标题被改成了期望值」，不依赖全局列表的绝对条数（并发安全）。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e_ui, pytest.mark.real_llm]

T_LAYOUT = 10_000
T_HIST = 10_000
T_EDIT = 5_000

RENAMED = "V59手动改名验证"


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（§9.1）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; }",
        selector,
    )


def _is_visible(locator) -> bool:
    """零等待可见性判断（等价 JS `isVisible().catch(() => false)`）。"""
    try:
        return locator.first.is_visible()
    except Exception:      # noqa: BLE001 —— 元素缺失/页面切换时按「不可见」处理
        return False


@pytest.mark.title("会话重命名与 AI 标题验证")
def test_conv_rename(page: Page, hud, server, js_errors):
    base = server

    # ① test 起始态 → 会话列表渲染
    hud.step("① test 起始态 · 会话列表渲染")
    page.goto(base, wait_until="networkidle")
    expect(page.locator(".rail-user").first).to_be_visible(timeout=T_LAYOUT)
    page.wait_for_selector(".hist-item", timeout=T_HIST)
    page.wait_for_timeout(800)
    assert hud.check("① test 起始态，会话列表渲染", True)

    # ② hover 出操作组
    hud.step("② hover 出现 ✏️/✨/🗑 三个操作钮")
    first_item = page.locator(".hist-item").first
    first_item.hover()
    page.wait_for_timeout(400)
    ren_visible = _is_visible(first_item.locator(".h-ren"))
    ai_visible = _is_visible(first_item.locator(".h-ai"))
    del_visible = _is_visible(first_item.locator(".h-del"))
    assert hud.check(
        f"② hover 出现三个操作钮（ren={ren_visible} ai={ai_visible} del={del_visible}）",
        ren_visible and ai_visible and del_visible,
    )

    # ③ 行内重命名
    hud.step("③ 行内重命名（Enter 提交）")
    title_before = _text(page, ".hist-item .h-name")
    first_item.locator(".h-ren").click()
    page.wait_for_selector(".hist-item .h-edit", timeout=T_EDIT)
    page.fill(".hist-item .h-edit", RENAMED)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    after_rename = _text(page, ".hist-item .h-name")
    assert hud.check(
        f"③ 行内重命名成功（{title_before} → {after_rename}）",
        after_rename == RENAMED,
    )

    # ④ AI 总结标题（选第一个会话，test 账户走平台池真模型）
    hud.step("④ ✨ AI 总结标题")
    first_item2 = page.locator(".hist-item").first
    first_item2.hover()
    title_before_ai = _text(page, ".hist-item .h-name")
    first_item2.locator(".h-ai").click()
    page.wait_for_timeout(6000)     # 等模型返回（平台池实测 ~0.5s，留足余量）
    after_ai = _text(page, ".hist-item .h-name")
    assert hud.check(
        f"④ AI 标题生效（{title_before_ai} → {after_ai}）",
        bool(after_ai) and after_ai != title_before_ai,
    )

    # ⑤ 全程 0 JS 错误
    hud.step("⑤ 全程 0 JS 错误")
    assert hud.check("⑤ 全程 0 JS 错误", not js_errors), f"JS 错误：{js_errors[:3]}"

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {t}" for t, ok in hud.checks))
