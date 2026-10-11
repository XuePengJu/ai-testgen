"""会话行内编辑提交语义（合并两份 JS，各自一个 test_* 函数，互不影响）：

`test_v591_edit_fix`  ← frontend/scripts/e2e-v591-edit-fix.mjs
    V5.9.1 编辑态输入框可见性复测（回归：hover 时输入框被 opacity:0 隐藏的 bug）：
    ① 点击 ✏️ 进入编辑 → input 可见（opacity=1）且 value=原标题 且已聚焦
    ② 悬浮在行内其他位置（hover 保持）→ 输入框文字仍可见
    ③ Enter 提交 → 改名生效
    ④ 0 JS 错误

`test_v592_blur_commit`  ← frontend/scripts/e2e-v592-blur-commit.mjs
    V5.9.2 blur 即提交复测：
    ① 点 ✏️ 编辑 → 点行外空白（侧栏头部）→ 等同回车，改名生效
    ② 点 ✏️ 编辑 → 直接点另一个会话（切换）→ 原会话改名生效
    ③ Esc 仍是取消（不提交）
    ④ 0 JS 错误

与 JS 版差异（**仅起始态**）：两份 JS 各自「访客 → 退出 → test 登录」，Python 版起始态由
tests/ui/conftest.py::_start_state 按模块（chat → test）自动注入，用例内不写登录。

共享态提示：两用例均按 JS 语义操作「第 N 条历史会话」并写入固定名，对 `test` 账户共享；
断言只针对「该条标题被改成期望值 / 保持不变」，不依赖全局列表绝对条数（并发安全）。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_ui

T_LAYOUT = 10_000
T_HIST = 10_000
T_EDIT = 5_000


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（§9.1）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; }",
        selector,
    )


def _title_of(page: Page, idx: int) -> str:
    """读取第 idx 条历史会话标题（等价 JS `titleOf(idx)`）。"""
    return page.evaluate(
        "(i) => { const e = document.querySelectorAll('.hist-item .h-name')[i];"
        " return e ? e.textContent.trim() : ''; }",
        idx,
    )


def _open_hist(page: Page, hud, server, step_name: str):
    """公共前置：进 test 起始态首屏 → 等会话列表渲染（等价两份 JS 的登录后等待段）。"""
    hud.step(step_name)
    page.goto(server, wait_until="networkidle")
    expect(page.locator(".rail-user").first).to_be_visible(timeout=T_LAYOUT)
    page.wait_for_selector(".hist-item", timeout=T_HIST)
    page.wait_for_timeout(800)


# --------------------------------------------------------------------------- V5.9.1
@pytest.mark.title("会话改名 · 编辑框可见与聚焦验证")
def test_v591_edit_fix(page: Page, hud, server, js_errors):
    _open_hist(page, hud, server, "① 进入编辑态 · 输入框可见且带原标题")

    first_item = page.locator(".hist-item").first
    title_before = _text(page, ".hist-item .h-name")
    first_item.hover()
    first_item.locator(".h-ren").click()
    page.wait_for_selector(".hist-item .h-edit", timeout=T_EDIT)
    page.wait_for_timeout(300)

    # ① 编辑态：输入框可见 + 原标题在框内 + 已聚焦（鼠标此时仍 hover 在行上）
    st = page.evaluate(
        """() => {
          const inp = document.querySelector(".hist-item .h-edit");
          if (!inp) return null;
          const cs = getComputedStyle(inp);
          return { opacity: cs.opacity, display: cs.display,
                   value: inp.value, focus: document.activeElement === inp };
        }"""
    )
    assert hud.check(
        f"① 编辑态输入框可见且带原标题「{title_before}」（{st}）",
        bool(st) and st["opacity"] == "1" and st["value"] == title_before and st["focus"],
    )

    # ② hover 移到行内 meta 区域（保持 hover）再检查可见性
    hud.step("② 行内悬浮时输入框仍可见")
    first_item.locator(".h-meta").hover()
    page.wait_for_timeout(200)
    st2 = page.evaluate(
        """() => {
          const inp = document.querySelector(".hist-item .h-edit");
          return inp ? getComputedStyle(inp).opacity : "gone";
        }"""
    )
    assert hud.check(f"② 行内悬浮时输入框仍可见（opacity={st2}）", st2 == "1")

    # ③ 提交改名
    hud.step("③ Enter 提交改名生效")
    new_title = "V591修复验证"
    page.fill(".hist-item .h-edit", new_title)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    after = _text(page, ".hist-item .h-name")
    assert hud.check(f"③ 提交改名生效（{title_before} → {after}）", after == new_title)

    # ④ 0 JS 错误
    hud.step("④ 0 JS 错误")
    assert hud.check("④ 0 JS 错误", not js_errors), f"JS 错误：{js_errors[:3]}"

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {t}" for t, ok in hud.checks))


# --------------------------------------------------------------------------- V5.9.2
@pytest.mark.title("会话改名 · 失焦与切换提交验证")
def test_v592_blur_commit(page: Page, hud, server, js_errors):
    _open_hist(page, hud, server, "① blur 提交（点行外空白）")

    # ① blur 提交：编辑第一个 → 点侧栏头部空白
    t0 = _title_of(page, 0)
    item0 = page.locator(".hist-item").first
    item0.hover()
    item0.locator(".h-ren").click()
    page.wait_for_selector(".hist-item .h-edit", timeout=T_EDIT)
    page.fill(".hist-item .h-edit", "V592点空白确认")
    page.click(".side-head")     # 行外空白（侧栏头部）
    page.wait_for_timeout(1200)
    after1 = _title_of(page, 0)
    assert hud.check(
        f"① 点空白即提交（{t0} → {after1}）",
        after1 == "V592点空白确认",
    )

    # ② 切换会话提交：编辑第二个 → 直接点第三个会话
    hud.step("② 切换会话即提交")
    t1 = _title_of(page, 1)
    item1 = page.locator(".hist-item").nth(1)
    item1.hover()
    item1.locator(".h-ren").click()
    page.wait_for_selector(".hist-item .h-edit", timeout=T_EDIT)
    page.fill(".hist-item .h-edit", "V592切会话确认")
    page.locator(".hist-item").nth(2).click()     # 点别的会话 = 切换
    page.wait_for_timeout(1500)                   # 等 blur 提交 + 会话切换落地
    after2 = _title_of(page, 1)
    assert hud.check(
        f"② 切换会话即提交（{t1} → {after2}）",
        after2 == "V592切会话确认",
    )

    # ③ Esc 仍是取消
    hud.step("③ Esc 取消不提交")
    t2 = _title_of(page, 0)
    item0b = page.locator(".hist-item").first
    item0b.hover()
    item0b.locator(".h-ren").click()
    page.wait_for_selector(".hist-item .h-edit", timeout=T_EDIT)
    page.fill(".hist-item .h-edit", "不该被保存的名字")
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    after3 = _title_of(page, 0)
    assert hud.check(f"③ Esc 取消不提交（标题保持「{after3}」）", after3 == t2)

    # ④ 0 JS 错误
    hud.step("④ 0 JS 错误")
    assert hud.check("④ 0 JS 错误", not js_errors), f"JS 错误：{js_errors[:3]}"

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {t}" for t, ok in hud.checks))
