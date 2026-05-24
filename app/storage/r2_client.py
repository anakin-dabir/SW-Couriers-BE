from __future__ import annotations

import threading
from typing import Any

import boto3
import structlog
from botocore.client import BaseClient

from app.common.enums.logger import LogEvent
from app.common.exceptions import StorageProviderError
from app.core.config import settings

"""Cloudflare R2 client — S3-compatible object storage.

This module centralises configuration and client creation for R2 so that:
  - All R2 access uses a single, well-typed configuration source (settings).
  - The integration can be moved into a separate microservice later with minimal changes.

We expose:
  - get_r2_client()           → low-level boto3 S3 client (creates a fresh one; use for tests/overrides).
  - get_default_r2_client()   → module-level singleton client for production use.
  - get_r2_bucket_name()      → configured bucket name (validated non-empty).
  - generate_presigned_url()  → generates a presigned URL for accessing R2 objects.

Higher-level storage operations (upload/download) should live in feature services
or dedicated storage helpers that depend on this client.
"""

logger = structlog.get_logger()

# Presigned URL expiry must be between 1 minute and 7 days.
_PRESIGN_EXPIRY_MIN: int = 60
_PRESIGN_EXPIRY_MAX: int = 7 * 24 * 3600

_ALLOWED_PRESIGN_METHODS: frozenset[str] = frozenset({"GET", "PUT"})

# Module-level singleton boto3 client and thread-safe initialisation lock.
# boto3 clients are thread-safe for read operations (presign, get_object);
# for put_object we call run_in_executor anyway so thread safety is guaranteed.
_default_client: BaseClient | None = None
_client_lock = threading.Lock()


def _build_r2_endpoint_url() -> str:
    """Derive the R2 endpoint URL from settings.

    R2 is S3-compatible; the recommended endpoint format is:
      https://<account_id>.r2.cloudflarestorage.com

    We also support overriding via R2_ENDPOINT_URL for non-standard setups.
    """
    if settings.R2_ENDPOINT_URL:
        return settings.R2_ENDPOINT_URL.rstrip("/")
    if not settings.R2_ACCOUNT_ID:
        logger.error(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="r2",
            reason="missing_account_id",
        )
        raise StorageProviderError("Object storage is not configured")
    return f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


def _ensure_r2_credentials() -> None:
    """Validate that required R2 credentials are present.

    Raises StorageProviderError in production when configuration is incomplete.
    In test and development we only log a warning so that unit tests can run
    with mocked clients.
    """
    access_key = (settings.R2_ACCESS_KEY_ID or "").strip()
    secret = settings.R2_SECRET_ACCESS_KEY.get_secret_value() or ""
    if not access_key or not secret:
        logger.warning(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="r2",
            access_key_set=bool(access_key),
            secret_set=bool(secret),
        )
        if settings.is_production:
            raise StorageProviderError("Object storage credentials are not configured")


def get_r2_bucket_name() -> str:
    """Return the configured R2 bucket name (non-empty)."""
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not bucket:
        logger.error(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="r2",
            reason="missing_bucket_name",
        )
        raise StorageProviderError("Object storage bucket is not configured")
    return bucket


def get_r2_client(**overrides: Any) -> BaseClient:
    """Return a boto3 S3 client configured for Cloudflare R2.

    Creates a fresh client each time — intended for use in tests where
    credentials and endpoints are overridden via monkeypatch.
    Creates a fresh client each time — intended for use in tests where
    credentials and endpoints are overridden via monkeypatch.

    For production upload/presign operations, prefer get_default_r2_client()
    which reuses a module-level singleton.
    For production upload/presign operations, prefer get_default_r2_client()
    which reuses a module-level singleton.
    """
    _ensure_r2_credentials()
    endpoint_url = overrides.pop("endpoint_url", _build_r2_endpoint_url())
    region_name = overrides.pop("region_name", "auto")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY.get_secret_value(),
        region_name=region_name,
        **overrides,
    )


def get_default_r2_client() -> BaseClient:
    """Return the module-level singleton boto3 client for production use.

    Initialised once and reused across requests. This avoids rebuilding the
    internal urllib3 connection pool on every upload or presign call.

    Thread-safe: uses a lock for the one-time initialisation. After that,
    reading the module global is safe without the lock.
    """
    global _default_client
    if _default_client is None:
        with _client_lock:
            # Double-checked locking: another thread may have initialised it
            # while we waited for the lock.
            if _default_client is None:
                _default_client = get_r2_client()
    return _default_client


def generate_presigned_url(
    file_key: str,
    *,
    expiry_seconds: int = 3600,
    http_method: str = "GET",
    content_type: str | None = None,
    response_content_disposition: str | None = None,
) -> str:
    """Generate a presigned URL for accessing an R2 object.

    Presigned URL generation is a local HMAC operation — no network call is made.

    Args:
        file_key: The R2 object key (e.g. "drivers/123/documents/20240101T120000Z_licence.pdf").
        expiry_seconds: URL validity window. Must be between 60 and 604800 (7 days).
        http_method: Must be "GET" (download) or "PUT" (direct client upload).
        content_type: For GET: sets the response Content-Type header returned by R2.
                      For PUT: enforces the Content-Type the client must send.
        response_content_disposition: For GET: sets response Content-Disposition
                      (for example, inline vs attachment filename behavior).

    Returns:
        A presigned URL string.

    Raises:
        ValueError: If http_method or expiry_seconds are out of range.
        StorageProviderError: If URL generation fails.
    """
    method = http_method.upper()
    if method not in _ALLOWED_PRESIGN_METHODS:
        raise ValueError(f"http_method must be one of {sorted(_ALLOWED_PRESIGN_METHODS)}, got {method!r}")

    if not (_PRESIGN_EXPIRY_MIN <= expiry_seconds <= _PRESIGN_EXPIRY_MAX):
        raise ValueError(f"expiry_seconds must be between {_PRESIGN_EXPIRY_MIN} and {_PRESIGN_EXPIRY_MAX}, " f"got {expiry_seconds}")

    try:
        bucket = get_r2_bucket_name()
        client = get_default_r2_client()

        params: dict[str, str] = {
            "Bucket": bucket,
            "Key": file_key,
        }
        if content_type:
            if method == "GET":
                # Override the Content-Type header in the R2 response.
                params["ResponseContentType"] = content_type
            else:
                # PUT: require the client to send this Content-Type.
                # R2 / S3 will reject uploads that don't match the signed type.
                params["ContentType"] = content_type
        if response_content_disposition and method == "GET":
            params["ResponseContentDisposition"] = response_content_disposition

        url: str = client.generate_presigned_url(
            ClientMethod=f"{method.lower()}_object",
            Params=params,
            ExpiresIn=expiry_seconds,
        )
        return url
    except (ValueError, StorageProviderError):
        raise
    except Exception as exc:
        logger.error(
            LogEvent.STORAGE_PROVIDER_ERROR,
            provider="r2",
            reason="presigned_url_generation_failed",
            file_key=file_key,
            error=str(exc),
        )
        raise StorageProviderError("Failed to generate presigned URL") from exc
