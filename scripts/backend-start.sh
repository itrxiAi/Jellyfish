#!/usr/bin/env bash
# 启动 Jellyfish 后端 API（FastAPI + Uvicorn）。
#
# 用法：
#   ./scripts/backend-start.sh
#
# 行为：
# - 后台启动 uvicorn，监听 :8000，开启 --reload
# - 自动设置 no_proxy 排除本地，避免系统代理拦截本地请求
# - 日志文件：/tmp/jellyfish-backend.log
# - 如已运行则提示并退出，不重复启动
#
# 前置依赖：
# - 已在 backend/ 执行过 uv sync
# - Redis 已启动（Celery broker）
# - MinIO 已启动（S3 存储，见 scripts/minio-start.sh）

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)/backend"
LOG_FILE="${BACKEND_LOG:-/tmp/jellyfish-backend.log}"
PORT="${BACKEND_PORT:-8000}"
HOST="${BACKEND_HOST:-0.0.0.0}"

# 排除本地地址走代理，避免前端/后端本地互调被系统代理拦截
export no_proxy="localhost,127.0.0.1,${no_proxy:-}"
export NO_PROXY="$no_proxy"

# 检测是否已有 uvicorn 在跑
if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[backend] 端口 ${PORT} 已被占用："
  lsof -i tcp:"${PORT}" -sTCP:LISTEN
  echo "[backend] 如需重启，先执行 ./scripts/backend-stop.sh"
  exit 0
fi

cd "${BACKEND_DIR}"

# 后台启动 uvicorn
nohup uv run uvicorn app.main:app \
  --reload \
  --host "${HOST}" \
  --port "${PORT}" \
  > "${LOG_FILE}" 2>&1 &

BACKEND_PID=$!
echo "[backend] 已启动 PID=${BACKEND_PID}"
echo "[backend] API 文档: http://localhost:${PORT}/docs"
echo "[backend] 健康检查: http://localhost:${PORT}/health"
echo "[backend] 日志文件: ${LOG_FILE}"
echo "[backend] no_proxy 已设置: ${no_proxy}"

# 等待健康检查通过，最多 15 秒
for i in $(seq 1 15); do
  if curl -fsS --noproxy localhost,127.0.0.1 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "[backend] 健康检查通过，服务就绪。"
    exit 0
  fi
  sleep 1
done

echo "[backend] 警告：15 秒内健康检查未通过，请查看日志：tail -f ${LOG_FILE}"
exit 0
