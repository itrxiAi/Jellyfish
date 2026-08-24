#!/usr/bin/env bash
# 启动本地 MinIO 对象存储（S3 兼容），供 Jellyfish 后端存储素材文件。
#
# 用法：
#   ./script/minio-start.sh
#
# 行为：
# - 后台启动 MinIO，监听 :9000（S3 API）和 :9001（Web 控制台）
# - 数据目录：/tmp/minio-data
# - 日志文件：/tmp/minio.log
# - 如已运行则提示并退出，不重复启动
#
# 默认账号：admin / admin123（仅本地开发用，请勿用于生产）
# 如需修改，编辑下面的 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD，
# 并同步修改 backend/.env 中的 S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY。

set -euo pipefail

DATA_DIR="${MINIO_DATA_DIR:-/tmp/minio-data}"
LOG_FILE="${MINIO_LOG_FILE:-/tmp/minio.log}"
ROOT_USER="${MINIO_ROOT_USER:-admin}"
ROOT_PASS="${MINIO_ROOT_PASSWORD:-admin123}"
S3_PORT="${MINIO_S3_PORT:-9000}"
CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"

# 检测是否已有 MinIO 进程在跑，避免重复启动
if pgrep -f "minio server ${DATA_DIR}" >/dev/null 2>&1; then
  echo "[minio] 已在运行："
  pgrep -fl "minio server ${DATA_DIR}"
  echo "[minio] 如需重启，先执行 ./script/minio-stop.sh"
  exit 0
fi

mkdir -p "${DATA_DIR}"

# 后台启动 MinIO，输出重定向到日志文件
# MINIO_DOMAIN=localhost 让 MinIO 支持 virtual-host 寻址（boto3 默认用 virtual）
nohup env \
  MINIO_ROOT_USER="${ROOT_USER}" \
  MINIO_ROOT_PASSWORD="${ROOT_PASS}" \
  MINIO_DOMAIN=localhost \
  minio server "${DATA_DIR}" \
    --address ":${S3_PORT}" \
    --console-address ":${CONSOLE_PORT}" \
  > "${LOG_FILE}" 2>&1 &

MINIO_PID=$!
echo "[minio] 已启动 PID=${MINIO_PID}"
echo "[minio] S3 API   : http://localhost:${S3_PORT}"
echo "[minio] Web 控制台: http://localhost:${CONSOLE_PORT}  (账号 ${ROOT_USER} / ${ROOT_PASS})"
echo "[minio] 数据目录  : ${DATA_DIR}"
echo "[minio] 日志文件  : ${LOG_FILE}"

# 等待健康检查通过，最多 10 秒
# 注意：curl 要绕过本地代理（用户可能设了 http_proxy），否则会误报超时
for i in $(seq 1 10); do
  if curl -fsS --noproxy localhost,127.0.0.1 "http://localhost:${S3_PORT}/minio/health/live" >/dev/null 2>&1; then
    echo "[minio] 健康检查通过，服务就绪。"
    exit 0
  fi
  sleep 1
done

echo "[minio] 警告：10 秒内健康检查未通过，请查看日志：tail -f ${LOG_FILE}"
exit 0
