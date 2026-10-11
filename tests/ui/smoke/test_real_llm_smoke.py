"""真实大模型端到端冒烟（源：V2.8 M5 里程碑验收脚本 e2e-m5-smoke.mjs）：

`test_real_llm_smoke`（对应 e2e-m5-smoke.mjs）：
  ① 模型配置页调度摘要显示「平台模型池接管」（真实 Key 由平台池提供）
  ② 回工作台发消息 → SSE 真实流式回复（非 mock 模板、非失败兜底提示）
  ③ 全程 0 JS 错误

起始态 = **test 账户**（由 tests/ui/conftest.py::_start_state 默认推导，用例内不写登录）。

与 JS 版的差异（D10 起始态规范化）：
- e2e-m5-smoke.mjs 自建临时用户 `e2e_m5_smoke` 并经 UI 登录后验证「注册用户经平台池出真实回复」，
  且用例末尾经 pymysql/sqlite **直连 DB** 整链删号。迁移版改用 **test 账户**起始态（起始态规范化，
  用例不手写登录/注册），故：注册步骤与 DB 直连清理不再需要（未新建用户 → 无残留需清）。
  冒烟核心「平台池 → 真实 LLM 流式回复」语义保持。副作用：会向 test 账户产生 1 条会话消息
  （test 为共享测试账户，可接受）。
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_ui

# 与 JS 版一致的 mock 模板判定：模板文案「好的，关于「<用户原文>」，我先理一下…」
_MOCK_RE = re.compile(r"好的，关于「.+?」，我先理一下")
# 与 JS 版一致的失败兜底判定：回复含「真实模型调用失败」notice
_FAIL_NOTICE = "真实模型调用失败"


@pytest.mark.title("真实大模型问答冒烟验证")
def test_real_llm_smoke(page: Page, hud, server, js_errors):
    base = server

    # ① 模型配置页：调度摘要显示平台池接管
    hud.step("① 模型配置页 · 调度摘要")
    page.goto(base, wait_until="networkidle")
    page.locator('.rail-btn:has-text("模型配置")').click(timeout=10_000)
    expect(page.locator("[data-testid='models-page']")).to_be_visible(timeout=8_000)
    expect(page.locator("[data-testid='sched-head-badge']")).to_be_visible(timeout=8_000)
    head_badge = page.locator("[data-testid='sched-head-badge']").inner_text(timeout=2_000).strip()
    text_tag = page.locator("[data-testid='sched-tag-text']").inner_text(timeout=2_000).strip()
    assert hud.check(
        f"① 调度摘要：{head_badge}（text 槽 {text_tag}）",
        ("平台" in head_badge) or ("平台" in text_tag),
    ), f"{head_badge} / {text_tag}"

    # ② 回工作台发消息 → 真实 LLM 流式回复
    hud.step("② 真实 LLM 流式回复")
    page.locator('.rail-btn:has-text("AI 会话")').click(timeout=10_000)
    expect(page.locator(".chat-panel textarea")).to_be_visible(timeout=8_000)
    page.locator(".chat-panel textarea").fill("用一句话说明等价类划分法在测试中的作用")
    page.locator(".chat-panel .send-btn").click()
    expect(page.locator(".msg.msg-ai").first).to_be_visible(timeout=30_000)
    # 流式完成标志：「✨ 生成测试用例」按钮出现（真实模型可能要 1~2 分钟）
    page.wait_for_selector(".msg-ai .qtag.confirm-btn", timeout=240_000)
    reply = page.evaluate(
        "() => { const els = document.querySelectorAll('.msg.msg-ai');"
        " return els.length ? els[els.length - 1].textContent : ''; }"
    )
    is_mock = _MOCK_RE.search(reply) is not None
    is_fail = _FAIL_NOTICE in reply
    assert hud.check(
        f"② 真实 LLM 流式回复完成（{reply.strip()[:80]}…）",
        len(reply) > 20 and not is_mock and not is_fail,
    ), f"mock={is_mock} fail={is_fail} len={len(reply)} reply={reply.strip()[:120]}"

    # ③ 全程 0 JS 错误
    hud.step("③ 全程 0 JS 错误")
    assert hud.check("③ 全程 0 JS 错误", not js_errors), js_errors[:3]

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
