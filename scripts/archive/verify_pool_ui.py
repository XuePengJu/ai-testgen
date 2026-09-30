"""模型池 + 模型调度摘要前端 UI 验证（V5.1）。

覆盖：
  A. 空态（仅当池本就为空时）→ 池卡空提示 + 单条配置展开
  B. 新增（走 UI 表单）→ 非空态渲染 → 卡头徽标 / 付费徽标 / 梯队状态词 / 折叠区
  C. 模型调度摘要卡（V5.1）：3 行槽位 + 「N 条 · 优先 ①」文案 + 旧逐槽测连通已移除
  C2. 访客视角（V5.1 P1）：无池列表权限（403），但摘要卡仍显示平台池条数与优先序号
      （访客态必须由前端静默建立 —— 见 AuthContext；httpx 直连只会拿到 {"enc": ...} 密文）
  D. 测连通（真实外网请求，假 Key 预期 ✗ 并归类）
  E. 平台默认 Tab（V5.2）：设置页内「我的模型 | 平台默认」分段切换；管理后台已无模型区
  F. 删除本次新增的条目 → 回到原状

⚠️ 数据安全：**不删除池中既有条目**（老板可能配了真实模型）。
   本脚本只新增 2 条假 Key 测试条目，结束时只删这 2 条；异常路径同样只清自己的。

用法：.venv/bin/python scripts/verify_pool_ui.py
前置：本地后端 8000 已起（bash scripts/start_local.sh restart）、前端已 build 到 dist
"""
import json
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SHOT = Path("/tmp/aitf_pool_shots")
TIMEOUT_TEST_MS = 25_000          # 测连通是真实外网请求，给足时间
created: list[int] = []           # 本次创建的池条目 id（只清这些）

# 本地 127.0.0.1 不能走环境里的代理，统一 trust_env=False
_client = httpx.Client(trust_env=False, timeout=30.0)

OK = "[OK] "
NG = "[NG] "


def login() -> str:
    r = _client.post(f"{BASE}/api/auth/login",
                     data={"username": "admin", "password": "Admin@123"})
    r.raise_for_status()
    return r.json()["access_token"]


def hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def count_pool(tok: str) -> int:
    return len(_client.get(f"{BASE}/api/llm/pool/text", headers=hdr(tok)).json())


def cleanup(tok: str) -> None:
    """只删本次创建的条目；既有配置原样保留。

    两个池都要兜底清：个人池按 id（created），平台池按备注标记扫一遍 ——
    异常退出时平台池的临时假 Key 可能还没来得及删，留在真实调度路径上会污染线上任务。
    """
    for i in created:
        try:
            _client.delete(f"{BASE}/api/llm/pool/text/{i}", headers=hdr(tok))
        except Exception as e:  # noqa: BLE001
            print(f"  清理个人池 {i} 失败：{e}")
    try:
        rows = _client.get(f"{BASE}/api/llm/platform-pool/text", headers=hdr(tok)).json()
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        if str(r.get("note", "")).startswith("UI 验证用"):
            try:
                _client.delete(f"{BASE}/api/llm/platform-pool/text/{r['id']}", headers=hdr(tok))
                print(f"  已兜底清理平台池临时条目 {r['id']}")
            except Exception as e:  # noqa: BLE001
                print(f"  清理平台池 {r['id']} 失败：{e}")


def main() -> int:
    tok = login()
    n_base = count_pool(tok)
    print(f"{OK}admin 登录成功；既有 text 池 {n_base} 条（脚本不会删除它们）")

    fails: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        errs: list[str] = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: d.accept())        # 删除确认框

        page.add_init_script(f"localStorage.setItem('aitf_token', {json.dumps(tok)})")
        page.goto(BASE + "/", wait_until="networkidle")
        # V5.3：模型配置是一级入口；「设置」按钮已移除
        page.click('button[aria-label="模型配置 · 模型池与调度"]')
        page.wait_for_selector('[data-testid="models-page"]')
        page.wait_for_selector('[data-testid="pool-card-personal-text"]')
        page.wait_for_timeout(700)

        card = page.locator('[data-testid="pool-card-personal-text"]')

        # ---- A. 空态（池本就非空则跳过，避免破坏既有数据）----
        if n_base == 0:
            card.screenshot(path=str(SHOT / "1-empty.png"))
            hint = page.locator('[data-testid="pool-empty-personal-text"]').is_visible()
            single_visible = page.locator('[data-testid="single-config-personal"]').is_visible()
            print(f"{OK if hint else NG}空态提示可见：{hint}")
            print(f"{OK if single_visible else NG}池空时单条配置仍展开：{single_visible}")
            if not hint:
                fails.append("空态提示未渲染")
        else:
            print(f"{OK}既有 {n_base} 条配置 → 跳过空态校验（不删你的数据）")

        # ---- B. 新增（UI 表单）----
        page.click('[data-testid="pool-add-personal-text"]')
        page.wait_for_selector('[data-testid="pool-form-personal-text-new"]')
        page.select_option('[data-testid="pool-provider-personal-text"]', "deepseek")
        page.fill('[data-testid="pool-model-personal-text"]', "deepseek-chat")
        page.fill('[data-testid="pool-apikey-personal-text"]', "sk-fake-ui-0001")
        page.fill('[data-testid="pool-note-personal-text"]', "UI 验证用")
        page.click('[data-testid="pool-save-personal-text-new"]')
        page.wait_for_timeout(1500)
        rows_now = page.locator('[data-testid^="pool-row-personal-text-"]').count()
        print(f"{OK if rows_now == n_base + 1 else NG}UI 新增后行数：{rows_now}（期望 {n_base + 1}）")
        if rows_now != n_base + 1:
            fails.append(f"UI 新增后行数应为 {n_base + 1}，实际 {rows_now}")

        # ---- 再补一条（API），构造多候选梯队 ----
        r = _client.post(f"{BASE}/api/llm/pool/text", headers=hdr(tok), json={
            "provider": "bailian", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus", "api_key": "sk-fake-ui-0002", "paid": True,
            "note": "UI 验证用 · 付费",
        })
        r.raise_for_status()

        # 只把「带本次备注标记」的条目纳入清理名单 —— 池中既有配置一律不动
        for it in _client.get(f"{BASE}/api/llm/pool/text", headers=hdr(tok)).json():
            if str(it.get("note", "")).startswith("UI 验证用") and it["id"] not in created:
                created.append(it["id"])

        page.reload(wait_until="networkidle")
        page.click('button[aria-label="模型配置 · 模型池与调度"]')
        page.wait_for_selector('[data-testid="pool-card-personal-text"]')
        page.wait_for_timeout(900)
        card = page.locator('[data-testid="pool-card-personal-text"]')

        expect = n_base + 2
        n_rows = page.locator('[data-testid^="pool-row-personal-text-"]').count()
        badge = card.locator('.slot-badge').first.inner_text()
        paid_tag = card.locator('.pool-tag.paid').count()
        eff_tag = card.locator('.slot-badge.pool-eff').inner_text()
        states = card.locator('.pool-state').all_inner_texts()
        fallback_closed = page.locator('.pool-fallback').count()
        single_in_details = page.locator('.pool-fallback [data-testid="single-config-personal"]').count()
        print(f"{OK if n_rows == expect else NG}非空态行数：{n_rows}（期望 {expect}）；卡头徽标：{badge}")
        print(f"{OK if paid_tag == 1 else NG}付费徽标数：{paid_tag}；命中徽标：{eff_tag}")
        print(f"{OK if states.count('使用中') == 1 and '待命' in states else NG}"
              f"梯队状态词：{states}")
        print(f"{OK if fallback_closed == 1 and single_in_details == 1 else NG}"
              f"单条配置已收进折叠区：details={fallback_closed}，内含单条卡={single_in_details}")
        if n_rows != expect:
            fails.append(f"非空态行数应为 {expect}，实际 {n_rows}")
        if states.count("使用中") != 1:
            fails.append(f"应有且仅有 1 条「使用中」，实际 {states}")
        if "优先" not in eff_tag:
            fails.append(f"命中徽标应含「优先」，实际 {eff_tag}")
        card.screenshot(path=str(SHOT / "2-filled.png"))

        # ---- C. 模型调度摘要卡（V5.1）----
        sched_rows = page.locator('[data-testid^="sched-row-"]').count()
        tag_text = page.locator('[data-testid="sched-tag-text"]').inner_text()
        head_badge = page.locator('[data-testid="sched-head-badge"]').inner_text()
        old_btns = page.locator('[data-testid^="test-effective-"]').count()
        print(f"{OK if sched_rows == 3 else NG}模型调度摘要行数：{sched_rows}")
        print(f"{OK if f'{expect} 条' in tag_text and '优先' in tag_text else NG}"
              f"文本槽摘要：{tag_text}（卡头：{head_badge}）")
        print(f"{OK if old_btns == 0 else NG}旧逐槽「测连通」按钮已移除：{old_btns} 个")
        if sched_rows != 3:
            fails.append(f"摘要卡应为 3 行，实际 {sched_rows}")
        if f"{expect} 条" not in tag_text or "优先" not in tag_text:
            fails.append(f"文本槽摘要文案异常：{tag_text}")
        if old_btns:
            fails.append("旧逐槽测连通按钮仍存在")
        page.locator('[data-testid="effective-bar"]').screenshot(path=str(SHOT / "6-sched.png"))

        # ---- C2. 访客视角（V5.1 P1）：无池列表权限，但仍能看到平台池条数 ----
        # ⚠️ 非 admin 角色的响应体被中间件整体加密成 {"enc": "..."}（app/core/middleware.py，
        #    API_ENCRYPT 默认开）—— httpx 直连只能拿到密文。所以访客侧必须走**浏览器**
        #    （前端 client.ts 有对应解密层），这也正是真实用户的路径。
        gt = _client.post(f"{BASE}/api/guest/token").json()["access_token"]
        plat_base = len(_client.get(f"{BASE}/api/llm/platform-pool/text", headers=hdr(tok)).json())
        rp = _client.post(f"{BASE}/api/llm/platform-pool/text", headers=hdr(tok), json={
            "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "api_key": "sk-fake-ui-guest",
            "note": "UI 验证用 · 访客视角临时条目",
        })
        rp.raise_for_status()
        plat_tmp = rp.json()["id"]

        pool_403 = _client.get(f"{BASE}/api/llm/pool/text", headers=hdr(gt)).status_code
        print(f"{OK if pool_403 == 403 else NG}访客读个人池列表被拒：HTTP {pool_403}")
        if pool_403 != 403:
            fails.append(f"访客读个人池应 403，实际 {pool_403}")

        gpage = browser.new_page(viewport={"width": 1500, "height": 1000})
        gerr: list[str] = []
        gpage.on("console", lambda m: gerr.append(m.text) if m.type == "error" else None)
        gpage.on("pageerror", lambda e: gerr.append(f"pageerror: {e}"))
        # ⚠️ 访客态必须让**前端自己建立**：AuthContext 在未登录时静默 POST /api/guest/token，
        #    并同时写入 token + enc_key + me（authState 的 aitf_token / aitf_enc_key / aitf_user）。
        #    非 admin 的响应体全程加密，只注入 token 而缺 enc_key → cryptoOn=false → 前端
        #    判定「登录状态需要刷新」并 reload，死循环，永远等不到「访客」头像。
        #    也不能直接 click rail：首次进站要现场建共享 guest 账号，click 会卡在等导航而超时
        #    → 等 me 到位后，用 App 监听的 nav-to 事件切到设置页。
        gpage.goto(BASE + "/", wait_until="domcontentloaded")
        gpage.wait_for_selector('button[aria-label^="访客"]', timeout=25_000)
        gpage.evaluate("window.dispatchEvent(new CustomEvent('nav-to', {detail: 'models'}))")
        gpage.wait_for_selector('[data-testid="effective-bar"]', timeout=20_000)
        gpage.wait_for_timeout(800)
        gseg = gpage.locator('[data-testid="seg-tabs"]').count()
        print(f"{OK if gseg == 0 else NG}访客视角无分段 Tab（只读）：{gseg}")
        if gseg:
            fails.append("访客不应看到模型配置分段 Tab")
        gtag = gpage.locator('[data-testid="sched-tag-text"]').inner_text()
        ghead = gpage.locator('[data-testid="sched-head-badge"]').inner_text()
        want = f"平台 {plat_base + 1} 条"
        print(f"{OK if want in gtag and '优先' in gtag else NG}"
              f"访客摘要卡文本槽：{gtag}（卡头：{ghead}）")
        if want not in gtag or "优先" not in gtag:
            fails.append(f"访客摘要卡应含「{want} · 优先」，实际 {gtag}")
        if gerr:
            fails.append(f"访客页控制台错误：{gerr[:3]}")
        gpage.locator('[data-testid="effective-bar"]').screenshot(
            path=str(SHOT / "8-guest-sched.png"))
        gpage.close()

        # 立刻清掉平台池临时条目（假 Key 绝不能留在真实调度路径上）
        _client.delete(f"{BASE}/api/llm/platform-pool/text/{plat_tmp}", headers=hdr(tok))
        back = len(_client.get(f"{BASE}/api/llm/platform-pool/text", headers=hdr(tok)).json())
        print(f"{OK if back == plat_base else NG}平台池临时条目已清理：{back} 条（基线 {plat_base}）")
        if back != plat_base:
            fails.append(f"平台池应回到 {plat_base} 条，实际 {back}")

        # ---- D. 测连通（对本次新增的最后一条，假 Key 预期 ✗）----
        tid = created[-1]
        page.click(f'[data-testid="pool-test-personal-text-{tid}"]')
        try:
            page.wait_for_selector(
                f'[data-testid="pool-row-personal-text-{tid}"] .test-msg',
                timeout=TIMEOUT_TEST_MS)
            msg = page.locator(f'[data-testid="pool-row-personal-text-{tid}"] .test-msg').inner_text()
        except Exception as e:  # noqa: BLE001
            msg = f"(超时未出结果：{type(e).__name__})"
            fails.append("测连通未返回结果")
        print(f"{OK if msg.startswith(('✓', '✗')) else NG}测连通结果：{msg}")
        card.screenshot(path=str(SHOT / "3-test.png"))

        # ---- E. 平台默认 Tab（V5.2：模型配置统一入口在设置页）----
        seg = page.locator('[data-testid="seg-tabs"]').count()
        print(f"{OK if seg == 1 else NG}模型配置页出现分段 Tab：{seg}")
        if seg != 1:
            fails.append("设置页未渲染分段 Tab")
        page.click('[data-testid="tab-platform"]')
        page.wait_for_selector('[data-testid="pool-card-platform-text"]')
        page.wait_for_timeout(900)
        plat_visible = page.locator('[data-testid="pool-card-platform-text"]').is_visible()
        personal_gone = page.locator('[data-testid="pool-card-personal-text"]').count() == 0
        print(f"{OK if plat_visible and personal_gone else NG}"
              f"切到「平台默认」：平台池卡可见={plat_visible}，个人池卡已隐藏={personal_gone}")
        if not plat_visible or not personal_gone:
            fails.append("平台默认 Tab 切换异常")
        page.locator('[data-testid="pool-card-platform-text"]').screenshot(
            path=str(SHOT / "4-platform.png"))

        # E2. 用户管理页（原管理后台）不再有模型区（V5.2 迁出 + V5.3 改名）
        page.click('button[aria-label="用户管理（仅管理员）"]')
        page.wait_for_selector('[data-testid="admin-page"]')
        page.wait_for_timeout(600)
        admin_model = page.locator('[data-testid="slot-group-platform"]').count()
        print(f"{OK if admin_model == 0 else NG}用户管理页已无模型区：{admin_model} 个")
        if admin_model:
            fails.append("用户管理页仍残留模型配置区")

        # ---- F. 回模型配置页切回「我的模型」→ 只删本次新增的 2 条 → 回基线 ----
        page.click('button[aria-label="模型配置 · 模型池与调度"]')
        page.wait_for_selector('[data-testid="seg-tabs"]')
        page.click('[data-testid="tab-personal"]')
        page.wait_for_selector('[data-testid="pool-card-personal-text"]')
        page.wait_for_timeout(600)
        for i in created:
            btn = page.locator(f'[data-testid="pool-del-personal-text-{i}"]')
            if btn.count() == 0:
                continue
            btn.click()
            page.wait_for_timeout(1400)
        left = page.locator('[data-testid^="pool-row-personal-text-"]').count()
        card.screenshot(path=str(SHOT / "5-after-delete.png"))
        print(f"{OK if left == n_base else NG}删除本次新增后剩余行：{left}（基线 {n_base}）")
        if left != n_base:
            fails.append(f"删除后应有 {n_base} 行，实际 {left}")

        # ---- H. 个人中心（V5.3）：「设置」按钮已移除，点 rail 底部用户名打开 ----
        no_settings_btn = page.locator('button[aria-label="设置"]').count()
        print(f"{OK if no_settings_btn == 0 else NG}「设置」rail 按钮已移除：{no_settings_btn} 个")
        if no_settings_btn:
            fails.append("「设置」按钮仍存在于侧栏")
        page.locator('.rail-avatar').click()
        page.wait_for_selector('[data-testid="settings-page"]')
        page.wait_for_timeout(500)
        profile_ok = (
            page.locator('[data-testid="settings-page"]').is_visible()
            and page.locator('[data-testid="models-page"]').count() == 0
        )
        print(f"{OK if profile_ok else NG}点用户名打开个人中心：页面切换={profile_ok}")
        if not profile_ok:
            fails.append("用户名点击未正确打开个人中心")
        page.click('button[aria-label="模型配置 · 模型池与调度"]')
        page.wait_for_selector('[data-testid="pool-card-personal-text"]')

        # ---- G. 窄屏（≤575px）：摘要行应换行、模型名独占一行 ----
        page.set_viewport_size({"width": 420, "height": 900})
        page.wait_for_timeout(600)
        page.locator('[data-testid="effective-bar"]').screenshot(
            path=str(SHOT / "7-sched-mobile.png"))
        print(f"{OK}窄屏 420px 摘要卡截图已输出")

        print(f"[JS 错误] {errs[:5] if errs else 'none'}")
        if errs:
            fails.append(f"控制台错误 {len(errs)} 条")
        browser.close()

    cleanup(tok)
    final_n = count_pool(tok)
    print(f"{OK if final_n == n_base else NG}接口复核：池剩余 {final_n} 条（基线 {n_base}）")

    print("\n=== 截图 ===")
    for f in sorted(SHOT.glob("*.png")):
        print(" ", f)
    if fails:
        print("\n失败项：")
        for f in fails:
            print(" -", f)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    # 异常也要清干净：假 Key 残留在池里会影响真实调度
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"SCRIPT FAIL: {type(e).__name__}: {e}")
        try:
            cleanup(login())
            print("已清理本次创建的池条目")
        except Exception as ce:  # noqa: BLE001
            print(f"清理失败（需手动检查文本模型池）：{ce}")
        sys.exit(1)
