/**
 * frontend-eng 本地探针（/tmp 一次性脚本，不入库）：验证归类菜单
 * 1. 能打开（Round 2 回归点：打开点击不把菜单立刻关掉）
 * 2. Esc 关闭
 * 3. 点外关闭
 * 4. 菜单内部点击不关闭
 */
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:8005";
const step = (name, ok, extra = "") =>
  console.log(`${ok ? "✅" : "❌"} ${name}${extra ? " — " + extra : ""}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
let failed = false;
const check = (name, ok, extra) => { if (!ok) failed = true; step(name, ok, extra); };

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator('button[aria-label="退出登录"]').click();
  await page.locator(".auth-modal").waitFor({ state: "visible", timeout: 8000 });
  await page.locator('.auth-modal input[type="text"], .auth-modal input:not([type="password"])').first().fill("admin");
  await page.locator('.auth-modal input[type="password"]').fill("Admin@123");
  await page.locator(".auth-btn").click();
  await page.locator(".auth-modal").waitFor({ state: "hidden", timeout: 8000 });
  await page.waitForTimeout(1500);

  await page.locator('button[aria-label^="用例库"]').click();
  await page.waitForTimeout(1500);

  const menuBtn = page.locator('[data-testid^="cat-menu-"]').first();
  if (!(await menuBtn.isVisible().catch(() => false)) && (await page.locator("tbody tr").count()) === 0) {
    console.log("⏭️ SKIPPED：用例库无任务行，无法验证");
  } else {
    const isOpen = async () => page.locator(".cl-move-menu").isVisible().catch(() => false);

    // 1. 打开（多次采样，覆盖同步 flush 时序）
    await menuBtn.click();
    let opened = false;
    for (let i = 0; i < 12; i++) {
      if (await isOpen()) { opened = true; break; }
      await page.waitForTimeout(50);
    }
    check("点归类按钮 → 菜单打开", opened);

    // 4. 菜单内部点击不关闭
    await page.locator(".cl-move-menu .cmm-title").click();
    await page.waitForTimeout(300);
    check("点菜单标题（内部）→ 仍打开", await isOpen());

    // 2. Esc 关闭
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    check("Esc → 关闭", !(await isOpen()));

    // 3. 再打开 → 点外部关闭
    await menuBtn.click();
    await page.waitForTimeout(400);
    check("再次点归类按钮 → 打开", await isOpen());
    await page.locator(".cl-title").click();
    await page.waitForTimeout(300);
    check("点页面标题（外部）→ 关闭", !(await isOpen()));

    // toggle 语义（注意上文「点外部关闭」后菜单已关：此处首点是"打开"）
    await menuBtn.click();
    await page.waitForTimeout(400);
    check("关闭状态点归类按钮 → 打开", await isOpen());
    await menuBtn.click();
    await page.waitForTimeout(400);
    check("打开状态再点同一归类按钮 → 关闭", !(await isOpen()));
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    check("收尾 Esc 清场 → 关闭", !(await isOpen()));
  }
  console.log(failed ? "RESULT: FAIL" : "RESULT: PASS");
} catch (e) {
  console.log("❌ 脚本异常 —", String(e).slice(0, 300));
} finally {
  await browser.close();
}
