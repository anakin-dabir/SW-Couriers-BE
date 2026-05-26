"""API tests for vehicle admin routes (GET/POST/PATCH/DELETE and sub-resources)."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import BulkUploadResult
from app.modules.depots.models import Depot
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.planning.models import Route, RouteEvent, RoutePlan
from app.modules.user.models import User
from app.modules.vehicles.enums import ScheduleCalendarFilterKind, ScheduleEntrySource
from app.modules.vehicles.models import Vehicle, VehicleImage, VehicleScheduleEntry
from app.modules.vehicles.utils import add_calendar_months
from tests.vehicle.conftest import admin_headers, idem_headers, make_mot_document_metadata, make_vehicle_payload

# ─── Auth / authorization ───────────────────────────────────────────────────


class TestVehicleAuth:
    """Unauthenticated or non-admin access must be rejected."""

    @pytest.mark.asyncio
    async def test_list_vehicles_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/vehicles")
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_list_vehicles_requires_admin_with_vehicle_management(self, client: AsyncClient, verified_user: User) -> None:
        from app.core.security import create_access_token

        token, _ = create_access_token(
            user_id=verified_user.id,
            role=verified_user.role,
            client_type="CUSTOMER_B2C",
            region_id=verified_user.region_id,
            organization_id=verified_user.organization_id,
        )
        headers = {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2C"}
        resp = await client.get("/v1/vehicles", headers=headers)
        assert resp.status_code == 403


# ─── Fleet stats ─────────────────────────────────────────────────────────────


class TestGetFleetStats:
    """GET /v1/vehicles/stats."""

    @pytest.mark.asyncio
    async def test_returns_ok_with_counts(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get("/v1/vehicles/stats", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_vehicles" in data
        assert "active_vehicles" in data
        assert "in_maintenance" in data
        assert "compliance_alerts" in data


# ─── List / create / get / update / delete vehicle ──────────────────────────


class TestListVehicles:
    """GET /v1/vehicles."""

    @pytest.mark.asyncio
    async def test_returns_paginated_list(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get("/v1/vehicles", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "size" in data
        if data["items"]:
            first = data["items"][0]
            assert "images" in first
            assert "tax" in first
            assert "mot" in first
            assert "live_status" in first
            assert "availability" in first
            assert "defects" in first

    @pytest.mark.asyncio
    async def test_query_params_accepted(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            "/v1/vehicles",
            headers=headers,
            params={"page": 1, "size": 10, "availability": "ACTIVE", "status": "IDLE"},
        )
        assert resp.status_code == 200


class TestCreateVehicle:
    """POST /v1/vehicles (multipart/form-data)."""

    @pytest.mark.asyncio
    async def test_creates_vehicle_201(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = make_vehicle_payload("CREATE1")
        resp = await client.post(
            "/v1/vehicles",
            data={"vehicle_data": json.dumps(payload)},
            headers=headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        data = body["data"]
        assert data["registration_number"] == "AB12CDECREATE1"
        assert data["fleet_number"]
        assert data["id"]
        assert isinstance(data["documents"], list)
        assert isinstance(body.get("failed_documents"), list)
        assert isinstance(body.get("failed_images"), list)

    @pytest.mark.asyncio
    async def test_create_with_invalid_vehicle_data_returns_422(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        resp = await client.post(
            "/v1/vehicles",
            data={"vehicle_data": "not-json"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestGetVehicle:
    """GET /v1/vehicles/{vehicle_id}."""

    @pytest.mark.asyncio
    async def test_returns_vehicle(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(f"/v1/vehicles/{created_vehicle}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == created_vehicle
        assert "images" in data

    @pytest.mark.asyncio
    async def test_nonexistent_returns_404(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = admin_headers(vehicle_admin)
        fake_id = str(uuid4())
        resp = await client.get(f"/v1/vehicles/{fake_id}", headers=headers)
        assert resp.status_code == 404


class TestUpdateVehicleSpecs:
    """PATCH /v1/vehicles/{vehicle_id}/specs."""

    @pytest.mark.asyncio
    async def test_updates_specs(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        get_resp = await client.get(
            f"/v1/vehicles/{created_vehicle}",
            headers=admin_headers(vehicle_admin),
        )
        assert get_resp.status_code == 200
        payload = {
            "make": "Mercedes",
            "model": "Sprinter",
            "fleet_custom_name": "Updated Van",
            "year": 2023,
            "fuel_type": "DIESEL",
            "cargo_volume_m3": 12.0,
            "max_payload_kg": 1200.0,
            "service_interval_miles": 10000,
            "service_interval_months": 12,
            "average_mpg": 32.0,
            "range_miles": 400.0,
            "max_continuous_driving_hours": 4.5,
            "break_duration_minutes": 45,
        }
        resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/specs",
            data={"vehicle_data": json.dumps(payload)},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["make"] == "Mercedes"

    @pytest.mark.asyncio
    async def test_specs_shift_next_service_due_when_month_interval_changes(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        created_vehicle: str,
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        get_resp = await client.get(
            f"/v1/vehicles/{created_vehicle}",
            headers=admin_headers(vehicle_admin),
        )
        assert get_resp.status_code == 200
        initial_next = date.fromisoformat(get_resp.json()["data"]["next_service_due"])
        expected_next = add_calendar_months(add_calendar_months(initial_next, -12), 6)
        payload = {
            "make": "Ford",
            "model": "Transit",
            "fleet_custom_name": "Test Van",
            "year": 2022,
            "fuel_type": "DIESEL",
            "cargo_volume_m3": 10.0,
            "max_payload_kg": 1000.0,
            "service_interval_miles": 10000,
            "service_interval_months": 6,
            "average_mpg": 35.0,
            "max_continuous_driving_hours": 4.0,
            "break_duration_minutes": 30,
        }
        resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/specs",
            data={"vehicle_data": json.dumps(payload)},
            headers=headers,
        )
        assert resp.status_code == 200
        assert date.fromisoformat(resp.json()["data"]["next_service_due"]) == expected_next


class TestUpdateVehicleMileage:
    """PATCH /v1/vehicles/{vehicle_id}/mileage."""

    @pytest.mark.asyncio
    async def test_updates_mileage(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/mileage",
            json={"new_mileage": 15000},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["current_mileage"] == 15000


class TestChangeAvailability:
    """PATCH /v1/vehicles/{vehicle_id}/availability."""

    @pytest.mark.asyncio
    async def test_changes_availability(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        effective = date.today().isoformat()
        resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/availability",
            json={
                "availability": "UNAVAILABLE",
                "effective_from": effective,
                "effective_to": None,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["availability"] == "UNAVAILABLE"


class TestDeleteVehicle:
    """DELETE /v1/vehicles/{vehicle_id} — log snapshot, hard delete row and dependents."""

    @pytest.mark.asyncio
    async def test_deletes_vehicle(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = make_vehicle_payload("DEL99ET")
        payload["current_mileage"] = 0
        cr = await client.post(
            "/v1/vehicles",
            data={"vehicle_data": json.dumps(payload)},
            headers=headers,
        )
        assert cr.status_code == 201
        vid = cr.json()["data"]["id"]
        resp = await client.request(
            "DELETE",
            f"/v1/vehicles/{vid}",
            headers=admin_headers(vehicle_admin),
            json={"reason": "Test removal"},
        )
        assert resp.status_code == 200
        get_resp = await client.get(f"/v1/vehicles/{vid}", headers=admin_headers(vehicle_admin))
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_deleted_includes_entry(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = make_vehicle_payload("DELLOG1")
        payload["current_mileage"] = 0
        cr = await client.post(
            "/v1/vehicles",
            data={"vehicle_data": json.dumps(payload)},
            headers=headers,
        )
        assert cr.status_code == 201
        vid = cr.json()["data"]["id"]
        reg = cr.json()["data"]["registration_number"]
        await client.request(
            "DELETE",
            f"/v1/vehicles/{vid}",
            headers=admin_headers(vehicle_admin),
            json={"reason": "Audit log test"},
        )
        list_resp = await client.get(
            "/v1/vehicles/deleted",
            headers=admin_headers(vehicle_admin),
            params={"page": 1, "size": 20},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        match = next((i for i in items if i["id"] == vid), None)
        assert match is not None
        assert match["registration_number"] == reg
        assert match["deletion_reason"] == "Audit log test"
        assert match["deleted_by"]["email"]
        assert match["deleted_by"]["first_name"]


# ─── Compliance ───────────────────────────────────────────────────────────────


class TestGetCompliance:
    """GET /v1/vehicles/{vehicle_id}/compliance."""

    @pytest.mark.asyncio
    async def test_returns_compliance_summary(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/compliance",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "mot" in data
        assert "tax" in data
        assert "insurance" in data
        assert "service_interval" in data


# ─── Schedule ────────────────────────────────────────────────────────────────


class TestGetSchedule:
    """GET /v1/vehicles/{vehicle_id}/schedule."""

    @pytest.mark.asyncio
    async def test_returns_schedule_for_date_range(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        start = date(2026, 4, 1)
        end = date(2026, 4, 30)
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/schedule",
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "events" in data
        assert "utilization_summary" in data
        assert len(data["events"]) == 30
        summary = data["utilization_summary"]
        assert summary["available_days"] == 30
        assert summary["available_percent"] == 100
        assert summary["maintenance_days"] == 0
        assert summary["maintenance_percent"] == 0
        assert summary["unavailable_days"] == 0
        assert summary["unavailable_percent"] == 0
        assert summary["completed_delivery_days"] == 0
        assert summary["completed_pickup_days"] == 0
        assert summary["out_for_delivery_days"] == 0
        assert summary["out_for_pickup_days"] == 0

    @pytest.mark.asyncio
    async def test_schedule_accepts_calendar_filter_kind(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        start = date(2026, 4, 1)
        end = date(2026, 4, 30)
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/schedule",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "event_types": ScheduleCalendarFilterKind.DELIVERY_ROUTE.value,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["events"] == []

    @pytest.mark.asyncio
    async def test_schedule_rejects_granular_event_type_in_filter(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/schedule",
            params={
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "event_types": "OUT_FOR_DELIVERY",
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_schedule_404_for_unknown_vehicle(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            "/v1/vehicles/00000000-0000-0000-0000-000000000000/schedule",
            params={"start_date": "2026-04-01", "end_date": "2026-04-30"},
            headers=headers,
        )
        assert resp.status_code == 404


# ─── Maintenance ─────────────────────────────────────────────────────────────


class TestLogMaintenance:
    """POST /v1/vehicles/{vehicle_id}/maintenance."""

    @pytest.mark.asyncio
    async def test_creates_maintenance_record(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        created_vehicle: str,
        db_session: AsyncSession,
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = {
            "maintenance_types": ["OIL_CHANGE"],
            "provider_type": "EXTERNAL",
            "date_from": date.today().isoformat(),
            "cost": 150.0,
            "date_to": None,
            "notes": "Test oil change",
            "garage": "Quick Fit Motors",
        }
        resp = await client.post(
            f"/v1/vehicles/{created_vehicle}/maintenance",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["vehicle_id"] == created_vehicle
        assert data["reference"].startswith("MT-")
        assert data["garage"] == "Quick Fit Motors"

        record_id = data["id"]
        res = await db_session.execute(
            select(VehicleScheduleEntry).where(
                VehicleScheduleEntry.vehicle_id == created_vehicle,
                VehicleScheduleEntry.source == ScheduleEntrySource.MAINTENANCE,
                VehicleScheduleEntry.source_id == record_id,
            )
        )
        row = res.scalars().one()
        assert row.details is not None
        assert row.details.get("maintenance_id") == record_id
        assert row.details.get("maintenance_reference") == data["reference"]


class TestGetMaintenanceById:
    """GET /v1/vehicles/{vehicle_id}/maintenance/{record_id}."""

    @pytest.mark.asyncio
    async def test_returns_maintenance_record(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        post_h = {**admin_headers(vehicle_admin), **idem_headers()}
        pr = await client.post(
            f"/v1/vehicles/{created_vehicle}/maintenance",
            json={
                "maintenance_types": ["MOT"],
                "provider_type": "EXTERNAL",
                "date_from": date.today().isoformat(),
                "cost": 50.0,
                "garage": "Test Garage",
            },
            headers=post_h,
        )
        assert pr.status_code == 201, pr.text
        record_id = pr.json()["data"]["id"]
        ref = pr.json()["data"]["reference"]

        headers = admin_headers(vehicle_admin)
        resp = await client.get(f"/v1/vehicles/{created_vehicle}/maintenance/{record_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == record_id
        assert data["reference"] == ref
        assert data["vehicle_id"] == created_vehicle
        assert data["maintenance_types"] == ["MOT"]

    @pytest.mark.asyncio
    async def test_404_when_record_missing(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/maintenance/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert resp.status_code == 404


class TestListMaintenance:
    """GET /v1/vehicles/{vehicle_id}/maintenance."""

    @pytest.mark.asyncio
    async def test_returns_paginated_list(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/maintenance",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]


class TestMaintenanceUpdateDelete:
    """PATCH/DELETE /v1/vehicles/{vehicle_id}/maintenance/{record_id} and schedule rows."""

    @pytest.mark.asyncio
    async def test_patch_replaces_schedule_row_dates(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        created_vehicle: str,
        db_session: AsyncSession,
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        d0 = date.today() - timedelta(days=14)
        d1 = date.today() - timedelta(days=12)
        payload = {
            "maintenance_types": ["OIL_CHANGE"],
            "provider_type": "EXTERNAL",
            "date_from": d0.isoformat(),
            "cost": 100.0,
            "date_to": d1.isoformat(),
            "notes": "Block A",
            "garage": "Garage A",
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/maintenance",
            json=payload,
            headers=headers,
        )
        assert cr.status_code == 201, cr.text
        record_id = cr.json()["data"]["id"]

        res = await db_session.execute(
            select(VehicleScheduleEntry).where(
                VehicleScheduleEntry.vehicle_id == created_vehicle,
                VehicleScheduleEntry.source == ScheduleEntrySource.MAINTENANCE,
                VehicleScheduleEntry.source_id == record_id,
            )
        )
        rows = list(res.scalars().all())
        assert len(rows) == 1
        assert rows[0].date_from == d0
        assert rows[0].date_to == d1
        assert rows[0].details is not None
        assert rows[0].details.get("maintenance_id") == record_id
        assert rows[0].details.get("maintenance_reference") == cr.json()["data"]["reference"]

        d2 = date.today() - timedelta(days=9)
        d3 = date.today() - timedelta(days=7)
        patch_h = {**admin_headers(vehicle_admin), **idem_headers()}
        pr = await client.patch(
            f"/v1/vehicles/{created_vehicle}/maintenance/{record_id}",
            json={"date_from": d2.isoformat(), "date_to": d3.isoformat(), "notes": "Block B"},
            headers=patch_h,
        )
        assert pr.status_code == 200, pr.text
        assert pr.json()["data"]["notes"] == "Block B"

        res2 = await db_session.execute(
            select(VehicleScheduleEntry).where(
                VehicleScheduleEntry.vehicle_id == created_vehicle,
                VehicleScheduleEntry.source == ScheduleEntrySource.MAINTENANCE,
                VehicleScheduleEntry.source_id == record_id,
            )
        )
        rows2 = list(res2.scalars().all())
        assert len(rows2) == 1
        assert rows2[0].date_from == d2
        assert rows2[0].date_to == d3

        del_resp = await client.delete(
            f"/v1/vehicles/{created_vehicle}/maintenance/{record_id}",
            headers={**admin_headers(vehicle_admin), **idem_headers()},
        )
        assert del_resp.status_code == 200, del_resp.text

        res3 = await db_session.execute(
            select(VehicleScheduleEntry).where(
                VehicleScheduleEntry.vehicle_id == created_vehicle,
                VehicleScheduleEntry.source == ScheduleEntrySource.MAINTENANCE,
                VehicleScheduleEntry.source_id == record_id,
            )
        )
        assert list(res3.scalars().all()) == []


class TestMaintenanceCostSummary:
    """GET /v1/vehicles/{vehicle_id}/maintenance/cost-summary."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/maintenance/cost-summary",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["vehicle_id"] == created_vehicle
        assert "total_cost" in data


# ─── Defects ─────────────────────────────────────────────────────────────────


class TestReportDefect:
    """POST /v1/vehicles/{vehicle_id}/defects."""

    @pytest.mark.asyncio
    async def test_reports_defect(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = {
            "reported_at": datetime.now(UTC).isoformat(),
            "category": "TYRES",
            "severity": "MINOR",
            "description": "Test defect",
            "status": "PENDING",
            "allowed_to_drive": False,
        }
        resp = await client.post(
            f"/v1/vehicles/{created_vehicle}/defects",
            data={"defect_data": json.dumps(payload)},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["vehicle_id"] == created_vehicle
        assert data["status"] == "PENDING"
        assert data["reference"].startswith("DF-")


class TestListDefects:
    """GET /v1/vehicles/{vehicle_id}/defects."""

    @pytest.mark.asyncio
    async def test_returns_paginated_list(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/defects",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]

    @pytest.mark.asyncio
    async def test_list_accepts_multi_status_filter(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/defects",
            headers=headers,
            params=[("status", "PENDING"), ("status", "RESOLVED")],
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]

    @pytest.mark.asyncio
    async def test_list_accepts_search_query(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/defects",
            headers=headers,
            params={"search": "ReporterSearchToken"},
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]

    @pytest.mark.asyncio
    async def test_search_matches_reporter_name(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = {
            "reported_at": datetime.now(UTC).isoformat(),
            "category": "TYRES",
            "severity": "MINOR",
            "description": "ReporterSearchToken defect description",
            "status": "PENDING",
            "allowed_to_drive": False,
            "reported_by_id": str(vehicle_admin.id),
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/defects",
            data={"defect_data": json.dumps(payload)},
            headers=headers,
        )
        assert cr.status_code == 201, cr.text
        defect_id = cr.json()["data"]["id"]
        list_headers = admin_headers(vehicle_admin)
        needle = vehicle_admin.first_name[:4].upper()
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/defects",
            headers=list_headers,
            params={"search": needle},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert data["total"] >= 1
        assert any(item["id"] == defect_id for item in data["items"])


class TestListMaintenanceFilters:
    """GET /v1/vehicles/{vehicle_id}/maintenance maintenance_type filter."""

    @pytest.mark.asyncio
    async def test_accepts_multi_maintenance_type_filter(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/maintenance",
            headers=headers,
            params=[("maintenance_type", "OIL_CHANGE"), ("maintenance_type", "MOT")],
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]


class TestUpdateAndDeleteDefect:
    """PATCH/DELETE /v1/vehicles/{vehicle_id}/defects/{defect_id}."""

    @pytest.mark.asyncio
    async def test_updates_defect_status(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        report = {
            "reported_at": datetime.now(UTC).isoformat(),
            "category": "TYRES",
            "severity": "MINOR",
            "description": "Defect to update",
            "status": "PENDING",
            "allowed_to_drive": False,
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/defects",
            data={"defect_data": json.dumps(report)},
            headers=headers,
        )
        assert cr.status_code == 201
        defect_data = cr.json()["data"]
        defect_id = defect_data["id"]
        patch_resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/defects/{defect_id}",
            json={"status": "IN_PROGRESS", "allowed_to_drive": False},
            headers={**admin_headers(vehicle_admin), **idem_headers()},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_updates_defect_description(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        report = {
            "reported_at": datetime.now(UTC).isoformat(),
            "category": "TYRES",
            "severity": "MINOR",
            "description": "Original text",
            "status": "PENDING",
            "allowed_to_drive": False,
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/defects",
            data={"defect_data": json.dumps(report)},
            headers=headers,
        )
        assert cr.status_code == 201
        defect_id = cr.json()["data"]["id"]
        patch_resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/defects/{defect_id}",
            json={"description": "Updated description", "category": "PART_REPLACEMENT"},
            headers={**admin_headers(vehicle_admin), **idem_headers()},
        )
        assert patch_resp.status_code == 200
        body = patch_resp.json()["data"]
        assert body["description"] == "Updated description"
        assert body["category"] == "PART_REPLACEMENT"

    @pytest.mark.asyncio
    async def test_deletes_defect(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        report = {
            "reported_at": datetime.now(UTC).isoformat(),
            "category": "TYRES",
            "severity": "MINOR",
            "description": "To delete",
            "status": "PENDING",
            "allowed_to_drive": False,
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/defects",
            data={"defect_data": json.dumps(report)},
            headers=headers,
        )
        assert cr.status_code == 201
        defect_id = cr.json()["data"]["id"]
        with patch("app.modules.vehicles.service.delete_image", new_callable=AsyncMock, return_value=None):
            del_resp = await client.delete(
                f"/v1/vehicles/{created_vehicle}/defects/{defect_id}",
                headers={**admin_headers(vehicle_admin), **idem_headers()},
            )
        assert del_resp.status_code == 200
        list_resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/defects",
            headers=admin_headers(vehicle_admin),
        )
        assert list_resp.status_code == 200
        ids = {item["id"] for item in list_resp.json()["data"]["items"]}
        assert defect_id not in ids


# ─── Service records ────────────────────────────────────────────────────────


class TestAddServiceRecord:
    """POST /v1/vehicles/{vehicle_id}/services."""

    @pytest.mark.asyncio
    async def test_adds_service_record(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        today = date.today()
        next_due = date(today.year + 1, today.month, today.day)
        payload = {
            "service_date": today.isoformat(),
            "service_type": "FULL_SERVICE",
            "next_service_due": next_due.isoformat(),
            "mileage_at_service": 5000,
            "cost": 300.0,
            "status": "COMPLETED",
            "notes": None,
        }
        resp = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["vehicle_id"] == created_vehicle

    @pytest.mark.asyncio
    async def test_adds_service_record_computes_next_and_default_mileage(
        self, client: AsyncClient, vehicle_admin: User, created_vehicle: str
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        today = date.today()
        payload = {
            "service_date": today.isoformat(),
            "service_type": "FULL_SERVICE",
            "cost": 150.0,
        }
        resp = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["next_service_due"] == add_calendar_months(today, 12).isoformat()
        assert data["mileage_at_service"] == 5000


class TestListServiceRecords:
    """GET /v1/vehicles/{vehicle_id}/services."""

    @pytest.mark.asyncio
    async def test_returns_paginated_list(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/services",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]


class TestUpdateServiceRecord:
    """PATCH /v1/vehicles/{vehicle_id}/services/{record_id}."""

    @pytest.mark.asyncio
    async def test_updates_service_record(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        today = date.today()
        next_due = date(today.year + 1, today.month, today.day)
        add_payload = {
            "service_date": today.isoformat(),
            "service_type": "FULL_SERVICE",
            "next_service_due": next_due.isoformat(),
            "mileage_at_service": 5000,
            "cost": 200.0,
            "status": "COMPLETED",
            "notes": "Before",
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json=add_payload,
            headers=headers,
        )
        assert cr.status_code == 201
        record_id = cr.json()["data"]["id"]
        patch_resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/services/{record_id}",
            json={"cost": 450.0, "notes": "After edit"},
            headers=admin_headers(vehicle_admin),
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()["data"]
        assert data["cost"] == 450.0
        assert data["notes"] == "After edit"

    @pytest.mark.asyncio
    async def test_patch_service_date_recomputes_next_service_due(
        self, client: AsyncClient, vehicle_admin: User, created_vehicle: str
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        old_service = date.today() - timedelta(days=60)
        add_payload = {
            "service_date": old_service.isoformat(),
            "service_type": "FULL_SERVICE",
            "cost": 200.0,
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json=add_payload,
            headers=headers,
        )
        assert cr.status_code == 201, cr.text
        record_id = cr.json()["data"]["id"]
        assert cr.json()["data"]["next_service_due"] == add_calendar_months(old_service, 12).isoformat()

        new_service = date.today() - timedelta(days=30)
        patch_resp = await client.patch(
            f"/v1/vehicles/{created_vehicle}/services/{record_id}",
            json={"service_date": new_service.isoformat()},
            headers=admin_headers(vehicle_admin),
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["data"]["next_service_due"] == add_calendar_months(new_service, 12).isoformat()


class TestDeleteServiceRecord:
    """DELETE /v1/vehicles/{vehicle_id}/services/{record_id}."""

    @pytest.mark.asyncio
    async def test_deletes_service_record(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        today = date.today()
        next_due = date(today.year + 1, today.month, today.day)
        add_payload = {
            "service_date": today.isoformat(),
            "service_type": "INTERIM_SERVICE",
            "next_service_due": next_due.isoformat(),
            "mileage_at_service": 5000,
            "cost": 100.0,
            "status": "COMPLETED",
            "notes": None,
        }
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json=add_payload,
            headers=headers,
        )
        assert cr.status_code == 201
        record_id = cr.json()["data"]["id"]
        resp = await client.delete(
            f"/v1/vehicles/{created_vehicle}/services/{record_id}",
            headers=admin_headers(vehicle_admin),
        )
        assert resp.status_code == 200


class TestNextServiceDueAfterDeletingAllServices:
    @pytest.mark.asyncio
    async def test_recomputes_next_service_due_from_vehicle_interval(
        self, client: AsyncClient, vehicle_admin: User, created_vehicle: str
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        today = date.today()
        cr = await client.post(
            f"/v1/vehicles/{created_vehicle}/services",
            json={
                "service_date": today.isoformat(),
                "service_type": "FULL_SERVICE",
                "cost": 120.0,
            },
            headers=headers,
        )
        assert cr.status_code == 201, cr.text
        record_id = cr.json()["data"]["id"]
        assert cr.json()["data"]["next_service_due"] == add_calendar_months(today, 12).isoformat()

        dl = await client.delete(
            f"/v1/vehicles/{created_vehicle}/services/{record_id}",
            headers={**admin_headers(vehicle_admin), **idem_headers()},
        )
        assert dl.status_code == 200, dl.text

        detail = await client.get(
            f"/v1/vehicles/{created_vehicle}",
            headers=admin_headers(vehicle_admin),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["next_service_due"] == add_calendar_months(date.today(), 12).isoformat()


# ─── Documents ───────────────────────────────────────────────────────────────


class TestListDocuments:
    """GET /v1/vehicles/{vehicle_id}/documents."""

    @pytest.mark.asyncio
    async def test_returns_list(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/documents",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)


# ─── Images ──────────────────────────────────────────────────────────────────


class TestListImages:
    """GET /v1/vehicles/{vehicle_id}/images."""

    @pytest.mark.asyncio
    async def test_returns_list(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = admin_headers(vehicle_admin)
        resp = await client.get(
            f"/v1/vehicles/{created_vehicle}/images",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)


class TestCreateVehicleWithDocuments:
    """POST /v1/vehicles with documents (R2 bulk upload mocked)."""

    @pytest.mark.asyncio
    async def test_create_with_document_upload_mocked(self, client: AsyncClient, vehicle_admin: User) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        payload = make_vehicle_payload("R2DOC1")
        meta = [make_mot_document_metadata()]
        pdf = b"%PDF-1.4 test content"
        with patch("app.modules.vehicles.service.bulk_upload_to_r2", new_callable=AsyncMock) as m_bulk:
            m_bulk.return_value = BulkUploadResult(succeeded=[(0, "vehicles/mock/documents/fake.pdf")], failed=[])
            resp = await client.post(
                "/v1/vehicles",
                data={
                    "vehicle_data": json.dumps(payload),
                    "documents_metadata": json.dumps(meta),
                },
                files=[("documents", ("mot.pdf", pdf, "application/pdf"))],
                headers=headers,
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["documents"]
        assert isinstance(body.get("failed_documents"), list)
        m_bulk.assert_awaited()


class TestAddAndDeleteDocument:
    """POST/DELETE /v1/vehicles/{vehicle_id}/documents (storage mocked)."""

    @pytest.mark.asyncio
    async def test_add_then_delete_document(self, client: AsyncClient, vehicle_admin: User, created_vehicle: str) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        pdf = b"%PDF-1.4 mot"
        meta = make_mot_document_metadata()
        with patch("app.modules.vehicles.service.upload_to_r2", new_callable=AsyncMock, return_value=None):
            post_resp = await client.post(
                f"/v1/vehicles/{created_vehicle}/documents",
                data={"metadata": json.dumps(meta)},
                files=[("file", ("mot.pdf", pdf, "application/pdf"))],
                headers=headers,
            )
        assert post_resp.status_code == 201, post_resp.text
        doc_id = post_resp.json()["data"]["id"]

        with patch("app.modules.vehicles.service.delete_from_r2", new_callable=AsyncMock, return_value=None):
            del_resp = await client.delete(
                f"/v1/vehicles/{created_vehicle}/documents/{doc_id}",
                headers=admin_headers(vehicle_admin),
            )
        assert del_resp.status_code == 200


class TestUploadAndDeleteVehicleImage:
    """POST/DELETE /v1/vehicles/{vehicle_id}/images (Cloudflare bulk upload mocked)."""

    @pytest.mark.asyncio
    async def test_upload_then_delete_image(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        created_vehicle: str,
        db_session: AsyncSession,
    ) -> None:
        headers = {**admin_headers(vehicle_admin), **idem_headers()}
        png = b"\x89PNG\r\n\x1a\n"

        async def _fake_bulk(_items, raise_if_all_failed=False):
            return BulkUploadResult(succeeded=[(0, SimpleNamespace(id="cf-mock-image-id"))], failed=[])

        with (
            patch("app.storage.upload.magic.from_buffer", return_value="image/png"),
            patch("app.modules.vehicles.service.bulk_upload_images", side_effect=_fake_bulk),
            patch("app.modules.vehicles.service.generate_image_url", return_value="https://example.test/signed"),
        ):
            post_resp = await client.post(
                f"/v1/vehicles/{created_vehicle}/images",
                files=[("images", ("x.png", png, "image/png"))],
                headers=headers,
            )
        assert post_resp.status_code == 200, post_resp.text
        listed = await client.get(f"/v1/vehicles/{created_vehicle}/images", headers=admin_headers(vehicle_admin))
        assert listed.status_code == 200
        assert listed.json()["data"]

        detail = await client.get(f"/v1/vehicles/{created_vehicle}", headers=admin_headers(vehicle_admin))
        assert detail.status_code == 200
        assert detail.json()["data"].get("images")

        res = await db_session.execute(select(VehicleImage).where(VehicleImage.vehicle_id == created_vehicle))
        row = res.scalars().first()
        assert row is not None
        image_id = row.id

        with patch("app.modules.vehicles.service.delete_image", new_callable=AsyncMock, return_value=None):
            del_resp = await client.delete(
                f"/v1/vehicles/{created_vehicle}/images/{image_id}",
                headers=admin_headers(vehicle_admin),
            )
        assert del_resp.status_code == 200


class TestVehicleRouteHistory:
    """GET route-history, route summary, and telematics scoped by vehicle (mirrors driver admin APIs)."""

    @pytest.mark.asyncio
    async def test_route_history_summary_telematics_and_scoping(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        headers = admin_headers(vehicle_admin)
        drv_user = await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")
        driver = Driver(
            user_id=drv_user.id,
            account_status=DriverAccountStatus.ACTIVE,
        )
        db_session.add(driver)
        await db_session.flush()

        seed_suffix = uuid4().hex[:8].upper()
        depot_code = f"DP-VH-{seed_suffix}"
        depot = Depot(
            name="Vehicle History Depot",
            code=depot_code,
            address_line_1="1 Demo Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()

        vehicle_reg = f"VH-{seed_suffix}"
        vehicle_other_reg = f"VH2-{seed_suffix}"
        vehicle = Vehicle(registration_number=vehicle_reg, depot_id=depot.id)
        vehicle_other = Vehicle(registration_number=vehicle_other_reg, depot_id=depot.id)
        db_session.add_all([vehicle, vehicle_other])
        await db_session.flush()

        plan = RoutePlan(service_date=date.today(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()

        route = Route(
            plan_id=plan.id,
            driver_id=driver.id,
            vehicle_id=vehicle.id,
            route_code=f"RT-VH-{seed_suffix}",
            route_type="DELIVERY",
            total_stops=10,
            total_duration_min=88.0,
            estimated_drive_time_min=90.0,
            actual_drive_time_min=88.0,
            total_distance_km=75.64,
            status="COMPLETED",
        )
        db_session.add(route)
        await db_session.flush()

        db_session.add_all(
            [
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver.id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=20),
                    lat=51.5074,
                    lng=-0.1278,
                    event_metadata={
                        "speed_mph": 38,
                        "limit_mph": 30,
                        "speed_over_mph": 8,
                        "route_code": route.route_code,
                        "location_text": "Rosewood Drive, Marlow, UK",
                        "distance_miles": 1.2,
                    },
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver.id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=10),
                    lat=51.5075,
                    lng=-0.1279,
                    event_metadata={"speed_mph": 36, "limit_mph": 30},
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver.id,
                    event_type="HARSH_BRAKING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=5),
                    lat=51.5076,
                    lng=-0.1280,
                    event_metadata={"severity": "HIGH", "start_speed_mph": 32, "end_speed_mph": 7},
                ),
            ]
        )
        await db_session.flush()

        hist_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/route-history",
            headers=headers,
            params={"type": "DELIVERY"},
        )
        assert hist_resp.status_code == 200
        table = hist_resp.json()["data"]["table"]
        assert table["total"] >= 1
        seeded = next((row for row in table["items"] if row["route_id"] == route.id), None)
        assert seeded is not None
        assert seeded["driver_name"] is not None
        assert seeded["type"] == "DELIVERY"
        assert seeded["estimated_miles"] == 47.0

        pickup_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/route-history",
            headers=headers,
            params={"type": "PICKUP"},
        )
        assert pickup_resp.status_code == 200
        assert all(row["route_id"] != route.id for row in pickup_resp.json()["data"]["table"]["items"])

        summary_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{route.id}/summary",
            headers=headers,
        )
        assert summary_resp.status_code == 200
        summary = summary_resp.json()["data"]
        assert summary["route_id"] == route.id
        assert summary["vehicle_reg"] == vehicle_reg
        assert summary["route_type"] == "DELIVERY"
        assert summary["date"] == date.today().isoformat()
        assert summary["stops"] == 10
        assert summary["estimated_drive_time_minutes"] == 90.0
        assert summary["actual_drive_time_minutes"] == 88.0
        assert "progress" in summary
        assert summary["telemetry"]["speeding_events"] == 2
        assert summary["telemetry"]["harsh_braking_events"] == 1

        stops_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{route.id}/stops",
            headers=headers,
        )
        assert stops_resp.status_code == 200
        assert stops_resp.json()["data"]["table"]["total"] == 0

        speed_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{route.id}/telematics/speeding",
            headers=headers,
        )
        assert speed_resp.status_code == 200
        speed_items = speed_resp.json()["data"]["items"]
        assert len(speed_items) == 2
        assert all(item["event_type"] == "SPEEDING" for item in speed_items)

        brake_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{route.id}/telematics/harsh-braking",
            headers=headers,
        )
        assert brake_resp.status_code == 200
        brake_items = brake_resp.json()["data"]["items"]
        assert len(brake_items) == 1
        assert brake_items[0]["event_type"] == "HARSH_BRAKING"

        notes_resp = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{route.id}/notes",
            headers=headers,
        )
        assert notes_resp.status_code == 200
        assert notes_resp.json()["data"]["route_id"] == route.id
        assert notes_resp.json()["data"]["stops"] == []

        wrong_veh = await client.get(
            f"/v1/vehicles/{vehicle_other.id}/routes/{route.id}/summary",
            headers=headers,
        )
        assert wrong_veh.status_code == 404

        wrong_route = await client.get(
            f"/v1/vehicles/{vehicle.id}/routes/{uuid4()}/summary",
            headers=headers,
        )
        assert wrong_route.status_code == 404

        wrong_telem = await client.get(
            f"/v1/vehicles/{vehicle_other.id}/routes/{route.id}/telematics/speeding",
            headers=headers,
        )
        assert wrong_telem.status_code == 404

    @pytest.mark.asyncio
    async def test_route_history_empty_for_vehicle_without_routes(
        self,
        client: AsyncClient,
        vehicle_admin: User,
        created_vehicle: str,
    ) -> None:
        resp = await client.get(f"/v1/vehicles/{created_vehicle}/route-history", headers=admin_headers(vehicle_admin))
        assert resp.status_code == 200
        table = resp.json()["data"]["table"]
        assert table["items"] == []
        assert table["total"] == 0
