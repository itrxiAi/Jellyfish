"""应用配置，从环境变量加载。"""

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Jellyfish API"
    debug: bool = False

    # API
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./jellyfish.db"

    # Redis / Celery Broker
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    celery_broker_url: str | None = None

    # CORS：环境变量中建议使用逗号分隔（更贴近 docker-compose 用法）
    # 也兼容 JSON 数组：'["http://a","http://b"]'
    cors_origins: str = "http://localhost:7788,http://127.0.0.1:7788"

    @property
    def cors_origins_list(self) -> list[str]:
        s = (self.cors_origins or "").strip()
        if not s:
            return []
        if s.startswith("["):
            loaded = json.loads(s)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
            return []
        return [x.strip() for x in s.split(",") if x.strip()]

    # S3 / 对象存储（用于素材文件）
    # 兼容两种模式：
    #   1) 直连 S3 兼容 API（MinIO / R2 S3 endpoint 等）：配置 s3_* 系列字段即可。
    #   2) Cloudflare R2 via Worker + CDN（绕开 R2 S3 endpoint SSL 失败问题）：
    #      配置 oss_upload_worker_url / oss_upload_secret / oss_cdn_domain。
    # 两种模式同时配置时，Worker 模式优先（更稳定）。
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str | None = None
    # 可选：统一前缀，方便按环境/项目隔离，如 "jellyfish/dev"
    s3_base_path: str = ""
    # 可选：对外访问基址（CDN 或自定义域名），为空则使用 S3 自带 URL 或预签名 URL
    s3_public_base_url: str | None = None

    # Cloudflare R2 via Worker 模式（推荐）
    # Worker 上传代理 URL，例如 https://upload-test.harmonylink.app
    # Worker 接受 PUT /upload/<key>?token=<HMAC token>，校验后通过 R2 binding 写入 bucket。
    oss_upload_worker_url: str | None = None
    # 与 Worker 共享的 HMAC secret（必须和 wrangler secret put UPLOAD_SECRET 一致）
    oss_upload_secret: str | None = None
    # CDN 公开访问域名（R2 Custom Domain 或 Public Dev URL），例如 https://cdns.harmonylink.app
    oss_cdn_domain: str | None = None
    # 上传 token 有效期（秒），默认 30 分钟
    oss_upload_token_ttl: int = 1800
    # 上传单文件大小上限（字节），默认 200MB，需与 Worker 端 maxBytes 一致
    oss_upload_max_bytes: int = 200 * 1024 * 1024

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url or not str(self.celery_broker_url).strip():
            password_part = f":{self.redis_password}@" if self.redis_password else ""
            self.celery_broker_url = f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
