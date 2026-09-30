import { chromium } from "playwright";

const BASE = "http://127.0.0.1:8000";
const OUT = "/tmp/e2e-w2";
const results = [];
const ok = (name, cond) => { results.push(`${cond ? "✅" : "❌"} ${name}`); if (!cond) process.exitCode = 1; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);

  // 访客自动登录 → 退出 → admin 登录
  await page.click('button[aria-label="退出登录"]');
  await page.waitForTimeout(800);
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForTimeout(2500);
  ok("admin 登录", await page.locator(".rail-user .rl-user-name").textContent().then(t => (t || "").includes("admin")).catch(() => false));

  // 知识库页 - 文档 Tab（M6：库头统计 + 拖拽区 + 轻行）
  await page.click('.rail-btn:has-text("知识库")');
  await page.waitForTimeout(2000);
  await page.locator('.kb-lib-head, [class*="lib-head"]').first().waitFor({ timeout: 5000 }).catch(() => {});
  const dropzone = await page.locator('[class*="dropzone"]').count();
  const docRows = await page.locator('[class*="doc-row"]').count();
  ok("M6 拖拽上传区存在", dropzone > 0);
  ok(`M6 文档轻行渲染（${docRows} 行）`, docRows > 0);
  const libHead = await page.locator('[class*="kb-lib-head"]').count();
  ok("M6 库头统计条存在", libHead > 0);
  await page.screenshot({ path: `${OUT}/kb-docs-tab.png`, fullPage: false });

  // Wiki Tab（M7：筛选 chips + 分类徽章 + AI 归类按钮）
  await page.locator('button:has-text("Wiki")').first().click();
  await page.waitForTimeout(2000);
  const chips = await page.locator('[class*="wiki-chip"]').count();
  ok(`M7 筛选 chips 渲染（${chips} 个）`, chips > 0);
  await page.screenshot({ path: `${OUT}/kb-wiki-tab.png`, fullPage: false });

  // 回 AI 会话页（整体视图 + 会话列表治理上下文）
  await page.click('.rail-btn:has-text("AI 会话")');
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/chat-main.png`, fullPage: false });

  ok("截图落盘", true);
} catch (e) {
  ok(`异常中断: ${e.message.slice(0, 120)}`, false);
  await page.screenshot({ path: `${OUT}/error-state.png` }).catch(() => {});
}

console.log(results.join("\n"));
await browser.close();
