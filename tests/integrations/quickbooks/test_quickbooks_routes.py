"""API tests for QuickBooks routes with real service dependencies."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token


def _headers(
    user_id: str,
    *,
    role: str,
    client_type: str,
    organization_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role=role,
        client_type=client_type,
        organization_id=organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": client_type,
    }


def _headers_with_idempotency(
    user_id: str,
    *,
    role: str,
    client_type: str,
    key: str,
    organization_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, str]:
    headers = _headers(user_id, role=role, client_type=client_type, organization_id=organization_id)
    headers["X-Idempotency-Key"] = key
    return headers


@pytest.fixture(autouse=True)
def _set_qb_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.quickbooks.service.settings.QUICKBOOKS_SCOPE_ID",
        "00000000-0000-0000-0000-000000000001",
        raising=False,
    )


@pytest.mark.asyncio
async def test_quickbooks_status_allows_admin_role(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/status",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["connected"] is False
    assert data["connection_status"] == "revoked"
    assert data["failed_syncs"] == 0


@pytest.mark.asyncio
async def test_quickbooks_status_rejects_customer_b2b_role(client: AsyncClient, user_factory) -> None:
    b2b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/status",
        headers=_headers(b2b.id, role="CUSTOMER_B2B", client_type="CUSTOMER_B2B"),
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_allows_admin(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={"status": "FAILED", "search": "job-123"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []


@pytest.mark.asyncio
async def test_quickbooks_status_allows_admin_without_org_claim(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    token, _ = create_access_token(user_id=admin.id, role="ADMIN", client_type="ADMIN", organization_id=None)
    resp = await client.get(
        "/v1/integrations/quickbooks/status",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "ADMIN",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_accepts_repeated_status_filter(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params=[("status", "FAILED"), ("status", "PENDING")],
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_rejects_empty_search(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={"search": ""},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_accepts_period_filter(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={"period": "LAST_7_DAYS", "status": "FAILED"},
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_accepts_custom_date_range(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={"date_from": "2026-02-19", "date_to": "2026-02-25"},
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_rejects_period_and_custom_dates(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={
            "period": "LAST_7_DAYS",
            "date_from": "2026-02-19",
            "date_to": "2026-02-25",
        },
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_failures_endpoint_rejects_invalid_entity_type(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        params={"entity_type": "vendor"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_failure_detail_returns_404_for_missing_log(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(
        "/v1/integrations/quickbooks/failures/00000000-0000-0000-0000-000000000000",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quickbooks_bulk_resync_allows_admin(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/bulk",
        headers=_headers_with_idempotency(
            admin.id,
            role="ADMIN",
            client_type="ADMIN",
            key="qb-bulk-resync-allows-admin-1",
        ),
        json={
            "statuses": ["FAILED", "PENDING"],
            "event_type": "INVOICE_UPDATED",
            "action": "Updated",
            "error_code": "pytest-bulk-resync-no-match",
            "include_non_connection_failures": True,
            "limit": 25,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["requested"] == 0
    assert data["queued"] == 0
    assert data["skipped"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_quickbooks_bulk_resync_rejects_invalid_entity_type(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/bulk",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={"entity_type": "vendor"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_bulk_resync_rejects_status_and_statuses_together(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/bulk",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={"status": "FAILED", "statuses": ["FAILED"]},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_bulk_resync_rejects_empty_statuses(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/bulk",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={"statuses": []},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_final_failures_resync_allows_admin(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/final-failures",
        headers=_headers_with_idempotency(
            admin.id,
            role="ADMIN",
            client_type="ADMIN",
            key="qb-final-failures-allows-admin-1",
        ),
        json={
            "entity_type": "invoice",
            "event_type": "INVOICE_UPDATED",
            "action": "Updated",
            "error_code": "ValidationError",
            "limit": 25,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["requested"] == 0
    assert data["queued"] == 0
    assert data["skipped"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_quickbooks_final_failures_resync_accepts_payment_entity(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/final-failures",
        headers=_headers_with_idempotency(
            admin.id,
            role="ADMIN",
            client_type="ADMIN",
            key="qb-final-failures-payment-entity-1",
        ),
        json={
            "entity_type": "payment",
            "event_type": "PAYMENT_CREATED",
            "action": "Created",
            "limit": 25,
        },
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quickbooks_final_failures_resync_rejects_invalid_entity_type(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/resync/final-failures",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={"entity_type": "vendor"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quickbooks_disconnect_accepts_empty_body(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/disconnect",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["message"] == "QuickBooks disconnected"


@pytest.mark.asyncio
async def test_quickbooks_validate_invoice_does_not_require_body(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/validate/invoices/00000000-0000-0000-0000-000000000000",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quickbooks_sync_payment_allows_admin(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/payments/00000000-0000-0000-0000-000000000000/sync",
        headers=_headers(admin.id, role="ADMIN", client_type="ADMIN"),
        json={"force": False},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quickbooks_sync_payment_rejects_customer_b2b(client: AsyncClient, user_factory) -> None:
    b2b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True)

    resp = await client.post(
        "/v1/integrations/quickbooks/payments/00000000-0000-0000-0000-000000000000/sync",
        headers=_headers(b2b.id, role="CUSTOMER_B2B", client_type="CUSTOMER_B2B"),
        json={"force": True},
    )

    assert resp.status_code == 403
