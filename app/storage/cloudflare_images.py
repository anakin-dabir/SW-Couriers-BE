from __future__ import annotations

"""Cloudflare Images client for driver/profile photos and other non-sensitive images.

This module wraps the Cloudflare Images HTTP API using httpx so that:
  - All configuration is centralised in settings.
  - Upload logic is reusable across modules and easy to extract into a microservice later.

We intentionally keep the surface area small:
  - CloudflareImagesClient.upload_image(...) → uploads a single image and returns its ID.
  - CloudflareImagesClient.generate_signed_url(...) → generates a time-limited signed CDN URL using
      HMAC-SHA256 (Cloudflare's requireSignedURLs mechanism). No API call is made — signing is local.
  - get_images_client()                      → constructs a client from settings.

Signed URL mechanism:
  Images uploaded with requireSignedURLs=True can only be accessed via signed CDN URLs.
  Cloudflare validates the HMAC-SHA256 signature and ``exp`` on each request. The signing key
  must be created in the Cloudflare dashboard (Images → Keys) and stored as CF_IMAGES_SIGNING_KEY.
  See: https://developers.cloudflare.com/images/manage-images/serve-images/serve-private-images/
"""

import hashlib  # noqa: E402
import hmac  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any, BinaryIO  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

import httpx  # noqa: E402
import structlog  # noqa: E402

from app.common.enums.logger import LogEvent  # noqa: E402
from app.common.exceptions import StorageProviderError  # noqa: E402
from app.core.config import settings  # noqa: E402

logger = structlog.get_logger()


def _resolve_image_cdn_base() -> str:
    """Return CDN base for signing: ``https://imagedelivery.net/<account_hash>`` only.

    Cloudflare docs often show a full example URL; if that is pasted into ``CF_IMAGE_CDN_URL``,
    we keep only the account hash segment. Placeholder strings (``<...>``) are ignored so
    ``CF_ACCOUNT_HASH`` can supply the real hash.
    """
    raw = (settings.CF_IMAGE_CDN_URL or "").strip().rstrip("/")
    account_hash = (settings.CF_ACCOUNT_HASH or "").strip()

    if raw and ("<" in raw or ">" in raw):
        logger.warning(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="cloudflare_images",
            reason="cf_image_cdn_url_contains_placeholders",
            hint="Use https://imagedelivery.net/<your_hash> only, or set CF_ACCOUNT_HASH and leave URL empty",
        )
        raw = ""

    if raw:
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https") and parsed.netloc == "imagedelivery.net":
            parts = [p for p in parsed.path.split("/") if p]
            if parts and "<" not in parts[0]:
                return f"https://imagedelivery.net/{parts[0]}".rstrip("/")
        return raw

    if account_hash:
        return f"https://imagedelivery.net/{account_hash}".rstrip("/")
    return ""


def _images_signing_key() -> str:
    """HMAC secret for signed imagedelivery.net URLs (Images → Keys). Either env name is accepted."""
    primary = (settings.CF_IMAGES_SIGNING_KEY.get_secret_value() or "").strip()
    if primary:
        return primary
    return (settings.CF_PRIVATE_IMAGE_TOKEN.get_secret_value() or "").strip()


@dataclass(slots=True)
class CloudflareImageUploadResult:
    """Result of a successful image upload."""

    id: str
    filename: str | None
    variants: list[str] | None


@dataclass(slots=True)
class CloudflareImagesClient:
    """Minimal async client for Cloudflare Images."""

    account_id: str
    api_token: str
    base_url: str

    async def upload_image(
        self,
        file: BinaryIO,
        *,
        filename: str = "image",
        require_signed_urls: bool = True,
        metadata: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> CloudflareImageUploadResult:
        """Upload a single image to Cloudflare Images.

        The uploaded image is marked private by using requireSignedURLs when configured
        on your Cloudflare account; we pass the flag via form field for clarity.
        """
        url = f"{self.base_url}/client/v4/accounts/{self.account_id}/images/v1"

        headers = {
            "Authorization": f"Bearer {self.api_token}",
        }

        form_data: dict[str, Any] = {
            "requireSignedURLs": "true" if require_signed_urls else "false",
        }
        if metadata:
            # Cloudflare expects JSON string for metadata.
            import json

            form_data["metadata"] = json.dumps(metadata)

        files = {
            "file": (filename, file),
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, data=form_data, files=files)
        except httpx.RequestError as exc:  # network / DNS / TLS issues
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="request_error",
                error=str(exc),
            )
            # Do not expose low-level network details to callers.
            raise StorageProviderError("Image upload failed") from exc

        if response.status_code >= 400:
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="http_error",
                status_code=response.status_code,
                body=response.text[:500],
            )
            # Treat all 4xx/5xx as a generic storage failure to callers.
            raise StorageProviderError("Image upload failed")

        payload = response.json()
        success = payload.get("success", False)
        if not success:
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="api_error",
                body=payload,
            )
            raise StorageProviderError("Image upload failed")

        result = payload.get("result") or {}
        return CloudflareImageUploadResult(
            id=str(result.get("id")),
            filename=result.get("filename"),
            variants=list(result.get("variants") or []),
        )

    async def delete_image(self, image_id: str, *, timeout: float = 15.0) -> None:
        url = f"{self.base_url}/client/v4/accounts/{self.account_id}/images/v1/{image_id}"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.delete(url, headers=headers)
        except httpx.RequestError as exc:
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="delete_request_error",
                image_id=image_id,
                error=str(exc),
            )
            raise StorageProviderError("Image deletion failed") from exc

        # 404 is acceptable — image already gone
        if response.status_code >= 400 and response.status_code != 404:
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="delete_http_error",
                image_id=image_id,
                status_code=response.status_code,
                body=response.text[:500],
            )
            raise StorageProviderError("Image deletion failed")

    def generate_signed_url(
        self,
        image_id: str,
        *,
        variant: str = "public",
        expiry_seconds: int = 3600,
    ) -> str:
        """Generate a time-limited signed CDN URL for a private Cloudflare Image.

        This signs the URL locally using HMAC-SHA256 — no API call is made, so
        this is fast and free to call on every request.

        Cloudflare validates the `sig` (HMAC-SHA256 hex) and `expiry` (Unix
        timestamp) query parameters on every CDN request. Once expired, the URL
        returns 403.

        The signing key must be created in the Cloudflare dashboard:
          Images → Keys → Add Key
        and stored in CF_IMAGES_SIGNING_KEY (or CF_PRIVATE_IMAGE_TOKEN — same secret).

        Args:
            image_id: The Cloudflare Images ID (returned from upload_image).
            variant: Image variant name (default: "public").
            expiry_seconds: How long the URL should be valid (default: 1 hour).

        Returns:
            A signed CDN URL string.

        Raises:
            StorageProviderError: If signing key or CDN URL is not configured.
        """
        cdn_base = _resolve_image_cdn_base()
        signing_key = _images_signing_key()

        if not cdn_base or not signing_key:
            logger.error(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="cloudflare_images",
                reason="signing_not_configured",
                cdn_url_set=bool(cdn_base),
                signing_key_set=bool(signing_key),
            )
            raise StorageProviderError("Image signing is not configured")

        expiry = int(time.time()) + expiry_seconds
        parsed = urlparse(cdn_base)
        path_segs = [p for p in parsed.path.split("/") if p]
        if not path_segs:
            raise StorageProviderError("Image signing is not configured")
        account_hash = path_segs[0]
        string_to_sign = f"/{account_hash}/{image_id}/{variant}?exp={expiry}"

        sig = hmac.new(
            signing_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return f"{cdn_base}/{image_id}/{variant}?exp={expiry}&sig={sig}"


def _ensure_images_credentials() -> None:
    """Validate that required Cloudflare Images upload credentials are present."""
    account_id = (settings.CF_ACCOUNT_ID or "").strip()
    token = settings.CF_API_TOKEN.get_secret_value() or ""
    if not account_id or not token:
        logger.warning(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="cloudflare_images",
            account_id_set=bool(account_id),
            token_set=bool(token),
        )
        if settings.is_production:
            raise StorageProviderError("Image storage is not configured")


def _ensure_signing_configured() -> None:
    """Validate that Cloudflare Images signed URL config is present.

    CF_IMAGE_CDN_URL / CF_ACCOUNT_HASH: resolved to https://imagedelivery.net/<hash> (see _resolve_image_cdn_base)
    CF_IMAGES_SIGNING_KEY or CF_PRIVATE_IMAGE_TOKEN: same HMAC key from CF dashboard → Images → Keys

    Raises StorageProviderError in production if either is missing.
    """
    cdn_url = _resolve_image_cdn_base()
    signing_key = _images_signing_key()
    if not cdn_url or not signing_key:
        logger.warning(
            LogEvent.STORAGE_NOT_CONFIGURED,
            provider="cloudflare_images",
            cdn_url_set=bool(cdn_url),
            signing_key_set=bool(signing_key),
        )
        if settings.is_production:
            raise StorageProviderError("Image signing is not configured")


def _build_base_url() -> str:
    """Base URL for Cloudflare API calls.

    We use CF_IMAGES_BASE_URL when provided to allow custom routing/CDN,
    otherwise default to Cloudflare's public API domain.
    """
    if settings.CF_IMAGES_BASE_URL:
        return settings.CF_IMAGES_BASE_URL.rstrip("/")
    # Uploads always go via the Images API host; CDN URLs (e.g. CF_IMAGE_CDN_URL)
    # are for serving images to clients, not for this client.
    return "https://api.cloudflare.com"


def get_images_client() -> CloudflareImagesClient:
    """Construct a CloudflareImagesClient from configuration and environment.

    Validates upload credentials and, in production, signing config too.
    """
    _ensure_images_credentials()
    _ensure_signing_configured()
    account_id = (settings.CF_ACCOUNT_ID or "").strip()
    api_token = settings.CF_API_TOKEN.get_secret_value() or ""
    return CloudflareImagesClient(
        account_id=account_id,
        api_token=api_token,
        base_url=_build_base_url(),
    )
