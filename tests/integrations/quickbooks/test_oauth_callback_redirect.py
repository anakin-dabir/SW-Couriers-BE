"""Route tests for QuickBooks OAuth callback browser redirect."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _set_qb_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.quickbooks.service.settings.QUICKBOOKS_SCOPE_ID",
        "00000000-0000-0000-0000-000000000001",
        raising=False,
    )


@pytest.mark.asyncio
async def test_oauth_callback_redirects_to_success_url_without_code(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success_url = "http://localhost:5173/settings/integrations/quickbooks"
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_SUCCESS_URL",
        success_url,
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_ERROR_URL",
        success_url,
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS",
        "localhost",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.LINK_BASE_URL_ADMIN",
        "http://localhost:5173",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.VERIFICATION_LINK_BASE_URL",
        "",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.APP_ENV",
        "development",
    )

    monkeypatch.setattr(
        "app.integrations.quickbooks.service.QuickBooksService.handle_callback",
        AsyncMock(return_value={"connected": True, "realm_id": "realm-test-99"}),
    )

    resp = await client.get(
        "/v1/integrations/quickbooks/callback",
        params={"state": "oauth-state-test-1", "code": "intuit-code-secret", "realmId": "realm-test-99"},
        follow_redirects=False,
    )

    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith(success_url)
    assert "status=connected" in location
    assert "connected=1" in location
    assert "realm_id=realm-test-99" in location
    assert "code=" not in location
    assert "intuit-code-secret" not in location


@pytest.mark.asyncio
async def test_oauth_callback_redirects_to_error_url_on_failure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_url = "http://localhost:5173/settings/integrations/quickbooks"
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_SUCCESS_URL",
        error_url,
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_ERROR_URL",
        error_url,
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS",
        "localhost",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.LINK_BASE_URL_ADMIN",
        "http://localhost:5173",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.VERIFICATION_LINK_BASE_URL",
        "",
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.routes.settings.APP_ENV",
        "development",
    )

    monkeypatch.setattr(
        "app.integrations.quickbooks.service.QuickBooksService.handle_callback",
        AsyncMock(side_effect=RuntimeError("token exchange failed")),
    )

    resp = await client.get(
        "/v1/integrations/quickbooks/callback",
        params={"state": "missing-state", "code": "code-x", "realmId": "realm-x"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert "status=error" in resp.headers["location"]
