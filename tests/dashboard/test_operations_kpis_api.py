"""API tests for GET /v1/dashboard/operations-kpis."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.depots.models import Depot
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus
from app.modules.orders.models import DeliveryStop, DeliveryStopEvent, Order
from app.modules.planning.enums import RoutePlanStatus, RouteStatus
from app.modules.planning.models import Route, RoutePlan, RouteStop

from tests.dashboard.conftest import admin_headers, create_test_org

OPERATIONS_KPIS = "/v1/dashboard/operations-kpis"


@pytest.mark.asyncio
async def test_operations_kpis_returns_expected_shape_for_admin(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(OPERATIONS_KPIS, headers=admin_headers(admin.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["as_of_date"]
    for key in (
        "next_7_day_stops",
        "delivered_today",
        "today_orders",
        "pending_orders",
        "active_drivers",
    ):
        assert key in data
    assert "current" in data["next_7_day_stops"]
    assert "change_abs" in data["today_orders"]
    assert "success_rate_pct" in data["delivered_today"]


@pytest.mark.asyncio
async def test_operations_kpis_counts_today_orders_and_delivered_events(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await create_test_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()

    order = Order(
        order_id="ORD-DASH-1",
        master_label_id="ML-DASH-1",
        organization_id=org.id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(order)
    await db_session.flush()

    stop = DeliveryStop(
        order_id=order.id,
        tracking_id="TRK-DASH-1",
        recipient_first_name="Test",
        recipient_last_name="User",
        recipient_phone="07123456789",
        recipient_email="test@example.com",
        line_1="1 Test Street",
        city="London",
        postcode="E1 1AA",
        status=DeliveryStopStatus.DELIVERED,
    )
    db_session.add(stop)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(
        DeliveryStopEvent(
            delivery_stop_id=stop.id,
            from_status=DeliveryStopStatus.OUT_FOR_DELIVERY.value,
            to_status=DeliveryStopStatus.DELIVERED.value,
            created_at=now,
        )
    )
    await db_session.flush()

    resp = await client.get(
        OPERATIONS_KPIS,
        headers=admin_headers(admin.id),
        params={"organization_id": org.id, "as_of_date": today.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["today_orders"]["current"] >= 1
    assert data["delivered_today"]["current"] >= 1
    assert data["pending_orders"]["current"] >= 1


@pytest.mark.asyncio
async def test_operations_kpis_counts_next_seven_day_route_stops(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await create_test_org(db_session, reference="DASH002")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()

    depot = Depot(
        name="Dashboard Depot",
        code="DPD-DASH",
        address_line_1="1 Depot Road",
        city="London",
        postcode="E2 2AA",
    )
    db_session.add(depot)
    await db_session.flush()

    order = Order(
        order_id="ORD-DASH-2",
        master_label_id="ML-DASH-2",
        organization_id=org.id,
        status=OrderStatus.PICKUP_SCHEDULED,
    )
    db_session.add(order)
    await db_session.flush()

    plan = RoutePlan(
        depot_id=depot.id,
        service_date=today + timedelta(days=2),
        status=RoutePlanStatus.READY,
    )
    db_session.add(plan)
    await db_session.flush()

    route = Route(plan_id=plan.id, status=RouteStatus.ASSIGNED, total_stops=1)
    db_session.add(route)
    await db_session.flush()

    db_session.add(RouteStop(route_id=route.id, order_id=order.id, sequence=1))
    await db_session.flush()

    resp = await client.get(
        OPERATIONS_KPIS,
        headers=admin_headers(admin.id),
        params={"organization_id": org.id, "as_of_date": today.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["next_7_day_stops"]["current"] >= 1


@pytest.mark.asyncio
async def test_operations_kpis_kpi_invariants(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(OPERATIONS_KPIS, headers=admin_headers(admin.id))
    data = resp.json()["data"]
    for key in ("next_7_day_stops", "today_orders", "pending_orders", "active_drivers"):
        block = data[key]
        assert block["change_abs"] == block["current"] - block["previous"]
    delivered = data["delivered_today"]
    assert delivered["change_abs"] == delivered["current"] - delivered["previous"]
