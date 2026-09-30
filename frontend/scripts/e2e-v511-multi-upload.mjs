/**
 * e2e-v511-multi-upload.mjs — V5.11 知识库批量上传验收
 *
 * 验证点：
 * 1. 文档上传 input 带 multiple、且不再设 accept（macOS md 置灰地雷移除）
 * 2. 一次选择 3 个 md 文件 → 逐个串行入库 → 汇总 toast「批量上传完成：成功 3 / 失败 0」
 * 3. 文档列表出现 3 篇新文档
 *
 * 运行：env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
 *       NODE_PATH=~/.workbuddy/binaries/node/workspace/node_modules \
 *       node scripts/e2e-v511-multi-upload.mjs
 * 截图：/tmp/e2e-v511-*.png
 */
import { chromium } from "playwright";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const URL = process.env.E2E_URL || "http://127.0.0.1:8000";
const SHOT_DIR = "/tmp";

const step = (ok, name) => console.log(`${ok ? "✅" : "❌"} 步骤: ${name}`);
const shot = (page, name) => page.screenshot({ path: join(SHOT_DIR, name), fullPage: false });

const tmp = mkdtempSync(join(tmpdir(), "e2e-v511-"));
const files = ["_alpha", "beta", "gamma"].map(
  (n) => {
    const p = join(tmp, `v511-${n}.md`);
    writeFileSync(p, `# V5.11 批量上传验收 ${n}\n\n这是一篇用于 e2e 的临时文档 ${n}，内容重复填充：\n${"测试内容行。\n".repeat(30)}`);
    return p;
  },
);

const browser = await chromium.launch();
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  // 1. 登录（默认访客自动登录 → 先退出 → admin 登录）
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForSelector(".rail", { timeout: 10000 });
  step(true, "admin 登录");

  // 2. API 建独立验收库（避免污染既有库），完成后删除
  const loginRes = await fetch(`${URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "username=admin&password=Admin@123",
  });
  const { access_token } = await loginRes.json();
  const H = { Authorization: `Bearer ${access_token}` };
  const kbRes = await fetch(`${URL}/api/knowledge/bases`, {
    method: "POST", headers: { ...H, "Content-Type": "application/json" },
    body: JSON.stringify({ name: "e2e-v511-multi-upload", description: "V5.11 批量上传验收临时库" }),
  });
  if (!kbRes.ok) throw new Error(`创建知识库失败 HTTP ${kbRes.status}`);
  const kb = await kbRes.json();
  step(true, `创建验收知识库 ${kb.id}`);

  // 3. 进知识库页并选中该库（localStorage 锚定 + 刷新）
  await page.evaluate((id) => localStorage.setItem("aitf_kb_sel", id), kb.id);
  await page.click('.rail-btn:has-text("知识库")');
  await page.waitForSelector(".kb-dropzone", { timeout: 10000 });
  const headName = await page.textContent(".kb-lib-name");
  step(headName === "e2e-v511-multi-upload", `选中验收库（当前库：${headName}）`);

  // 4. 断言 input multiple 且无 accept 属性
  const multi = await page.getAttribute('input[type="file"]', "multiple");
  const acceptAttr = await page.getAttribute('input[type="file"]', "accept");
  step(multi !== null, "上传 input 支持 multiple 多选");
  step(acceptAttr === null, "上传 input 已移除 accept 属性（macOS md 置灰地雷）");

  await shot(page, "e2e-v511-1-dropzone.png");

  // 5. 一次选择 3 个 md → 等批量汇总 toast（串行入库，放宽超时）
  await page.setInputFiles('input[type="file"]', files);
  await page.waitForFunction(
    () => document.body.innerText.includes("批量上传完成：成功 3 / 失败 0"),
    null, { timeout: 240000 },
  );
  step(true, "批量上传 3 个 md 全部入库成功（汇总 toast 出现）");
  await shot(page, "e2e-v511-2-batch-done.png");

  // 6. 文档列表出现 3 篇
  await page.waitForFunction(
    () => document.querySelectorAll(".kb-panel tbody tr, .kb-table tbody tr, [class*='doc-row']").length >= 3,
    { timeout: 15000 },
  );
  const rows = await page.locator(".kb-panel tbody tr, .kb-table tbody tr, [class*='doc-row']").count();
  step(rows >= 3, `文档列表展示 ${rows} 篇（≥3）`);
  await shot(page, "e2e-v511-3-doc-list.png");

  // 7. 清理：删除验收库
  const delRes = await fetch(`${URL}/api/knowledge/bases/${kb.id}`, { method: "DELETE", headers: H });
  step(delRes.ok, "清理验收知识库");
} catch (e) {
  step(false, `异常中断：${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
