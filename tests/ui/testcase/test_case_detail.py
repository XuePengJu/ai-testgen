"""用例详情端到端（源：V2.8 M3 里程碑验收脚本 e2e-m3-browser.mjs，逐条对齐）：

① test 账户起始态（D10 自动注入，弃用访客）+ 用例库有条目
② 点击用例集 → 详情抽屉弹出，默认「思维导图」Tab + 尺寸正常
②b 思维导图节点树（me-tpc）+ 用例子节点标签胶囊 + 布局 fit
③ 用例节点（TC-*）点击不跳转（停留导图 Tab）
④ 用例列表 Tab（统计 + 表格）
④b 搜索过滤
⑤ 导出 Tab：各格式下载按钮（fetch 200 + 体积 > 50B）
⑥ 迭代补充链路（继续优化 → 迭代 chip → 发消息 → ⚡生成用例 → 用例库出现 vN）
⑦ Esc 关闭抽屉
⑧ 全程 0 JS 错误

起始态 = **test 账户**（由 tests/ui/conftest.py::_start_state 依模块路径自动注入 storage_state，
用例内**不写登录**；JS 版开头的「退出 → test 登录 → reload」在 Python 版由夹具等价承担）。
BASE URL 走 conftest 的 `server` 夹具（单一真源 AITF_BASE_URL）。

⚠️ **第⑥步「迭代补充链路」失败 = 测试侧断言缺陷（2026-10-10 实测订正；非 app 侧、非环境阻塞）**

> **订正（2026-10-10 实测）**：本文档早期把失败归因于「②b 抽屉竞态」，**经实跑证伪**——本轮
> ②/②b/③ **均通过**（抽屉稳定打开、导图 244 节点），真实失败点在**第⑥步**。根因 = 断言正则
> `\\bv[2-9]\\d*\\b` 的 `[2-9]` **不含 1** → **漏配 v10–v19**，而迭代链已增至 **v12**（用例库首行
> `t.name`「…(v12)」与徽章 `v{链长}` 两处均两位数）→ 断言**恒假**，无论等多久都超时；⑥ 60s 超时
> 又紧贴实测 **58.0s**。证据（DB/MySQL 只读）：iterate 任务 **status=completed**，v2→v12 耗时
> **36.9–58.0s**（v11=58.0s、v12=52.5s，step_logs 5/5 completed）；真实 LLM 可用
> （`test_real_llm_smoke` PASSED）。→ **非环境阻塞、非迁移 bug**，属测试侧断言缺陷。
> **本次处置**：正则放宽为 `/\\bv(?:[2-9]|[1-9]\\d+)\\b/`（覆盖 v2–v9 与 v10+，仍排除 v1）；
> ⑥ 两处超时 60s → 120s 留裕量。**未改 app、未改断言语义（仅修 bug 化的版本边界）**。
> 下段原「②b」叙述保留作历史留痕，其结论已被上方订正覆盖。

- **（历史假设，已证伪）现象**：步①② 通过后，`page.waitForSelector(".map-container .me-tpc", timeout=15000)` 超时，
  测试在同点中断。故 ②b 用**硬断言**（超时即 fail，对齐 JS「同点中断」语义），不做 try/except 软记、
  不改用「首个可稳定打开的用例集」绕过——那会把真实信号变成静默放行。
- **根因**：**用例库首个用例集**（当次为「库存盘点管理 v1」，35 用例）点击后**详情抽屉不稳定**——
  被「外部点击关闭」竞态吞掉，抽屉无法持续 `.show`，其后 `.map-container .me-tpc` 永不渲染；
  而第 1/2 行用例集抽屉稳定、导图正常（实测 me-tpc=226 / 106 节点）。即缺陷出在**首个用例集的抽屉打开时序**，
  疑与列表首屏渲染 / 轮询重排的外部点击处理有关（app 侧既有竞态）。
- **与数据强相关（天然易抖）**：失败取决于**首个用例集是哪一个**。用例库条数在 7/8 间浮动
  （并发跑 + 后端重启期间数据变动），首行任务会变；首行恰为可稳定打开的任务时全链路通过
  （本用例首轮实测即 214 节点全绿）。JS 原脚本同样受此影响（S0 记录即为该现象）。
- **JS 原脚本同缺陷**：`M3_URL=http://127.0.0.1:8005 node scripts/e2e-m3-browser.mjs` 当前输出
  `✅① ✅① ✅② ✅②` 后 `❌ 脚本异常中断: TimeoutError: page.waitForSelector: Timeout 15000ms exceeded.
  waiting for locator('.map-container .me-tpc')`——**同位置、同选择器、同 15s、同样失败**。
- **复现命令**：
  ```
  AITF_BASE_URL=http://127.0.0.1:8005 .venv/bin/python -m pytest \
      tests/ui/testcase/test_case_detail.py -q -s -p no:cacheprovider
  # JS 对照：cd frontend && M3_URL=http://127.0.0.1:8005 node scripts/e2e-m3-browser.mjs
  ```
- **处置**：如实留档为**本次迁移的有效产出**（e2e 暴露既有竞态）；**不改 app**，交产品/前端侧评估。
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

pytestmark = pytest.mark.e2e_ui


def _text(page: Page, selector: str) -> str:
    """零等待读取 textContent（§9.1：元素缺失时不走默认 30s 隐式空等）。"""
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? (e.textContent || '').trim() : ''; }",
        selector,
    )


def _open_drawer_stable(page: Page, idx: int = 0) -> bool:
    """稳定打开用例详情抽屉（对应 JS 版 openDrawerStable，最多重试 3 次）。

    列表轮询重渲染时点击可能被「外部点击关闭」吞掉 → 验证抽屉持续可见。
    """
    for _ in range(3):
        page.locator(".cases-page tbody tr .cl-name").nth(idx).click()
        try:
            page.wait_for_selector(".task-drawer.show", timeout=8_000)
            page.wait_for_timeout(2_500)
            if page.locator(".task-drawer.show").count() > 0:
                return True
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(800)
    return False


@pytest.mark.title("用例详情全链路（思维导图 / 用例列表 / 导出）+ 迭代补充用例")
def test_case_detail(page: Page, hud, server, js_errors):
    """M3 全链路；断言经 hud.check 逐条记录，末尾统一汇总（基线失败点见文件头）。"""
    base = server

    # ① test 账户起始态 + 用例库
    hud.step("① test 账户起始态 · 用例库")
    page.goto(base, wait_until="networkidle")
    expect(page.locator(".rail-user")).to_be_visible(timeout=10_000)
    ident = {"name": _text(page, ".rail-user .rl-user-name"), "role": _text(page, ".rail-user .rl-user-role")}
    assert hud.check(f"① test 账户起始态（{ident['name']} · {ident['role']}）", ident["role"] == "用户"), ident
    page.locator('button[aria-label^="用例库"]').click()
    page.wait_for_selector(".cases-page tbody tr", timeout=20_000)
    task_count = page.locator(".cases-page tbody tr").count()
    assert hud.check(f"① 用例库 {task_count} 条用例集", task_count >= 1), task_count

    # ② 打开详情抽屉（默认思维导图 Tab）
    hud.step("② 详情抽屉 · 默认思维导图 Tab")
    assert hud.check("② 详情抽屉稳定打开", _open_drawer_stable(page, 0))
    page.wait_for_selector(".drawer-head .drawer-title", timeout=8_000)
    def_tab = _text(page, ".dtab.active")
    assert hud.check(f"② 默认 Tab 为思维导图（{def_tab}）", "思维导图" in (def_tab or ""))
    drawer_size = page.evaluate(
        """() => {
            const d = document.querySelector('.task-drawer.show');
            if (!d) return { w: 0, h: 0, vw: 0, vh: 0 };
            const r = d.getBoundingClientRect();
            return { w: Math.round(r.width), h: Math.round(r.height),
                     vw: window.innerWidth, vh: window.innerHeight };
        }"""
    )
    assert hud.check(
        f"② 弹窗尺寸正常（{drawer_size['w']}×{drawer_size['h']}，视口 {drawer_size['vw']}×{drawer_size['vh']}）",
        drawer_size["w"] >= drawer_size["vw"] * 0.7 and drawer_size["h"] >= drawer_size["vh"] * 0.6,
    ), drawer_size

    # ②b 思维导图节点树（硬断言：me-tpc 须在 15s 内渲染，超时即 fail —— 对齐 JS 同点中断语义）
    hud.step("②b 思维导图节点树")
    page.wait_for_selector(".map-container .me-tpc", timeout=15_000)
    assert hud.check(
        "②b 思维导图渲染（.map-container .me-tpc）",
        page.locator(".map-container .me-tpc").count() >= 1,
    )
    # fit 布局：等 map-canvas 的 scale transform 收敛（对应 JS waitForFunction，失败可忽略）
    try:
        page.wait_for_function(
            "() => { const c = document.querySelector('.map-container .map-canvas');"
            " return !!(c && /scale\\(/.test(c.style.transform || '')); }",
            timeout=5_000,
        )
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(800)  # 等 ResizeObserver 回调收敛
    node_count = page.locator(".map-container .me-tpc").count()
    assert hud.check(f"②b 思维导图渲染 {node_count} 节点", node_count >= 3)
    tag_texts = page.eval_on_selector_all(
        ".map-container .tags span", "els => els.map(e => (e.textContent || '').trim())"
    )
    assert hud.check(
        f"②b 用例子节点恢复（标签：{'/'.join(sorted(set(tag_texts))) or '无'}）",
        "操作步骤" in tag_texts or "预期结果" in tag_texts,
    )
    map_layout = page.evaluate(
        """() => {
            const cont = document.querySelector('.map-container');
            const nodes = [...document.querySelectorAll('.map-container .me-tpc')];
            const root = document.querySelector('.map-container .me-root .me-tpc, .map-container .me-root');
            if (!cont || nodes.length === 0) return { ok: false, inView: 0, total: 0, rootInView: false };
            const cr = cont.getBoundingClientRect();
            const inViewOf = (r) => r.width > 0 && r.right > cr.left && r.left < cr.right
                                    && r.bottom > cr.top && r.top < cr.bottom;
            const inView = nodes.filter((n) => inViewOf(n.getBoundingClientRect())).length;
            const rootInView = root ? inViewOf(root.getBoundingClientRect()) : false;
            return { ok: rootInView && inView >= 5, inView, total: nodes.length, rootInView };
        }"""
    )
    assert hud.check(
        f"②b 导图布局正常（根节点在可视区，{map_layout['inView']}/{map_layout['total']} 节点可见）",
        bool(map_layout["ok"]),
    )

    # ③ 用例节点点击不跳转（依赖 me-tpc；导图未渲染时该步必然失败，与 JS 一致）
    hud.step("③ 用例节点点击不跳转")
    clicked = page.evaluate(
        """() => {
            const tpcs = [...document.querySelectorAll('.map-container .me-tpc')];
            const t = tpcs.find((e) => /^TC-/.test((e.textContent || '').trim()));
            if (!t) return false;
            t.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            return true;
        }"""
    )
    if clicked:
        page.wait_for_timeout(600)
        tab_active = _text(page, ".dtab.active")
        hud.check(f"③ 用例节点点击不跳转（停留导图 Tab）", "思维导图" in (tab_active or ""))
    else:
        hud.check("③ 未找到用例节点（无文本以 TC- 开头的 me-tpc）", False)

    # ④ 用例列表 Tab（表格 + 统计）
    hud.step("④ 用例列表 Tab")
    page.locator(".dtab").filter(has_text=re.compile("用例列表")).first.click()
    page.wait_for_selector(".case-table tbody tr", timeout=15_000)
    case_count = page.locator(".case-table tbody tr").count()
    stats_text = _text(page, ".case-stats")
    hud.check(
        f"④ 用例列表表格 {case_count} 行（{stats_text[:60]}）",
        case_count >= 1 and "共" in (stats_text or ""),
    )
    if page.locator(".case-table .ct-step").count() > 0:
        hud.check("④ 用例表格步骤/预期列内联展示正常", True)
    else:
        hud.check("④ 用例表格渲染正常（该任务无步骤列内容）", True)

    # ④b 搜索过滤（只断言「过滤后仍有 >=1 条」，不对全局绝对量断言）
    hud.step("④b 搜索过滤")
    first_id = _text(page, ".case-table tbody tr td.ct-id")
    page.fill(".case-search input", first_id)
    page.wait_for_timeout(400)
    filtered = page.locator(".case-table tbody tr").count()
    hud.check(f"④b 搜索「{first_id}」过滤后 {filtered} 条", filtered >= 1)
    page.fill(".case-search input", "")
    page.wait_for_timeout(300)

    # ⑤ 导出 Tab 下载验证（页面内 fetch，用当前登录态 token）
    hud.step("⑤ 导出 Tab 下载")
    page.locator(".dtab").filter(has_text=re.compile("导出")).first.click()
    page.wait_for_selector(".export-item", timeout=8_000)
    ex_names = page.eval_on_selector_all(".export-item .ex-name", "els => els.map(e => e.textContent || '')")
    avail_fmts = [m.group(1).lower() for n in ex_names for m in [re.search(r"\.([a-zA-Z]+)$", n)] if m]
    hud.check(f"⑤ 导出 Tab：{len(ex_names)} 个下载项（{' / '.join(avail_fmts)}）", len(ex_names) >= 1)
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
        hud.check(f"⑤ 下载 {fmt}：HTTP {v.get('status')}，{v.get('len')} 字节", bool(v.get("ok")))

    # ⑥ 迭代补充链路（重：含 LLM 生成，最长 120s 等待）
    hud.step("⑥ 迭代补充链路")
    iter_btn = page.locator(".drawer-footer .btn-primary")
    if iter_btn.count() and iter_btn.is_enabled():
        iter_btn.click()
        page.wait_for_selector(".iter-ref-chip", timeout=8_000)
        hud.check("⑥ 「继续优化」回到会话，输入框挂上迭代引用 chip", True)
        page.fill(".chat-panel textarea", "M3 端到端迭代补充：增加一个边界场景用例")
        page.click(".chat-panel .send-btn")
        page.wait_for_selector(".msg.msg-ai", timeout=20_000)
        hud.check("⑥ 迭代沟通回复完成", True)
        page.wait_for_function(
            "() => { const b = document.querySelector('.irc-gen'); return !!b && !b.disabled; }",
            timeout=120_000,
        )
        page.click(".irc-gen")
        page.locator('button[aria-label^="用例库"]').click()
        page.wait_for_selector(".cases-page", timeout=8_000)
        try:
            page.wait_for_function(
                """() => {
                    const s = document.querySelector('.cases-page tbody tr')?.textContent || '';
                    return /\\bv(?:[2-9]|[1-9]\\d+)\\b/.test(s) && !s.includes('生成中') && !s.includes('排队中');
                }""",
                timeout=120_000,
                polling=1_000,
            )
            hud.check("⑥ 用例库出现迭代版本（vN 徽章）", True)
        except PlaywrightTimeoutError:
            hud.check("⑥ 迭代子任务未出现（120s 内首任务项未变为 v(N) 已完成）", False)
    else:
        hud.check("⑥ 迭代按钮不可用（任务未终态或未关联会话）", False)

    # ⑦ Esc 关闭抽屉
    hud.step("⑦ Esc 关闭抽屉")
    if not _open_drawer_stable(page, 0):
        hud.check("⑦ 抽屉重开失败（3 次点击后仍不稳定可见）", False)
    else:
        page.keyboard.press("Escape")
        try:
            page.wait_for_selector(".task-drawer:not(.show)", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        hud.check("⑦ Esc 关闭抽屉正常", page.locator(".task-drawer.show").count() == 0)

    # ⑧ 全程 0 JS 错误
    hud.step("⑧ 全程 0 JS 错误")
    hud.check("⑧ 全程 0 JS 错误", not js_errors)

    # 逐条落 stdout（配合 -s 可读，便于 CI 留痕）
    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))

    failed = [text for text, ok in hud.checks if not ok]
    assert not failed, "M3 未通过步骤：\n" + "\n".join(f"  ✘ {t}" for t in failed)
