#!/usr/bin/env bash
# 停止 Jellyfish Celery worker。
#
# 用法：
#   ./scripts/celery-stop.sh

set -euo pipefail

PIDS=$(pgrep -f "celery.*app.core.celery_app" 2>/dev/null || true)

if [[ -z "${PIDS}" ]]; then
  echo "[celery] 未发现 celery worker 进程，可能未运行。"
  exit 0
fi

for PID in ${PIDS}; do
  echo "[celery] 正在停止 PID=${PID} ..."
  kill "${PID}" 2>/dev/null || true
done

for i in $(seq 1 5); do
  REMAIN=$(pgrep -f "celery.*app.core.celery_app" 2>/dev/null || true)
  if [[ -z "${REMAIN}" ]]; then
    echo "[celery] 已停止。"
    exit 0
  fi
  sleep 1
done

echo "[celery] 优雅停止超时，强制终止。"
for PID in ${PIDS}; do
  kill -9 "${PID}" 2>/dev/null || true
done
echo "[celery] 已强制停止。"
