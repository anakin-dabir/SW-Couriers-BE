"""Integration tests for operations dashboard repository."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.operations_repository import OperationsDashboardRepository
from app.modules.dashboard.utils import utc_day_window
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus
from app.modules.orders.models import DeliveryStop, DeliveryStopEvent, Order

from tests.dashboard.conftest import create_test_org


@pytest.mark.asyncio
async def test_count_pending_orders_excludes_terminal_statuses(db_session: AsyncSession) -> None:
    org = await create_test_org(db_session, reference="REPO-PEND")
    repo = OperationsDashboardRepository(db_session)

    db_session.add(
        Order(
            order_id="ORD-PEND-OPEN",
            master_label_id="ML-PEND-OPEN",
            organization_id=org.id,
            status=OrderStatus.PENDING_PICKUP,
        )
    )
    db_session.add(
        Order(
            order_id="ORD-PEND-DONE",
            master_label_id="ML-PEND-DONE",
            organization_id=org.id,
            status=OrderStatus.DELIVERED,
        )
    )
    await db_session.flush()

    count = await repo.count_pending_orders(org.id)
    assert count == 1


@pytest.mark.asyncio
async def test_count_distinct_delivery_events_respects_org_scope(db_session: AsyncSession) -> None:
    org = await create_test_org(db_session, reference="REPO-EVT")
    other = await create_test_org(db_session, reference="REPO-EV2")
    repo = OperationsDashboardRepository(db_session)
    today = date.today()
    window = utc_day_window(today)

    for suffix, oid in (("A", org.id), ("B", other.id)):
        order = Order(
            order_id=f"ORD-EVT-{suffix}",
            master_label_id=f"ML-EVT-{suffix}",
            organization_id=oid,
            status=OrderStatus.DELIVERY_IN_PROGRESS,
        )
        db_session.add(order)
        await db_session.flush()
        stop = DeliveryStop(
            order_id=order.id,
            tracking_id=f"TRK-EVT-{suffix}",
            recipient_first_name="A",
            recipient_last_name="B",
            recipient_phone="07111111111",
            recipient_email="a@example.com",
            line_1="1 St",
            city="London",
            postcode="E1 1AA",
            status=DeliveryStopStatus.DELIVERED,
        )
        db_session.add(stop)
        await db_session.flush()
        db_session.add(
            DeliveryStopEvent(
                delivery_stop_id=stop.id,
                from_status=DeliveryStopStatus.OUT_FOR_DELIVERY.value,
                to_status=DeliveryStopStatus.DELIVERED.value,
                created_at=datetime.now(timezone.utc),
            )
        )
    await db_session.flush()

    count = await repo.count_distinct_delivery_stop_events(
        org.id,
        window=window,
        to_statuses=frozenset({DeliveryStopStatus.DELIVERED.value}),
    )
    assert count == 1
