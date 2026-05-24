"""Allowlisted OAuth / post-auth redirect URL validation."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.common.exceptions import ValidationError
from app.core.config import settings


def _allowed_hosts() -> frozenset[str]:
    hosts: set[str] = set()
    for raw in (
        settings.QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS or "",
        settings.LINK_BASE_URL_ADMIN,
        settings.VERIFICATION_LINK_BASE_URL,
    ):
        for part in str(raw).split(","):
            piece = part.strip()
            if not piece:
                continue
            parsed = urlparse(piece if "://" in piece else f"https://{piece}")
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
    return frozenset(hosts)


def validate_oauth_redirect_url(url: str, *, field_name: str = "redirect_url") -> str:
    """Reject open redirects; only https (or http in dev/test) to allowlisted hosts."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValidationError(f"{field_name} is required")
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"https", "http"}:
        raise ValidationError(f"{field_name} must use https")
    if scheme == "http" and settings.APP_ENV not in ("development", "test"):
        raise ValidationError(f"{field_name} must use https")
    host = (parsed.hostname or "").lower()
    if not host or host not in _allowed_hosts():
        raise ValidationError(f"{field_name} host is not allowlisted")
    return cleaned


def build_oauth_redirect(base_url: str, *, query: dict[str, str]) -> str:
    """Append safe query params to an allowlisted base URL."""
    base = validate_oauth_redirect_url(base_url)
    parsed = urlparse(base)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in query.items():
        if key.lower() in {"code", "state", "access_token", "refresh_token"}:
            continue
        existing[key] = value
    new_query = urlencode(existing)
    return urlunparse(parsed._replace(query=new_query))
