#!/usr/bin/env bash
# ai-testgen 一键启动脚本（AI 对话 + 用例生成 + 知识库，clone 即用，无机器相关路径）
#
# 用法：
#   ./start.sh                 # 首次：建 .venv + 装依赖 + 启动（8005 端口）
#                              # 之后：依赖已装过则秒起
#   PORT=9001 ./start.sh       # 换端口启动
#   SKIP_DEPS=1 ./start.sh     # 跳过依赖检查，直接启动（最快）
#   FORCE_INSTALL=1 ./start.sh # 强制重装依赖（requirements.txt 变更后）
#
# 前置：Python 3.10+（命令行 python3 可用）；数据库见 .env（MySQL 或默认 SQLite）
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8005}"
SKIP_DEPS="${SKIP_DEPS:-0}"
FORCE_INSTALL="${FORCE_INSTALL:-0}"
MARKER=".venv/.deps-installed"

echo "=============================================="
echo " ai-testgen · AI 对话 + 用例生成 + 知识库"
echo " 目录: $(pwd)"
echo "=============================================="

# ── 1. Python 虚拟环境 ──────────────────────────────
if [ ! -x .venv/bin/python ]; then
  echo "[1/3] 未发现虚拟环境，创建 .venv ..."
  python3 -m venv .venv
  rm -f "$MARKER"   # 新环境必须装依赖
else
  echo "[1/3] 虚拟环境已就绪"
fi

# ── 2. 依赖安装（新建环境 / 强制 / 未装过 时执行） ──
if [ "$SKIP_DEPS" = "1" ]; then
  echo "[2/3] 跳过依赖安装（SKIP_DEPS=1）"
elif [ "$FORCE_INSTALL" = "1" ] || [ ! -f "$MARKER" ]; then
  echo "[2/3] 安装依赖（首次约几分钟，chromadb 已钉版本）..."
  .venv/bin/pip install --quiet --no-cache-dir --upgrade pip
  .venv/bin/pip install --quiet --no-cache-dir -r requirements.txt
  touch "$MARKER"
  echo "      依赖安装完成"
else
  echo "[2/3] 依赖已安装过，跳过（重装请用 FORCE_INSTALL=1）"
fi

# ── 3. 数据库迁移（幂等：建表 + 预置 admin + 存量迁移） ──
echo "[3/3] 数据库迁移 ..."
if ! .venv/bin/python scripts/migrate_v2.py; then
  echo "❌ 迁移失败：请检查 .env 数据库配置（MySQL 是否就绪，或留空走 SQLite）"
  exit 1
fi

# ── 停掉占用端口的旧服务 ────────────────────────────
if [ -n "$(lsof -ti tcp:"$PORT" 2>/dev/null || true)" ]; then
  echo "端口 $PORT 被占用，停止旧服务..."
  lsof -ti tcp:"$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi

echo "=============================================="
echo " 启动: http://127.0.0.1:$PORT"
echo " Swagger: http://127.0.0.1:$PORT/docs"
echo " 停止: Ctrl+C"
echo "=============================================="
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port "$PORT"
