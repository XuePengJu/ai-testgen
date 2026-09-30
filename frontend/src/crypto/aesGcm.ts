/**
 * AES-256-GCM 纯 TS 实现（无 Web Crypto 依赖，HTTP 非安全上下文可用）。
 *
 * V2.8 M1：自 frontend-legacy/index.html 原生 JS 版逐行平移，逻辑零改动。
 * 格式与后端 Python cryptography 互通：base64url(nonce(12) || 密文 || tag(16))
 *
 * ⚠️ 静默失败风险最高模块（方案 §8 风险#1）：
 *    与后端不对齐时不报错、只是解密失败，必须配套 TestClient 端到端验证。
 */

const SBOX = [
  0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
  0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
  0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
  0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
  0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
  0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
  0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
  0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
  0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
  0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
  0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
  0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
  0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
  0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
  0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
  0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
];

function xtime(a: number): number {
  return ((a << 1) ^ (((a >> 7) & 1) * 0x1b)) & 0xff;
}

function keyExpand(key: Uint8Array): Uint32Array {
  const W = new Uint32Array(60);
  for (let i = 0; i < 8; i++)
    W[i] = (((key[4 * i] << 24) | (key[4 * i + 1] << 16) | (key[4 * i + 2] << 8) | key[4 * i + 3]) >>> 0);
  const RCON = [1, 2, 4, 8, 16, 32, 64];
  let rc = 0;
  for (let i = 8; i < 60; i++) {
    let t = W[i - 1];
    if (i % 8 === 0) {
      t = ((t << 8) | (t >>> 24)) >>> 0;
      t =
        ((SBOX[(t >>> 24) & 255] << 24) |
          (SBOX[(t >>> 16) & 255] << 16) |
          (SBOX[(t >>> 8) & 255] << 8) |
          SBOX[t & 255]) >>>
        0;
      t ^= RCON[rc++] << 24;
    } else if (i % 8 === 4) {
      t =
        ((SBOX[(t >>> 24) & 255] << 24) |
          (SBOX[(t >>> 16) & 255] << 16) |
          (SBOX[(t >>> 8) & 255] << 8) |
          SBOX[t & 255]) >>>
        0;
    }
    W[i] = (W[i - 8] ^ t) >>> 0;
  }
  return W;
}

function aesBlock(input: Uint8Array, rk: Uint32Array): Uint8Array {
  const s = new Uint8Array(input);
  const ark = (r: number) => {
    for (let c = 0; c < 4; c++) {
      const w = rk[r * 4 + c];
      s[4 * c] ^= (w >>> 24) & 255;
      s[4 * c + 1] ^= (w >>> 16) & 255;
      s[4 * c + 2] ^= (w >>> 8) & 255;
      s[4 * c + 3] ^= w & 255;
    }
  };
  const sb = () => {
    for (let i = 0; i < 16; i++) s[i] = SBOX[s[i]];
  };
  const sr = () => {
    const t = new Uint8Array(16);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) t[r + 4 * c] = s[r + 4 * ((c + r) % 4)];
    s.set(t);
  };
  const mc = () => {
    for (let c = 0; c < 4; c++) {
      const i = 4 * c;
      const a0 = s[i], a1 = s[i + 1], a2 = s[i + 2], a3 = s[i + 3];
      const m0 = xtime(a0), m1 = xtime(a1), m2 = xtime(a2), m3 = xtime(a3);
      s[i] = m0 ^ m1 ^ a1 ^ a2 ^ a3;
      s[i + 1] = a0 ^ m1 ^ m2 ^ a2 ^ a3;
      s[i + 2] = a0 ^ a1 ^ m2 ^ m3 ^ a3;
      s[i + 3] = m0 ^ a0 ^ a1 ^ a2 ^ m3;
    }
  };
  ark(0);
  for (let r = 1; r < 14; r++) {
    sb();
    sr();
    mc();
    ark(r);
  }
  sb();
  sr();
  ark(14);
  return s;
}

function ghMul(X: Uint8Array, Y: Uint8Array): Uint8Array {
  const Z = new Uint8Array(16);
  const V = new Uint8Array(Y);
  for (let i = 0; i < 128; i++) {
    if ((X[i >> 3] >> (7 - (i & 7))) & 1) for (let j = 0; j < 16; j++) Z[j] ^= V[j];
    const lsb = V[15] & 1;
    for (let j = 15; j > 0; j--) V[j] = ((V[j] >>> 1) | ((V[j - 1] & 1) << 7)) & 255;
    V[0] = V[0] >>> 1;
    if (lsb) V[0] ^= 0xe1;
  }
  return Z;
}

function ghash(H: Uint8Array, aad: Uint8Array, ct: Uint8Array): Uint8Array {
  let y = new Uint8Array(16);
  const feed = (data: Uint8Array) => {
    for (let i = 0; i < data.length; i += 16) {
      const blk = new Uint8Array(16);
      blk.set(data.subarray(i, i + 16));
      for (let j = 0; j < 16; j++) blk[j] ^= y[j];
      y = ghMul(blk, H);
    }
  };
  if (aad.length) feed(aad);
  if (ct.length) feed(ct);
  const len = new Uint8Array(16);
  const aB = aad.length * 8;
  const cB = ct.length * 8;
  len[4] = (aB >>> 24) & 255;
  len[5] = (aB >>> 16) & 255;
  len[6] = (aB >>> 8) & 255;
  len[7] = aB & 255;
  len[12] = (cB >>> 24) & 255;
  len[13] = (cB >>> 16) & 255;
  len[14] = (cB >>> 8) & 255;
  len[15] = cB & 255;
  for (let j = 0; j < 16; j++) len[j] ^= y[j];
  return ghMul(len, H);
}

function inc32(b: Uint8Array): Uint8Array {
  const r = new Uint8Array(b);
  for (let j = 15; j >= 12; j--) {
    r[j] = (r[j] + 1) & 255;
    if (r[j] !== 0) break;
  }
  return r;
}

function gctr(rk: Uint32Array, icb: Uint8Array, data: Uint8Array): Uint8Array {
  const out = new Uint8Array(data.length);
  let ctr = new Uint8Array(icb);
  for (let i = 0; i < data.length; i += 16) {
    const ks = aesBlock(ctr, rk);
    const n = Math.min(16, data.length - i);
    for (let j = 0; j < n; j++) out[i + j] = data[i + j] ^ ks[j];
    ctr = inc32(ctr);
  }
  return out;
}

const B64U_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function b64uEnc(u8: Uint8Array): string {
  let s = "";
  for (let i = 0; i < u8.length; i += 3) {
    const b = (u8[i] << 16) | ((u8[i + 1] || 0) << 8) | (u8[i + 2] || 0);
    s += B64U_ALPHABET[(b >> 18) & 63] + B64U_ALPHABET[(b >> 12) & 63];
    if (i + 1 < u8.length) s += B64U_ALPHABET[(b >> 6) & 63];
    if (i + 2 < u8.length) s += B64U_ALPHABET[b & 63];
  }
  return s;
}

function b64uDec(s: string): Uint8Array {
  const rev: Record<string, number> = {};
  for (let i = 0; i < 64; i++) rev[B64U_ALPHABET[i]] = i;
  s = s.replace(/=+$/, "");
  const out = new Uint8Array((s.length * 3) >> 2);
  let o = 0;
  let buf = 0;
  let bits = 0;
  for (const ch of s) {
    const v = rev[ch];
    if (v === undefined) throw new Error("bad base64");
    buf = (buf << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[o++] = (buf >> bits) & 255;
    }
  }
  return out;
}

const strToBytes = (str: string): Uint8Array => new TextEncoder().encode(str);
const bytesToStr = (u8: Uint8Array): string => new TextDecoder().decode(u8);

function aesGcmCore(keyB64: string, plaintextStr: string, iv: Uint8Array): Uint8Array {
  const key = b64uDec(keyB64);
  const pt = strToBytes(plaintextStr);
  const rk = keyExpand(key);
  const H = aesBlock(new Uint8Array(16), rk);
  const j0 = new Uint8Array(16);
  j0.set(iv);
  j0[15] = 1;
  const ct = gctr(rk, inc32(j0), pt);
  const sgh = ghash(H, new Uint8Array(0), ct);
  const ekj0 = aesBlock(j0, rk);
  const tag = new Uint8Array(16);
  for (let i = 0; i < 16; i++) tag[i] = sgh[i] ^ ekj0[i];
  const all = new Uint8Array(12 + ct.length + 16);
  all.set(iv);
  all.set(ct, 12);
  all.set(tag, 12 + ct.length);
  return all;
}

/** 加密：随机 12B nonce，返回 base64url(nonce||ct||tag) */
export function aesGcmEncrypt(keyB64: string, plaintextStr: string): string {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  return b64uEnc(aesGcmCore(keyB64, plaintextStr, iv));
}

/** 解密：校验 tag，失败抛 Error（密钥不匹配或数据被篡改） */
export function aesGcmDecrypt(keyB64: string, payloadB64: string): string {
  const key = b64uDec(keyB64);
  const raw = b64uDec(payloadB64);
  if (raw.length < 28) throw new Error("密文过短");
  const iv = raw.subarray(0, 12);
  const ct = raw.subarray(12, raw.length - 16);
  const tag = raw.subarray(raw.length - 16);
  const rk = keyExpand(key);
  const H = aesBlock(new Uint8Array(16), rk);
  const j0 = new Uint8Array(16);
  j0.set(iv);
  j0[15] = 1;
  const sgh = ghash(H, new Uint8Array(0), ct);
  const ekj0 = aesBlock(j0, rk);
  let diff = 0;
  for (let i = 0; i < 16; i++) diff |= tag[i] ^ (sgh[i] ^ ekj0[i]);
  if (diff !== 0) throw new Error("解密失败（密钥不匹配或数据被篡改）");
  return bytesToStr(gctr(rk, inc32(j0), ct));
}
