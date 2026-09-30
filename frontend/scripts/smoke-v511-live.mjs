/** 线上冒烟：V5.11.2 部署后验证 —— 侧栏渲染 + 登出弹窗（hook 修复回归） */
import { chromium } from "playwright";
const BASE = process.env.SMOKE_URL || "https://ai.agentest.vip";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
p.on("pageerror", (e) => errs.push(String(e).slice(0, 200)));
p.on("console", (m) => { if (m.type() === "error") errs.push("[console] " + m.text().slice(0, 200)); });

const say = (ok, n) => console.log(`${ok ? "✅" : "❌"} ${n}`);

await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(2500);
say(await p.locator(".conv-panel").isVisible(), "侧栏会话面板渲染");
say((await p.locator("button[aria-label=\"退出登录\"]").count()) > 0, "退出登录入口存在");
await p.screenshot({ path: "/tmp/e2e-v511/live-1-loaded.png" });

await p.locator('button[aria-label="退出登录"]').click();
await p.waitForTimeout(3000);
const modal = await p.locator(".auth-modal").count();
say(modal > 0, "登出后弹出登录框（hook 修复回归点）");
await p.screenshot({ path: "/tmp/e2e-v511/live-2-logout-modal.png" });

console.log(errs.length ? "页面错误: " + errs.slice(0, 3).join(" | ") : "无页面 JS 错误");
await b.close();
process.exit(modal > 0 && errs.length === 0 ? 0 : 1);
