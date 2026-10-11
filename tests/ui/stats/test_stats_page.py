"""V5.12 使用统计页（自 frontend/scripts/e2e-v512-stats.mjs 迁移，逐条对齐）：

① admin 起始态（自动注入）+ 主页渲染正常（rail 改动未破坏导航）
② 使用统计入口（admin-only）→ 统计页打开
③ 今日 / 累计卡片区（各 5 张）
④ 趋势图（echarts canvas ≥ 2）
⑤ 最近 LLM 调用明细表

起始态 = **admin**（`@pytest.mark.admin`，由 conftest::_start_state 注入 admin storage_state，用例内不写登录）。

⚠️ **与 team-lead 口径的偏差说明**：任务简报写「stats 模块默认 = test 账户」，但 JS 版原脚本用
admin 登录，且统计页为 **admin-only**（`App.tsx` 中 `role !== "admin"` 会强制回主页、rail 入口
`button[aria-label="使用统计（仅管理员）"]` 仅 admin 可见）。若以 test 账户起步，第②步入对不可达
→ 无法对齐 JS。故此处用 `@pytest.mark.admin`（符合 D10「后台/管理类用例」规则，且**未手写登录**）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e_ui


@pytest.mark.admin
@pytest.mark.title("使用统计页验证（卡片 / 趋势图 / 明细）")
def test_stats_page(page, hud, server):
    """admin 进入使用统计页，核对卡片 / 图表 / 明细表。"""
    base = server

    # ① admin 起始态 + 主页渲染
    hud.step("① admin 起始态 · 主页")
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    role = page.evaluate(
        "() => { const e = document.querySelector('.rail-user .rl-user-role'); return e ? e.textContent.trim() : ''; }"
    )
    assert hud.check(f"admin 起始态（rail 显示 {role}）", role == "管理员"), role
    assert hud.check("主页渲染（rail 导航按钮存在）", page.locator(".rail-btn").count() > 0)

    # ② 使用统计入口（admin-only）→ 统计页打开
    hud.step("② 使用统计页")
    page.click('button[aria-label="使用统计（仅管理员）"]')
    page.locator('[data-testid="stats-page"]').wait_for(timeout=5_000)
    page.wait_for_timeout(2000)  # 等 echarts 渲染
    assert hud.check("统计页打开", page.locator('[data-testid="stats-page"]').count() == 1)
    # 原 JS 即为恒真对照（用户管理入口 count >= 0），此处保持等价语义
    assert hud.check(
        "非 admin 不可见入口对照（用户管理入口存在性查询）",
        page.locator('button[aria-label="用户管理（仅管理员）"]').count() >= 0,
    )

    # ③ 今日 / 累计卡片区
    hud.step("③ 卡片区")
    today_cards = page.locator('[data-testid="stats-today"] .stat-card').count()
    total_cards = page.locator('[data-testid="stats-total"] .stat-card').count()
    assert hud.check(f"今日卡片 {today_cards}/5", today_cards == 5)
    assert hud.check(f"累计卡片 {total_cards}/5", total_cards == 5)

    # ④ 趋势图（echarts canvas）
    hud.step("④ 趋势图")
    charts = page.locator('[data-testid="stats-page"] canvas').count()
    assert hud.check(f"echarts 图表 {charts} 个", charts >= 2)

    # ⑤ 最近 LLM 调用明细表
    hud.step("⑤ LLM 调用明细表")
    table = page.locator('[data-testid="stats-llm-recent"] table').count()
    assert hud.check("LLM 调用明细表存在", table == 1)

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
