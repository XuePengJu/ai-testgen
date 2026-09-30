/**
 * M3 浏览器端到端验证（Playwright，V5.3 UI 适配）：
 *   ① **test 账户登录**（2026-09-26 起弃用访客执行：guest 示例任务曾因导出文件缺失
 *      导致下载 404 误报；test 账户任务均为真实生成、导出文件齐全）
 *   ② 点击任务 → 详情弹窗（默认打开思维导图 Tab）
 *   ②b 思维导图完整节点树（用例子节点：前置/数据/步骤→预期 标签胶囊 + 布局 fit）
 *   ③ 用例节点点击不跳转（停留导图 Tab）
 *   ④ 用例列表 Tab（统计 + 表格）+ 搜索过滤
 *   ⑤ 导出 Tab：下载按钮（fetch 200 验证各格式）
 *   ⑥ 迭代补充（V2.10 新链路）：💬 继续优化回会话 → 挂迭代 chip → 发消息 → 生成用例 → 迭代任务出现
 *   ⑦ Esc 关闭抽屉
 *   ⑧ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m3/
 *
 * 运行：node scripts/e2e-m3-browser.mjs（M3_URL 默认 http://localhost:8000，需后端已启动）
 * 依赖：test / test1234 账号；批跑时 m2 先行会为 test 账户新建带导出文件的任务
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M3_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-m3";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
function ok(name) {
  results.push(`✅ ${name}`);
  console.log(`✅ ${name}`);
}
function fail(name, detail) {
  results.push(`❌ ${name}: ${detail}`);
  console.error(`❌ ${name}: ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
// 记录具体 4xx/5xx 资源，⑧ 报错时能直接定位是哪个请求
page.on("response", (r) => {
  if (r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url()}`);
});

/** 稳定打开任务抽屉：列表有进行中任务时会在轮询中重渲染，点击可能被"外部点击关闭"吞掉 → 验证抽屉持续可见，最多重试 3 次
 *  V5.5：入口改为用例库页表格行的用例集名称 */
const openDrawerStable = async (idx = 0) => {
  for (let i = 0; i < 3; i++) {
    await page.locator(".cases-page tbody tr .cl-name").nth(idx).click();
    const stayed = await page
      .waitForSelector(".task-drawer.show", { timeout: 8000 })
      .then(async () => {
        await page.waitForTimeout(2500);
        return !!(await page.$(".task-drawer.show"));
      })
      .catch(() => false);
    if (stayed) return true;
    await page.waitForTimeout(800);
  }
  return false;
};

try {
  // ① 进入首页（默认访客态）→ 退出 → test 账户登录 → 任务列表出现已完成任务
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[placeholder="用户名"]', "test");
  await page.fill('.auth-modal input[placeholder="密码"]', "test1234");
  await page.click(".auth-btn");
  await page.waitForFunction(
    () => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === "test",
    undefined,
    { timeout: 10000 },
  );
  const role = ((await page.textContent(".rail-user .rl-user-role")) || "").trim();
  if (role === "用户") ok("① test 账户登录成功（test · 用户）");
  else fail("① test 登录后角色异常", role);
  // 关键：访客态残留的会话消息卡（含 guest 任务的轮询）会以 test token 请求 → 权限 404。
  // reload 以 test 身份全新加载，彻底脱离访客态。
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  // V5.5：任务列表资产化为用例库页，改在用例库断言
  await page.click('button[aria-label^="用例库"]');
  await page.waitForSelector(".cases-page tbody tr", { timeout: 20000 });
  const taskCount = await page.locator(".cases-page tbody tr").count();
  ok(`① 用例库 ${taskCount} 条用例集`);

  // ② 点击已完成任务 → 弹窗默认思维导图 Tab
  const drawerOpened = await openDrawerStable(0);
  await page.waitForSelector(".drawer-head .drawer-title", { timeout: 8000 });
  const defTab = await page.textContent(".dtab.active");
  if (/思维导图/.test(defTab || "")) ok("② 详情弹窗弹出，默认打开思维导图 Tab");
  else fail("② 默认 Tab 不是思维导图", `tab=${defTab}`);
  const drawerSize = await page.evaluate(() => {
    const d = document.querySelector(".task-drawer.show");
    if (!d) return { w: 0, h: 0, vw: 0, vh: 0 };
    const r = d.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), vw: window.innerWidth, vh: window.innerHeight };
  });
  if (drawerSize.w >= drawerSize.vw * 0.7 && drawerSize.h >= drawerSize.vh * 0.6)
    ok(`② 弹窗尺寸正常（${drawerSize.w}×${drawerSize.h}，视口 ${drawerSize.vw}×${drawerSize.vh}）`);
  else fail("② 弹窗尺寸异常", JSON.stringify(drawerSize));

  // ②b 思维导图（默认 Tab）：节点树 + fit 布局（mind-elixir 6.x 节点为 div.me-tpc，非标签选择器）
  await page.waitForSelector(".map-container .me-tpc", { timeout: 15000 });
  await page.waitForFunction(
    () => {
      const c = document.querySelector(".map-container .map-canvas");
      return !!(c && /scale\(/.test(c.style.transform || ""));
    },
    null,
    { timeout: 5000 },
  ).catch(() => {});
  await page.waitForTimeout(800); // 等 ResizeObserver 回调收敛
  const nodeCount = await page.$$eval(".map-container .me-tpc", (els) => els.length);
  if (nodeCount >= 3) ok(`②b 思维导图渲染 ${nodeCount} 节点`);
  else fail("②b 导图节点不足", String(nodeCount));
  // 用例子节点：标签胶囊（前置条件/测试数据/操作步骤/预期结果 + 类型/优先级）
  const tagTexts = await page.$$eval(".map-container .tags span", (els) => els.map((e) => (e.textContent || "").trim()));
  const hasStep = tagTexts.includes("操作步骤");
  const hasExp = tagTexts.includes("预期结果");
  if (hasStep || hasExp)
    ok(`②b 用例子节点恢复（标签：${[...new Set(tagTexts)].join("/") || "无"}）`);
  else fail("②b 导图缺少用例子节点", `tags=[${tagTexts.join(",")}] nodeCount=${nodeCount}`);
  // 节点可见性：V5.3 禁 scaleFit（大图不整体缩放，靠平移查看），大任务部分节点在视区外是预期；
  // 断言改为「根节点在可视区 + 至少 5 个节点可见」
  const mapLayout = await page.evaluate(() => {
    const cont = document.querySelector(".map-container");
    const nodes = [...document.querySelectorAll(".map-container .me-tpc")];
    const root = document.querySelector(".map-container .me-root .me-tpc, .map-container .me-root");
    if (!cont || nodes.length === 0) return { ok: false, inView: 0, total: 0, rootInView: false };
    const cr = cont.getBoundingClientRect();
    const inViewOf = (r) => r.width > 0 && r.right > cr.left && r.left < cr.right && r.bottom > cr.top && r.top < cr.bottom;
    const inView = nodes.filter((n) => inViewOf(n.getBoundingClientRect())).length;
    const rootInView = root ? inViewOf(root.getBoundingClientRect()) : false;
    return { ok: rootInView && inView >= 5, inView, total: nodes.length, rootInView };
  });
  if (mapLayout.ok) ok(`②b 导图布局正常（根节点在可视区，${mapLayout.inView}/${mapLayout.total} 节点可见）`);
  else fail("②b 导图布局异常", JSON.stringify(mapLayout));
  await page.screenshot({ path: `${SHOT_DIR}/1-mindmap-full.png`, fullPage: true });

  // ③ 用例节点点击不跳转
  const clicked = await page.evaluate(() => {
    const tpcs = [...document.querySelectorAll(".map-container .me-tpc")];
    const t = tpcs.find((e) => /^TC-/.test((e.textContent || "").trim()));
    if (!t) return false;
    t.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  });
  if (clicked) {
    await page.waitForTimeout(600);
    const tabActive = await page.textContent(".dtab.active");
    if (/思维导图/.test(tabActive || "")) ok("③ 用例节点点击不跳转（停留导图 Tab）");
    else fail("③ 导图节点点击仍触发跳转", `tab=${tabActive}`);
  } else {
    fail("③ 未找到用例节点", "无文本以 TC- 开头的 me-tpc");
  }

  // ④ 用例列表 Tab（表格）
  await page.click(".dtab >> text=用例列表");
  await page.waitForSelector(".case-table tbody tr", { timeout: 15000 });
  const caseCount = await page.$$eval(".case-table tbody tr", (els) => els.length);
  const statsText = await page.textContent(".case-stats");
  if (caseCount >= 1 && statsText && statsText.includes("共")) {
    ok(`④ 用例列表表格 ${caseCount} 行（${statsText.trim().replace(/\s+/g, " ").slice(0, 60)}）`);
  } else fail("④ 用例列表异常", `caseCount=${caseCount} stats=${statsText}`);
  const stepCell = await page.$(".case-table .ct-step");
  if (stepCell) ok("④ 用例表格步骤/预期列内联展示正常");
  else ok("④ 用例表格渲染正常（该任务无步骤列内容）");
  await page.screenshot({ path: `${SHOT_DIR}/2-cases-table.png`, fullPage: true });

  // ④b 搜索过滤
  const firstId = (await page.textContent(".case-table tbody tr td.ct-id")).trim();
  await page.fill(".case-search input", firstId);
  await page.waitForTimeout(400);
  const filtered = await page.$$eval(".case-table tbody tr", (els) => els.length);
  if (filtered >= 1) ok(`④b 搜索「${firstId}」过滤后 ${filtered} 条`);
  else fail("④b 搜索过滤异常", String(filtered));
  await page.fill(".case-search input", "");
  await page.waitForTimeout(300);

  // ⑤ 导出 Tab 下载验证
  await page.click(".dtab >> text=导出");
  await page.waitForSelector(".export-item", { timeout: 8000 });
  const exNames = await page.$$eval(".export-item .ex-name", (els) => els.map((e) => e.textContent || ""));
  const availFmts = exNames.map((n) => (n.match(/\.([a-z]+)$/)?.[1] || "").toLowerCase()).filter(Boolean);
  ok(`⑤ 导出 Tab：${exNames.length} 个下载项（${availFmts.join(" / ")}）`);
  const dl = await page.evaluate(async (fmts) => {
    const taskId = document.querySelector(".task-drawer")?.getAttribute("data-task-id") || "";
    const token = (localStorage.getItem("aitf_token") || "").replace(/^"|"$/g, "");
    const out = {};
    for (const fmt of fmts) {
      try {
        const r = await fetch(`/api/tasks/${taskId}/download?fmt=${fmt}`, {
          headers: { Authorization: "Bearer " + token },
        });
        let okRes = r.ok;
        let len = 0;
        if (okRes) {
          const b = await r.blob();
          len = b.size;
          okRes = len > 50;
        }
        out[fmt] = { status: r.status, ok: okRes, len };
      } catch (e) {
        out[fmt] = { error: String(e) };
      }
    }
    return out;
  }, availFmts);
  for (const [fmt, v] of Object.entries(dl)) {
    if (v.ok) ok(`⑤ 下载 ${fmt}：HTTP ${v.status}，${v.len} 字节`);
    else fail(`⑤ 下载 ${fmt} 失败`, JSON.stringify(v));
  }
  await page.screenshot({ path: `${SHOT_DIR}/4-export.png`, fullPage: true });

  // ⑥ 迭代补充（V2.10 链路）：继续优化 → 回会话挂 chip → 发消息 → ⚡生成用例 → 迭代版本出现
  const iterBtn = page.locator(".drawer-footer .btn-primary");
  if (await iterBtn.isEnabled()) {
    await iterBtn.click();
    // 回到会话且输入框挂上迭代引用 chip
    await page.waitForSelector(".iter-ref-chip", { timeout: 8000 });
    ok("⑥ 「继续优化」回到会话，输入框挂上迭代引用 chip");
    await page.fill(".chat-panel textarea", "M3 端到端迭代补充：增加一个边界场景用例");
    await page.click(".chat-panel .send-btn");
    await page.waitForSelector(".msg.msg-ai", { timeout: 20000 });
    ok("⑥ 迭代沟通回复完成");
    // V2.10 迭代确认入口 = chip 上的「⚡ 生成用例」（流式结束 / iterGenerating 空闲后才可点）
    await page.waitForFunction(
      () => {
        const b = document.querySelector(".irc-gen");
        return !!b && !b.disabled;
      },
      null,
      { timeout: 60000 },
    );
    await page.click(".irc-gen");
    // 切到用例库页断言迭代版本（V5.5：右栏任务列表已移除）
    await page.click('button[aria-label^="用例库"]');
    await page.waitForSelector(".cases-page", { timeout: 8000 });
    // 用例库渲染版本链：迭代子任务聚合进同一行，版本徽章升为 vN（N≥2）且非生成中——
    // 断言改为首行出现 vN 徽章（V5.5：原任务列表「xxx (vN)」断言迁移）
    const iterAppeared = await page
      .waitForFunction(() => {
        const s = document.querySelector(".cases-page tbody tr")?.textContent || "";
        return /\bv[2-9]\d*\b/.test(s) && !s.includes("生成中") && !s.includes("排队中");
      }, null, { timeout: 60000, polling: 1000 })
      .then(() => true)
      .catch(() => false);
    if (iterAppeared) ok("⑥ 用例库出现迭代版本（vN 徽章）");
    else fail("⑥ 迭代子任务未出现", "60s 内首任务项未变为 (vN) 已完成");
  } else {
    fail("⑥ 迭代按钮不可用", "任务未终态或未关联会话");
  }
  await page.screenshot({ path: `${SHOT_DIR}/5-iterate.png`, fullPage: true });

  // ⑦ Esc 关闭抽屉（⑥ 流程中抽屉已随「继续优化」关闭；重开再验 Esc）
  if (!(await openDrawerStable(0))) fail("⑦ 抽屉重开失败", "3 次点击后抽屉仍未稳定可见");
  await page.keyboard.press("Escape");
  await page.waitForSelector(".task-drawer:not(.show)", { timeout: 5000 }).catch(() => {});
  const drawerGone = await page.$(".task-drawer.show") === null;
  if (drawerGone) ok("⑦ Esc 关闭抽屉正常");
  else fail("⑦ Esc 未关闭抽屉", "");

  // ⑧ JS 错误
  if (errors.length === 0) ok("⑧ 全程 0 JS 错误");
  else fail("⑧ JS 错误", errors.slice(0, 5).join(" | "));
} catch (e) {
  fail("脚本异常中断", String(e).slice(0, 300));
  try {
    await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true });
  } catch {}
} finally {
  fs.writeFileSync(`${SHOT_DIR}/result.txt`, results.join("\n") + `\n\nJS errors: ${errors.length}\n`);
  await browser.close();
}
const failed = results.filter((r) => r.startsWith("❌")).length;
console.log(`\n${failed === 0 ? "全部通过" : failed + " 项失败"}`);
process.exit(failed === 0 ? 0 : 1);
