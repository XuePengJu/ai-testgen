#!/bin/bash
# ai-testgen 本地开发服务启动（脱离当前 shell 会话，避免被回收）
# 用法: bash scripts/start_local.sh [restart]
#
# 环境说明：
#   数据库：腾讯云测试机 MySQL（.env DB_*，远程直连，无需本地 Docker MySQL）
#   端口：默认 8001（8000 归 ai-testgen 原项目）
#   配置源：.env（由 app/core/config.py 读取），可用 AITF_ENV_FILE 切换
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="/tmp/atgen-8001.log"
PORT=8001

# Python 解释器：项目 .venv（含 langchain / chromadb 等全套依赖）
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  echo "❌ 项目 .venv 不存在。请先执行: cd $ROOT && ./start.sh （自动建环境+装依赖）"
  exit 1
fi

if [ "${1:-}" = "restart" ] || [ -n "$(lsof -ti tcp:$PORT)" ]; then
  lsof -ti tcp:$PORT | xargs -r kill -9
  sleep 1
fi

cd "$ROOT" || exit 1
$PY -c "
import os, sys
log = open('$LOG', 'a', buffering=1)
os.dup2(log.fileno(), 1); os.dup2(log.fileno(), 2)
os.setsid()
os.execv('$PY', ['$PY', 'main.py'])
" &
disown 2>/dev/null || true
echo "launched on :$PORT, log=$LOG"
