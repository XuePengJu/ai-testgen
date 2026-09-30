/**
 * M1 浏览器端到端验证（Playwright，V5.3 UI 适配）：
 *   ① 首开静默进访客（rail 用户区显示「访客」）
 *   ② guest 加密链路：/api/guest/token 返回 enc 密文 + 前端透明解密出访客身份
 *   ③ 退出 → 弹登录框
 *   ④ admin 登录（明文直通角色）→ rail 显示管理员
 *   ⑤ 退出 → 注册新 user（注册接口返回密文，加密链路）→ rail 显示用户
 *   ⑥ 清理钩子：admin API 删除本次注册的测试账号（防 e2e 残留累积）
 *   ⑦ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m1/
 *
 * 运行：node scripts/e2e-m1-browser.mjs（M1_URL 默认 http://localhost:8000，需后端已启动）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:8000";
const SHOT_DIR = "/tmp/e2e-m1";
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

// 收集应用发起的 JSON 接口响应体（用于 AES enc 密文断言；排除 SSE 流与密钥分发通道）
const apiBodies = [];
page.on("response", async (r) => {
  if (!r.url().includes("/api/")) return;
  if (r.url().includes("/chat/stream") || r.url().includes("/guest/token")) return;
  const ct = (r.headers()["content-type"] || "");
  if (!ct.includes("json")) return;
  try { apiBodies.push({ url: r.url(), body: await r.text() }); } catch { /* ignore */ }
});

/** rail 底部用户区身份断言 */
async function railIdentity(timeout = 10000) {
  await page.waitForSelector(".rail-user", { timeout });
  return {
    name: ((await page.textContent(".rail-user .rl-user-name")) || "").trim(),
    role: ((await page.textContent(".rail-user .rl-user-role")) || "").trim(),
  };
}

// ⑤ 注册时标记已有响应数，注册后的新增响应用于 user 加密断言
let apiCountBeforeRegister = 0;
let uname = ""; // 本次注册的测试账号名（⑥ 清理钩子用）
const regP = Promise.resolve(null);

try {
  // ① 首开 → 静默访客
  await page.goto(URL, { waitUntil: "networkidle" });
  const id1 = await railIdentity();
  if (id1.role === "访客") ok(`① 首开静默进访客（${id1.name} · 访客）`);
  else fail("① 访客标识异常", `${id1.name}/${id1.role}`);
  await page.screenshot({ path: `${SHOT_DIR}/1-guest-auto.png`, fullPage: true });

  // ② guest 加密链路：登录态业务接口返回 enc 密文，前端透明解密出访客身份
  await page.waitForTimeout(1200); // 等首屏业务接口（tasks/conversations 等）落地
  const encHit = apiBodies.find((b) => b.body.trimStart().startsWith('{"enc"'));
  if (encHit) ok(`② 业务接口返回 enc 密文 + 前端透明解密（AES 链路：${encHit.url.split("/api/")[1]?.slice(0, 30)}）`);
  else fail("② guest 加密链路异常", `捕获 ${apiBodies.length} 个响应，无 enc 密文`);
  await page.screenshot({ path: `${SHOT_DIR}/2-guest-crypto-check.png`, fullPage: true });

  // ③ 退出 → 弹登录框（logout 会主动打开登录模态）
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  ok("③ 退出后弹登录框");
  await page.screenshot({ path: `${SHOT_DIR}/3-login-modal.png`, fullPage: false });

  // ④ admin 登录（明文直通角色）
  await page.fill('.auth-modal input[placeholder="用户名"]', "admin");
  await page.fill('.auth-modal input[placeholder="密码"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForFunction(
    () => document.querySelector(".rail-user .rl-user-role")?.textContent?.trim() === "管理员",
    undefined,
    { timeout: 10000 },
  );
  const idA = await railIdentity();
  if (idA.name === "admin") ok("④ admin 登录成功（rail 显示 管理员 · admin）");
  else ok(`④ admin 登录成功（rail 显示 ${idA.name} · 管理员）`);
  await page.screenshot({ path: `${SHOT_DIR}/4-admin-plain.png`, fullPage: true });

  // ⑤ 退出 → 注册新 user（加密链路 + 三级角色最后一环）
  await page.click('button[aria-label="退出登录"]');
  await page.waitForSelector(".auth-modal", { timeout: 8000 });
  apiCountBeforeRegister = apiBodies.length;
  await page.click(".auth-tabs button >> nth=1"); // 注册 tab
  uname = "m1verify" + Date.now().toString(36); // 本次注册的测试账号名（⑥ 清理钩子用）
  await page.fill('.auth-modal input[placeholder="用户名"]', uname);
  await page.fill('.auth-modal input[placeholder="邮箱"]', `${uname}@163.com`);
  await page.fill('.auth-modal input[placeholder="密码"]', "Passw0rd123");
  await page.click(".auth-btn");
  try {
    await page.waitForFunction(
      (u) => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === u,
      uname,
      { timeout: 12000 },
    );
  } catch {
    const err = await page.textContent(".auth-error").catch(() => "(无错误提示)");
    fail("⑤ 注册失败", err);
    await page.screenshot({ path: `${SHOT_DIR}/5-register-fail.png`, fullPage: true });
    throw new Error("register failed: " + err);
  }
  const idU = await railIdentity();
  if (idU.role === "用户") ok(`⑤ 注册 user 登录成功（${idU.name} · 用户）`);
  else fail("⑤ user 标识异常", `${idU.name}/${idU.role}`);
  await page.waitForTimeout(1200); // 等 user 身份的业务接口落地
  const encUser = apiBodies.slice(apiCountBeforeRegister).find((b) => b.body.trimStart().startsWith('{"enc"'));
  if (encUser) ok("⑤ user 登录态业务接口返回 enc 密文（加密链路）");
  else fail("⑤ user 加密链路异常", "注册后响应无 enc 密文");
  await page.screenshot({ path: `${SHOT_DIR}/5-user-crypto-check.png`, fullPage: true });

  // ⑥ 清理钩子：admin API 删除本次注册的测试账号（注册→测完→删号，不残留）
  try {
    const lr = await fetch(`${URL}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username: "admin", password: "Admin@123" }),
    });
    if (!lr.ok) throw new Error("admin 登录失败 HTTP " + lr.status);
    const auth = "Bearer " + ((await lr.json()).access_token || "");
    const users = await (await fetch(`${URL}/api/users`, { headers: { Authorization: auth } })).json();
    const target = (Array.isArray(users) ? users : []).find((u) => u.username === uname);
    if (!target) throw new Error("未找到本次注册的账号 " + uname);
    const dr = await fetch(`${URL}/api/users/${target.id}`, {
      method: "DELETE",
      headers: { Authorization: auth },
    });
    if (dr.ok) ok(`⑥ 清理钩子：测试账号 ${uname}（id=${target.id}）已删除`);
    else fail("⑥ 清理钩子失败", `删除 HTTP ${dr.status}`);
  } catch (e) {
    fail("⑥ 清理钩子异常", String(e));
  }

  if (errors.length) fail("JS 错误", errors.slice(0, 3).join(" | "));
  else ok("全程 0 JS 错误");
} catch (e) {
  fail("运行异常", String(e));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(
    `${SHOT_DIR}/result.txt`,
    results.join("\n") + `\n\n截图目录: ${SHOT_DIR}\n时间: ${new Date().toISOString()}\n`,
  );
  const failed = results.some((r) => r.startsWith("❌"));
  console.log(failed ? "\n存在失败项，见 /tmp/e2e-m1/result.txt" : "\n全部通过");
  process.exit(failed ? 1 : 0);
}
