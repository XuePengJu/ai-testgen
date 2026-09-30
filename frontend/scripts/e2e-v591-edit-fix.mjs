/**
 * V5.9.1 编辑态输入框可见性复测（回归：hover 时输入框被 opacity:0 隐藏的 bug）：
 *   ① 点击 ✏️ 进入编辑 → input 可见（opacity=1）且 value=原标题
 *   ② 悬浮在行内其他位置（hover 保持）→ 输入框文字仍可见
 *   ③ Enter 提交 → 改名生效
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-v591-edit-fix";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
const ok = (n) => { results.push(`✅ ${n}`); console.log(`✅ ${n}`); };
const fail = (n, d) => { results.push(`❌ ${n}: ${d}`); console.error(`❌ ${n}: ${d}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

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

  const firstItem = page.locator(".hist-item").first();
  const titleBefore = ((await firstItem.locator(".h-name").textContent()) || "").trim();
  await firstItem.hover();
  await firstItem.locator(".h-ren").click();
  await page.waitForSelector(".hist-item .h-edit", { timeout: 5000 });
  await page.waitForTimeout(300);

  // ① 编辑态：输入框可见 + 原标题在框内（鼠标此时仍 hover 在行上）
  const st = await page.evaluate(() => {
    const inp = document.querySelector(".hist-item .h-edit");
    if (!inp) return null;
    const cs = getComputedStyle(inp);
    return { opacity: cs.opacity, display: cs.display, value: inp.value, focus: document.activeElement === inp };
  });
  if (st && st.opacity === "1" && st.value === titleBefore && st.focus) {
    ok(`① 编辑态输入框可见且带原标题「${st.value}」（opacity=${st.opacity}, 聚焦=${st.focus}）`);
  } else {
    fail("① 编辑态异常", JSON.stringify(st) + ` 期望标题「${titleBefore}」`);
  }
  await page.screenshot({ path: `${SHOT_DIR}/1-editing-hover.png` });

  // ② hover 移到行内 meta 区域（保持 hover）再检查可见性
  await firstItem.locator(".h-meta").hover();
  await page.waitForTimeout(200);
  const st2 = await page.evaluate(() => {
    const inp = document.querySelector(".hist-item .h-edit");
    return inp ? getComputedStyle(inp).opacity : "gone";
  });
  if (st2 === "1") ok("② 行内悬浮时输入框仍可见");
  else fail("② hover 后输入框被隐藏", `opacity=${st2}`);

  // ③ 提交改名
  await page.fill(".hist-item .h-edit", "V591修复验证");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1200);
  const after = await page.evaluate(() => document.querySelector(".hist-item .h-name")?.textContent?.trim() || "");
  if (after === "V591修复验证") ok(`③ 提交改名生效（${titleBefore} → ${after}）`);
  else fail("③ 改名未生效", `实际「${after}」`);
  await page.screenshot({ path: `${SHOT_DIR}/2-after-save.png` });

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
