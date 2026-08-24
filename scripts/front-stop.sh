#!/usr/bin/env bash
# 停止 Jellyfish 前端 dev server。
#
# 用法：
#   ./scripts/front-stop.sh

set -euo pipefail

PORT="${FRONT_PORT:-7788}"

PIDS=$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)

if [[ -z "${PIDS}" ]]; then
  echo "[front] 未发现监听 ${PORT} 端口的进程，可能未运行。"
  exit 0
fi

for PID in ${PIDS}; do
  echo "[front] 正在停止 PID=${PID} ..."
  kill "${PID}" 2>/dev/null || true
done

for i in $(seq 1 5); do
  REMAIN=$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -z "${REMAIN}" ]]; then
    echo "[front] 已停止。"
    exit 0
  fi
  sleep 1
done

echo "[front] 优雅停止超时，强制终止。"
for PID in ${PIDS}; do
  kill -9 "${PID}" 2>/dev/null || true
done
echo "[front] 已强制停止。"
