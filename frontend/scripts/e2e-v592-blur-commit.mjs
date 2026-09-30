/**
 * V5.9.2 blur 即提交复测：
 *   ① 点 ✏️ 编辑 → 点行外空白 → 等同回车，改名生效
 *   ② 点 ✏️ 编辑 → 直接点另一个会话（切换）→ 原会话改名生效
 *   ③ Esc 仍是取消（不提交）
 * 截图落档 /tmp/e2e-v592-blur/
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-v592-blur";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
const ok = (n) => { results.push(`✅ ${n}`); console.log(`✅ ${n}`); };
const fail = (n, d) => { results.push(`❌ ${n}: ${d}`); console.error(`❌ ${n}: ${d}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const titleOf = (n) => page.evaluate((idx) => {
  const el = document.querySelectorAll(".hist-item .h-name")[idx];
  return el ? el.textContent.trim() : "";
}, n);

try {
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
  await page.waitForSelector(".hist-item", { timeout: 10000 });
  await page.waitForTimeout(800);

  // ① blur 提交：编辑第一个 → 点侧栏头部空白
  const t0 = await titleOf(0);
  const item0 = page.locator(".hist-item").first();
  await item0.hover();
  await item0.locator(".h-ren").click();
  await page.waitForSelector(".hist-item .h-edit", { timeout: 5000 });
  await page.fill(".hist-item .h-edit", "V592点空白确认");
  await page.click(".side-head"); // 行外空白（侧栏头部）
  await page.waitForTimeout(1200);
  const after1 = await titleOf(0);
  if (after1 === "V592点空白确认") ok(`① 点空白即提交（${t0} → ${after1}）`);
  else fail("① 点空白未提交", `期望 V592点空白确认，实际「${after1}」`);
  await page.screenshot({ path: `${SHOT_DIR}/1-blur-commit.png` });

  // ② 切换会话提交：编辑第二个 → 直接点第三个会话
  const t1 = await titleOf(1);
  const item1 = page.locator(".hist-item").nth(1);
  await item1.hover();
  await item1.locator(".h-ren").click();
  await page.waitForSelector(".hist-item .h-edit", { timeout: 5000 });
  await page.fill(".hist-item .h-edit", "V592切会话确认");
  await page.locator(".hist-item").nth(2).click(); // 点别的会话 = 切换
  await page.waitForTimeout(1500); // 等 blur 提交 + 会话切换落地
  const after2 = await titleOf(1);
  if (after2 === "V592切会话确认") ok(`② 切换会话即提交（${t1} → ${after2}）`);
  else fail("② 切换会话未提交", `期望 V592切会话确认，实际「${after2}」`);
  await page.screenshot({ path: `${SHOT_DIR}/2-switch-commit.png` });

  // ③ Esc 仍是取消
  const t2 = await titleOf(0);
  const item0b = page.locator(".hist-item").first();
  await item0b.hover();
  await item0b.locator(".h-ren").click();
  await page.waitForSelector(".hist-item .h-edit", { timeout: 5000 });
  await page.fill(".hist-item .h-edit", "不该被保存的名字");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(800);
  const after3 = await titleOf(0);
  if (after3 === t2) ok(`③ Esc 取消不提交（标题保持「${after3}」）`);
  else fail("③ Esc 被误提交", `标题变成「${after3}」`);

  if (errors.length === 0) ok("④ 0 JS 错误");
  else fail("④ JS 错误", errors.slice(0, 3).join(" | "));
} catch (e) {
  fail("脚本异常", String(e).slice(0, 200));
  await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
console.log("\n===== 结果汇总 =====");
results.forEach((r) => console.log(r));
process.exit(results.some((r) => r.startsWith("❌")) ? 1 : 0);
