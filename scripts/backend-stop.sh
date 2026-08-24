#!/usr/bin/env bash
# 停止 Jellyfish 后端 API 服务。
#
# 用法：
#   ./scripts/backend-stop.sh
#
# 行为：
# - 查找监听 8000 端口的 uvicorn 进程并终止
# - 找不到进程时提示并退出 0，不报错

set -euo pipefail

PORT="${BACKEND_PORT:-8000}"

PIDS=$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)

if [[ -z "${PIDS}" ]]; then
  echo "[backend] 未发现监听 ${PORT} 端口的进程，可能未运行。"
  exit 0
fi

for PID in ${PIDS}; do
  echo "[backend] 正在停止 PID=${PID} ..."
  kill "${PID}" 2>/dev/null || true
done

for i in $(seq 1 5); do
  REMAIN=$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -z "${REMAIN}" ]]; then
    echo "[backend] 已停止。"
    exit 0
  fi
  sleep 1
done

echo "[backend] 优雅停止超时，强制终止。"
for PID in ${PIDS}; do
  kill -9 "${PID}" 2>/dev/null || true
done
echo "[backend] 已强制停止。"
