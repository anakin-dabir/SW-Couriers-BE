"""API tests for vehicle inspection driver-facing routes."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drivers.models import Driver
from app.modules.vehicle_inspections.enums import InspectionResult, InspectionStatus
from app.modules.vehicle_inspections.models import VehicleInspection
from app.modules.vehicles.enums import DefectSeverity, DefectStatus
from app.modules.vehicles.models import Vehicle, VehicleDefect
from tests.vehicle_inspections.conftest import (
    driver_headers,
    make_checklist,
    make_inspection_payload,
)

BASE = "/v1/vehicle-inspections"

# Minimal valid PNG (1x1 transparent pixel) so python-magic detects image/png
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _sign_files():
    """Signature file tuple for multipart upload."""
    return {"signature": ("sig.png", TINY_PNG, "image/png")}


def _image_files():
    """Defect image file tuple for multipart upload."""
    return {"images": ("tyre.png", TINY_PNG, "image/png")}


# ─── Auth / authorization ───────────────────────────────────────────────────


class TestInspectionAuth:
    """Unauthenticated or non-driver access must be rejected."""

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(BASE, json={})
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_create_requires_driver_role(self, client: AsyncClient, verified_user) -> None:
        from app.core.security import create_access_token

        token, _ = create_access_token(
            user_id=verified_user.id,
            role=verified_user.role,
            client_type="CUSTOMER_B2C",
            region_id=verified_user.region_id,
            organization_id=verified_user.organization_id,
        )
        headers = {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2C"}
        resp = await client.post(BASE, json={}, headers=headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_assigned_vehicle_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/assigned-vehicle")
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/{uuid4()}/status")
        assert resp.status_code in (401, 422)


# ─── Assigned vehicle ───────────────────────────────────────────────────────


class TestGetAssignedVehicle:
    """GET /vehicle-inspections/assigned-vehicle."""

    @pytest.mark.asyncio
    async def test_returns_assigned_vehicle(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        resp = await client.get(f"{BASE}/assigned-vehicle", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == inspection_vehicle.id
        assert data["registration_number"] == inspection_vehicle.registration_number
        assert data["make"] == "Ford"
        assert data["model"] == "Transit"

    @pytest.mark.asyncio
    async def test_no_vehicle_assigned_returns_error(
        self,
        client: AsyncClient,
        other_driver: Driver,
        other_driver_headers: dict,
    ) -> None:
        resp = await client.get(f"{BASE}/assigned-vehicle", headers=other_driver_headers)
        assert resp.status_code == 422


# ─── Lookup vehicle ─────────────────────────────────────────────────────────


class TestLookupVehicle:
    """GET /vehicle-inspections/lookup/{registration_number}."""

    @pytest.mark.asyncio
    async def test_lookup_assigned_vehicle(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        reg = inspection_vehicle.registration_number
        resp = await client.get(f"{BASE}/lookup/{reg}", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == inspection_vehicle.id

    @pytest.mark.asyncio
    async def test_lookup_nonexistent_plate_returns_404(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        resp = await client.get(f"{BASE}/lookup/ZZZZZZ999", headers=driver_auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_lookup_unassigned_vehicle_returns_error(
        self,
        client: AsyncClient,
        inspection_vehicle: Vehicle,
        other_driver: Driver,
        other_driver_headers: dict,
    ) -> None:
        reg = inspection_vehicle.registration_number
        resp = await client.get(f"{BASE}/lookup/{reg}", headers=other_driver_headers)
        assert resp.status_code == 422


# ─── Create inspection ──────────────────────────────────────────────────────


class TestCreateInspection:
    """POST /vehicle-inspections."""

    @pytest.mark.asyncio
    async def test_create_success(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        payload = make_inspection_payload(inspection_vehicle.registration_number)
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["status"] == "IN_PROGRESS"
        assert data["inspection_type"] == "PRE_TRIP"
        assert data["result"] is None
        assert data["declaration_accepted"] is False
        assert data["vehicle"]["id"] == inspection_vehicle.id
        assert data["driver"]["id"] == inspection_driver.id
        assert data["mileage"] == 12345.0
        assert len(data["checklist_status"]) == 3
        assert data["defects"] == []

    @pytest.mark.asyncio
    async def test_create_post_trip(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        payload = make_inspection_payload(
            inspection_vehicle.registration_number,
            inspection_type="POST_TRIP",
        )
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 201
        assert resp.json()["data"]["inspection_type"] == "POST_TRIP"

    @pytest.mark.asyncio
    async def test_create_normalizes_registration(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        lower_reg = f"  {inspection_vehicle.registration_number.lower()}  "
        payload = make_inspection_payload(lower_reg)
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_duplicate_in_progress_rejected(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
        created_inspection: dict,
    ) -> None:
        payload = make_inspection_payload(inspection_vehicle.registration_number)
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_wrong_vehicle_rejected(
        self,
        client: AsyncClient,
        other_driver: Driver,
        inspection_vehicle: Vehicle,
        other_driver_headers: dict,
    ) -> None:
        payload = make_inspection_payload(inspection_vehicle.registration_number)
        resp = await client.post(BASE, json=payload, headers=other_driver_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_nonexistent_vehicle_returns_404(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        payload = make_inspection_payload("DOESNOTEXIST999")
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_missing_checklist_section_rejected(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        payload = make_inspection_payload(inspection_vehicle.registration_number)
        # Remove one section
        payload["checklist"] = [s for s in payload["checklist"] if s["category"] != "LOAD_EQUIPMENT"]
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_empty_checklist_rejected(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        inspection_vehicle: Vehicle,
        driver_auth_headers: dict,
    ) -> None:
        payload = make_inspection_payload(inspection_vehicle.registration_number)
        payload["checklist"] = []
        resp = await client.post(BASE, json=payload, headers=driver_auth_headers)
        assert resp.status_code == 422


# ─── Get inspection ─────────────────────────────────────────────────────────


class TestGetInspection:
    """GET /vehicle-inspections/{inspection_id}."""

    @pytest.mark.asyncio
    async def test_get_returns_inspection(
        self,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        resp = await client.get(f"{BASE}/{iid}", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == iid
        assert data["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        resp = await client.get(f"{BASE}/{uuid4()}", headers=driver_auth_headers)
        assert resp.status_code == 404


# ─── Report defect ──────────────────────────────────────────────────────────


class TestReportDefect:
    """POST /vehicle-inspections/{inspection_id}/defects."""

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/path.png")
    async def test_report_defect_with_images(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        defect_data = {
            "category": "TYRES",
            "severity": "MAJOR",
            "description": "Front left tyre worn below legal limit",
        }
        resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            files=_image_files(),
            headers=driver_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["category"] == "TYRES"
        assert data["severity"] == "MAJOR"
        assert data["status"] == "PENDING"
        assert data["allowed_to_drive"] is False
        assert data["description"] == "Front left tyre worn below legal limit"

    @pytest.mark.asyncio
    async def test_report_defect_without_images(
        self,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        defect_data = {
            "category": "BREAKDOWN",
            "severity": "CRITICAL",
            "description": "Engine warning light on",
        }
        resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["category"] == "BREAKDOWN"
        assert data["severity"] == "CRITICAL"
        assert data["images"] == []

    @pytest.mark.asyncio
    async def test_report_defect_on_finalized_inspection_rejected(
        self,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
        db_session: AsyncSession,
    ) -> None:
        iid = created_inspection["id"]
        # Finalize the inspection directly in DB
        await db_session.execute(
            update(VehicleInspection)
            .where(VehicleInspection.id == iid)
            .values(status=InspectionStatus.COMPLETED, result=InspectionResult.PASS, declaration_accepted=True)
        )
        await db_session.commit()

        defect_data = {"category": "TYRES", "severity": "MINOR"}
        resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_report_defect_other_driver_rejected(
        self,
        client: AsyncClient,
        created_inspection: dict,
        other_driver: Driver,
        other_driver_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        defect_data = {"category": "TYRES", "severity": "MINOR"}
        resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=other_driver_headers,
        )
        assert resp.status_code == 404


# ─── Sign inspection ────────────────────────────────────────────────────────


class TestSignInspection:
    """POST /vehicle-inspections/{inspection_id}/sign."""

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_sign_no_defects_completes(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        sign_data = {"declaration_accepted": True}
        resp = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "COMPLETED"
        assert data["result"] == "PASS"
        assert data["declaration_accepted"] is True

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_sign_with_defects_awaits_resolution(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]

        # Report a defect first
        defect_data = {"category": "TYRES", "severity": "MAJOR", "description": "Worn tyre"}
        await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )

        # Sign
        sign_data = {"declaration_accepted": True}
        resp = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "AWAITING_RESOLUTION"
        assert data["result"] == "FAIL"
        assert len(data["defects"]) == 1

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_sign_already_finalized_rejected(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        sign_data = {"declaration_accepted": True}
        # Sign once
        resp1 = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp1.status_code == 200

        # Try signing again
        resp2 = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp2.status_code == 422

    @pytest.mark.asyncio
    async def test_sign_declaration_not_accepted_rejected(
        self,
        client: AsyncClient,
        created_inspection: dict,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        sign_data = {"declaration_accepted": False}
        resp = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_sign_other_driver_rejected(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        other_driver: Driver,
        other_driver_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        sign_data = {"declaration_accepted": True}
        resp = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=other_driver_headers,
        )
        assert resp.status_code == 404


# ─── Poll status & auto-resolution ──────────────────────────────────────────


class TestInspectionStatus:
    """GET /vehicle-inspections/{inspection_id}/status."""

    @pytest.mark.asyncio
    async def test_status_in_progress_no_defects(
        self,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["inspection_id"] == iid
        assert data["status"] == "IN_PROGRESS"
        assert data["total_defects"] == 0
        assert data["resolved_defects"] == 0
        assert data["allowed_to_drive_count"] == 0
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_status_awaiting_with_pending_defect(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        iid = created_inspection["id"]

        # Report defect + sign
        defect_data = {"category": "TYRES", "severity": "MAJOR"}
        await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )
        sign_data = {"declaration_accepted": True}
        resp_sign = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp_sign.status_code == 200, resp_sign.text

        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "AWAITING_RESOLUTION"
        assert data["total_defects"] == 1
        assert data["resolved_defects"] == 0
        assert data["can_proceed"] is False

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_auto_resolve_when_all_defects_resolved(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
        db_session: AsyncSession,
    ) -> None:
        iid = created_inspection["id"]

        # Report defect + sign
        defect_data = {"category": "TYRES", "severity": "MINOR"}
        defect_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )
        defect_id = defect_resp.json()["data"]["id"]

        sign_data = {"declaration_accepted": True}
        resp_sign = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp_sign.status_code == 200, resp_sign.text

        # Resolve defect directly in DB (admin action)
        await db_session.execute(
            update(VehicleDefect)
            .where(VehicleDefect.id == defect_id)
            .values(status=DefectStatus.RESOLVED)
        )
        await db_session.commit()

        # Poll status — should auto-resolve
        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "RESOLVED"
        assert data["total_defects"] == 1
        assert data["resolved_defects"] == 1
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_auto_resolve_when_all_allowed_to_drive(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
        db_session: AsyncSession,
    ) -> None:
        iid = created_inspection["id"]

        # Report defect + sign
        defect_data = {"category": "ROUTINE_SERVICE", "severity": "MINOR"}
        defect_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps(defect_data)},
            headers=driver_auth_headers,
        )
        defect_id = defect_resp.json()["data"]["id"]

        sign_data = {"declaration_accepted": True}
        resp_sign = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp_sign.status_code == 200, resp_sign.text

        # Mark allowed_to_drive (admin action)
        await db_session.execute(
            update(VehicleDefect)
            .where(VehicleDefect.id == defect_id)
            .values(allowed_to_drive=True)
        )
        await db_session.commit()

        # Poll status — should auto-resolve
        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "RESOLVED"
        assert data["allowed_to_drive_count"] == 1
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_mixed_resolved_and_allowed_auto_resolves(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
        db_session: AsyncSession,
    ) -> None:
        """Two defects: one resolved, one allowed_to_drive. Should auto-resolve."""
        iid = created_inspection["id"]

        # Report two defects
        d1_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps({"category": "TYRES", "severity": "MAJOR"})},
            headers=driver_auth_headers,
        )
        d2_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps({"category": "ROUTINE_SERVICE", "severity": "MINOR"})},
            headers=driver_auth_headers,
        )
        d1_id = d1_resp.json()["data"]["id"]
        d2_id = d2_resp.json()["data"]["id"]

        # Sign
        sign_data = {"declaration_accepted": True}
        resp_sign = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp_sign.status_code == 200, resp_sign.text

        # Resolve first, allow second
        await db_session.execute(
            update(VehicleDefect).where(VehicleDefect.id == d1_id).values(status=DefectStatus.RESOLVED)
        )
        await db_session.execute(
            update(VehicleDefect).where(VehicleDefect.id == d2_id).values(allowed_to_drive=True)
        )
        await db_session.commit()

        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "RESOLVED"
        assert data["resolved_defects"] == 1
        assert data["allowed_to_drive_count"] == 1
        assert data["can_proceed"] is True

    @pytest.mark.asyncio
    @patch("app.modules.vehicle_inspections.service.upload_to_r2", new_callable=AsyncMock, return_value="mock/sig.png")
    async def test_no_double_count_resolved_and_allowed(
        self,
        mock_upload,
        client: AsyncClient,
        created_inspection: dict,
        inspection_driver: Driver,
        driver_auth_headers: dict,
        db_session: AsyncSession,
    ) -> None:
        """A defect that is both resolved AND allowed_to_drive should not be double-counted."""
        iid = created_inspection["id"]

        # Report two defects
        d1_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps({"category": "TYRES", "severity": "MAJOR"})},
            headers=driver_auth_headers,
        )
        d2_resp = await client.post(
            f"{BASE}/{iid}/defects",
            data={"defect_data": json.dumps({"category": "BREAKDOWN", "severity": "CRITICAL"})},
            headers=driver_auth_headers,
        )
        d1_id = d1_resp.json()["data"]["id"]
        d2_id = d2_resp.json()["data"]["id"]

        # Sign
        sign_data = {"declaration_accepted": True}
        resp_sign = await client.post(
            f"{BASE}/{iid}/sign",
            data={"sign_data": json.dumps(sign_data)},
            files=_sign_files(),
            headers=driver_auth_headers,
        )
        assert resp_sign.status_code == 200, resp_sign.text

        # Resolve d1 AND set allowed_to_drive (both flags)
        await db_session.execute(
            update(VehicleDefect)
            .where(VehicleDefect.id == d1_id)
            .values(status=DefectStatus.RESOLVED, allowed_to_drive=True)
        )
        # d2 remains PENDING, not allowed
        await db_session.commit()

        resp = await client.get(f"{BASE}/{iid}/status", headers=driver_auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        # resolved=1 (d1), allowed=0 (d1 is resolved so not counted as allowed)
        assert data["resolved_defects"] == 1
        assert data["allowed_to_drive_count"] == 0
        # total=2, resolved+allowed=1, so can_proceed=False
        assert data["can_proceed"] is False
        assert data["status"] == "AWAITING_RESOLUTION"

    @pytest.mark.asyncio
    async def test_status_other_driver_rejected(
        self,
        client: AsyncClient,
        created_inspection: dict,
        other_driver: Driver,
        other_driver_headers: dict,
    ) -> None:
        iid = created_inspection["id"]
        resp = await client.get(f"{BASE}/{iid}/status", headers=other_driver_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_nonexistent_inspection_returns_404(
        self,
        client: AsyncClient,
        inspection_driver: Driver,
        driver_auth_headers: dict,
    ) -> None:
        resp = await client.get(f"{BASE}/{uuid4()}/status", headers=driver_auth_headers)
        assert resp.status_code == 404
