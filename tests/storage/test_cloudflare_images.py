"""Tests for the Cloudflare Images client.

Most tests monkeypatch settings. One env contract test reads real ``settings`` so that if
``CF_ACCOUNT_ID`` and ``CF_API_TOKEN`` are set (e.g. from ``.env.local``) but signing/CDN
vars are missing, the suite fails—matching what breaks in production for ``requireSignedURLs`` flows.
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from app.common.exceptions import StorageProviderError
from app.core.config import settings
from app.storage.cloudflare_images import (
    CloudflareImageUploadResult,
    CloudflareImagesClient,
    _images_signing_key,
    _resolve_image_cdn_base,
    get_images_client,
)


def test_env_has_cloudflare_images_signing_when_api_configured() -> None:
    """If Images API credentials are present in env, CDN base and signing key must be too.

    This uses live ``settings`` (not monkeypatched). Commenting ``CF_PRIVATE_IMAGE_TOKEN`` without
    setting ``CF_IMAGES_SIGNING_KEY`` fails here when account + API token are still configured.

    CI/agents without Cloudflare signing: set ``SWC_SKIP_CF_IMAGES_ENV_CONTRACT=1``.
    """
    if os.environ.get("SWC_SKIP_CF_IMAGES_ENV_CONTRACT") == "1":
        pytest.skip("SWC_SKIP_CF_IMAGES_ENV_CONTRACT=1")
    account = (settings.CF_ACCOUNT_ID or "").strip()
    api = (settings.CF_API_TOKEN.get_secret_value() or "").strip()
    if not account or not api:
        pytest.skip("Cloudflare Images API not configured (no CF_ACCOUNT_ID / CF_API_TOKEN)")
    cdn = _resolve_image_cdn_base()
    key = _images_signing_key()
    assert cdn, (
        "Set CF_IMAGE_CDN_URL or CF_ACCOUNT_HASH when CF_ACCOUNT_ID and CF_API_TOKEN are set "
        "(signed delivery needs https://imagedelivery.net/<hash>)."
    )
    assert key, (
        "Set CF_IMAGES_SIGNING_KEY or CF_PRIVATE_IMAGE_TOKEN when CF_ACCOUNT_ID and CF_API_TOKEN are set."
    )


def _patch_cf_images_upload_and_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal config so get_images_client() matches production requirements (upload + signed URLs)."""
    monkeypatch.setattr(settings, "CF_ACCOUNT_ID", "acc-1", raising=False)
    monkeypatch.setattr(settings, "CF_API_TOKEN", SecretStr("token-1"), raising=False)
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "test-delivery-hash", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr("test-signing-key"), raising=False)
    monkeypatch.setattr(settings, "CF_PRIVATE_IMAGE_TOKEN", SecretStr(""), raising=False)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def test_resolve_image_cdn_base_strips_extra_path_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "https://imagedelivery.net/myhash/img-uuid/public", raising=False)
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "", raising=False)
    assert _resolve_image_cdn_base() == "https://imagedelivery.net/myhash"


def test_resolve_image_cdn_base_ignores_template_and_uses_account_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "CF_IMAGE_CDN_URL",
        "https://imagedelivery.net/<CF_ACCOUNT_HASH>/<image_id>/<variant_name>",
        raising=False,
    )
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "realhash", raising=False)
    assert _resolve_image_cdn_base() == "https://imagedelivery.net/realhash"


def test_images_signing_key_falls_back_to_private_image_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr(""), raising=False)
    monkeypatch.setattr(settings, "CF_PRIVATE_IMAGE_TOKEN", SecretStr("from-private-token"), raising=False)
    assert _images_signing_key() == "from-private-token"


def test_generate_signed_url_raises_when_signing_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "h", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr(""), raising=False)
    monkeypatch.setattr(settings, "CF_PRIVATE_IMAGE_TOKEN", SecretStr(""), raising=False)
    client = CloudflareImagesClient(account_id="a", api_token="t", base_url="https://api.cloudflare.com")
    with pytest.raises(StorageProviderError, match="signing"):
        client.generate_signed_url("img-1")


def test_generate_signed_url_raises_when_cdn_base_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr("key"), raising=False)
    client = CloudflareImagesClient(account_id="a", api_token="t", base_url="https://api.cloudflare.com")
    with pytest.raises(StorageProviderError, match="signing"):
        client.generate_signed_url("img-1")


def test_get_images_client_raises_in_production_when_signing_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "CF_ACCOUNT_ID", "acc-1", raising=False)
    monkeypatch.setattr(settings, "CF_API_TOKEN", SecretStr("token-1"), raising=False)
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr(""), raising=False)
    monkeypatch.setattr(settings, "CF_PRIVATE_IMAGE_TOKEN", SecretStr(""), raising=False)
    with pytest.raises(StorageProviderError, match="signing"):
        get_images_client()


def test_generate_signed_url_after_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CF_IMAGE_CDN_URL", "", raising=False)
    monkeypatch.setattr(settings, "CF_ACCOUNT_HASH", "acc-hash", raising=False)
    monkeypatch.setattr(settings, "CF_IMAGES_SIGNING_KEY", SecretStr("test-signing-key"), raising=False)
    monkeypatch.setattr("app.storage.cloudflare_images.time.time", lambda: 1_700_000_000.0)

    client = CloudflareImagesClient(account_id="a", api_token="t", base_url="https://api.cloudflare.com")
    url = client.generate_signed_url("image-xyz", variant="public", expiry_seconds=60)
    assert url.startswith("https://imagedelivery.net/acc-hash/image-xyz/public?exp=")
    assert "&sig=" in url


@pytest.mark.asyncio
async def test_get_images_client_uses_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_images_client uses CF_IMAGES_BASE_URL when provided."""
    _patch_cf_images_upload_and_signing(monkeypatch)
    monkeypatch.setattr(settings, "CF_IMAGES_BASE_URL", "https://example.com/api", raising=False)

    client = get_images_client()
    assert client.account_id == "acc-1"
    assert client.api_token == "token-1"
    assert client.base_url == "https://example.com/api"


@pytest.mark.asyncio
async def test_upload_image_sends_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_image posts to the correct URL with headers, data and file."""
    _patch_cf_images_upload_and_signing(monkeypatch)
    monkeypatch.setattr(settings, "CF_IMAGES_BASE_URL", "https://api.cloudflare.com", raising=False)

    fake_response = _FakeResponse(
        status_code=200,
        payload={
            "success": True,
            "result": {
                "id": "image-id-123",
                "filename": "photo.jpg",
                "variants": ["https://cdn.example.com/image-id-123/public"],
            },
        },
    )

    with patch("app.storage.cloudflare_images.httpx.AsyncClient") as mock_client_cls:
        client_instance = mock_client_cls.return_value
        client_instance.__aenter__.return_value = client_instance
        client_instance.__aexit__.return_value = False
        client_instance.post = AsyncMock(return_value=fake_response)

        images_client = get_images_client()
        data = BytesIO(b"image-bytes")
        result: CloudflareImageUploadResult = await images_client.upload_image(
            data,
            filename="photo.jpg",
            require_signed_urls=True,
            metadata={"foo": "bar"},
        )

        client_instance.post.assert_called_once()
        args, kwargs = client_instance.post.call_args
        assert args[0].endswith("/client/v4/accounts/acc-1/images/v1")

        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer token-1"

        form_data = kwargs["data"]
        assert form_data["requireSignedURLs"] == "true"

        files = kwargs["files"]
        assert "file" in files
        file_tuple = files["file"]
        assert file_tuple[0] == "photo.jpg"

        assert result.id == "image-id-123"
        assert result.filename == "photo.jpg"
        assert result.variants == ["https://cdn.example.com/image-id-123/public"]


@pytest.mark.asyncio
async def test_upload_image_raises_when_success_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Cloudflare returns success=False, upload_image raises StorageProviderError."""
    _patch_cf_images_upload_and_signing(monkeypatch)
    monkeypatch.setattr(settings, "CF_IMAGES_BASE_URL", "https://api.cloudflare.com", raising=False)

    fake_response = _FakeResponse(status_code=200, payload={"success": False, "errors": [{"code": 1234}]})

    with patch("app.storage.cloudflare_images.httpx.AsyncClient") as mock_client_cls:
        client_instance = mock_client_cls.return_value
        client_instance.__aenter__.return_value = client_instance
        client_instance.__aexit__.return_value = False
        client_instance.post = AsyncMock(return_value=fake_response)

        images_client = get_images_client()
        with pytest.raises(StorageProviderError):
            await images_client.upload_image(BytesIO(b"image-bytes"), filename="photo.jpg")


# ── Optional live smoke test (uses real Cloudflare Images credentials) ───────


def _images_configured() -> bool:
    """Return True if Cloudflare Images appears to be configured for a live smoke test."""
    account = (settings.CF_ACCOUNT_ID or os.environ.get("CF_ACCOUNT_ID") or "").strip()
    token = settings.CF_API_TOKEN.get_secret_value() or os.environ.get("CF_API_TOKEN") or ""
    return bool(account and token)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _images_configured(),
    reason="Cloudflare Images not configured (set CF_IMAGES_ACCOUNT_ID and CF_IMAGES_API_TOKEN to run live smoke test)",
)
async def test_cloudflare_images_live_smoke_connectivity() -> None:
    """Live smoke test: perform a small, real request to Cloudflare Images.

    This test is skipped by default unless Cloudflare Images credentials are set.
    It issues a lightweight GET request to verify authentication and API reachability,
    without uploading or modifying any images.
    """
    client = get_images_client()

    url = f"{client.base_url}/client/v4/accounts/{client.account_id}/images/v1"
    headers = {"Authorization": f"Bearer {client.api_token}"}

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        response = await http_client.get(url, headers=headers, params={"per_page": 1})

    # Any 2xx response confirms credentials and connectivity.
    assert 200 <= response.status_code < 300
