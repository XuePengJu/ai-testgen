/**
 * M5 真实大模型冒烟验证（Playwright，V5.3 UI 适配）：
 *   ① UI 登录（访客态 → 注册新用户）
 *   ② 模型配置页：调度摘要显示「平台模型池接管」（真实 Key 由平台池提供，admin 维护）
 *   ③ 回工作台发消息 → SSE 真实流式回复（非 mock 模板、非失败兜底提示）
 *   ④ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m5/
 *
 * 运行：node scripts/e2e-m5-smoke.mjs
 * 前置：后端 8000 启动；平台模型池（user_id=0）至少一条可用候选
 *
 * 设计依据（llm_service.chat_stream）：source in (user, platform) 都走真实调用 ——
 * 用户自配池的 UI 路径由 M4 ③④ 覆盖；本冒烟验证「注册用户经平台池出真实回复」的
 * 生产默认链路。个人自配 Key 冒烟曾走 .env 的 MODELSCOPE Key，2026-09-26 起
 * ModelScope 429 配额尽、DASHSCOPE/ZHIPU Key 失效，故切换为平台池路径。
 * mock 判定：模板文案「好的，关于「<用户原文>」，我先理一下：…」
 * 失败兜底判定：回复含「真实模型调用失败」notice。
 */
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const URL = process.env.M5_URL || "http://localhost:8000";
const API = URL + "/api";
const SHOT_DIR = "/tmp/e2e-m5";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const USER = "e2e_m5_smoke";
const EMAIL = "e2e-m5-smoke@e2e-testmail.com";
const PWD = "M5Smoke!2026";

const results = [];
function ok(name) { results.push(`✅ ${name}`); console.log(`✅ ${name}`); }
function fail(name, detail) { results.push(`❌ ${name}: ${detail}`); console.error(`❌ ${name}: ${detail}`); }

/* DB 清理：后端 .env 是 DB_TYPE=mysql → pymysql；sqlite 配置兜底 app.db */
const ENV_TXT = fs.readFileSync(`${ROOT}/.env`, "utf-8");
function envVal(k) {
  return (ENV_TXT.match(new RegExp(`^${k}=(.*)$`, "m")) || [])[1]?.trim() || "";
}
function dbExec(sql) {
  let py;
  if ((envVal("DB_TYPE") || "sqlite").toLowerCase() === "mysql") {
    py = `
import pymysql, json
from pymysql.constants import CLIENT
cfg = json.loads(${JSON.stringify(JSON.stringify({
      host: envVal("DB_HOST") || "127.0.0.1",
      port: envVal("DB_PORT") || "3306",
      user: envVal("DB_USER"),
      password: envVal("DB_PASSWORD"),
      database: envVal("DB_NAME"),
    }))})
c = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
                    password=cfg["password"], database=cfg["database"],
                    client_flag=CLIENT.MULTI_STATEMENTS)
cur = c.cursor(); cur.execute(${JSON.stringify(sql)}); c.commit(); c.close()`;
  } else {
    py = `import sqlite3;c=sqlite3.connect(${JSON.stringify(`${ROOT}/app.db`)});c.executescript(${JSON.stringify(sql)});c.commit();c.close()`;
  }
  const pyBin = [`${ROOT}/.venv/bin/python`, `${ROOT}/venv/bin/python`, "python3"]
    .find((p) => p === "python3" || fs.existsSync(p));
  execFileSync(pyBin, ["-c", py], { stdio: "pipe" });
}

async function apiJson(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const d = await r.json().catch(() => ({}));
  return { status: r.status, ok: r.ok, data: d };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error" && !/Failed to load resource.*40[04]/.test(t)) errors.push(t);
});

try {
  // 准备：注册临时用户（已存在则忽略 400）
  await apiJson("/auth/register", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USER, email: EMAIL, password: PWD }),
  });
  ok("准备：临时用户就绪");

  // ① UI 登录（进站即访客态：点登出 → 弹登录框 → 填表提交）
  await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });
  await page.click('button[aria-label="退出登录"]', { timeout: 10000 });
  await page.waitForSelector(".auth-modal input[placeholder='用户名']", { timeout: 8000 });
  await page.fill(".auth-modal input[placeholder='用户名']", USER);
  await page.fill(".auth-modal input[placeholder='密码']", PWD);
  await page.click(".auth-btn");
  await page.waitForFunction(
    (u) => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === u,
    USER,
    { timeout: 10000 },
  );
  ok("① UI 登录成功");

  // ② 模型配置页：调度摘要显示平台池接管（真实 Key 由平台池提供）
  await page.click('.rail-btn:has-text("模型配置")');
  await page.waitForSelector("[data-testid='models-page']", { timeout: 8000 });
  await page.waitForSelector("[data-testid='sched-head-badge']", { timeout: 8000 });
  const headBadge = (await page.textContent("[data-testid='sched-head-badge']"))?.trim() || "";
  const textTag = (await page.textContent("[data-testid='sched-tag-text']"))?.trim() || "";
  if (/平台/.test(headBadge) || /平台/.test(textTag)) ok(`② 调度摘要：${headBadge}（text 槽 ${textTag}）`);
  else fail("② 调度摘要未显示平台池接管", `${headBadge} / ${textTag}`);
  await page.screenshot({ path: `${SHOT_DIR}/5-real-llm-settings.png` });

  // ③ 回工作台发消息 → 真实 LLM 流式回复（平台池调度）
  await page.click('.rail-btn:has-text("AI 会话")');
  await page.waitForSelector(".chat-panel textarea", { timeout: 8000 });
  await page.fill(".chat-panel textarea", "用一句话说明等价类划分法在测试中的作用");
  await page.click(".chat-panel .send-btn");
  await page.waitForSelector(".msg.msg-ai", { timeout: 30000 });
  // 流式完成标志：「✨ 生成测试用例」按钮出现（真实模型可能要 1~2 分钟）
  await page.waitForSelector(".msg-ai .qtag.confirm-btn", { timeout: 240000 });
  const reply = await page.$$eval(".msg.msg-ai", (els) => (els[els.length - 1] || {}).textContent || "");
  const mockTemplate = /好的，关于「.+?」，我先理一下/.test(reply);
  const failNotice = /真实模型调用失败/.test(reply);
  if (reply.length > 20 && !mockTemplate && !failNotice)
    ok(`③ 真实 LLM 流式回复完成（${reply.trim().slice(0, 80)}…）`);
  else if (failNotice) fail("③ 模型池全部候选失败（走了兜底提示）", reply.trim().slice(0, 120));
  else fail("③ 回复疑似 mock 模板", `mockTemplate=${mockTemplate}，长度=${reply.length}，内容=${reply.trim().slice(0, 100)}`);
  await page.screenshot({ path: `${SHOT_DIR}/6-real-llm-reply.png` });

  if (errors.length === 0) ok("④ 全程 0 JS 错误");
  else fail("④ 存在 JS 错误", errors.slice(0, 3).join(" | "));
} catch (e) {
  fail("脚本异常中断", String(e).slice(0, 300));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png` }).catch(() => {});
} finally {
  await browser.close();
  // 清理：整链删除临时用户数据
  try {
    dbExec(`
DELETE FROM step_logs WHERE task_id IN (SELECT id FROM tasks WHERE user_id=(SELECT id FROM users WHERE username='${USER}'));
DELETE FROM tasks WHERE user_id=(SELECT id FROM users WHERE username='${USER}');
DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=(SELECT id FROM users WHERE username='${USER}'));
DELETE FROM conversations WHERE user_id=(SELECT id FROM users WHERE username='${USER}');
DELETE FROM categories WHERE user_id=(SELECT id FROM users WHERE username='${USER}');
DELETE FROM llm_configs WHERE user_id=(SELECT id FROM users WHERE username='${USER}');
DELETE FROM llm_model_pool WHERE user_id=(SELECT id FROM users WHERE username='${USER}');
DELETE FROM users WHERE username='${USER}';`);
    console.log("🧹 清理完成（e2e_m5_smoke 整链）");
  } catch (e) {
    console.error("🧹 清理失败:", String(e).slice(0, 150));
  }
}

console.log("\n===== M5 冒烟结果汇总 =====\n" + results.join("\n"));
if (results.some((r) => r.startsWith("❌"))) process.exit(1);
