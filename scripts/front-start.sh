#!/usr/bin/env bash
# 启动 Jellyfish 前端 dev server（Vite + React）。
#
# 用法：
#   ./scripts/front-start.sh
#
# 行为：
# - 后台启动 vite dev server，监听 :7788
# - 自动设置 no_proxy 排除本地，避免系统代理拦截前端调后端的请求
# - 日志文件：/tmp/jellyfish-front.log
# - 如已运行则提示并退出，不重复启动
#
# 前置依赖：
# - 已在 front/ 执行过 pnpm install
# - 后端 API 已启动（见 scripts/backend-start.sh），否则 openapi:update 会失败

set -euo pipefail

FRONT_DIR="$(cd "$(dirname "$0")/.." && pwd)/front"
LOG_FILE="${FRONT_LOG:-/tmp/jellyfish-front.log}"
PORT="${FRONT_PORT:-7788}"

# 排除本地地址走代理，避免前端调后端被系统代理拦截
export no_proxy="localhost,127.0.0.1,${no_proxy:-}"
export NO_PROXY="$no_proxy"

# 检测是否已有 vite 在跑
if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[front] 端口 ${PORT} 已被占用："
  lsof -i tcp:"${PORT}" -sTCP:LISTEN
  echo "[front] 如需重启，先执行 ./scripts/front-stop.sh"
  exit 0
fi

cd "${FRONT_DIR}"

# 检查 node_modules 是否已安装
if [[ ! -d "node_modules" ]]; then
  echo "[front] 未发现 node_modules，先执行 pnpm install ..."
  pnpm install
fi

# 后台启动 vite
nohup pnpm dev > "${LOG_FILE}" 2>&1 &

FRONT_PID=$!
echo "[front] 已启动 PID=${FRONT_PID}"
echo "[front] 页面地址: http://localhost:${PORT}"
echo "[front] 日志文件: ${LOG_FILE}"
echo "[front] no_proxy 已设置: ${no_proxy}"

# 等待端口就绪，最多 15 秒
for i in $(seq 1 15); do
  if lsof -ti tcp:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[front] 端口 ${PORT} 已就绪，服务启动完成。"
    exit 0
  fi
  sleep 1
done

echo "[front] 警告：15 秒内端口未就绪，请查看日志：tail -f ${LOG_FILE}"
exit 0
