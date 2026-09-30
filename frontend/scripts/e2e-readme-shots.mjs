/**
 * README 截图翻新（V5.9 UI）：重拍 3 张 README 配图替换 docs/screenshots/ 旧图。
 *   1-home.jpg          首页 Dashboard（V5.9 rail 4 项 + 驾驶舱）
 *   2-task-cases.jpg    任务详情抽屉 · 测试用例表格（31 条用例任务）
 *   3-rag-citations.jpg 会话回放 · RAG 引用溯源 chips（真实历史会话）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-shots-v59";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

try {
  // 登录 test 账号
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

  // ① 首页 Dashboard
  await page.waitForTimeout(2500); // 等驾驶舱数据（最近用例/最近运行）落地
  await page.screenshot({ path: `${SHOT_DIR}/1-home.png`, fullPage: false });
  console.log("✅ 1-home.png");

  // ② 任务详情抽屉 · 用例列表（经用例库页进入，31 条用例任务「登录跳转首页」）
  await page.click('button[aria-label^="用例库"]');
  await page.waitForSelector(".cases-page", { timeout: 8000 });
  await page.waitForTimeout(1200);
  // 找到「登录跳转首页」行点开抽屉
  const row = page.locator("tbody tr", { hasText: "登录跳转首页" }).first();
  await row.locator(".cl-name").click();
  await page.waitForSelector(".task-drawer.show", { timeout: 8000 });
  await page.waitForTimeout(1500); // 等导图渲染（默认思维导图 tab）
  // 切到「用例列表」tab
  await page.locator(".dtab", { hasText: "用例列表" }).first().click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${SHOT_DIR}/2-task-cases.png`, fullPage: false });
  console.log("✅ 2-task-cases.png");
  await page.keyboard.press("Escape"); // 关抽屉（若不支持再点遮罩）
  await page.waitForTimeout(500);

  // ③ 会话回放 · RAG 引用 chips（会话「登录功能」有 2 条 citations 消息）
  await page.click('.rail-btn:has-text("AI 会话")');
  await page.waitForSelector(".hist-item", { timeout: 8000 });
  await page.waitForTimeout(800);
  const conv = page.locator(".hist-item", { hasText: "登录功能" }).first();
  await conv.click();
  await page.waitForTimeout(2500); // 等消息回放渲染
  const citeCount = await page.locator(".cite-chip").count();
  // 引用 chips 在消息流后段时滚动到底部再截
  await page.keyboard.press("End");
  await page.evaluate(() => {
    const el = document.querySelector(".chat-messages, .msg-list, main");
    if (el) el.scrollTop = el.scrollHeight;
  });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${SHOT_DIR}/3-rag-citations.png`, fullPage: false });
  console.log(`✅ 3-rag-citations.png（cite-chip 共 ${citeCount} 个）`);
} catch (e) {
  console.error("❌ 脚本异常:", String(e).slice(0, 300));
  await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true }).catch(() => {});
  process.exit(1);
} finally {
  await browser.close();
}
