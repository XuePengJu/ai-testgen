"""业务主线端到端（ERP 问答 → 生成用例 → 查看迭代 → 预览）。

源：用户反馈 —— `tests/ui` 全是按**页面 / 版本**拆的散点用例（用例库、详情、会话重命名…），
缺一条「从零对话 → 出用例 → 迭代 → 导出」的**业务主线**。诊断确认：可跑用例里
「多轮问答 → 点『✨ 生成测试用例』→ 建任务」这一段**完全缺失**——
`tests/ui/smoke/test_real_llm_smoke.py` 只等生成按钮出现却不点击；
`tests/ui/testcase/test_case_detail.py` 从「已有用例」起步；
唯一点过 `.qtag.confirm-btn` 的是 `tests/manual/chat/m2_conversation_flow.py`（未被 pytest 收集）。
本条即补齐该主线，串起 6 个业务阶段（①→⑥）。

为什么标 `slow`（默认门禁不执行）：
  真实大模型全链路，单次实测成本 —— 单轮问答数十秒；**from-scratch 生成用例 ~4–7 min**
  （中位 ~260s，最大实测 401.9s）；迭代补充 36.9–58.0s。整条主线 ≈8–12 min。
  放进默认门禁会把每次 `pytest tests/ui` 拖成十几分钟，故按 `slow` 标记默认排除，
  需要时显式 `-m "slow"` 运行（见「运行方式」）。

与散点用例的边界（**不重复覆盖**）：
  - 用户/密码登录 → 起始态由 `tests/ui/conftest.py::_start_state` 自动注入 **test 账户**
    storage_state（用例内**不写登录**），与其余 ui 用例口径一致；
  - 详情抽屉的细粒度断言（导图布局 fit、搜索过滤、Esc 关闭、TC-* 节点点击）留给
    `test_case_detail.py`；本条只验「主线走通」的关键节点。

起始态 = **test 账户**（`tests/ui/conftest.py::_start_state` 默认，非 auth 目录 → user）。
BASE URL 走 `server` 夹具（单一真源 AITF_BASE_URL）。

slow：默认门禁不执行；按需运行：
  env -u PYTHONPATH .venv/bin/python -m pytest tests/ui/e2e -m "slow" \\
      --video=on --tracing=off --output=reports/e2e-artifacts -p no:cacheprovider -rsxX -s

（`--tracing=off` 是本沙箱硬约束：Playwright trace 收尾的 host-copy 会被文件策略无差别拒绝 →
用例断言全过却被标 teardown ERROR，见 README「本地 WorkBuddy 沙箱环境」。）
"""
from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

pytestmark = [pytest.mark.e2e_ui, pytest.mark.slow, pytest.mark.real_llm]

# mock 模板判定 / 失败兜底判定：与 test_real_llm_smoke.py 同源（真实回复不得命中）
_MOCK_RE = re.compile(r"好的，关于「.+?」，我先理一下")
_FAIL_NOTICE = "真实模型调用失败"

# 超时（真实大模型：单轮问答数十秒；from-scratch 生成 4–7 min；迭代 36.9–58.0s）
T_RAIL = 10_000            # rail / 面板就位
T_REPLY = 240_000          # 单轮问答等「✨ 生成测试用例」按钮出现（= 回复完成）
T_TASK_CARD = 20_000       # 点击生成后任务卡出现
T_GEN_DONE = 600_000       # 用例库里该任务生成完成（并发修复后仍留足裕量，早失败由轮询兜底）
T_ITER_REPLY = 180_000     # 迭代沟通回复 + 「⚡ 生成用例」可点
T_DRAWER = 10_000          # 详情抽屉

# 末轮用户消息：内容围绕 ERP 采购模块，用于「据此设计测试用例」。
# ⚠️ 任务名**不**取自该文本——工作流解析步会用 LLM 生成的 title 覆盖 task.name
# （见 app/workflow/engine.py:70 `task.name = title`，并同步覆盖会话标题），
# 故用例**不按名称定位**，而是按「生成后从会话任务卡拿到的 task_id」精确定位用例库行
# （`data-testid="case-row-<id>"`，见 CaseLibraryPage.tsx），避免与历史同名任务串行。
STEP3_PROMPT = "请据此为 ERP 系统的采购管理模块设计测试用例"


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（元素缺失时同步返回 ''，避免隐式 30s 空等）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? (e.textContent || '').trim() : ''; }",
        selector,
    )


def _last_ai_reply(page: Page) -> str:
    """读取最后一条 AI 气泡全文（含思考面板；mock/失败关键词命中即为异常）。"""
    return page.evaluate(
        "() => { const els = document.querySelectorAll('.msg.msg-ai');"
        " return els.length ? (els[els.length - 1].textContent || '').trim() : ''; }"
    )


def _send_and_wait_reply(page: Page, prompt: str) -> str:
    """发一条消息 → 等真实 LLM 回复完成 → 返回该条回复全文。

    两步确保等到的是**本轮**回复（避免历史气泡干扰）：
      1) 记录发送前 `.msg.msg-ai` 数量，等数量 +1 —— 确认本轮的 AI 气泡已挂载；
      2) 等**最后一条** AI 气泡内部出现 `.qtag.confirm-btn` —— 该按钮仅在
         `state==='done' && !task && draft` 时渲染（见 MessageView.tsx），
         即「本条回复已收尾且可生成」，是本轮完成的锚点（不用全局计数，规避竞态）。
    """
    n_before = page.locator(".msg.msg-ai").count()
    page.locator(".chat-panel textarea").fill(prompt)
    page.locator(".chat-panel .send-btn").click()
    page.wait_for_function(
        "(n) => document.querySelectorAll('.msg.msg-ai').length > n",
        arg=n_before,
        timeout=30_000,
    )
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('.msg.msg-ai');"
        " return !!els.length && !!els[els.length - 1].querySelector('.qtag.confirm-btn'); }",
        timeout=T_REPLY,
    )
    return _last_ai_reply(page)


def _last_task_card_id(page: Page) -> str:
    """会话流里最后一张任务卡的 `data-task-card`（创建/迭代后即该任务 id）。"""
    return page.evaluate(
        "() => { const els = document.querySelectorAll('.task-steps-card[data-task-card]');"
        " return els.length ? els[els.length - 1].getAttribute('data-task-card') : ''; }"
    )


def _card_state(page: Page, task_id: str) -> tuple[str, str]:
    """读会话任务卡的状态徽章与完成态用例数：(badge_text, cnt_text)。

    徽章 = TaskStepsCard.statusBadge 渲染的 `.pill.pill-<cls>`（✓ 已完成 / ⏳ 生成中 /
    ✗ 失败 / 排队中）。**判定只看徽章**：卡片正文会渲染生成中的用例内容（预期里就有
    「保存失败」等字样），整卡文本匹配「失败」必误报（2026-10-11 实测翻车）。
    """
    badge, cnt = page.evaluate(
        """(tid) => {
            const c = document.querySelector('[data-task-card="' + tid + '"]');
            if (!c) return ['', ''];
            const b = c.querySelector('.pill');
            const n = c.querySelector('.tsc-cnt');
            return [b ? (b.textContent || '').trim() : '',
                    n ? (n.textContent || '').trim() : ''];
        }""",
        task_id,
    )
    return badge or "", cnt or ""


def _await_card_done(page: Page, task_id: str, timeout_ms: int) -> str:
    """**留在会话内**等该任务卡生成完成（早失败语义），返回完成态用例数文案（如「共 91 个用例」）。

    每 2s 轮询状态徽章：`✗ 失败` → 立刻 AssertionError；`✓ 已完成` → 返回；
    其余（⏳ 生成中 / 排队中）继续等；超时带最后徽章报错。
    """
    page.wait_for_selector(f'[data-task-card="{task_id}"]', timeout=20_000)
    deadline = time.time() + timeout_ms / 1000.0
    badge = ""
    while True:
        badge, cnt = _card_state(page, task_id)
        if "失败" in badge:
            raise AssertionError(f"任务生成失败（任务卡徽章：{badge!r}）")
        if "已完成" in badge:
            return cnt
        if time.time() >= deadline:
            raise AssertionError(f"任务卡未在 {timeout_ms / 1000:.0f}s 内完成（徽章：{badge!r}）")
        time.sleep(2)


def _open_card_drawer(page: Page, task_id: str, attempts: int = 3) -> bool:
    """点任务卡上的「查看用例 / 思维导图 →」打开详情抽屉（全程留在会话，不切用例库）。

    轮询重渲染时点击可能被吞 → 校验抽屉**持续可见**（原用例库行打开抽屉的稳定思路，同为开抽屉语义）。
    """
    card = page.locator(f'[data-task-card="{task_id}"]')
    for _ in range(attempts):
        card.locator(".tsc-actions .qtag.confirm-btn").click()
        try:
            page.wait_for_selector(".task-drawer.show", timeout=8_000)
            page.wait_for_timeout(2_500)
            if page.locator(".task-drawer.show").count() > 0:
                return True
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(800)
    return False


def _close_drawer(page: Page) -> None:
    """Esc 关闭详情抽屉（回到会话，露出任务卡以便后续「继续优化」/开迭代版）。"""
    page.keyboard.press("Escape")
    page.wait_for_selector(".task-drawer.show", state="hidden", timeout=5_000)


@pytest.mark.title("业务主线：ERP 问答 → 生成用例 → 查看迭代 → 预览")
def test_business_main_flow(page: Page, hud, server, js_errors):
    """①→⑥ 业务主线；每一步经 `hud.check` 逐条记录，末尾统一汇总失败项。"""
    base = server

    # ① 新建会话 · 问「ERP 是什么」
    hud.step("① 新建会话 · 问「ERP 是什么」")
    page.goto(base, wait_until="networkidle")
    expect(page.locator(".rail-user").first).to_be_visible(timeout=T_RAIL)
    page.locator('.rail-btn:has-text("AI 会话")').click(timeout=T_RAIL)
    expect(page.locator(".conv-panel").first).to_be_visible(timeout=T_RAIL)
    expect(page.locator(".chat-panel textarea")).to_be_visible(timeout=T_RAIL)
    new_conv = page.locator('.conv-panel .side-head button:has-text("新对话")')
    if new_conv.count():
        new_conv.first.click()
        page.wait_for_selector(".chat-panel .welcome", timeout=T_RAIL)
    reply1 = _send_and_wait_reply(page, "用几句话介绍一下 ERP 系统主要是做什么的")
    is_mock1 = _MOCK_RE.search(reply1) is not None
    assert hud.check(
        f"① 首轮真实回复完成（{reply1.strip()[:60]}…）",
        len(reply1) > 20 and not is_mock1 and _FAIL_NOTICE not in reply1,
    ), f"mock={is_mock1} fail={_FAIL_NOTICE in reply1} len={len(reply1)} reply={reply1[:120]}"

    # ② 追问「采购管理模块」
    hud.step("② 追问「采购管理模块」")
    reply2 = _send_and_wait_reply(page, "它的采购管理模块通常包含哪些功能")
    is_mock2 = _MOCK_RE.search(reply2) is not None
    assert hud.check(
        f"② 追问回复完成（{reply2.strip()[:60]}…）",
        len(reply2) > 20 and not is_mock2 and _FAIL_NOTICE not in reply2,
    ), f"mock={is_mock2} fail={_FAIL_NOTICE in reply2} len={len(reply2)} reply={reply2[:120]}"

    # ③ 让它设计测试用例
    hud.step("③ 让它设计测试用例")
    reply3 = _send_and_wait_reply(page, STEP3_PROMPT)
    is_mock3 = _MOCK_RE.search(reply3) is not None
    assert hud.check(
        f"③ 设计用例回复完成 · 生成按钮就绪（{reply3.strip()[:60]}…）",
        len(reply3) > 20 and not is_mock3 and _FAIL_NOTICE not in reply3,
    ), f"mock={is_mock3} fail={_FAIL_NOTICE in reply3} len={len(reply3)} reply={reply3[:120]}"

    # ④ 点击生成 · 任务创建
    hud.step("④ 点击生成 · 任务创建")
    # 点「最后一条 AI 气泡」内的生成按钮 —— 即第③轮回复，避免点到历史气泡（含任务卡的
    # 「查看用例 / 思维导图 →」按钮同为 .qtag.confirm-btn）而把任务挂到错误的消息上。
    page.locator(".msg.msg-ai").last.locator(".qtag.confirm-btn").click()
    expect(page.locator(".task-steps-card[data-task-card]").first).to_be_visible(timeout=T_TASK_CARD)
    task_id = _last_task_card_id(page)
    assert hud.check(f"④ 任务卡出现，任务 id 已获取（{task_id}）", bool(task_id)), task_id

    # ⑤ 会话内查看用例（全程不切页：任务卡「查看用例」→ 详情抽屉；用例库链路另出独立用例）
    hud.step("⑤ 会话内等生成完成 → 详情抽屉（思维导图 / 用例列表）")
    cnt1 = _await_card_done(page, task_id, T_GEN_DONE)
    m1 = re.search(r"共 (\d+) 个用例", cnt1)
    n1 = int(m1.group(1)) if m1 else 0
    assert hud.check(
        f"⑤ 会话任务卡生成完成（{cnt1 or '已完成'}，全程留在会话看进度）",
        n1 >= 1,
    ), f"cnt={cnt1!r}"
    assert hud.check("⑤ 任务卡「查看用例」打开详情抽屉", _open_card_drawer(page, task_id))
    expect(page.locator(".drawer-head .drawer-title").first).to_be_visible(timeout=T_DRAWER)
    def_tab = _text(page, ".dtab.active")
    assert hud.check(f"⑤ 默认 Tab 为思维导图（{def_tab}）", "思维导图" in (def_tab or ""))
    page.wait_for_selector(".map-container .me-tpc", timeout=15_000)
    node_count = page.locator(".map-container .me-tpc").count()
    # 补实阈值：76 条用例实测 711 个导图节点（≈9 节点/条），历史最小产出 16 条 ≈150 节点
    # → 门槛从 3 提到 10（导图只画个位数节点说明生成内容残缺）
    assert hud.check(f"⑤ 思维导图渲染 {node_count} 个节点", node_count >= 10), node_count
    page.locator(".dtab").filter(has_text=re.compile("用例列表")).first.click()
    page.wait_for_selector(".case-table tbody tr", timeout=15_000)
    case_count = page.locator(".case-table tbody tr").count()
    # 补实阈值：真实模型历史最小产出 16 条（V1.3 实测），<5 条说明生成内容残缺
    assert hud.check(
        f"⑤ 用例列表 {case_count} 行（任务卡口径 {n1} 条）",
        case_count >= 5 and (not n1 or case_count <= n1),
    ), f"rows={case_count} card={n1}"
    _close_drawer(page)

    # ⑥ 迭代补充用例（会话内闭环：任务卡「💬 继续优化」直接挂 chip，无需先开抽屉）
    hud.step("⑥ 迭代补充用例")
    page.locator(f'[data-task-card="{task_id}"] .tsc-actions .qtag').filter(
        has_text="继续优化"
    ).click()
    page.wait_for_selector(".iter-ref-chip", timeout=T_RAIL)
    # 补实断言（原为恒真占位）：chip 文案必须是「基于《<任务名>》迭代」，且
    # chat-input-wrap 挂上 iter-mode —— 证明本次发送走迭代模式而非新建任务
    # （chip 文案 = `基于《{iterTaskName}》迭代`，iterTaskName=task.name，与任务卡标题同源）。
    chip_text = _text(page, ".iter-ref-chip .irc-text")
    iter_mode = page.evaluate(
        "() => { const w = document.querySelector('.chat-input-wrap');"
        " return !!w && w.classList.contains('iter-mode'); }"
    )
    chip_ok = "基于《" in chip_text and "》迭代" in chip_text and iter_mode
    assert hud.check(
        f"⑥ 任务卡「继续优化」挂上迭代引用 chip（{chip_text}）· iter-mode 已挂",
        chip_ok,
    ), f"chip={chip_text!r} iter_mode={iter_mode}"
    before_ai = page.locator(".msg.msg-ai").count()
    page.locator(".chat-panel textarea").fill("补充：增加一个库存不足的异常场景用例")
    page.locator(".chat-panel .send-btn").click()
    page.wait_for_function(
        "(n) => document.querySelectorAll('.msg.msg-ai').length > n",
        arg=before_ai,
        timeout=30_000,
    )
    page.wait_for_function(
        "() => { const b = document.querySelector('.irc-gen');"
        " const s = document.querySelector('.send-btn');"
        " return !!b && !b.disabled && !(s && s.classList.contains('streaming')); }",
        timeout=T_ITER_REPLY,
    )
    # 补实断言（原为恒真占位）：① 迭代回复本身是真实 LLM 内容（非 mock / 非失败兜底）；
    # ② 发送后 iterNotes 累积 → chip hint 必须从「先沟通补充方向」变为「已记录 N 条补充要求」。
    reply4 = _last_ai_reply(page)
    hint = _text(page, ".iter-ref-chip .irc-hint")
    iter_reply_ok = (
        len(reply4) > 20
        and _MOCK_RE.search(reply4) is None
        and _FAIL_NOTICE not in reply4
        and "已记录" in hint
    )
    assert hud.check(
        f"⑥ 迭代沟通回复完成 · 补充要求已记录（{hint}）·「⚡ 生成用例」可点",
        iter_reply_ok,
    ), f"reply_len={len(reply4)} mock={bool(_MOCK_RE.search(reply4))} hint={hint!r} reply={reply4[:120]}"
    page.locator(".irc-gen").click()
    page.wait_for_function(
        """(pid) => {
            const els = [...document.querySelectorAll('.task-steps-card[data-task-card]')];
            return els.some((e) => e.getAttribute('data-task-card') !== pid);
        }""",
        arg=task_id,
        timeout=30_000,
    )
    child_id = _last_task_card_id(page)
    # 迭代生成同样全程留在会话看任务卡进度
    cnt2 = _await_card_done(page, child_id, T_GEN_DONE)
    m2 = re.search(r"共 (\d+) 个用例", cnt2)
    n2 = int(m2.group(1)) if m2 else 0
    # 迭代成功标志（会话内口径）：父任务卡被标记「已被新版本取代」（版本链推进），
    # 且 iterate 是「合并旧用例再补充」→ 子版用例数 ≥ 父版
    parent_stale = page.evaluate(
        "(tid) => { const c = document.querySelector('[data-task-card=\"' + tid + '\"]');"
        " return !!c && (c.textContent || '').includes('已被新版本取代'); }",
        task_id,
    )
    child_ok = n2 >= 1 and n2 >= n1 and parent_stale
    assert hud.check(
        f"⑥ 迭代版任务卡完成（{cnt2 or '已完成'} ≥ 父版 {n1} 条）· 父卡已被新版本取代",
        child_ok,
    ), f"n1={n1} n2={n2} parent_stale={parent_stale}"

    # ⑦ 预览 / 导出（迭代版，仍全程在会话内）
    hud.step("⑦ 预览 / 导出")
    assert hud.check("⑦ 迭代版任务卡打开详情抽屉", _open_card_drawer(page, child_id))
    page.locator(".dtab").filter(has_text=re.compile("导出")).first.click()
    page.wait_for_selector(".export-item", timeout=8_000)
    ex_names = page.eval_on_selector_all(
        ".export-item .ex-name", "els => els.map(e => e.textContent || '')"
    )
    avail_fmts = [m.group(1).lower() for n in ex_names for m in [re.search(r"\.([a-zA-Z]+)$", n)] if m]
    # 补实断言：导出 Tab 必须三项齐全（xlsx / json / xmind），而非「有任意一项就行」
    need_fmts = {"xlsx", "json", "xmind"}
    assert hud.check(
        f"⑦ 导出 Tab：{len(ex_names)} 个下载项（{' / '.join(avail_fmts)}），xlsx/json/xmind 齐全",
        set(avail_fmts) >= need_fmts,
    ), f"missing={need_fmts - set(avail_fmts)}"
    dl = page.evaluate(
        """async (fmts) => {
            const taskId = document.querySelector('.task-drawer')?.getAttribute('data-task-id') || '';
            const token = (localStorage.getItem('aitf_token') || '').replace(/^"|"$/g, '');
            const out = {};
            for (const fmt of fmts) {
                try {
                    const r = await fetch(`/api/tasks/${taskId}/download?fmt=${fmt}`,
                        { headers: { Authorization: 'Bearer ' + token } });
                    let okRes = r.ok; let len = 0;
                    if (okRes) { const b = await r.blob(); len = b.size; okRes = len > 50; }
                    out[fmt] = { status: r.status, ok: okRes, len };
                } catch (e) { out[fmt] = { error: String(e) }; }
            }
            return out;
        }""",
        avail_fmts,
    )
    for fmt, v in (dl or {}).items():
        assert hud.check(f"⑦ 下载 {fmt}：HTTP {v.get('status')}，{v.get('len')} 字节", bool(v.get("ok")))

    # ⑧ 全程 0 JS 错误
    hud.step("⑧ 全程 0 JS 错误")
    assert hud.check("⑧ 全程 0 JS 错误", not js_errors), js_errors[:3]

    # 逐条落 stdout（配合 -s 可读，便于 CI / 回放留痕）
    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))

    failed = [text for text, ok in hud.checks if not ok]
    assert not failed, "业务主线未通过步骤：\n" + "\n".join(f"  ✘ {t}" for t in failed)
