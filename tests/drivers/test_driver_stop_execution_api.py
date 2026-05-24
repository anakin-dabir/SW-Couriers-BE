"""API tests for consolidated driver stop execution routes."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.depots.models import Depot
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus, ServiceTier
from app.modules.orders.models import DeliveryStop, Order, Package, StopNote, StopNoteImage
from app.modules.organizations.models import Organization
from app.modules.planning.models import Route, RoutePlan, RouteStop, StopPodPhoto
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

DRIVERS = "/v1/drivers"
DRIVER_PROFILE = "/v1/driver-profile/me"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


def _driver_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="DRIVER", client_type="DRIVER")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "DRIVER",
    }


async def _create_driver_and_headers(client: AsyncClient, user_factory) -> tuple[dict[str, str], dict]:
    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    admin_headers = _admin_headers(admin.id)
    email = f"exec-driver-{uuid.uuid4().hex[:8]}@example.com"
    with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=admin_headers,
            data={
                "email": email,
                "first_name": "Exec",
                "last_name": "Driver",
                "phone": "07123456789",
                "state": "England",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "10 Exec Street",
                "city": "London",
                "postcode": "SW1A 1AA",
                "max_stops": "20",
                "okay_with_layover": True,
                "layover_cost_per_night": "85",
                "max_layover_nights": 5,
                "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
            },
            files=[
                ("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf")),
            ],
        )
    assert resp.status_code == 201
    payload = resp.json()["data"]["driver"]
    return _driver_headers(payload["user_id"]), payload


async def _seed_execution_route(
    db_session: AsyncSession,
    *,
    driver_id: str,
    customer_user_id: str,
    blocking_note: bool = False,
    with_wrong_stop_package: bool = False,
    requires_signature: bool = False,
    package_count: int = 2,
    stop_flow_type: str = "DELIVERY",
    package_initial_status: PackageStatus = PackageStatus.OUT_FOR_DELIVERY,
) -> dict:
    suffix = uuid.uuid4().hex[:8].upper()
    org = Organization(
        reference=f"T{suffix}"[:20],
        trading_name=f"Exec Org {suffix}",
        legal_entity_name=f"Exec Org {suffix} Limited",
        companies_house_number=f"CH{suffix[:8]}",
        vat_number=f"GB{suffix[:9]}",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="1 Test Street",
        reg_city="London",
        reg_postcode="EC1A 1BB",
        status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()
    depot = Depot(
        name=f"Exec Depot {suffix}",
        code=f"DP-EX-{suffix}",
        address_line_1="1 Exec Lane",
        city="London",
        postcode="SW1A 1AA",
    )
    db_session.add(depot)
    await db_session.flush()
    vehicle = Vehicle(registration_number=f"EX-{suffix}", depot_id=depot.id)
    db_session.add(vehicle)
    await db_session.flush()
    plan = RoutePlan(service_date=date.today(), depot_id=depot.id, status="READY")
    db_session.add(plan)
    await db_session.flush()

    total_stops = 2 if with_wrong_stop_package else 1
    route = Route(
        plan_id=plan.id,
        driver_id=driver_id,
        vehicle_id=vehicle.id,
        route_code=f"RT-EX-{suffix}",
        route_type="DELIVERY",
        total_stops=total_stops,
        status="ASSIGNED",
    )
    db_session.add(route)
    await db_session.flush()

    order1 = Order(
        order_id=f"SWC-ORD-{suffix}1",
        master_label_id=f"ML-EX1-{suffix}",
        organization_id=org.id,
        customer_id=customer_user_id,
        subtotal=0,
        vat_amount=0,
        total_amount=0,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(order1)
    await db_session.flush()
    dstop1 = DeliveryStop(
        order_id=order1.id,
        tracking_id=f"TRK1-{suffix}",
        recipient_first_name="R1",
        recipient_last_name="",
        recipient_phone="07123456789",
        recipient_email="r1@example.com",
        line_1="Stop One Street",
        city="London",
        postcode="E1 1AA",
        service_tier=ServiceTier.STANDARD,
        signature_required=requires_signature,
        safe_place_allowed=True,
        status=DeliveryStopStatus.OUT_FOR_DELIVERY,
    )
    db_session.add(dstop1)
    await db_session.flush()
    stop1 = RouteStop(
        route_id=route.id,
        delivery_stop_id=dstop1.id,
        sequence=1,
        status="PENDING",
        stop_flow_type=stop_flow_type,
    )
    db_session.add(stop1)

    packages: list[Package] = []
    for _i in range(package_count):
        packages.append(
            Package(
                order_id=order1.id,
                delivery_stop_id=dstop1.id,
                status=package_initial_status,
            )
        )
    db_session.add_all(packages)

    wrong_pkg: Package | None = None
    if with_wrong_stop_package:
        order2 = Order(
            order_id=f"SWC-ORD-{suffix}2",
            master_label_id=f"ML-EX2-{suffix}",
            organization_id=org.id,
            customer_id=customer_user_id,
            subtotal=0,
            vat_amount=0,
            total_amount=0,
            status=OrderStatus.DELIVERY_IN_PROGRESS,
        )
        db_session.add(order2)
        await db_session.flush()
        dstop2 = DeliveryStop(
            order_id=order2.id,
            tracking_id=f"TRK2-{suffix}",
            recipient_first_name="R2",
            recipient_last_name="",
            recipient_phone="07987654321",
            recipient_email="r2@example.com",
            line_1="Stop Two Street",
            city="London",
            postcode="E2 2AA",
            service_tier=ServiceTier.STANDARD,
            signature_required=False,
            safe_place_allowed=True,
            status=DeliveryStopStatus.OUT_FOR_DELIVERY,
        )
        db_session.add(dstop2)
        await db_session.flush()
        stop2 = RouteStop(
            route_id=route.id,
            delivery_stop_id=dstop2.id,
            sequence=2,
            status="PENDING",
            stop_flow_type=stop_flow_type,
        )
        db_session.add(stop2)
        wrong_pkg = Package(
            order_id=order2.id,
            delivery_stop_id=dstop2.id,
            status=PackageStatus.OUT_FOR_DELIVERY,
        )
        db_session.add(wrong_pkg)

    if blocking_note:
        db_session.add(
            StopNote(
                delivery_stop_id=dstop1.id,
                note_type="ADMIN",
                message="Blocking instruction",
                is_blocking=True,
                sort_order=0,
            )
        )

    await db_session.commit()
    for p in packages:
        await db_session.refresh(p)
    if wrong_pkg is not None:
        await db_session.refresh(wrong_pkg)
    await db_session.refresh(route)
    await db_session.refresh(stop1)
    await db_session.refresh(dstop1)

    return {
        "route": route,
        "stop": stop1,
        "dstop": dstop1,
        "order": order1,
        "packages": packages,
        "wrong_pkg": wrong_pkg,
    }


def _notes_url(route_id: str, stop_id: str) -> str:
    return f"{DRIVER_PROFILE}/routes/{route_id}/stops/{stop_id}/notes"


def _exec_base(route_id: str, stop_id: str) -> str:
    return f"{DRIVER_PROFILE}/routes/{route_id}/stops/{stop_id}"


class TestDriverStopExecutionApi:
    @pytest.mark.asyncio
    async def test_get_stop_notes_empty(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(db_session, driver_id=created["id"], customer_user_id=created["user_id"])
        r = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=headers)
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["requires_acknowledgement"] is False
        assert body["acknowledged"] is False
        assert body["items"] == []

    @pytest.mark.asyncio
    async def test_get_stop_notes_includes_package_ids(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        db_session.add(
            StopNote(
                delivery_stop_id=ctx["dstop"].id,
                note_type="PACKAGE_ISSUE_NOTE",
                message="corner crush",
                is_blocking=False,
                sort_order=0,
                package_ids=[pkg.id],
            )
        )
        await db_session.commit()
        r = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["package_ids"] == [pkg.id]

    @pytest.mark.asyncio
    async def test_get_stop_notes_sorts_package_ids_lexicographically(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=2
        )
        p0, p1 = ctx["packages"][0], ctx["packages"][1]
        canonical = sorted([p0.id, p1.id])
        stored = [canonical[1], canonical[0]]
        db_session.add(
            StopNote(
                delivery_stop_id=ctx["dstop"].id,
                note_type="PACKAGE_ISSUE_NOTE",
                message="multi pkg",
                is_blocking=False,
                sort_order=0,
                package_ids=stored,
            )
        )
        await db_session.commit()
        r = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["package_ids"] == canonical

    @pytest.mark.asyncio
    async def test_pickup_stop_master_label_scan_collects_packages(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=2,
            stop_flow_type="PICKUP",
            package_initial_status=PackageStatus.PENDING_PICKUP,
        )
        scan = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/scan",
            headers=headers,
            json={"scan_value": ctx["order"].master_label_id},
        )
        assert scan.status_code == 200
        body = scan.json()["data"]
        assert body["matched_by"] == "MASTER_LABEL"
        assert body["packages_confirmed"] == 2
        for p in ctx["packages"]:
            await db_session.refresh(p)
            assert p.status == PackageStatus.LOADED_FOR_DELIVERY
        prog = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/progress",
            headers=headers,
        )
        assert prog.status_code == 200
        pdata = prog.json()["data"]
        assert pdata["stop_flow_type"] == "PICKUP"
        assert pdata["master_label_id"] == ctx["order"].master_label_id
        assert pdata["scanned_packages"] == 2

        fin = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert fin.status_code == 200
        assert fin.json()["data"]["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_return_stop_accepts_returned_to_sender_label(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        status_resp = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "RETURNED_TO_SENDER"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["data"]["status"] == "RETURNED"

    @pytest.mark.asyncio
    async def test_return_stop_accepts_sender_not_home_label(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        status_resp = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "SENDER_NOT_HOME"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["data"]["status"] == "CUSTOMER_NOT_HOME"

    @pytest.mark.asyncio
    async def test_return_batch_status_all_packages(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=3,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        ids = [p.id for p in ctx["packages"]]
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r = await client.post(
            url,
            headers=headers,
            json={"package_ids": ids, "status": "SENDER_NOT_HOME"},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["updated_count"] == 3
        assert len(data["items"]) == 3
        assert {x["status"] for x in data["items"]} == {"CUSTOMER_NOT_HOME"}
        for p in ctx["packages"]:
            await db_session.refresh(p)
            assert p.status == PackageStatus.CUSTOMER_NOT_HOME

    @pytest.mark.asyncio
    async def test_return_batch_status_rejects_delivery_stop(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=2,
            stop_flow_type="DELIVERY",
        )
        ids = [p.id for p in ctx["packages"]]
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r = await client.post(
            url,
            headers=headers,
            json={"package_ids": ids, "status": "RETURNED_TO_SENDER"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_return_batch_status_rejects_unknown_package_id(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        bad = str(uuid.uuid4())
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r = await client.post(
            url,
            headers=headers,
            json={"package_ids": [ctx["packages"][0].id, bad], "status": "DISPOSED"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_return_batch_status_rejects_foreign_stop_package(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
            with_wrong_stop_package=True,
        )
        assert ctx["wrong_pkg"] is not None
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r = await client.post(
            url,
            headers=headers,
            json={
                "package_ids": [ctx["packages"][0].id, ctx["wrong_pkg"].id],
                "status": "RETURNED_TO_SENDER",
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_return_batch_status_rejects_mixed_terminal_lock(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=2,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        p0, p1 = ctx["packages"][0], ctx["packages"][1]
        base = _exec_base(ctx["route"].id, ctx["stop"].id)
        r1 = await client.patch(
            f"{base}/packages/{p0.id}/status",
            headers=headers,
            json={"status": "RETURNED_TO_SENDER"},
        )
        assert r1.status_code == 200
        r_batch = await client.post(
            f"{base}/packages/batch-status",
            headers=headers,
            json={"package_ids": [p0.id, p1.id], "status": "SENDER_NOT_HOME"},
        )
        assert r_batch.status_code == 422

    @pytest.mark.asyncio
    async def test_return_batch_status_idempotent_same_outcome(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=2,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        ids = [p.id for p in ctx["packages"]]
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r1 = await client.post(url, headers=headers, json={"package_ids": ids, "status": "DISPOSED"})
        assert r1.status_code == 200
        r2 = await client.post(url, headers=headers, json={"package_ids": ids, "status": "DISPOSED"})
        assert r2.status_code == 200
        assert r2.json()["data"]["updated_count"] == 2

    @pytest.mark.asyncio
    async def test_return_batch_status_requires_notes_ack_when_blocking(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
            blocking_note=True,
        )
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/batch-status"
        r = await client.post(
            url,
            headers=headers,
            json={"package_ids": [ctx["packages"][0].id], "status": "RETURNED_TO_SENDER"},
        )
        assert r.status_code == 422
        assert r.json()["message"] == "NOTES_ACK_REQUIRED"

    @pytest.mark.asyncio
    async def test_return_stop_rejects_legacy_returned_string(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "RETURNED"},
        )
        assert r.status_code == 422
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_return_stop_rejects_customer_not_home_string(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "CUSTOMER_NOT_HOME"},
        )
        assert r.status_code == 422
        assert "SENDER_NOT_HOME" in r.json()["message"]

    @pytest.mark.asyncio
    async def test_return_sender_not_home_readiness_skips_pod(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        status_resp = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "SENDER_NOT_HOME"},
        )
        assert status_resp.status_code == 200
        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        assert ready.status_code == 200
        d = ready.json()["data"]
        assert d["return_requires_pod"] is False
        assert d["pod_ok"] is True
        assert d["packages_ok"] is True

        done = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert done.status_code == 200

    @pytest.mark.asyncio
    async def test_return_returned_requires_pod_then_complete(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        status_resp = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "RETURNED_TO_SENDER"},
        )
        assert status_resp.status_code == 200
        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        assert ready.status_code == 200
        d = ready.json()["data"]
        assert d["return_requires_pod"] is True
        assert d["pod_ok"] is False
        assert d["packages_ok"] is True

        bad = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert bad.status_code == 422
        assert bad.json()["message"] == "POD_INCOMPLETE"

        with patch("app.modules.drivers.service.get_images_client") as images_client_factory:
            images_client_factory.return_value = SimpleNamespace(
                upload_image=AsyncMock(return_value=SimpleNamespace(id="cf-ret-pod")),
                generate_signed_url=lambda image_id, expiry_seconds=3600: f"https://img.example/{image_id}",
            )
            up = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
                headers=headers,
                files=[("files", ("ret.jpg", b"\xff\xd8\xff", "image/jpeg"))],
            )
        assert up.status_code == 200

        done = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert done.status_code == 200

    @pytest.mark.asyncio
    async def test_return_disposed_skips_pod_gate(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "DISPOSED"},
            )
        ).status_code == 200
        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        assert ready.json()["data"]["return_requires_pod"] is False
        assert ready.json()["data"]["pod_ok"] is True
        assert (
            await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
                headers=headers,
                json={},
            )
        ).status_code == 200

    @pytest.mark.asyncio
    async def test_return_terminal_outcome_cannot_change(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "SENDER_NOT_HOME"},
            )
        ).status_code == 200
        conflict = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "RETURNED_TO_SENDER"},
        )
        assert conflict.status_code == 422
        assert "already recorded" in conflict.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_return_same_status_patch_idempotent(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        url = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status"
        assert (await client.patch(url, headers=headers, json={"status": "RETURNED_TO_SENDER"})).status_code == 200
        again = await client.patch(url, headers=headers, json={"status": "RETURNED_TO_SENDER"})
        assert again.status_code == 200
        assert again.json()["data"]["status"] == "RETURNED"

    @pytest.mark.asyncio
    async def test_return_mixed_packages_returned_drives_pod_requirement(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=2,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        p0, p1 = ctx["packages"]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{p0.id}/status",
                headers=headers,
                json={"status": "SENDER_NOT_HOME"},
            )
        ).status_code == 200
        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        d = ready.json()["data"]
        assert d["return_requires_pod"] is False
        assert d["packages_ok"] is False
        assert p1.id in d["pending_package_ids"]

        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{p1.id}/status",
                headers=headers,
                json={"status": "RETURNED_TO_SENDER"},
            )
        ).status_code == 200
        ready2 = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        d2 = ready2.json()["data"]
        assert d2["return_requires_pod"] is True
        assert d2["packages_ok"] is True
        assert d2["pod_ok"] is False

    @pytest.mark.asyncio
    async def test_return_scan_after_terminal_rejected(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "SENDER_NOT_HOME"},
            )
        ).status_code == 200
        scan = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/scan",
            headers=headers,
            json={"scan_value": pkg.package_id},
        )
        assert scan.status_code == 422
        assert "finalized" in scan.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_return_stop_rejects_delivery_outcome_status(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "DELIVERED_TO_CUSTOMER"},
        )
        assert r.status_code == 422
        assert "return" in r.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_return_stop_rejects_refused_by_customer(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "REFUSED_BY_CUSTOMER"},
        )
        assert r.status_code == 422
        assert "return" in r.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_pickup_stop_rejects_package_status_patch(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="PICKUP",
            package_initial_status=PackageStatus.PENDING_PICKUP,
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "DELIVERED_TO_CUSTOMER"},
        )
        assert r.status_code == 422
        assert "pickup" in r.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_delivery_stop_rejects_disposed_terminal_status(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "DISPOSED"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_delivery_stop_rejects_return_screen_labels(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        base = f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status"
        r_sn = await client.patch(base, headers=headers, json={"status": "SENDER_NOT_HOME"})
        assert r_sn.status_code == 422
        r_rts = await client.patch(base, headers=headers, json={"status": "RETURNED_TO_SENDER"})
        assert r_rts.status_code == 422

    @pytest.mark.asyncio
    async def test_return_complete_includes_return_requires_pod_in_readiness(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "SENDER_NOT_HOME"},
            )
        ).status_code == 200
        done = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert done.status_code == 200
        nested = done.json()["data"]["readiness"]
        assert nested["return_requires_pod"] is False
        assert nested["stop_flow_type"] == "RETURN"

    @pytest.mark.asyncio
    async def test_return_pending_packages_empty_after_customer_not_home(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "SENDER_NOT_HOME"},
            )
        ).status_code == 200
        pending = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/pending",
            headers=headers,
        )
        assert pending.status_code == 200
        assert pending.json()["data"]["items"] == []

    @pytest.mark.asyncio
    async def test_return_readiness_pod_incomplete_when_six_photos_seeded(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            stop_flow_type="RETURN",
            package_initial_status=PackageStatus.RETURN_IN_TRANSIT,
        )
        pkg = ctx["packages"][0]
        assert (
            await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "RETURNED_TO_SENDER"},
            )
        ).status_code == 200
        dstop_id = ctx["dstop"].id
        for i in range(1, 7):
            db_session.add(
                StopPodPhoto(
                    delivery_stop_id=dstop_id,
                    image_key=f"seed-pod-{i}",
                    sort_order=i,
                )
            )
        await db_session.commit()

        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        assert ready.status_code == 200
        d = ready.json()["data"]
        assert d["return_requires_pod"] is True
        assert d["photo_count"] == 6
        assert d["pod_ok"] is False

        fin = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert fin.status_code == 422
        assert fin.json()["message"] == "POD_INCOMPLETE"

    @pytest.mark.asyncio
    async def test_delivery_stop_rejects_returned_terminal_status(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        status_resp = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "RETURNED_TO_SENDER"},
        )
        assert status_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delivery_detail_includes_package_issue_note_images(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        note = StopNote(
            delivery_stop_id=ctx["dstop"].id,
            note_type="PACKAGE_ISSUE_NOTE",
            message="see photo",
            is_blocking=False,
            sort_order=0,
            package_ids=[pkg.id],
        )
        db_session.add(note)
        await db_session.flush()
        db_session.add(
            StopNoteImage(
                stop_note_id=note.id,
                image_key="cf-image-damage-1",
                sort_order=1,
            )
        )
        await db_session.commit()

        url = f"{DRIVER_PROFILE}/routes/{ctx['route'].id}/stops/{ctx['stop'].id}/delivery-detail"
        with patch("app.modules.drivers.service.generate_image_url", return_value="https://signed.example/dmg"):
            r = await client.get(url, headers=headers)
        assert r.status_code == 200
        issue_notes = r.json()["data"]["package_issue_stop_notes"]
        assert len(issue_notes) == 1
        assert len(issue_notes[0]["images"]) == 1
        assert issue_notes[0]["images"][0]["image_key"] == "cf-image-damage-1"
        assert issue_notes[0]["images"][0]["image_url"] == "https://signed.example/dmg"

    @pytest.mark.asyncio
    async def test_blocking_note_requires_ack_before_scan(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            blocking_note=True,
            package_count=1,
        )
        pkg = ctx["packages"][0]
        scan = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/scan",
            headers=headers,
            json={"scan_value": pkg.package_id},
        )
        assert scan.status_code == 422
        assert scan.json()["message"] == "NOTES_ACK_REQUIRED"

    @pytest.mark.asyncio
    async def test_acknowledge_then_scan_and_finalize_happy_path(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            blocking_note=True,
            package_count=2,
        )
        notes = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=headers)
        assert notes.status_code == 200
        h = notes.json()["data"]["notes_hash"]
        ack = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/notes/acknowledge",
            headers=headers,
            json={"notes_hash": h},
        )
        assert ack.status_code == 200

        for pkg in ctx["packages"]:
            sc = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/scan",
                headers=headers,
                json={"scan_value": pkg.package_id},
            )
            assert sc.status_code == 200
            st = await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "DELIVERED_TO_CUSTOMER"},
            )
            assert st.status_code == 200
            assert st.json()["data"]["status"] == "DELIVERED_TO_CUSTOMER"

        with patch("app.modules.drivers.service.get_images_client") as images_client_factory:
            images_client_factory.return_value = SimpleNamespace(
                upload_image=AsyncMock(return_value=SimpleNamespace(id="cf-pod-photo-1")),
                generate_signed_url=lambda image_id, expiry_seconds=3600: f"https://img.example/{image_id}",
            )
            up = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
                headers=headers,
                files=[("files", ("pod1.jpg", b"\xff\xd8\xff", "image/jpeg"))],
            )
        assert up.status_code == 200
        key = up.json()["data"]["items"][0]["image_id"]
        assert up.json()["data"]["items"][0]["image_url"] == f"https://img.example/{key}"

        done = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert done.status_code == 200
        assert done.json()["data"]["status"] == "COMPLETED"

        logs = (await db_session.execute(select(AuditLog).where(AuditLog.action == "driver.stop.notes.ack"))).scalars().all()
        assert any(log.entity_id == ctx["dstop"].id for log in logs)

    @pytest.mark.asyncio
    async def test_scan_wrong_stop_rejected(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            with_wrong_stop_package=True,
            package_count=1,
        )
        assert ctx["wrong_pkg"] is not None
        scan = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/scan",
            headers=headers,
            json={"scan_value": ctx["wrong_pkg"].package_id},
        )
        assert scan.status_code == 422
        assert "stop" in scan.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_report_finalizes_package(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=2)
        p0, p1 = ctx["packages"]
        rep = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{p0.id}/missing-report",
            headers=headers,
            json={"reason_code": "NOT_IN_MY_VEHICLE", "details": "Lost in van"},
        )
        assert rep.status_code == 200
        assert rep.json()["data"]["status"] == "MISSING"

        patch_p1 = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{p1.id}/status",
            headers=headers,
            json={"status": "DELIVERED_TO_CUSTOMER"},
        )
        assert patch_p1.status_code == 200

        with patch("app.modules.drivers.service.get_images_client") as images_client_factory:
            images_client_factory.return_value = SimpleNamespace(
                upload_image=AsyncMock(return_value=SimpleNamespace(id="cf-pod-photo-2")),
                generate_signed_url=lambda image_id, expiry_seconds=3600: f"https://img.example/{image_id}",
            )
            up = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
                headers=headers,
                files=[("files", ("pod2.jpg", b"\xff\xd8\xff", "image/jpeg"))],
            )
        assert up.status_code == 200
        fin = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert fin.status_code == 200

    @pytest.mark.asyncio
    async def test_complete_requires_pod_and_signature_when_required(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session,
            driver_id=created["id"],
            customer_user_id=created["user_id"],
            package_count=1,
            requires_signature=True,
        )
        pkg = ctx["packages"][0]
        await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "DELIVERED_TO_CUSTOMER"},
        )

        bad = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert bad.status_code == 422
        assert bad.json()["message"] in {"POD_INCOMPLETE", "SIGNATURE_REQUIRED"}

        with patch("app.modules.drivers.service.get_images_client") as images_client_factory:
            images_client_factory.return_value = SimpleNamespace(
                upload_image=AsyncMock(return_value=SimpleNamespace(id="cf-pod-photo-3")),
                generate_signed_url=lambda image_id, expiry_seconds=3600: f"https://img.example/{image_id}",
            )
            up = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
                headers=headers,
                files=[("files", ("pod3.jpg", b"\xff\xd8\xff", "image/jpeg"))],
            )
        assert up.status_code == 200

        bad2 = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert bad2.status_code == 422
        assert bad2.json()["message"] == "SIGNATURE_REQUIRED"

        sig = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/signature",
            headers=headers,
            json={"signature_image_key": "sig/key/1.png", "signature_required": True},
        )
        assert sig.status_code == 200

        ok_resp = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/complete",
            headers=headers,
            json={},
        )
        assert ok_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_pod_photos_for_stop(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        with patch("app.modules.drivers.service.get_images_client") as images_client_factory:
            images_client_factory.return_value = SimpleNamespace(
                upload_image=AsyncMock(return_value=SimpleNamespace(id="cf-pod-photo-4")),
                generate_signed_url=lambda image_id, expiry_seconds=3600: f"https://img.example/{image_id}",
            )
            up = await client.post(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
                headers=headers,
                files=[
                    ("files", ("pod4-a.jpg", b"\xff\xd8\xff", "image/jpeg")),
                    ("files", ("pod4-b.jpg", b"\xff\xd8\xff", "image/jpeg")),
                ],
            )
        assert up.status_code == 200
        key = up.json()["data"]["items"][0]["image_id"]

        res = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos",
            headers=headers,
        )
        assert res.status_code == 200
        body = res.json()["data"]
        assert body["delivery_stop_id"] == ctx["dstop"].id
        assert body["photos_count"] == 2
        assert len(body["items"]) == 2
        ids = {item["image_id"] for item in body["items"]}
        assert key in ids
        assert all(item["image_url"] is not None for item in body["items"])

    @pytest.mark.asyncio
    async def test_upload_rejects_more_than_5_photos_per_request(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        files = [("files", (f"pod-{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(6)]
        res = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
            headers=headers,
            files=files,
        )
        assert res.status_code == 422
        assert "Maximum 5 photos" in res.json()["message"]

    @pytest.mark.asyncio

    async def test_upload_rejects_non_image_file(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        res = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/pod/photos/upload",
            headers=headers,
            files=[("files", ("note.txt", b"hello", "text/plain"))],
        )
        assert res.status_code == 422
        assert "only JPEG and PNG" in res.json()["message"]

    @pytest.mark.asyncio
    async def test_readiness_lists_pending_packages(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=2
        )
        ready = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/readiness",
            headers=headers,
        )
        assert ready.status_code == 200
        d = ready.json()["data"]
        assert d["packages_ok"] is False
        assert len(d["pending_package_ids"]) == 2
        assert d["pod_ok"] is False

    @pytest.mark.asyncio
    async def test_readiness_gate_endpoints_match_aggregate(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=2
        )
        base = _exec_base(ctx["route"].id, ctx["stop"].id)
        full = await client.get(f"{base}/readiness", headers=headers)
        assert full.status_code == 200
        agg = full.json()["data"]

        notes = await client.get(f"{base}/readiness/notes", headers=headers)
        packages = await client.get(f"{base}/readiness/packages", headers=headers)
        pod = await client.get(f"{base}/readiness/pod", headers=headers)
        signature = await client.get(f"{base}/readiness/signature", headers=headers)
        for resp in (notes, packages, pod, signature):
            assert resp.status_code == 200

        assert notes.json()["data"]["ok"] is agg["notes_ok"]
        assert packages.json()["data"]["ok"] is agg["packages_ok"]
        assert pod.json()["data"]["ok"] is agg["pod_ok"]
        assert signature.json()["data"]["ok"] is agg["signature_ok"]
        assert packages.json()["data"]["pending_package_ids"] == agg["pending_package_ids"]
        assert pod.json()["data"]["photo_count"] == agg["photo_count"]

    @pytest.mark.asyncio
    async def test_pending_packages_endpoint_returns_only_unfinalized(
        self, client: AsyncClient, user_factory, db_session: AsyncSession
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=2
        )
        p0, p1 = ctx["packages"]

        finalize = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{p0.id}/status",
            headers=headers,
            json={"status": "DELIVERED_TO_CUSTOMER"},
        )
        assert finalize.status_code == 200

        pending = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/pending",
            headers=headers,
        )
        assert pending.status_code == 200
        data = pending.json()["data"]
        assert data["delivery_stop_id"] == ctx["dstop"].id
        assert len(data["items"]) == 1
        assert data["items"][0]["package_id"] == p1.id
        assert data["items"][0]["reference_number"] == p1.package_id

    @pytest.mark.asyncio
    async def test_packages_progress_summary(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=3
        )
        p0, p1, _p2 = ctx["packages"]
        for pkg in (p0, p1):
            done = await client.patch(
                f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
                headers=headers,
                json={"status": "DELIVERED_TO_CUSTOMER"},
            )
            assert done.status_code == 200
        res = await client.get(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/progress",
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["packages_to_scan"] == 3
        assert data["scanned_packages"] == 2
        assert data["completion_percent"] == 66
        assert data["tracking_id"] == ctx["dstop"].tracking_id

    @pytest.mark.asyncio
    async def test_cross_driver_get_notes_returns_404(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers_a, a = await _create_driver_and_headers(client, user_factory)
        _headers_b, b = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(db_session, driver_id=b["id"], customer_user_id=b["user_id"], package_count=1)
        r = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=headers_a)
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_finalize_status_rejects_invalid_value(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        r = await client.patch(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/status",
            headers=headers,
            json={"status": "DELIVERED"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_report_rejects_invalid_reason_code(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        pkg = ctx["packages"][0]
        r = await client.post(
            f"{_exec_base(ctx['route'].id, ctx['stop'].id)}/packages/{pkg.id}/missing-report",
            headers=headers,
            json={"reason_code": "LOST", "details": "Unknown"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_cannot_call_execution_endpoints(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        _, created = await _create_driver_and_headers(client, user_factory)
        ctx = await _seed_execution_route(
            db_session, driver_id=created["id"], customer_user_id=created["user_id"], package_count=1
        )
        h = _admin_headers(admin.id)
        r = await client.get(_notes_url(ctx["route"].id, ctx["stop"].id), headers=h)
        assert r.status_code == 403


def _note_hash_row(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "n1",
        "note_type": "PACKAGE_ISSUE_NOTE",
        "message": "m",
        "is_blocking": False,
        "sort_order": 0,
        "package_ids": [],
        "images": [],
    }
    base.update(kwargs)
    return base


def test_compute_stop_notes_hash_sort_order_of_package_ids_matters() -> None:
    from app.modules.drivers.service import DriverService

    base = _note_hash_row()
    a = {
        **base,
        "id": "note-a",
        "package_ids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"],
    }
    b = {
        **base,
        "id": "note-a",
        "package_ids": ["bbbbbbbb-cccc-dddd-eeee-ffffffffffff", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
    }
    assert DriverService._compute_stop_notes_hash(notes=[a]) != DriverService._compute_stop_notes_hash(notes=[b])


def test_compute_stop_notes_hash_depends_on_package_ids() -> None:
    from app.modules.drivers.service import DriverService

    h0 = DriverService._compute_stop_notes_hash(notes=[_note_hash_row(package_ids=[])])
    h1 = DriverService._compute_stop_notes_hash(
        notes=[_note_hash_row(package_ids=["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])]
    )
    assert h0 != h1
