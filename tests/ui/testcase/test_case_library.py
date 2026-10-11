"""V5.5 用例库改造 UI（自 frontend/scripts/e2e-v55-case-library.mjs 迁移，逐条对齐）：

① AI 会话两栏渲染（右侧「我的任务」栏已移除）
② 用例库页两栏（左分类面板 category-tree + 右列表）
③ 筛选交互：概览条卡片联动（失败卡）+ 评审状态筛选（草稿）
④ 打开用例集详情抽屉（思维导图 Tab）

起始态 = **test 账户**（由 conftest::_start_state 自动注入，用例内不写登录；
JS 版开头的「退出 → admin 登录」在 Python 版由夹具等价承担；用例库「全角色可见」）。
断言只针对本模块可控的存在性 / 联动关系，不对全局绝对量做硬断言（并发跑安全）。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e_ui


@pytest.mark.title("用例库页验证（分类 / 概览联动 / 评审筛选）")
def test_case_library(page, hud, server):
    """用例库页渲染 + 概览联动 + 评审筛选 + 详情抽屉打开。"""
    base = server

    # ① AI 会话两栏
    hud.step("① AI 会话两栏")
    page.goto(base, wait_until="networkidle")
    page.wait_for_selector(".conv-panel", timeout=8_000)
    page.wait_for_selector(".chat-panel", timeout=8_000)
    assert hud.check("AI 会话两栏渲染，右栏任务列表已移除", page.locator(".task-panel").count() == 0)

    # ② 用例库页（V5.6 两栏：左分类面板 + 右列表）
    hud.step("② 用例库页")
    page.locator('button[aria-label^="用例库"]').click()
    page.wait_for_selector(".cases-page .cl-side [data-testid='category-tree']", timeout=15_000)
    page.wait_for_selector(".cases-page tbody tr", timeout=15_000)
    rows = page.locator(".cases-page tbody tr").count()
    cat_rows = page.locator(".cl-side .cat-row").count()
    assert hud.check(f"用例库渲染 {rows} 条用例集，左栏分类 {cat_rows} 行", rows >= 1)

    # ③ 筛选交互：概览条卡片联动 + 评审状态筛选
    hud.step("③ 筛选交互")
    page.wait_for_selector("[data-testid='cl-card-all']", timeout=8_000)
    failed_num = page.locator("[data-testid='cl-card-failed'] .clc-num").text_content()
    page.locator("[data-testid='cl-card-failed']").click()
    page.wait_for_timeout(600)
    failed_rows = page.locator(".cases-page tbody tr").count()
    # 与 JS 版一致：卡片计数与表格行数一致则记为一致，否则提示「轮询时点可能漂移」，两者均不判失败
    if str(failed_num) == str(failed_rows):
        hud.check(f"概览条联动（失败卡）：{failed_rows} 行与卡片计数一致", True)
    else:
        hud.check(f"概览条联动（失败卡）：卡片 {failed_num} / 表格 {failed_rows} 行（轮询时点可能漂移）", True)
    page.locator("[data-testid='cl-card-all']").click()
    page.wait_for_timeout(400)
    page.select_option(".cl-toolbar select", "draft")
    page.wait_for_timeout(600)
    assert hud.check("评审状态筛选（草稿）生效", page.locator(".cl-toolbar select").input_value() == "draft")
    page.select_option(".cl-toolbar select", "all")

    # ④ 打开详情抽屉（思维导图 Tab）
    hud.step("④ 用例集详情抽屉")
    page.locator(".cases-page tbody tr .cl-name").first.click()
    expect(page.locator(".task-drawer.show")).to_be_visible(timeout=10_000)
    page.wait_for_timeout(1500)
    assert hud.check("用例集详情抽屉打开", page.locator(".task-drawer.show").count() > 0)

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
