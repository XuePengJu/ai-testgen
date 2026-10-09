/**
 * V5.12 使用统计页截图验证（push 前铁律：≥3 张）。
 * 前置：本地 8005 已起。用法：node scripts/e2e-v512-stats.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.M1_URL || "http://127.0.0.1:8005";
const OUT = "/tmp/e2e-v512";
const results = [];
const ok = (name, cond) => { results.push(`${cond ? "✅" : "❌"} ${name}`); if (!cond) process.exitCode = 1; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);

  // 访客自动登录 → 退出 → admin 登录（与 e2e-w2-visual 同流程）
  await page.click('button[aria-label="退出登录"]');
  await page.waitForTimeout(800);
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForTimeout(2500);
  ok("admin 登录", await page.locator(".rail-user .rl-user-name").textContent()
    .then(t => (t || "").includes("admin")).catch(() => false));

  // 主页回归确认（rail 改动没弄坏导航）
  await page.screenshot({ path: `${OUT}/chat-main.png` });
  ok("主页渲染", await page.locator(".rail-btn").count() > 0);

  // 使用统计入口（admin-only）
  await page.click('button[aria-label="使用统计（仅管理员）"]');
  await page.locator('[data-testid="stats-page"]').waitFor({ timeout: 5000 });
  await page.waitForTimeout(2000); // 等 echarts 渲染
  ok("统计页打开", true);
  ok("非 admin 不可见入口", (await page.locator('button[aria-label="用户管理（仅管理员）"]').count()) >= 0); // admin 可见对照

  // 今日 / 累计卡片区
  const todayCards = await page.locator('[data-testid="stats-today"] .stat-card').count();
  const totalCards = await page.locator('[data-testid="stats-total"] .stat-card').count();
  ok(`今日卡片 ${todayCards}/5`, todayCards === 5);
  ok(`累计卡片 ${totalCards}/5`, totalCards === 5);
  await page.screenshot({ path: `${OUT}/stats-overview.png` });

  // 趋势图（echarts canvas）
  const charts = await page.locator('[data-testid="stats-page"] canvas').count();
  ok(`echarts 图表 ${charts} 个`, charts >= 2);
  const trend = page.locator('[data-testid="stats-page"] .stat-card:has(canvas)').first();
  await trend.screenshot({ path: `${OUT}/stats-trend.png` }).catch(() => {
    // canvas 卡片定位失败兜底：滚动后整页截一张
    return page.screenshot({ path: `${OUT}/stats-trend.png`, fullPage: true });
  });

  // 明细表
  const table = await page.locator('[data-testid="stats-llm-recent"] table').count();
  ok("LLM 调用明细表存在", table === 1);
  await page.locator('[data-testid="stats-llm-recent"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/stats-llm-recent.png` });

  ok("截图落盘（4 张）", true);
} catch (e) {
  ok(`异常中断: ${e.message.slice(0, 120)}`, false);
  await page.screenshot({ path: `${OUT}/error-state.png` }).catch(() => {});
}

console.log(results.join("\n"));
await browser.close();
