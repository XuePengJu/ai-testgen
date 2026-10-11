"""M2 会话主链路浏览器端到端（自 frontend/scripts/e2e-m2-browser.mjs 迁移，步骤逐条对齐）：

① 起始态 = test 账户 → 两栏布局渲染（历史会话 | 对话流；V5.5 右栏任务列表已移除）
①b 新建会话 → 空白欢迎页
①c 记录用例库基线行数（V5.5：新用例集入库断言用）
② 发消息 → AI 流式回复 → 出现「✨ 生成测试用例」按钮
③ 点击生成 → 任务创建 → 消息升级任务卡 → 轮询到「✓ 已完成」
④ 用例库出现新用例集 → 点击打开详情抽屉
⑤ 新建会话 → 切回旧会话回放历史消息
⑥ 全程 0 JS 错误

与 JS 版差异（**仅起始态**）：JS 版自带「访客 → 退出 → test 登录 + reload 清残留」，
Python 版起始态由 tests/ui/conftest.py::_start_state 按模块（chat → test）自动注入
storage_state，用例内不写登录、不写 reload（无访客残留）。

⚠️ 基线对齐（S0 实测，2026-10-10）：JS 版 m2 在步骤③「AI 流式 / 任务轮询到已完成」环节的
   `waitForFunction(... '已完成' ..., 180000)` **180s 超时**（判为**非通过**）。
   本 Python 版若在**同一位置/同一原因**失败，属**基线对齐失败**（非迁移 bug）——照实报告，
   文件内保留原 180s 超时与断言，**不绕过、不改 app**。

定位说明：单一选择器操作用 `page.click/fill`（非 strict，等价 JS `page.click`）；互斥多分支
选择器（任务卡三形态）用 `.first` 显式取首个，避免 strict 冲突（§9.1）。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_ui

# 与 JS 版一致的超时（照搬原数字，避免 §9.1 隐式 30s 空等）
T_LAYOUT = 10_000
T_PANEL = 8_000
T_AI_MSG = 15_000
T_CONFIRM = 60_000
T_TASK_CARD = 20_000
T_TASK_DONE = 180_000     # test 账户走平台池真实 workflow，比 mock 慢（S0 实测此步超时）
T_LIB_ROWS = 30_000
T_DRAWER = 10_000
T_REPLAY = 8_000

PROMPT = "测试一个登录页面：输入正确的用户名和密码后跳转首页"
# 任务卡三形态：mock 链路 `.task` / `.tsc-io`，平台池真实链路 `.task-steps-card`
TASK_CARD_SEL = ".msg-ai .task, .msg-ai .tsc-io, .msg-ai .task-steps-card"


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（§9.1：元素缺失时同步返回 ''，不隐式空等）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; }",
        selector,
    )


@pytest.mark.xfail(
    reason=(
        "已留档的基线缺陷（2026-10-10 团队裁定「接受并留档」）：步骤②③ 等 AI 流式回复后的"
        "生成确认按钮 `.qtag.confirm-btn`，`wait_for_function` 180s 超时——本环境无真实平台池 "
        "LLM/workflow。JS 原脚本 frontend/scripts/e2e-m2-browser.mjs 在**同一位置、同一 180s "
        "超时**失败（见 docs/S0-基线采样-2026-10-10.md §4），非本次测试迁移引入。"
        "strict=False：链路修好后会报 XPASS，届时删掉本标记。"
    ),
    strict=False,
)
def test_m2_conversation_flow(page: Page, hud, server, js_errors):
    """完整 5+1 步链路；任一 `hud.check(...)` 为 False 即断言失败（HUD 同步记录该步）。"""
    base = server
    page.on("dialog", lambda d: d.accept())     # 删除任务走 window.confirm，统一接受（与 JS 一致）

    # ① 起始态 test 账户 + 两栏布局
    hud.step("① 起始态 test 账户 · 两栏布局")
    page.goto(base, wait_until="networkidle")
    expect(page.locator(".rail-user").first).to_be_visible(timeout=T_LAYOUT)
    expect(page.locator(".conv-panel").first).to_be_visible(timeout=T_PANEL)
    expect(page.locator(".chat-panel").first).to_be_visible(timeout=T_PANEL)
    ident = {
        "name": _text(page, ".rail-user .rl-user-name"),
        "role": _text(page, ".rail-user .rl-user-role"),
    }
    assert hud.check(
        f"① 两栏布局渲染（历史会话 | 对话流）+ test 起始态（{ident['name']} · {ident['role']}）",
        ident["role"] == "用户",
    ), ident

    # ①b 新建会话 → 空白欢迎页
    hud.step("①b 新建会话")
    page.click(".conv-panel .side-head .qtag")
    expect(page.locator(".chat-panel .welcome").first).to_be_visible(timeout=T_PANEL)
    assert hud.check("①b 新建会话，空白欢迎页", True)

    # ①c 记录用例库基线行数（后续 ④ 用「比基线多」判定，不写死绝对量）
    hud.step("①c 用例库基线")
    page.click('button[aria-label^="用例库"]')
    expect(page.locator(".cases-page").first).to_be_visible(timeout=T_PANEL)
    lib_rows_before = page.locator(".cases-page tbody tr").count()
    assert hud.check(f"①c 用例库基线 {lib_rows_before} 条", True)
    page.click('button[aria-label^="AI 会话"]')
    expect(page.locator(".chat-panel .welcome").first).to_be_visible(timeout=T_PANEL)

    # ② 发消息 → 流式回复 → 生成按钮
    hud.step("② 发消息 → AI 流式回复 → 生成按钮")
    page.fill(".chat-panel textarea", PROMPT)
    page.click(".chat-panel .send-btn")
    expect(page.locator(".msg.msg-ai").first).to_be_visible(timeout=T_AI_MSG)
    assert hud.check("② 发送消息，AI 回复开始流式输出", True)
    expect(page.locator(".qtag.confirm-btn").first).to_be_visible(timeout=T_CONFIRM)
    assert hud.check("② 回复完成，出现「✨ 生成测试用例」按钮", True)

    # ③ 点击生成 → 任务卡 → 轮询完成
    hud.step("③ 生成 → 任务卡 → 轮询完成")
    page.click(".qtag.confirm-btn")
    expect(page.locator(TASK_CARD_SEL).first).to_be_visible(timeout=T_TASK_CARD)
    assert hud.check("③ 任务创建，消息升级为任务卡", True)
    # ⚠️ 基线对齐点：S0 实测 JS 版在此 180s 超时（见文件头 ⚠️）——保留原超时与断言
    page.wait_for_function(
        '() => (document.querySelector(".msg-ai")?.textContent || "").includes("已完成")',
        timeout=T_TASK_DONE,
    )
    assert hud.check("③ 任务轮询到「✓ 已完成」", True)

    # ④ 用例库出现新用例集 → 打开详情抽屉
    hud.step("④ 用例库新增用例集 → 详情抽屉")
    page.click('button[aria-label^="用例库"]')
    expect(page.locator(".cases-page").first).to_be_visible(timeout=T_PANEL)
    page.wait_for_function(
        '(n) => document.querySelectorAll(".cases-page tbody tr").length > n',
        arg=lib_rows_before,
        timeout=T_LIB_ROWS,
    )
    assert hud.check("④ 用例库出现新生成的用例集", True)
    page.locator(".cases-page tbody tr").first.locator(".cl-name").click()
    expect(page.locator(".drawer-head .drawer-title").first).to_be_visible(timeout=T_DRAWER)
    assert hud.check("④ 点击用例集打开详情抽屉", True)
    page.keyboard.press("Escape")      # 关抽屉，避免遮挡后续步骤

    # ⑤ 回 AI 会话 → 新建会话 → 切回旧会话回放历史
    hud.step("⑤ 新建会话 → 切回旧会话回放历史")
    page.click('button[aria-label^="AI 会话"]')
    expect(page.locator(".conv-panel").first).to_be_visible(timeout=T_PANEL)
    page.click(".conv-panel .side-head .qtag")
    expect(page.locator(".chat-panel .welcome").first).to_be_visible(timeout=T_PANEL)
    assert hud.check("⑤ 新建会话回到空白欢迎页", True)
    page.locator(".conv-panel .hist-item").first.click()
    expect(page.locator(".msg.msg-user").first).to_be_visible(timeout=T_REPLAY)
    msg_count = page.locator(".msg").count()
    assert hud.check(f"⑤ 切回旧会话，历史消息回放（{msg_count} 条）", msg_count >= 2)

    # ⑥ 全程 0 JS 错误
    hud.step("⑥ 全程 0 JS 错误")
    assert hud.check("⑥ 全程 0 JS 错误", not js_errors), f"JS 错误：{js_errors[:3]}"

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {t}" for t, ok in hud.checks))
