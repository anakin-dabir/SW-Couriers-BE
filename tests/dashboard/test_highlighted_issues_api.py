"""API tests for GET /v1/dashboard/highlighted-issues."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.enums import DeliveryStopStatus, OrderStatus
from app.modules.orders.models import DeliveryStop, Order
from app.modules.planning.enums import RoutePlanStatus, RouteStatus
from app.modules.planning.models import Route, RoutePlan, RouteStop
from app.modules.depots.models import Depot

from tests.dashboard.conftest import admin_headers, create_test_org

HIGHLIGHTED_ISSUES = "/v1/dashboard/highlighted-issues"


@pytest.mark.asyncio
async def test_highlighted_issues_lists_unassigned_route_stop(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await create_test_org(db_session, reference="HI001")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    order = Order(
        order_id="ORD-HI-1",
        master_label_id="ML-HI-1",
        organization_id=org.id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(order)
    await db_session.flush()

    stop = DeliveryStop(
        order_id=order.id,
        tracking_id="SW-2024-HI-001",
        recipient_first_name="Bob",
        recipient_last_name="Johnson",
        recipient_phone="07123456789",
        recipient_email="bob@example.com",
        line_1="1 Test St",
        city="London",
        postcode="E1 1AA",
        status=DeliveryStopStatus.OUT_FOR_DELIVERY,
        scheduled_for=date.today(),
    )
    db_session.add(stop)
    await db_session.flush()

    depot = Depot(name="Issue Depot", code="DP-HI-1", address_line_1="1 Depot Rd", city="London", postcode="E2 2AA")
    db_session.add(depot)
    await db_session.flush()

    plan = RoutePlan(depot_id=depot.id, service_date=date.today(), status=RoutePlanStatus.READY)
    db_session.add(plan)
    await db_session.flush()

    route = Route(plan_id=plan.id, status=RouteStatus.ASSIGNED, driver_id=None)
    db_session.add(route)
    await db_session.flush()

    db_session.add(RouteStop(route_id=route.id, delivery_stop_id=stop.id, sequence=1))
    await db_session.flush()

    resp = await client.get(
        HIGHLIGHTED_ISSUES,
        headers=admin_headers(admin.id),
        params={"organization_id": org.id, "search": "SW-2024-HI"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] >= 1
    item = data["items"][0]
    assert item["tracking_number"] == "SW-2024-HI-001"
    assert item["issue_code"] == "NO_DRIVER_ASSIGNED"
    assert item["client_name"] == "Bob Johnson"
