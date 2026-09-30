/**
 * V5.11 会话归档豁免规则目检：
 * ① admin：有 running 任务（电商订单流程）的短会话不被折叠 → 出现在主列表
 * ② test：历史短会话（消息<4 且过今天）仍折叠进「已归档」
 * ③ test：把一条历史短会话 updated_at 改成今天 → 刷新后出现在「今天」分组（随后恢复）
 */
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:8000";
const SHOT_DIR = "/tmp/e2e-v511";
import fs from "fs";
fs.mkdirSync(SHOT_DIR, { recursive: true });

let okSteps = 0, failSteps = 0;
function step(name, ok, extra = "") {
  if (ok) { okSteps++; console.log(`✅ ${name} ${extra}`); }
  else { failSteps++; console.log(`❌ ${name} ${extra}`); }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function logoutIfNeeded() {
  const btn = page.locator('button[aria-label="退出登录"]');
  if (await btn.count() && await btn.isVisible().catch(() => false)) {
    await btn.click();
    await page.waitForTimeout(800);
  }
}

async function login(user, pass) {
  await logoutIfNeeded();
  await page.waitForSelector(".auth-modal", { timeout: 5000 });
  await page.locator('.auth-modal input').nth(0).fill(user);
  await page.locator('.auth-modal input[type="password"]').fill(pass);
  await page.locator(".auth-btn").last().click();
  await page.waitForTimeout(1500);
}

// 场景 ①：admin 登录 —— running 任务会话豁免折叠
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await login("admin", "Admin@123");
await page.waitForTimeout(2500);
const convPanel = page.locator(".conv-panel");
const runningConvVisible = await convPanel.getByText("电商订单流程", { exact: false }).first().isVisible().catch(() => false);
step("① admin 主列表可见 running 任务会话「电商订单流程」", runningConvVisible);
await page.screenshot({ path: `${SHOT_DIR}/1-admin-running-conv-visible.png`, fullPage: false });

// 场景 ②：test 登录 —— 历史短会话仍折叠
await login("test", "test1234");
await page.waitForTimeout(2500);
const loginPageInMain = await convPanel.getByText("登录页面", { exact: false }).first().isVisible().catch(() => false);
const archivedToggle = await convPanel.getByText("已归档").first().isVisible().catch(() => false);
step("② test 历史短会话「登录页面」已折叠（主列表不可见）", !loginPageInMain);
step("② test 归档分组入口可见", archivedToggle);
await page.screenshot({ path: `${SHOT_DIR}/2-test-folded.png`, fullPage: false });

// 场景 ③：DB 把 a812b7b8bdb0（登录页面，mc=2）updated_at 改为今天 → 刷新应出现在「今天」
import { execSync } from "child_process";
const PY = "/Users/xp/Documents/软件测试示例项目/ai-testflow/.venv/bin/python";
fs.writeFileSync("/tmp/e2e-v511/touch_conv.py", `
import pymysql
conn = pymysql.connect(host='127.0.0.1', port=3306, user='aitf', password='aitf_dev_2026', database='ai-testflow', charset='utf8mb4')
cur = conn.cursor()
cur.execute("SELECT updated_at FROM conversations WHERE id='a812b7b8bdb0'")
old = cur.fetchone()[0].strftime('%Y-%m-%d %H:%M:%S')
cur.execute("UPDATE conversations SET updated_at=UTC_TIMESTAMP() WHERE id='a812b7b8bdb0'")
conn.commit()
print(old)
conn.close()`);
const oldTs = execSync(`${PY} /tmp/e2e-v511/touch_conv.py`, { encoding: "utf8" }).trim();
console.log("   (updated_at 原值:", oldTs, "→ 已改为今天)");

await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(2500);
const freshVisible = await convPanel.getByText("登录页面", { exact: false }).first().isVisible().catch(() => false);
const todayLabel = await convPanel.getByText("今天", { exact: true }).first().isVisible().catch(() => false);
step("③ 改成今天后短会话豁免折叠，出现在主列表", freshVisible);
step("③ 「今天」分组渲染", todayLabel);
await page.screenshot({ path: `${SHOT_DIR}/3-test-fresh-today.png`, fullPage: false });

// 恢复 updated_at
fs.writeFileSync("/tmp/e2e-v511/restore_conv.py", `
import pymysql
conn = pymysql.connect(host='127.0.0.1', port=3306, user='aitf', password='aitf_dev_2026', database='ai-testflow', charset='utf8mb4')
cur = conn.cursor()
cur.execute("UPDATE conversations SET updated_at='%s' WHERE id='a812b7b8bdb0'" % "${oldTs}")
conn.commit(); conn.close()`);
execSync(`${PY} /tmp/e2e-v511/restore_conv.py`);
step("③ updated_at 已恢复原值", true);

await browser.close();
console.log(`\n结果：${okSteps} 通过 / ${failSteps} 失败；截图目录 ${SHOT_DIR}`);
process.exit(failSteps ? 1 : 0);
