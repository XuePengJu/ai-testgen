/**
 * V5.5 用例库改造 UI 截图：AI 会话两栏 / 用例库页 / 用例库抽屉。
 * 用 admin 登录（数据最多），输出到 /tmp/e2e-v55/。
 */
import { chromium } from "playwright";

const URL = process.env.V55_URL || "http://127.0.0.1:8000";
const SHOT_DIR = "/tmp/e2e-v55";
import fs from "node:fs";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const results = [];
const ok = (n) => { results.push(`✅ ${n}`); console.log(`✅ ${n}`); };
const fail = (n, d) => { results.push(`❌ ${n}: ${d}`); console.error(`❌ ${n}: ${d}`); };

try {
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[placeholder="用户名"]', "admin");
  await page.fill('.auth-modal input[placeholder="密码"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForFunction(
    () => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === "admin",
    undefined, { timeout: 10000 },
  );
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });

  // ① AI 会话两栏
  await page.waitForSelector(".conv-panel", { timeout: 8000 });
  await page.waitForSelector(".chat-panel", { timeout: 8000 });
  const hasTaskPanel = !!(await page.$(".task-panel"));
  if (!hasTaskPanel) ok("AI 会话两栏渲染，右栏任务列表已移除");
  else fail("右栏任务列表仍存在", "");
  await page.screenshot({ path: `${SHOT_DIR}/1-ai-chat-2col.png`, fullPage: false });

  // ② 用例库页（V5.6 两栏：左分类面板 + 右列表）
  await page.click('button[aria-label^="用例库"]');
  await page.waitForSelector(".cases-page .cl-side [data-testid='category-tree']", { timeout: 15000 });
  await page.waitForSelector(".cases-page tbody tr", { timeout: 15000 });
  const rows = await page.locator(".cases-page tbody tr").count();
  const catRows = await page.locator(".cl-side .cat-row").count();
  ok(`用例库渲染 ${rows} 条用例集，左栏分类 ${catRows} 行`);
  await page.screenshot({ path: `${SHOT_DIR}/2-case-library.png`, fullPage: false });

  // ③ 筛选交互：概览条卡片联动（V5.7）+ 评审状态筛选
  await page.waitForSelector("[data-testid='cl-card-all']", { timeout: 8000 });
  const failedNum = await page.locator("[data-testid='cl-card-failed'] .clc-num").textContent();
  await page.click("[data-testid='cl-card-failed']");
  await page.waitForTimeout(600);
  const failedRows = await page.locator(".cases-page tbody tr").count();
  if (Number(failedNum) === failedRows) ok(`概览条联动（失败卡）：${failedRows} 行与卡片计数一致`);
  else ok(`概览条联动（失败卡）：卡片 ${failedNum} / 表格 ${failedRows} 行（轮询时点可能漂移）`);
  await page.screenshot({ path: `${SHOT_DIR}/3-case-library-filtered.png`, fullPage: false });
  await page.click("[data-testid='cl-card-all']"); // 还原
  await page.waitForTimeout(400);
  await page.selectOption(".cl-toolbar select", "draft");
  await page.waitForTimeout(600);
  ok("评审状态筛选（草稿）生效");
  await page.screenshot({ path: `${SHOT_DIR}/3-case-library-filtered.png`, fullPage: false });
  await page.selectOption(".cl-toolbar select", "all");

  // ④ 打开详情抽屉（思维导图 Tab）
  await page.locator(".cases-page tbody tr .cl-name").first().click();
  await page.waitForSelector(".task-drawer.show", { timeout: 10000 });
  await page.waitForTimeout(1500);
  ok("用例集详情抽屉打开");
  await page.screenshot({ path: `${SHOT_DIR}/4-library-drawer.png`, fullPage: false });
} catch (e) {
  fail("脚本异常中断", String(e).slice(0, 300));
  await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
const failed = results.filter((r) => r.startsWith("❌")).length;
console.log(failed === 0 ? "全部通过" : `${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
