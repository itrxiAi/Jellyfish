"""统一对象存储封装。

支持两种后端模式，由配置自动选择：

1. **Cloudflare R2 via Worker + CDN（推荐）**
   - 上传：PUT `${OSS_UPLOAD_WORKER_URL}/upload/<key>?token=<HMAC token>`
     Worker 校验 HMAC token 后通过 R2 binding 写入 bucket，绕开 R2 S3 endpoint
     SSL handshake 失败的已知问题。
   - 下载 / 文件信息：直接 GET / HEAD `${OSS_CDN_DOMAIN}/<key>`（R2 Custom Domain）。
   - 删除 / 列表：Worker 未实现，`delete_file` 为 no-op，`list_files` 返回空列表
     （业务侧 `list_files_paginated` 实际查 DB，不依赖此接口）。

2. **S3 兼容 API（fallback）**
   - 通过 boto3 直连 MinIO / R2 S3 endpoint / AWS S3 等。
   - 当未配置 `oss_upload_worker_url` 时自动启用。

对外函数签名在两种模式下保持一致，调用方无需感知底层差异。
所有阻塞 IO（boto3 / httpx 同步客户端）均通过 anyio 在线程池中执行，
避免阻塞 FastAPI 事件循环。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from functools import partial
from typing import Any, BinaryIO

import httpx
from anyio import to_thread

from app.config import settings


@dataclass
class StoredFileInfo:
    """文件基础信息（供调用方在路由层自行封装为 Pydantic schema）。"""

    key: str
    url: str
    size: int | None = None
    content_type: str | None = None
    etag: str | None = None
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 模式选择
# ---------------------------------------------------------------------------


def _use_worker_mode() -> bool:
    """判断是否启用 Worker + CDN 模式。

    启用条件：同时配置了 `oss_upload_worker_url`、`oss_upload_secret`、`oss_cdn_domain`。
    """
    return bool(
        settings.oss_upload_worker_url
        and settings.oss_upload_secret
        and settings.oss_cdn_domain
    )


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _normalize_key(key: str) -> str:
    """将逻辑 key 拼接上 `s3_base_path` 前缀，并去掉前导斜杠。"""
    key = key.lstrip("/")
    base = settings.s3_base_path.strip().strip("/")
    if base:
        return f"{base}/{key}"
    return key


def _build_public_url(key: str) -> str:
    """构建对外访问 URL。

    优先级：
    1. Worker 模式：`${OSS_CDN_DOMAIN}/<key>`
    2. `s3_public_base_url`（CDN / 自定义域名）
    3. S3 endpoint path 形式
    """
    norm_key = _normalize_key(key)
    if _use_worker_mode():
        return f"{settings.oss_cdn_domain.rstrip('/')}/{norm_key}"
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{norm_key}"
    # fallback：使用标准 S3 URL 形式
    if not settings.s3_bucket_name:
        raise RuntimeError("对象存储未配置：缺少 s3_bucket_name 或 oss_cdn_domain")
    endpoint = settings.s3_endpoint_url.rstrip("/") if settings.s3_endpoint_url else ""
    if endpoint:
        return f"{endpoint}/{settings.s3_bucket_name}/{norm_key}"
    return f"/{settings.s3_bucket_name}/{norm_key}"


# ---------------------------------------------------------------------------
# Worker 模式实现
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    """base64url 编码（无 padding），用于生成 HMAC token。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign_upload_token(*, key: str, max_bytes: int) -> str:
    """签发 Worker 上传 token。

    格式：`base64url(JSON({key, exp, maxBytes})).base64url(hmac_sha256(payload, secret))`
    与 Cloudflare Worker 端 `verifyToken` 实现完全对应。
    """
    payload = {
        "key": key,
        "exp": int(time.time()) + settings.oss_upload_token_ttl,
        "maxBytes": max_bytes,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    secret = settings.oss_upload_secret.encode("utf-8")
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


def _worker_upload_sync(
    *,
    upload_url: str,
    data: bytes | BinaryIO,
    content_type: str | None,
) -> dict[str, Any]:
    """同步执行 Worker PUT 上传（在线程池中调用）。

    返回 Worker 响应 JSON（含 `ok`、`key`）。
    """
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type

    if isinstance(data, (bytes, bytearray)):
        body: Any = bytes(data)
        headers.setdefault("Content-Length", str(len(body)))
    else:
        # 类文件对象：httpx 支持流式上传
        body = data

    # 超时给足：大文件 + 弱网，与 hak 项目一致用 10 分钟
    with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
        resp = client.put(upload_url, content=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _cdn_download_sync(*, url: str) -> bytes:
    """同步从 CDN GET 文件内容（在线程池中调用）。"""
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _cdn_head_sync(*, url: str) -> dict[str, str]:
    """同步从 CDN HEAD 文件元信息（在线程池中调用）。

    返回响应头字典（小写键）。
    显式禁用压缩以获取真实 Content-Length（CDN 默认 zstd 压缩会去掉该字段）。
    """
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = client.head(url, headers={"Accept-Encoding": "identity"})
        resp.raise_for_status()
        return {k.lower(): v for k, v in resp.headers.items()}


# ---------------------------------------------------------------------------
# S3 兼容模式实现（fallback）
# ---------------------------------------------------------------------------


def _build_s3_client():
    """构建 boto3 S3 客户端（仅 S3 兼容模式使用）。"""
    if not settings.s3_bucket_name:
        raise RuntimeError("S3 未配置：请在配置中设置 s3_bucket_name 等必要字段")

    import boto3
    from botocore.client import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region_name,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def init_storage() -> None:
    """初始化对象存储。

    - Worker 模式：仅做 healthz 检查，bucket 由 R2 binding 预绑定，无需创建。
    - S3 模式：检查 bucket 是否存在，不存在则创建。
    """
    if _use_worker_mode():
        # Worker 模式下 bucket 由 R2 binding 管理，这里仅做一次健康检查
        worker_base = settings.oss_upload_worker_url.rstrip("/")
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = client.get(f"{worker_base}/healthz")
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Upload Worker healthz 检查失败：{resp.status_code} {resp.text}"
                    )
        except httpx.HTTPError as e:
            raise RuntimeError(f"Upload Worker 不可达：{worker_base} ({e})") from e
        return

    # 以下为 S3 兼容模式的 bucket 初始化逻辑
    from botocore.exceptions import ClientError

    client = _build_s3_client()
    bucket = settings.s3_bucket_name
    if not bucket:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    params: dict[str, Any] = {"Bucket": bucket}
    region = settings.s3_region_name
    if region and region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}

    try:
        client.create_bucket(**params)
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            return
        raise

    client.head_bucket(Bucket=bucket)


async def upload_file(
    *,
    key: str,
    data: bytes | BinaryIO,
    content_type: str | None = None,
    extra_args: dict[str, Any] | None = None,
) -> StoredFileInfo:
    """上传文件到对象存储。

    参数：
    - key：逻辑 key（不需要带 base_path，会自动拼接）。
    - data：字节内容或类文件对象。
    - content_type：MIME 类型，例如 image/png。
    - extra_args：S3 模式下透传给 boto3 的 ExtraArgs（如 ``{"ACL": "public-read"}``）；
      Worker 模式下会忽略（R2 公开访问由 bucket / CDN 域名控制）。
    """
    s3_key = _normalize_key(key)

    if _use_worker_mode():
        token = _sign_upload_token(key=s3_key, max_bytes=settings.oss_upload_max_bytes)
        worker_base = settings.oss_upload_worker_url.rstrip("/")
        upload_url = f"{worker_base}/upload/{s3_key}?token={token}"

        result = await to_thread.run_sync(
            partial(
                _worker_upload_sync,
                upload_url=upload_url,
                data=data,
                content_type=content_type,
            )
        )
        etag = result.get("etag")
        url = _build_public_url(key)
        return StoredFileInfo(key=s3_key, url=url, etag=etag)

    # ----- S3 兼容模式 -----
    bucket = settings.s3_bucket_name
    if bucket is None:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    extra = extra_args.copy() if extra_args else {}
    if content_type and "ContentType" not in extra:
        extra["ContentType"] = content_type

    def _upload():
        if isinstance(data, (bytes, bytearray)):
            return client.put_object(Bucket=bucket, Key=s3_key, Body=data, **extra)
        return client.upload_fileobj(data, bucket, s3_key, ExtraArgs=extra)  # type: ignore[arg-type]

    client = _build_s3_client()
    result = await to_thread.run_sync(_upload)

    etag = None
    if isinstance(result, dict):
        etag = result.get("ETag")

    url = _build_public_url(key)
    return StoredFileInfo(key=s3_key, url=url, etag=etag)


async def download_file(*, key: str) -> bytes:
    """下载文件内容（整个对象读入内存）。"""
    s3_key = _normalize_key(key)

    if _use_worker_mode():
        url = _build_public_url(key)
        return await to_thread.run_sync(partial(_cdn_download_sync, url=url))

    # ----- S3 兼容模式 -----
    client = _build_s3_client()
    bucket = settings.s3_bucket_name
    if bucket is None:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    def _download() -> bytes:
        obj = client.get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"].read()
        return body  # type: ignore[no-any-return]

    return await to_thread.run_sync(_download)


async def get_file_info(*, key: str) -> StoredFileInfo:
    """获取文件元信息（不下载内容）。

    Worker 模式下通过 CDN HEAD 获取 Content-Length / Content-Type / ETag。
    """
    s3_key = _normalize_key(key)

    if _use_worker_mode():
        url = _build_public_url(key)
        headers = await to_thread.run_sync(partial(_cdn_head_sync, url=url))
        size = int(headers.get("content-length") or 0) or None
        content_type = headers.get("content-type")
        etag = headers.get("etag")
        return StoredFileInfo(
            key=s3_key,
            url=url,
            size=size,
            content_type=content_type,
            etag=etag,
            extra={k: v for k, v in headers.items() if k not in {"content-length", "content-type", "etag"}},
        )

    # ----- S3 兼容模式 -----
    client = _build_s3_client()
    bucket = settings.s3_bucket_name
    if bucket is None:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    def _head() -> dict[str, Any]:
        return client.head_object(Bucket=bucket, Key=s3_key)  # type: ignore[no-any-return]

    meta = await to_thread.run_sync(_head)

    size = int(meta.get("ContentLength") or 0)
    content_type = meta.get("ContentType")
    etag = meta.get("ETag")

    url = _build_public_url(key)
    return StoredFileInfo(
        key=s3_key,
        url=url,
        size=size,
        content_type=content_type,
        etag=etag,
        extra={k: v for k, v in meta.items() if k not in {"ContentLength", "ContentType", "ETag"}},
    )


async def list_files(*, prefix: str = "") -> list[StoredFileInfo]:
    """根据前缀列出文件（最多一页，若需翻页可扩展）。

    Worker 模式下 Worker 未实现 list 接口，返回空列表。
    业务侧 `list_files_paginated` 实际查 DB 中的 FileItem 记录，不依赖此接口。
    """
    if _use_worker_mode():
        return []

    # ----- S3 兼容模式 -----
    client = _build_s3_client()
    bucket = settings.s3_bucket_name
    if bucket is None:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    normalized_prefix = _normalize_key(prefix) if prefix else settings.s3_base_path.strip().strip("/")

    def _list() -> list[dict[str, Any]]:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=normalized_prefix or None)
        return resp.get("Contents", [])  # type: ignore[no-any-return]

    contents = await to_thread.run_sync(_list)

    results: list[StoredFileInfo] = []
    for item in contents:
        item_key = item["Key"]
        size = int(item.get("Size") or 0)
        url = _build_public_url(item_key)
        results.append(
            StoredFileInfo(
                key=item_key,
                url=url,
                size=size,
                extra={"LastModified": item.get("LastModified"), "StorageClass": item.get("StorageClass")},
            )
        )
    return results


async def delete_file(*, key: str) -> None:
    """删除文件。

    Worker 模式下 Worker 未实现 delete 接口，此处为 no-op。
    业务侧 `files.delete_file` 已用 try/except 兜底，DB 记录仍会删除，
    R2 中的对象会残留（与 hak 项目行为一致，可通过 R2 生命周期规则清理）。
    """
    if _use_worker_mode():
        return

    # ----- S3 兼容模式 -----
    client = _build_s3_client()
    bucket = settings.s3_bucket_name
    if bucket is None:
        raise RuntimeError("S3 未配置：缺少 s3_bucket_name")

    s3_key = _normalize_key(key)

    def _delete() -> None:
        client.delete_object(Bucket=bucket, Key=s3_key)

    await to_thread.run_sync(_delete)
