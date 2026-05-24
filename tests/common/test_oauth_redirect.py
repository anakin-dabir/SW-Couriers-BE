import pytest

from app.common.exceptions import ValidationError
from app.common.oauth_redirect import build_oauth_redirect, validate_oauth_redirect_url


def test_validate_oauth_redirect_rejects_unknown_host(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.common.oauth_redirect.settings.QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS",
        "localhost",
    )
    monkeypatch.setattr("app.common.oauth_redirect.settings.LINK_BASE_URL_ADMIN", "http://localhost:5173")
    monkeypatch.setattr("app.common.oauth_redirect.settings.VERIFICATION_LINK_BASE_URL", "")
    with pytest.raises(ValidationError):
        validate_oauth_redirect_url("https://evil.example.com/callback")


def test_build_oauth_redirect_strips_sensitive_params(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.common.oauth_redirect.settings.QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS",
        "localhost",
    )
    monkeypatch.setattr("app.common.oauth_redirect.settings.LINK_BASE_URL_ADMIN", "http://localhost:5173")
    monkeypatch.setattr("app.common.oauth_redirect.settings.VERIFICATION_LINK_BASE_URL", "")
    url = build_oauth_redirect(
        "http://localhost:5173/settings",
        query={"connected": "1", "code": "must-not-appear", "realm_id": "123"},
    )
    assert "connected=1" in url
    assert "code=" not in url
    assert "realm_id=123" in url
