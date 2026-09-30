/**
 * V5.9 会话重命名 + AI 总结标题 UI 目检（Playwright）：
 *   ① test 账号登录 → 会话列表渲染
 *   ② hover 会话行出现 3 个操作钮（✏️ 重命名 / ✨ AI 标题 / 🗑 删除）
 *   ③ 行内重命名：Enter 提交 → 标题更新
 *   ④ ✨ AI 总结标题 → 等待返回 → 标题被 AI 重写
 *   ⑤ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-v59-rename/
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-v59-rename";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
const ok = (n) => { results.push(`✅ ${n}`); console.log(`✅ ${n}`); };
const fail = (n, d) => { results.push(`❌ ${n}: ${d}`); console.error(`❌ ${n}: ${d}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

try {
  // ① 退出访客 → test 登录
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[placeholder="用户名"]', "test");
  await page.fill('.auth-modal input[placeholder="密码"]', "test1234");
  await page.click(".auth-btn");
  await page.waitForFunction(
    () => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === "test",
    undefined, { timeout: 10000 },
  );
  ok("① test 账号登录成功");
  await page.waitForSelector(".hist-item", { timeout: 10000 });
  await page.waitForTimeout(800);

  // ② hover 出操作组
  const firstItem = page.locator(".hist-item").first();
  await firstItem.hover();
  await page.waitForTimeout(400);
  const renVisible = await firstItem.locator(".h-ren").isVisible().catch(() => false);
  const aiVisible = await firstItem.locator(".h-ai").isVisible().catch(() => false);
  const delVisible = await firstItem.locator(".h-del").isVisible().catch(() => false);
  if (renVisible && aiVisible && delVisible) ok("② hover 出现 ✏️/✨/🗑 三个操作钮");
  else fail("② 操作钮可见性", `ren=${renVisible} ai=${aiVisible} del=${delVisible}`);
  await page.screenshot({ path: `${SHOT_DIR}/1-hover-ops.png` });

  // ③ 行内重命名
  const titleBefore = ((await firstItem.locator(".h-name").textContent()) || "").trim();
  await firstItem.locator(".h-ren").click();
  await page.waitForSelector(".hist-item .h-edit", { timeout: 5000 });
  await page.fill(".hist-item .h-edit", "V59手动改名验证");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1200);
  const afterRename = await page.evaluate(() => {
    const el = document.querySelector(".hist-item .h-name");
    return el ? el.textContent.trim() : "";
  });
  if (afterRename === "V59手动改名验证") ok(`③ 行内重命名成功（${titleBefore} → ${afterRename}）`);
  else fail("③ 重命名未生效", `期望 V59手动改名验证，实际「${afterRename}」`);
  await page.screenshot({ path: `${SHOT_DIR}/2-after-rename.png` });

  // ④ AI 总结标题（选第一个会话，test 账号走平台池真模型）
  const firstItem2 = page.locator(".hist-item").first();
  await firstItem2.hover();
  const titleBeforeAI = ((await firstItem2.locator(".h-name").textContent()) || "").trim();
  await firstItem2.locator(".h-ai").click();
  await page.waitForTimeout(6000); // 等模型返回（平台池实测 ~0.5s，留足余量）
  const afterAI = await page.evaluate(() => {
    const el = document.querySelector(".hist-item .h-name");
    return el ? el.textContent.trim() : "";
  });
  if (afterAI && afterAI !== titleBeforeAI) ok(`④ AI 标题生效（${titleBeforeAI} → ${afterAI}）`);
  else fail("④ AI 标题未生效", `标题仍为「${afterAI}」`);
  await page.screenshot({ path: `${SHOT_DIR}/3-after-ai-title.png` });

  if (errors.length === 0) ok("⑤ 全程 0 JS 错误");
  else fail("⑤ JS 错误", errors.slice(0, 3).join(" | "));
} catch (e) {
  fail("脚本异常", String(e).slice(0, 200));
  await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
console.log("\n===== 结果汇总 =====");
results.forEach((r) => console.log(r));
process.exit(results.some((r) => r.startsWith("❌")) ? 1 : 0);
