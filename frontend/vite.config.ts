import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 本地开发：API 走相对路径 /api，代理到 FastAPI(8005)，
    // 与生产同源架构保持一致（config 不再需要 API_BASE 分支）
    proxy: {
      "/api": {
        // 默认 8005（本地常规开发）；多实例时用 AITF_BACKEND_ORIGIN 指到别的端口，
        // 如 AITF_BACKEND_ORIGIN=http://127.0.0.1:8015 npm run dev -- --port 5199
        target: process.env.AITF_BACKEND_ORIGIN || "http://127.0.0.1:8005",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
