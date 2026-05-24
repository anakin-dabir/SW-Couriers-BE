"""Unit tests for QuickBooks auth helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from app.integrations.quickbooks.auth import (
    build_oauth_authorize_url,
    decrypt_token,
    encrypt_token,
    verify_webhook_signature,
)


def test_encrypt_decrypt_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_ENCRYPTION_KEY", SecretStr("qb-encryption-key-for-tests"))

    plain = "token-value-123"
    encrypted = encrypt_token(plain)

    assert encrypted != plain
    assert decrypt_token(encrypted) == plain


def test_build_oauth_authorize_url_contains_required_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_CLIENT_ID", "client-id-1")
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_CLIENT_SECRET", SecretStr("client-secret-1"))
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_REDIRECT_URI", "https://api.example.com/v1/integrations/quickbooks/callback")
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_SCOPES", "com.intuit.quickbooks.accounting")

    state = "state-xyz"
    url = build_oauth_authorize_url(state)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert params["client_id"] == ["client-id-1"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["https://api.example.com/v1/integrations/quickbooks/callback"]
    assert params["scope"] == ["com.intuit.quickbooks.accounting"]
    assert params["state"] == [state]


def test_verify_webhook_signature_true_and_false(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = "webhook-shared-secret"
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN", SecretStr(verifier))

    body = b'{"event":"invoice.updated"}'
    expected = base64.b64encode(hmac.new(verifier.encode(), body, hashlib.sha256).digest()).decode()

    assert verify_webhook_signature(body, expected) is True
    assert verify_webhook_signature(body, "invalid-signature") is False
