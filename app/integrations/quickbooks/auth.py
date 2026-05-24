"""QuickBooks OAuth and token cryptography helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.common.exceptions import AuthenticationError, ValidationError
from app.core.config import settings

QBO_OAUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_STATE_TTL_SECONDS = 600


def get_qbo_api_base_url() -> str:
    if settings.QUICKBOOKS_API_BASE_URL:
        return settings.QUICKBOOKS_API_BASE_URL.rstrip("/")
    return (
        "https://sandbox-quickbooks.api.intuit.com"
        if settings.QUICKBOOKS_ENV == "sandbox"
        else "https://quickbooks.api.intuit.com"
    )


def get_oauth_state_ttl_seconds() -> int:
    return _STATE_TTL_SECONDS


def build_oauth_authorize_url(state: str) -> str:
    if not settings.QUICKBOOKS_CLIENT_ID or not settings.QUICKBOOKS_CLIENT_SECRET.get_secret_value():
        raise ValidationError("QuickBooks client credentials are not configured")
    if not settings.QUICKBOOKS_REDIRECT_URI:
        raise ValidationError("QUICKBOOKS_REDIRECT_URI is required")

    params = {
        "client_id": settings.QUICKBOOKS_CLIENT_ID,
        "response_type": "code",
        "scope": settings.QUICKBOOKS_SCOPES.strip(),
        "redirect_uri": settings.QUICKBOOKS_REDIRECT_URI,
        "state": state,
    }
    return f"{QBO_OAUTH_BASE}?{urlencode(params)}"


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def _get_fernet() -> Fernet:
    raw_key = settings.QUICKBOOKS_ENCRYPTION_KEY.get_secret_value().strip()
    if not raw_key:
        raise ValidationError("QUICKBOOKS_ENCRYPTION_KEY is required before connecting QuickBooks")

    # Accept either a valid Fernet key or raw text (converted deterministically).
    try:
        return Fernet(raw_key.encode())
    except Exception:
        digest = hashlib.sha256(raw_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
        return Fernet(key)


def encrypt_token(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise AuthenticationError("QuickBooks token decryption failed") from None


async def exchange_code_for_tokens(code: str, realm_id: str) -> dict:
    auth = (
        settings.QUICKBOOKS_CLIENT_ID,
        settings.QUICKBOOKS_CLIENT_SECRET.get_secret_value(),
    )
    timeout = httpx.Timeout(
        connect=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
        read=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
        write=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
        pool=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
    )
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.QUICKBOOKS_REDIRECT_URI,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                QBO_TOKEN_URL,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise AuthenticationError("QuickBooks code exchange failed due to network error") from exc
    if response.status_code >= 400:
        raise AuthenticationError("QuickBooks code exchange failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AuthenticationError("QuickBooks code exchange returned non-JSON response") from exc
    return _normalize_token_payload(payload, realm_id)


async def refresh_tokens(refresh_token: str, realm_id: str) -> dict:
    auth = (
        settings.QUICKBOOKS_CLIENT_ID,
        settings.QUICKBOOKS_CLIENT_SECRET.get_secret_value(),
    )
    timeout = httpx.Timeout(
        connect=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
        read=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
        write=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
        pool=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
    )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                QBO_TOKEN_URL,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise AuthenticationError("QuickBooks token refresh failed due to network error") from exc
    if response.status_code >= 400:
        raise AuthenticationError("QuickBooks token refresh failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AuthenticationError("QuickBooks token refresh returned non-JSON response") from exc
    return _normalize_token_payload(payload, realm_id, current_refresh_token=refresh_token)


def _normalize_token_payload(payload: dict, realm_id: str, current_refresh_token: str | None = None) -> dict:
    now = datetime.now(UTC)
    access_expires_in = int(payload.get("expires_in", 3600))
    refresh_expires_in = int(payload.get("x_refresh_token_expires_in", 86400 * 100))
    access_token = payload.get("access_token")
    if not access_token:
        raise AuthenticationError("QuickBooks token payload missing access token")
    resolved_refresh_token = payload.get("refresh_token") or current_refresh_token
    if not resolved_refresh_token:
        raise AuthenticationError("QuickBooks token payload missing refresh token")
    return {
        "realm_id": realm_id,
        "access_token": access_token,
        "refresh_token": resolved_refresh_token,
        "access_token_expires_at": now + timedelta(seconds=max(access_expires_in - 30, 60)),
        "refresh_token_expires_at": now + timedelta(seconds=refresh_expires_in),
    }


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    verifier = settings.QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN.get_secret_value()
    if not verifier:
        return False
    digest = hmac.new(verifier.encode(), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature_header.strip())
