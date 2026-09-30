/**
 * M2 浏览器端到端验证（Playwright，V5.3 UI 适配）：
 *   ① 静默进访客 → 退出 → **test 账户登录**（2026-09-26 起弃用访客执行：访客任务上限
 *      GUEST_MAX_TASKS=10 曾被历史运行占满导致创建 429；test 为注册用户无配额限制，
 *      且对话走平台池真实模型链路）
 *   ② 新建会话 → 发消息 → 流式回复 → 出现「✨ 生成测试用例」按钮
 *   ③ 点击生成 → 任务创建 → 消息升级任务卡 → 轮询到「✓ 已完成」
 *   ④ 任务列表出现新任务 → 点击打开详情抽屉
 *   ⑤ 新建会话 → 切回旧会话回放历史消息
 *   ⑥ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m2/
 *
 * 运行：node scripts/e2e-m2-browser.mjs（M2_URL 默认 http://localhost:8000，需后端已启动）
 * 依赖：test / test1234 账号（本地种子账号）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M2_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-m2";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
function ok(name) {
  results.push(`✅ ${name}`);
  console.log(`✅ ${name}`);
}
function fail(name, detail) {
  results.push(`❌ ${name}: ${detail}`);
  console.error(`❌ ${name}: ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
// 删除任务走 window.confirm，统一接受
page.on("dialog", (d) => void d.accept());

try {
  // ① 进入首页（默认访客态）→ 退出 → test 账户登录
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  for (const sel of [".conv-panel", ".chat-panel"]) {
    await page.waitForSelector(sel, { timeout: 8000 });
  }
  ok("① 两栏布局渲染（历史会话 | 对话流）—— V5.5 右栏任务列表已移除");
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  await page.fill('.auth-modal input[placeholder="用户名"]', "test");
  await page.fill('.auth-modal input[placeholder="密码"]', "test1234");
  await page.click(".auth-btn");
  await page.waitForFunction(
    () => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === "test",
    undefined,
    { timeout: 10000 },
  );
  const roleText = ((await page.textContent(".rail-user .rl-user-role")) || "").trim();
  if (roleText === "用户") ok("① test 账户登录成功（test · 用户）");
  else fail("① test 登录后角色异常", roleText);
  // reload 清掉访客态残留（访客会话消息卡轮询会以 test token 请求 guest 任务 → 权限 404）
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".rail-user", { timeout: 10000 });
  await page.screenshot({ path: `${SHOT_DIR}/1-layout.png`, fullPage: true });

  // ①b 新建会话：test 账户有历史会话，旧讨论回复可能干扰生成按钮定位
  await page.click(".conv-panel .side-head .qtag");
  await page.waitForSelector(".chat-panel .welcome", { timeout: 8000 });
  ok("① 新建会话，空白欢迎页");

  // ①c 记录用例库基线行数（V5.5：新用例集入库断言用）
  await page.click('button[aria-label^="用例库"]');
  await page.waitForSelector(".cases-page", { timeout: 8000 });
  const libRowsBefore = await page.locator(".cases-page tbody tr").count();
  ok(`①c 用例库基线 ${libRowsBefore} 条`);
  await page.click('button[aria-label^="AI 会话"]');
  await page.waitForSelector(".chat-panel .welcome", { timeout: 8000 });

  // ② 发消息 → mock 流式回复 → 生成用例确认按钮
  await page.fill(".chat-panel textarea", "测试一个登录页面：输入正确的用户名和密码后跳转首页");
  await page.click(".chat-panel .send-btn");
  await page.waitForSelector(".msg.msg-ai", { timeout: 15000 });
  ok("② 发送消息，AI 回复开始流式输出");
  await page.waitForSelector(".qtag.confirm-btn", { timeout: 60000 });
  ok("② 回复完成，出现「✨ 生成测试用例」按钮");
  await page.screenshot({ path: `${SHOT_DIR}/2-reply.png`, fullPage: true });

  // ③ 点击生成 → 任务卡 → 轮询到完成
  await page.click(".qtag.confirm-btn");
  // 任务卡：mock 链路渲染 .task / .tsc-io；平台池真实链路渲染 .task-steps-card
  await page.waitForSelector(".msg-ai .task, .msg-ai .tsc-io, .msg-ai .task-steps-card", { timeout: 20000 });
  ok("③ 任务创建，消息升级为任务卡");
  await page.waitForFunction(
    () => (document.querySelector(".msg-ai")?.textContent || "").includes("已完成"),
    undefined,
    { timeout: 180000 }, // test 账户走平台池真实 workflow，比 mock 慢
  );
  ok("③ 任务轮询到「✓ 已完成」");
  await page.screenshot({ path: `${SHOT_DIR}/3-task-card.png`, fullPage: true });

  // ④ 用例库出现新用例集 → 打开详情抽屉（V5.5：右栏任务列表移除，断言迁到用例库页）
  await page.click('button[aria-label^="用例库"]');
  await page.waitForSelector(".cases-page", { timeout: 8000 });
  await page.waitForFunction(
    (n) => document.querySelectorAll(".cases-page tbody tr").length > n,
    libRowsBefore,
    { timeout: 30000 },
  );
  ok("④ 用例库出现新生成的用例集");
  await page.locator(".cases-page tbody tr").first().locator(".cl-name").click();
  await page.waitForSelector(".drawer-head .drawer-title", { timeout: 10000 });
  ok("④ 点击用例集打开详情抽屉");
  await page.screenshot({ path: `${SHOT_DIR}/4-drawer.png`, fullPage: false });
  await page.keyboard.press("Escape"); // 关抽屉，避免遮挡后续步骤

  // ⑤ 回 AI 会话 → 新建会话 → 切回旧会话回放历史
  await page.click('button[aria-label^="AI 会话"]');
  await page.waitForSelector(".conv-panel", { timeout: 8000 });
  await page.click(".conv-panel .side-head .qtag");
  await page.waitForSelector(".chat-panel .welcome", { timeout: 8000 });
  ok("⑤ 新建会话回到空白欢迎页");
  await page.click(".conv-panel .hist-item >> nth=0");
  await page.waitForSelector(".msg.msg-user", { timeout: 8000 });
  const msgCount = await page.locator(".msg").count();
  if (msgCount >= 2) ok(`⑤ 切回旧会话，历史消息回放（${msgCount} 条）`);
  else fail("⑤ 历史回放消息数不足", String(msgCount));
  await page.screenshot({ path: `${SHOT_DIR}/5-replay.png`, fullPage: true });

  if (errors.length) fail("JS 错误", errors.slice(0, 3).join(" | "));
  else ok("全程 0 JS 错误");
} catch (e) {
  fail("流程异常中断", String(e));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(
    `${SHOT_DIR}/result.txt`,
    results.join("\n") + `\n\n截图目录: ${SHOT_DIR}\n时间: ${new Date().toISOString()}\n`,
  );
  const failed = results.some((r) => r.startsWith("❌"));
  console.log(failed ? "\n存在失败项" : "\n全部通过");
  process.exit(failed ? 1 : 0);
}
