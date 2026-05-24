"""Cross-cutting dashboard API tests (auth, validation, org scope)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.modules.permission.service import PermissionService

from tests.dashboard.conftest import DASHBOARD_BASE, admin_headers, b2b_headers, create_test_org

OPERATIONS_KPIS = f"{DASHBOARD_BASE}/operations-kpis"
TODAYS_FINANCIALS = f"{DASHBOARD_BASE}/todays-financials"
HIGHLIGHTED_ISSUES = f"{DASHBOARD_BASE}/highlighted-issues"


@pytest.mark.asyncio
async def test_all_dashboard_endpoints_require_auth(client: AsyncClient) -> None:
    for path in (OPERATIONS_KPIS, TODAYS_FINANCIALS, HIGHLIGHTED_ISSUES):
        resp = await client.get(path)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_b2b_cannot_access_other_org_dashboard(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org_a = await create_test_org(db_session, reference="DASHA")
    org_b = await create_test_org(db_session, reference="DASHB")
    b2b_user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org_a.id,
    )
    headers = b2b_headers(b2b_user.id, org_a.id)
    resp = await client.get(
        OPERATIONS_KPIS,
        headers=headers,
        params={"organization_id": org_b.id},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_highlighted_issues_rejects_invalid_status_filter(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(
        HIGHLIGHTED_ISSUES,
        headers=admin_headers(admin.id),
        params={"status": "NOT_A_REAL_STATUS"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_todays_financials_rejects_as_of_date_too_far_in_past(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    past = (date.today() - timedelta(days=400)).isoformat()
    resp = await client.get(
        TODAYS_FINANCIALS,
        headers=admin_headers(admin.id),
        params={"as_of_date": past},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_highlighted_issues_rejects_as_of_date_too_far_in_future(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    future = (date.today() + timedelta(days=30)).isoformat()
    resp = await client.get(
        HIGHLIGHTED_ISSUES,
        headers=admin_headers(admin.id),
        params={"as_of_date": future},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_operations_kpis_rejects_as_of_date_too_far_in_future(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    future = (date.today() + timedelta(days=30)).isoformat()
    resp = await client.get(
        OPERATIONS_KPIS,
        headers=admin_headers(admin.id),
        params={"as_of_date": future},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_operations_kpis_org_scoping_isolates_counts(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    from app.modules.orders.enums import OrderStatus
    from app.modules.orders.models import Order

    org_a = await create_test_org(db_session, reference="SCOPEA")
    org_b = await create_test_org(db_session, reference="SCOPEB")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()

    for ref, org in (("ORD-SCOPE-A", org_a), ("ORD-SCOPE-B", org_b)):
        db_session.add(
            Order(
                order_id=ref,
                master_label_id=f"ML-{ref}",
                organization_id=org.id,
                status=OrderStatus.PENDING_PICKUP,
            )
        )
    await db_session.flush()

    resp_a = await client.get(
        OPERATIONS_KPIS,
        headers=admin_headers(admin.id),
        params={"organization_id": org_a.id, "as_of_date": today.isoformat()},
    )
    resp_b = await client.get(
        OPERATIONS_KPIS,
        headers=admin_headers(admin.id),
        params={"organization_id": org_b.id, "as_of_date": today.isoformat()},
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["data"]["today_orders"]["current"] >= 1
    assert resp_b.json()["data"]["today_orders"]["current"] >= 1


@pytest.mark.asyncio
async def test_todays_financials_revenue_trend_always_seven_days(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(TODAYS_FINANCIALS, headers=admin_headers(admin.id))
    assert resp.status_code == 200
    trend = resp.json()["data"]["revenue_trend"]
    assert len(trend) == 7
    assert trend[0]["weekday"]
    assert trend[-1]["date"] == resp.json()["data"]["as_of_date"]


@pytest.mark.asyncio
async def test_highlighted_issues_pagination_empty_page(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(
        HIGHLIGHTED_ISSUES,
        headers=admin_headers(admin.id),
        params={"page": 99, "size": 20},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []
    assert data["page"] == 99


@pytest.mark.asyncio
async def test_dashboard_denied_when_dashboard_permission_revoked(
    client_real_permissions: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    perm = PermissionService(db_session)
    await perm.set_permission(admin.id, Resource.DASHBOARD, PermissionLevel.NONE, granted_by=admin.id)
    headers = admin_headers(admin.id)
    for path in (OPERATIONS_KPIS, TODAYS_FINANCIALS, HIGHLIGHTED_ISSUES):
        assert (await client_real_permissions.get(path, headers=headers)).status_code == 403
