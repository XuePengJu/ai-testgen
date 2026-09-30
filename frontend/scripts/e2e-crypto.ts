/**
 * M1 加密链路端到端自检（V2.8 方案 §8 风险#1 的回归防线）。
 *
 * 运行（后端须先起在 8000）：
 *   node --experimental-strip-types scripts/e2e-crypto.ts
 *
 * 覆盖 4 段链路：
 *   ① guest/token 明文分发 enc_key（密钥协商）
 *   ② 加密响应：GET /api/auth/me 返回 {enc:...}，前端实现解密还原
 *   ③ 加密请求：POST /api/categories 请求体 {enc:...}，后端解密入库
 *   ④ 非 JSON 响应（204）透传不加密
 */
import { aesGcmEncrypt, aesGcmDecrypt } from "../src/crypto/aesGcm.ts";

const BASE = process.env.AITF_BASE || "http://127.0.0.1:8000";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) {
    console.error("❌ " + msg);
    process.exit(1);
  }
}

async function main() {
  // ① 密钥分发（明文通道）
  const r1 = await fetch(BASE + "/api/guest/token", { method: "POST" });
  const d1 = await r1.json();
  assert(r1.ok && d1.access_token && d1.enc_key, "① guest/token 失败: " + JSON.stringify(d1));
  const { access_token, enc_key } = d1;
  const auth = { Authorization: "Bearer " + access_token };

  // ② 加密响应 → 前端解密
  const r2 = await fetch(BASE + "/api/auth/me", { headers: auth });
  const d2 = await r2.json();
  assert(d2 && typeof d2.enc === "string", "② me 响应未加密（middleware 未生效？）: " + JSON.stringify(d2));
  const me = JSON.parse(aesGcmDecrypt(enc_key, d2.enc));
  assert(me.role === "guest", "② 解密后 role 应为 guest，实际 " + JSON.stringify(me));

  // ③ 加密请求 → 后端解密
  const name = "M1加密自检-" + Date.now();
  const r3 = await fetch(BASE + "/api/categories", {
    method: "POST",
    headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ enc: aesGcmEncrypt(enc_key, JSON.stringify({ name })) }),
  });
  const d3 = await r3.json();
  assert(r3.status === 201, "③ 创建分类应为 201，实际 " + r3.status + " " + JSON.stringify(d3));
  const cat = JSON.parse(aesGcmDecrypt(enc_key, d3.enc));
  assert(cat.name === name, "③ 解密后分类名不匹配: " + JSON.stringify(cat));

  // ④ JSON 响应同样加密（DELETE 返回 200 + JSON，验证加密通道一致性）
  const r4 = await fetch(BASE + "/api/categories/" + cat.id, { method: "DELETE", headers: auth });
  assert(r4.status === 200, "④ 删除分类应为 200，实际 " + r4.status);
  const d4 = JSON.parse(aesGcmDecrypt(enc_key, (await r4.json()).enc));
  assert(d4.ok === true && d4.deleted_categories >= 1, "④ 解密后删除结果异常: " + JSON.stringify(d4));

  console.log("✅ 加密链路端到端 4/4 通过：明文分发 enc_key → 加密响应解密 → 加密请求解密 → 删除响应解密");
}

main().catch((e) => {
  console.error("❌ 运行异常: " + (e instanceof Error ? e.stack : String(e)));
  process.exit(1);
});
