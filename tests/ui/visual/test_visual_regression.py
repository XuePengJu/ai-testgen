"""W2 视觉基线 / 截图（自 `frontend/scripts/e2e-w2-visual.mjs` 迁移）：

原 JS 脚本 = 登录 admin → 知识库页截图（M6 文档 Tab / M7 Wiki Tab）→ 回 AI 会话截图。
迁就 pytest 体系后：
- `pytestmark = [e2e_ui, visual]`；带 `visual` 标记时 conftest 自动**不注入 HUD、不录屏**
  （见 tests/ui/conftest.py `_hud_enabled` / `_video_wanted`），用例内直接断言 HUD 不存在。
- 起始态 = **admin**：原 JS 脚本显式 admin 登录（需要一条含文档的知识库来渲染 M6/M7 元素），
  按 D10 起始态规范化改由 `@pytest.mark.admin` 注入，用例内不手写登录。
- 用途是**视觉基线 / 截图留档**，断言保持轻量（只校验页面已渲染出目标区域、截图落盘非空），
  不做像素级回归。截图写入 pytest-playwright 的 `output_path`（受 `--output`/`AITF_E2E_ARTIFACTS` 控制）。

⚠️ §9.1：所有「可能缺失」的定位都用显式 `timeout=` 的 `expect`，不用默认 30s 隐式空等。
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e_ui, pytest.mark.visual]

ADMIN_ROLE = "管理员"


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（元素缺失返回 ''，不触发隐式超时）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; }",
        selector,
    )


def _shot(page: Page, out_dir: str, name: str) -> str:
    """截图落盘到 output_path 目录（视觉基线产物），返回绝对路径供后续校验。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    page.screenshot(path=path, full_page=False)
    return path


@pytest.mark.admin
@pytest.mark.title("页面视觉基线截图（知识库 / 会话页）")
def test_w2_visual_baseline(page: Page, server, output_path):
    base = server
    shots: list[str] = []

    # 起始态 = admin（D10）：rail 底部应显示「管理员」
    page.goto(base, wait_until="domcontentloaded")
    expect(page.locator(".rail-user")).to_be_visible(timeout=15_000)
    role = _text(page, ".rail-user .rl-user-role")
    assert ADMIN_ROLE in role, f"起始态应为 admin，实际 rail 角色 = {role!r}"

    # 验收点：@visual 用例不注入 HUD（不叠浮层，保证截图纯净）
    assert page.evaluate("() => !!document.getElementById('__hud')") is False, \
        "visual 用例不应注入 HUD（conftest _hud_enabled 应因 mark.visual 返回 False）"

    # 知识库页 · 文档 Tab（M6：库头统计 + 拖拽上传区 + 文档轻行）
    page.locator('.rail-btn:has-text("知识库")').click(timeout=10_000)
    expect(page.locator('[class*="kb-lib-head"]').first).to_be_visible(timeout=10_000)
    dropzone = page.locator('[class*="dropzone"]').count()
    doc_rows = page.locator('[class*="doc-row"]').count()
    lib_head = page.locator('[class*="kb-lib-head"]').count()
    shots.append(_shot(page, output_path, "kb-docs-tab.png"))
    assert dropzone > 0, "M6 拖拽上传区应存在（[class*=dropzone]）"
    assert doc_rows > 0, f"M6 文档轻行应渲染（实际 {doc_rows} 行）"
    assert lib_head > 0, "M6 库头统计条应存在（[class*=kb-lib-head]）"

    # Wiki Tab（M7：筛选 chips）
    page.locator('button:has-text("Wiki")').first.click(timeout=10_000)
    expect(page.locator('[class*="wiki-chip"]').first).to_be_visible(timeout=10_000)
    chips = page.locator('[class*="wiki-chip"]').count()
    shots.append(_shot(page, output_path, "kb-wiki-tab.png"))
    assert chips > 0, f"M7 筛选 chips 应渲染（实际 {chips} 个）"

    # 回 AI 会话页（整体视图）
    page.locator('.rail-btn:has-text("AI 会话")').click(timeout=10_000)
    expect(page.locator(".chat-panel textarea")).to_be_visible(timeout=10_000)
    shots.append(_shot(page, output_path, "chat-main.png"))

    # 截图落盘（视觉基线产物存在且非空）
    for p in shots:
        assert os.path.isfile(p) and os.path.getsize(p) > 0, f"截图未落盘或为空：{p}"

    print("\n视觉基线截图：" + "、".join(os.path.basename(p) for p in shots))
