// 运行时环境注入点。
// 本地开发：VITE_BACKEND_URL 未设时，由 openapi.ts fallback 到 http://localhost:8000。
// 生产构建：默认空串（同源），走 nginx 反代 /api/ → 后端。
// 如需覆盖，可由 nginx/入口脚本在加载前设置 window.__ENV.BACKEND_URL。
window.__ENV = window.__ENV || {
  BACKEND_URL: '',
}
