/**
 * M0 Node e2e 轻量 runner：逐个执行 e2e-m1~m5 脚本，产出 e2e-report.json。
 *
 * 用法：node frontend/scripts/run-e2e.mjs [输出目录] [套件 ...]
 *   输出目录默认 <项目根>/quality_data；
 *   套件参数可选（m1~m5，或完整脚本名），只跑指定套件（部分运行用）；
 *   每套件记录 name/outcome/duration_ms/error/steps，
 *   单套件失败不中断后续套件（try/catch + 退出码判定）。
 *
 * 进度透传：子脚本 "✅/❌ 步骤" 行实时转发到本进程 stdout（质量运行进度条解析用），
 * 套件结束时打 "[e2e] <脚本>: <结果> (耗时ms)" 汇总行。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const outDir = args[0] || path.resolve(__dirname, "../../reports");
fs.mkdirSync(outDir, { recursive: true });

/** 与设计文档 2.2① 对齐的套件清单（按里程碑顺序执行） */
const SUITES = [
  "e2e-m1-browser.mjs",
  "e2e-m2-browser.mjs",
  "e2e-m3-browser.mjs",
  "e2e-m5-smoke.mjs",
];

/** 套件参数解析：m1 / e2e-m1-browser.mjs 均可；非法参数直接报错退出 */
const wanted = args.slice(1).map((a) => {
  const byShort = SUITES.find((s) => s === `e2e-${a}-browser.mjs` || s === `e2e-${a}-smoke.mjs`);
  const byFull = SUITES.find((s) => s === a);
  const hit = byShort || byFull;
  if (!hit) {
    console.error(`[e2e] 未知套件：${a}（可选 m1~m5 或完整脚本名）`);
    process.exit(2);
  }
  return hit;
});
const selected = wanted.length ? SUITES.filter((s) => wanted.includes(s)) : SUITES;

/** 单套件超时（毫秒）：浏览器脚本含启动开销，5 分钟兜底 */
const SUITE_TIMEOUT_MS = 5 * 60 * 1000;

/** spawn 子脚本并实时转发 ✅/❌ 步骤行（供上层进度解析）；返回 {status, stdout} */
function runSuite(script) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script], {
      cwd: path.resolve(__dirname, ".."),
    });
    let stdout = "";
    let finished = false;
    const timer = setTimeout(() => child.kill("SIGKILL"), SUITE_TIMEOUT_MS);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      // 实时转发步骤行（其余行静默，避免日志淹没进度条）
      for (const line of chunk.split(/\r?\n/)) {
        if (/^[✅❌]/.test(line)) console.log(line);
      }
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stdout += chunk;
    });
    child.on("close", (code, signal) => {
      finished = true;
      clearTimeout(timer);
      resolve({ code: signal ? 1 : code, stdout });
    });
    child.on("error", (e) => {
      if (!finished) {
        finished = true;
        clearTimeout(timer);
        resolve({ code: 1, stdout: stdout + String(e.message) });
      }
    });
  });
}

const results = [];
for (const name of selected) {
  const script = path.join(__dirname, name);
  const t0 = Date.now();
  let outcome = "passed";
  let error = null;
  let steps = [];
  if (!fs.existsSync(script)) {
    outcome = "error";
    error = "脚本不存在";
  } else {
    const r = await runSuite(script);
    if (r.code !== 0) {
      outcome = "failed";
      error = r.stdout.trim().slice(-500);
    }
    // 步骤透传：脚本用 "✅/❌ 步骤名" 行打印真实操作步骤，解析进报告供看板展示
    for (const m of r.stdout.matchAll(/^[✅❌]\s*(.+)$/gm)) {
      steps.push({ ok: m[0].includes("✅"), name: m[1].trim() });
    }
  }
  results.push({ name, outcome, duration_ms: Date.now() - t0, error, steps });
  console.log(`[e2e] ${name}: ${outcome} (${Date.now() - t0}ms)`);
}

const report = {
  suites: results,
  total: results.length,
  passed: results.filter((r) => r.outcome === "passed").length,
  generated_at: new Date().toISOString(),
};
const outPath = path.join(outDir, "e2e-report.json");
fs.writeFileSync(outPath, JSON.stringify(report, null, 2), "utf8");
console.log(`[e2e] 报告已写入 ${outPath}（${report.passed}/${report.total} 通过）`);
// 有套件失败时以非零退出码结束，便于上层感知（报告仍完整落盘）
process.exit(report.passed === report.total ? 0 : 1);
