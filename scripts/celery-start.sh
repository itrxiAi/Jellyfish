#!/usr/bin/env bash
# 启动 Jellyfish Celery worker（异步任务执行进程）。
#
# 用法：
#   ./scripts/celery-start.sh
#
# 行为：
# - 后台启动 celery worker，从 Redis 队列消费异步任务
# - 自动设置 no_proxy 排除本地
# - 日志文件：/tmp/jellyfish-celery.log
# - 如已运行则提示并退出，不重复启动
#
# 前置依赖：
# - 已在 backend/ 执行过 uv sync
# - Redis 已启动（broker）

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)/backend"
LOG_FILE="${CELERY_LOG:-/tmp/jellyfish-celery.log}"

# 排除本地地址走代理
export no_proxy="localhost,127.0.0.1,${no_proxy:-}"
export NO_PROXY="$no_proxy"

# 检测是否已有 celery worker 在跑
if pgrep -f "celery.*app.core.celery_app" >/dev/null 2>&1; then
  echo "[celery] 已在运行："
  pgrep -fl "celery.*app.core.celery_app"
  echo "[celery] 如需重启，先执行 ./scripts/celery-stop.sh"
  exit 0
fi

cd "${BACKEND_DIR}"

# 后台启动 celery worker
nohup uv run celery -A app.core.celery_app:celery_app worker -l info \
  > "${LOG_FILE}" 2>&1 &

CELERY_PID=$!
echo "[celery] 已启动 PID=${CELERY_PID}"
echo "[celery] 日志文件: ${LOG_FILE}"
echo "[celery] no_proxy 已设置: ${no_proxy}"

# 等待 worker ready，最多 15 秒
for i in $(seq 1 15); do
  if grep -q "ready" "${LOG_FILE}" 2>/dev/null; then
    echo "[celery] worker 已 ready，服务就绪。"
    exit 0
  fi
  sleep 1
done

echo "[celery] 警告：15 秒内未检测到 ready，请查看日志：tail -f ${LOG_FILE}"
exit 0
