/**
 * 后端时间解析统一入口。
 *
 * 背景：后端 `utcnow()` 存的是**无时区标记的 UTC**（naive datetime），
 * JSON 序列化后形如 `"2026-09-19T18:05:53"`（不带 Z / 偏移量）。
 * JS 的 `new Date()` 对无时区字符串按**本地时区**解析，
 * GMT+8 环境下所有时间显示与「生成中」计时都会偏差 +8 小时。
 *
 * 规则：字符串末尾已带时区（Z 或 ±hh:mm）则原样解析，否则按 UTC 补 Z。
 */
export function parseServerTime(s?: string | null): Date | null {
  if (!s) return null;
  const iso = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

/* ---- V5.14.3 格式化：转北京时间展示 ----
 * 中国无夏令时，固定 +8（按 UTC 分量计算，跨运行时行为一致）。
 * 供列表/时间轴替换裸 slice 字符串的旧写法（那是 UTC 生时间）。 */

const CN_OFFSET_MS = 8 * 3600 * 1000;
const pad = (n: number) => String(n).padStart(2, "0");

function cnParts(d: Date) {
  const t = new Date(d.getTime() + CN_OFFSET_MS);
  return {
    y: t.getUTCFullYear(),
    m: pad(t.getUTCMonth() + 1),
    day: pad(t.getUTCDate()),
    hh: pad(t.getUTCHours()),
    mm: pad(t.getUTCMinutes()),
    ss: pad(t.getUTCSeconds()),
  };
}

/** ISO → 北京时间 "MM-DD HH:mm"（列表/时间轴通用；空/非法 → "—"） */
export function fmtCnTime(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "—";
  const p = cnParts(d);
  return `${p.m}-${p.day} ${p.hh}:${p.mm}`;
}

/** ISO → 北京时间 "YYYY-MM-DD HH:mm:ss"（完整时间戳） */
export function fmtCnFull(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "—";
  const p = cnParts(d);
  return `${p.y}-${p.m}-${p.day} ${p.hh}:${p.mm}:${p.ss}`;
}

/** ISO → 北京时间 "YYYY-MM-DD"（仅日期） */
export function fmtCnDate(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "—";
  const p = cnParts(d);
  return `${p.y}-${p.m}-${p.day}`;
}
