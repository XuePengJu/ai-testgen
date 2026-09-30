/**
 * e2e-v511-dir-upload.mjs — V5.11 知识库目录上传验收
 *
 * 验证点：
 * 1. 存在 webkitdirectory 隐藏 input（选择文件夹入口）
 * 2. 目录上传管线：setInputFiles 喂 webkitdirectory input（3 合法 + 1 隐藏 + 1 非法）：
 *    根 2 个（md+txt）+ 子目录 1 个 md → 3 个全部入库；
 *    .exe 自动拒绝（汇总 toast），.DS_Store 隐藏文件静默跳过
 * 3. 汇总 toast「批量上传完成：成功 3 / 失败 0」+ 列表 3 篇
 *
 * 运行：env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
 *       NODE_PATH=~/.workbuddy/binaries/node/workspace/node_modules \
 *       node scripts/e2e-v511-dir-upload.mjs
 */
import { chromium } from "playwright";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const URL = process.env.E2E_URL || "http://127.0.0.1:8000";

const step = (ok, name) => console.log(`${ok ? "✅" : "❌"} 步骤: ${name}`);
const shot = (page, name) => page.screenshot({ path: `/tmp/${name}`, fullPage: false });

const browser = await chromium.launch();
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  // 1. admin 登录
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForSelector(".rail", { timeout: 10000 });
  step(true, "admin 登录");

  // 2. 建临时验收库
  const loginRes = await fetch(`${URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "username=admin&password=Admin@123",
  });
  const { access_token } = await loginRes.json();
  const H = { Authorization: `Bearer ${access_token}` };
  const kbRes = await fetch(`${URL}/api/knowledge/bases`, {
    method: "POST", headers: { ...H, "Content-Type": "application/json" },
    body: JSON.stringify({ name: "e2e-v511-dir-upload", description: "目录上传验收临时库" }),
  });
  if (!kbRes.ok) throw new Error(`创建知识库失败 HTTP ${kbRes.status}`);
  const kb = await kbRes.json();

  // 3. 进知识库页选中验收库
  await page.evaluate((id) => localStorage.setItem("aitf_kb_sel", id), kb.id);
  await page.click('.rail-btn:has-text("知识库")');
  await page.waitForSelector(".kb-dropzone", { timeout: 10000 });

  // 4. 目录入口就位：webkitdirectory input + 上传区文案
  const dirInput = await page.locator('input[webkitdirectory]').count();
  step(dirInput === 1, "存在 webkitdirectory 目录选择 input");
  const dzText = await page.textContent(".kb-dropzone");
  step(!!dzText && dzText.includes("选择文件夹"), "上传区有「选择文件夹」入口");

  // 5. 目录上传：Playwright 对 webkitdirectory input 支持传目录路径（原生全链路，
  //    含 webkitRelativePath 与子目录递归）：
  //    3 个合法（含子目录 c.md）+ .DS_Store 静默跳过 + .exe 拒绝
  const dirTmp = mkdtempSync(join(tmpdir(), "e2e-v511-dirfiles-"));
  writeFileSync(join(dirTmp, "a.md"), "# 目录验收 a\n" + "内容行。\n".repeat(30));
  writeFileSync(join(dirTmp, "b.txt"), "目录验收 b\n" + "内容行。\n".repeat(20));
  mkdirSync(join(dirTmp, "sub"));
  writeFileSync(join(dirTmp, "sub", "c.md"), "# 目录验收 c（子目录）\n" + "内容行。\n".repeat(25));
  writeFileSync(join(dirTmp, ".DS_Store"), "junk");
  writeFileSync(join(dirTmp, "bad.exe"), "MZjunk");
  await page.setInputFiles('input[webkitdirectory]', dirTmp);
  // 汇总 toast 包含成功/失败/跳过三段（跳过信息并入汇总，避免被进度 toast 顶掉）
  await page.waitForFunction(
    () => document.body.innerText.includes("批量上传完成：成功 3 / 失败 0，跳过 1 个"),
    null, { timeout: 240000 },
  );
  step(true, "目录上传：md×2 + txt×1 全部入库；.exe 拒绝 + .DS_Store 跳过均汇总提示");
  await shot(page, "e2e-v511-dir-1-done.png");

  // 6. 列表 3 篇
  const rows = await page.locator(".kb-panel tbody tr, .kb-table tbody tr, [class*='doc-row']").count();
  step(rows >= 3, `文档列表展示 ${rows} 篇（≥3）`);
  await shot(page, "e2e-v511-dir-2-list.png");

  // 7. 清理
  const delRes = await fetch(`${URL}/api/knowledge/bases/${kb.id}`, { method: "DELETE", headers: H });
  step(delRes.ok, "清理验收知识库");
  rmSync(dirTmp, { recursive: true, force: true });
} catch (e) {
  step(false, `异常中断：${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
