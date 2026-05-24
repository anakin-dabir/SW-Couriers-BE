"""Runtime evaluator and transition-hook tests for status automation rules."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.common.enums.jobs import Job
from app.common.exceptions import ValidationError
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package
from app.modules.orders.repository import PackageExecutionRepository
from app.modules.orders.service import OrderService
from app.modules.status_automation_rules.models import StatusAutomationExecutionLog
from app.modules.status_automation_rules.service import StatusAutomationRulesService


async def _seed_order_stop_packages(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    suffix = uuid.uuid4().hex[:8].upper()
    org = await org_factory()
    user = await user_factory(status="ACTIVE", email_verified=True, role="CUSTOMER_B2B", organization_id=org.id)
    order = Order(
        order_id=f"SWC-ORD-SA-{suffix}",
        master_label_id=f"ML-SA-{suffix}",
        organization_id=org.id,
        customer_id=user.id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(order)
    await db_session.flush()
    stop = DeliveryStop(
        order_id=order.id,
        tracking_id=f"TRK-SA-{suffix}",
        recipient_first_name="A",
        recipient_last_name="B",
        recipient_phone="07123456789",
        recipient_email="a@example.com",
        line_1="1 St",
        city="London",
        postcode="E1 1AA",
        status=DeliveryStopStatus.OUT_FOR_DELIVERY,
    )
    db_session.add(stop)
    await db_session.flush()
    pkg1 = Package(order_id=order.id, delivery_stop_id=stop.id, status=PackageStatus.OUT_FOR_DELIVERY)
    pkg2 = Package(order_id=order.id, delivery_stop_id=stop.id, status=PackageStatus.OUT_FOR_DELIVERY)
    db_session.add_all([pkg1, pkg2])
    await db_session.flush()
    return org, order, stop, pkg1, pkg2


@pytest.mark.asyncio
async def test_package_repository_enqueues_status_automation(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    _, _, _, pkg1, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    repo = PackageExecutionRepository(db_session)
    with patch("app.modules.orders.repository.enqueue", new_callable=AsyncMock) as mock_enqueue:
        await repo.update_package_status(package=pkg1, status=PackageStatus.DAMAGED, actor_user_id=None)
        mock_enqueue.assert_awaited_once()
        args = mock_enqueue.await_args.args
        assert args[0] == Job.EVALUATE_STATUS_AUTOMATION_RULES
        payload = args[1]
        assert payload["entity_type"] == "PACKAGE"
        assert payload["entity_id"] == pkg1.id
        assert payload["to_status"] == "DAMAGED"


@pytest.mark.asyncio
async def test_delivery_stop_status_event_enqueues_status_automation(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    _, _, stop, _, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    service = OrderService(db_session, request=None)
    with patch("app.modules.orders.service.enqueue", new_callable=AsyncMock) as mock_enqueue:
        await service._append_delivery_stop_status_event(
            delivery_stop_id=stop.id,
            from_status=DeliveryStopStatus.OUT_FOR_DELIVERY,
            to_status=DeliveryStopStatus.FAILED,
            actor_user_id=None,
        )
        mock_enqueue.assert_awaited_once()
        payload = mock_enqueue.await_args.args[1]
        assert payload["entity_type"] == "DELIVERY_STOP"
        assert payload["entity_id"] == stop.id
        assert payload["to_status"] == "FAILED"


@pytest.mark.asyncio
async def test_order_status_event_enqueues_status_automation(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    _, order, _, _, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    service = OrderService(db_session, request=None)
    with patch("app.modules.orders.service.enqueue", new_callable=AsyncMock) as mock_enqueue:
        await service._append_order_status_event(
            order_id=order.id,
            from_status=OrderStatus.PENDING_PICKUP,
            to_status=OrderStatus.FAILED,
            actor_user_id=None,
        )
        mock_enqueue.assert_awaited_once()
        payload = mock_enqueue.await_args.args[1]
        assert payload["entity_type"] == "BOOKING_ORDER"
        assert payload["entity_id"] == order.id
        assert payload["to_status"] == "FAILED"


@pytest.mark.asyncio
async def test_runtime_evaluator_first_match_only_and_dedupes(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    org, order, stop, pkg1, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    service = StatusAutomationRulesService(db_session, request=None)

    await service.create_rule_set(
        payload={
            "name": f"runtime-high-{uuid.uuid4().hex[:8]}",
            "scope_type": "GLOBAL",
            "scope_org_id": None,
            "status": "ACTIVE",
            "priority": 300,
            "notes": None,
        },
        trigger={"entity_type": "PACKAGE", "status_value": "DAMAGED"},
        conditions=[],
        actions=[{"new_status": "RETURN_INITIATED"}],
    )
    await service.create_rule_set(
        payload={
            "name": f"runtime-low-{uuid.uuid4().hex[:8]}",
            "scope_type": "GLOBAL",
            "scope_org_id": None,
            "status": "ACTIVE",
            "priority": 100,
            "notes": None,
        },
        trigger={"entity_type": "PACKAGE", "status_value": "DAMAGED"},
        conditions=[],
        actions=[{"new_status": "DELIVERED_TO_CUSTOMER"}],
    )

    event = {
        "event_id": f"evt-{uuid.uuid4().hex}",
        "organization_id": org.id,
        "entity_type": "PACKAGE",
        "entity_id": pkg1.id,
        "order_id": order.id,
        "delivery_stop_id": stop.id,
        "from_status": "OUT_FOR_DELIVERY",
        "to_status": "DAMAGED",
        "actor_user_id": None,
    }
    with (
        patch.object(service, "_acquire_entity_lock", new_callable=AsyncMock, return_value=(True, "lock-key")),
        patch.object(service, "_release_entity_lock", new_callable=AsyncMock),
    ):
        result1 = await service.evaluate_for_event(event, commit=True)
    assert result1["matched"] == 1

    refreshed_pkg1 = await db_session.get(Package, pkg1.id)
    assert refreshed_pkg1 is not None and refreshed_pkg1.status == PackageStatus.RETURN_INITIATED

    with (
        patch.object(service, "_acquire_entity_lock", new_callable=AsyncMock, return_value=(True, "lock-key")),
        patch.object(service, "_release_entity_lock", new_callable=AsyncMock),
    ):
        result2 = await service.evaluate_for_event(event, commit=True)
    assert result2["matched"] == 0
    count = (
        await db_session.execute(
            select(func.count()).select_from(StatusAutomationExecutionLog).where(StatusAutomationExecutionLog.event_id == event["event_id"])
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_runtime_delivery_stop_cancelled_after_pickup_changes_stop(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    org, order, stop, _, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    order.status = OrderStatus.DELIVERY_IN_PROGRESS
    await db_session.flush()
    service = StatusAutomationRulesService(db_session, request=None)
    await service.create_rule_set(
        payload={
            "name": f"stop-cancel-{uuid.uuid4().hex[:8]}",
            "scope_type": "GLOBAL",
            "scope_org_id": None,
            "status": "ACTIVE",
            "priority": 200,
            "notes": None,
        },
        trigger={"entity_type": "DELIVERY_STOP", "status_value": "CANCELLED"},
        conditions=[{"value": "AFTER_PICKUP"}],
        actions=[{"new_status": "RETURN_INITIATED"}],
    )

    with (
        patch.object(service, "_acquire_entity_lock", new_callable=AsyncMock, return_value=(True, "lock-key")),
        patch.object(service, "_release_entity_lock", new_callable=AsyncMock),
    ):
        out = await service.evaluate_for_event(
            {
                "event_id": f"evt-{uuid.uuid4().hex}",
                "organization_id": org.id,
                "entity_type": "DELIVERY_STOP",
                "entity_id": stop.id,
                "order_id": order.id,
                "delivery_stop_id": stop.id,
                "from_status": "OUT_FOR_DELIVERY",
                "to_status": "CANCELLED",
            },
            commit=True,
        )
    assert out["matched"] == 1
    refreshed_stop = await db_session.get(DeliveryStop, stop.id)
    assert refreshed_stop is not None and refreshed_stop.status == DeliveryStopStatus.RETURN_INITIATED


@pytest.mark.asyncio
async def test_runtime_shadow_mode_does_not_execute_actions(db_session, org_factory, user_factory):  # type: ignore[no-untyped-def]
    org, order, stop, pkg1, _ = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    service = StatusAutomationRulesService(db_session, request=None)
    await service.create_rule_set(
        payload={
            "name": f"shadow-{uuid.uuid4().hex[:8]}",
            "scope_type": "GLOBAL",
            "scope_org_id": None,
            "status": "ACTIVE",
            "priority": 200,
            "notes": None,
        },
        trigger={"entity_type": "PACKAGE", "status_value": "DAMAGED"},
        conditions=[],
        actions=[{"new_status": "RETURN_INITIATED"}],
    )
    from app.core.config import settings

    old_shadow = settings.STATUS_AUTOMATION_SHADOW_MODE
    settings.STATUS_AUTOMATION_SHADOW_MODE = True
    try:
        with (
            patch.object(service, "_acquire_entity_lock", new_callable=AsyncMock, return_value=(True, "lock-key")),
            patch.object(service, "_release_entity_lock", new_callable=AsyncMock),
        ):
            out = await service.evaluate_for_event(
                {
                    "event_id": f"evt-{uuid.uuid4().hex}",
                    "organization_id": org.id,
                    "entity_type": "PACKAGE",
                    "entity_id": pkg1.id,
                    "order_id": order.id,
                    "delivery_stop_id": stop.id,
                    "from_status": "OUT_FOR_DELIVERY",
                    "to_status": "DAMAGED",
                },
                commit=True,
            )
    finally:
        settings.STATUS_AUTOMATION_SHADOW_MODE = old_shadow
    assert out["matched"] == 1
    refreshed_pkg1 = await db_session.get(Package, pkg1.id)
    assert refreshed_pkg1 is not None and refreshed_pkg1.status == PackageStatus.OUT_FOR_DELIVERY


@pytest.mark.asyncio
async def test_runtime_evaluator_rejects_missing_payload_fields(db_session) -> None:  # type: ignore[no-untyped-def]
    service = StatusAutomationRulesService(db_session, request=None)
    with pytest.raises(ValidationError):
        await service.evaluate_for_event({"organization_id": "x"}, commit=False)
