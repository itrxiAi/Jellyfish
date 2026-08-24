#!/usr/bin/env bash
# 停止本地 MinIO 对象存储服务。
#
# 用法：
#   ./script/minio-stop.sh
#
# 行为：
# - 查找监听 S3 API 端口（默认 9000）的 minio 进程并终止
# - 找不到进程时提示并退出 0，不报错
#
# 如使用了非默认端口，可通过 MINIO_S3_PORT 环境变量覆盖。

set -euo pipefail

S3_PORT="${MINIO_S3_PORT:-9000}"

# 通过端口定位 MinIO 进程，避免误杀其他同名进程
PIDS=$(lsof -ti tcp:"${S3_PORT}" -sTCP:LISTEN 2>/dev/null || true)

if [[ -z "${PIDS}" ]]; then
  echo "[minio] 未发现监听 ${S3_PORT} 端口的进程，可能未运行。"
  exit 0
fi

for PID in ${PIDS}; do
  if ps -p "${PID}" -o command= | grep -q "minio server"; then
    echo "[minio] 正在停止 PID=${PID} ..."
    kill "${PID}" 2>/dev/null || true
  fi
done

# 等待进程退出，最多 5 秒
for i in $(seq 1 5); do
  REMAIN=$(lsof -ti tcp:"${S3_PORT}" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -z "${REMAIN}" ]]; then
    echo "[minio] 已停止。"
    exit 0
  fi
  sleep 1
done

# 仍未退出则强制 kill
echo "[minio] 优雅停止超时，强制终止。"
for PID in ${PIDS}; do
  kill -9 "${PID}" 2>/dev/null || true
done
echo "[minio] 已强制停止。"
