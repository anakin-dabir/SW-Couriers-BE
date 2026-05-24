"""Fixtures for vehicle module tests: admin user, auth headers, idempotency."""

import json
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.user.models import User


@pytest.fixture(autouse=True)
def _bypass_vehicle_document_stepup_auth(request: pytest.FixtureRequest, app) -> None:
    """Skip X-Vehicle-Doc-Access-Token for vehicle API tests; reserve real checks for dedicated doc-access tests."""
    node_path = getattr(request.node, "path", None)
    name = getattr(node_path, "name", None) or ""
    if name in ("test_vehicle_doc_access_api.py",):
        yield
        return

    from app.modules.vehicles.deps import _require_vehicle_doc_access

    async def _ok() -> None:
        return None

    app.dependency_overrides[_require_vehicle_doc_access] = _ok
    yield
    app.dependency_overrides.pop(_require_vehicle_doc_access, None)


def admin_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="ADMIN",
        region_id=user.region_id,
        organization_id=user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


def idem_headers(key: str | None = None) -> dict[str, str]:
    return {"X-Idempotency-Key": key or str(uuid.uuid4())}


@pytest_asyncio.fixture
async def vehicle_admin(user_factory) -> User:
    """Admin user for vehicle API tests (has VEHICLE_MANAGEMENT from role defaults)."""
    return await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)


@pytest.fixture
def vehicle_admin_headers(vehicle_admin: User) -> dict[str, str]:
    """Auth headers for vehicle admin."""
    return admin_headers(vehicle_admin)


def make_vehicle_payload(reg_suffix: str = "") -> dict:
    """Build a valid vehicle creation payload dict."""
    suffix = reg_suffix or uuid.uuid4().hex[:4].upper()
    return {
        "registration_number": f"AB12CDE{suffix}",
        "fleet_custom_name": "Test Van",
        "make": "Ford",
        "model": "Transit",
        "year": 2022,
        "vehicle_type": "INTERNAL",
        "fuel_type": "DIESEL",
        "cargo_volume_m3": 10.0,
        "max_payload_kg": 1000.0,
        "average_mpg": 35.0,
        "current_mileage": 5000,
        "service_interval_miles": 10000,
        "service_interval_months": 12,
        "max_continuous_driving_hours": 4.0,
        "break_duration_minutes": 30,
        "status": "ACTIVE",
    }


def make_mot_document_metadata() -> dict:
    """Valid UploadDocumentRequest-shaped dict for MOT (future expiry)."""
    expiry = (date.today() + timedelta(days=200)).isoformat()
    return {
        "document_type": "MOT",
        "expiry_date": expiry,
        "reference_number": f"MOT-REF-{uuid.uuid4().hex[:8]}",
        "provider": "Test Garage",
    }


@pytest_asyncio.fixture
async def created_vehicle(client: AsyncClient, vehicle_admin: User) -> str:
    """Create one vehicle via API and return its id. Uses unique registration for isolation."""
    headers = {**admin_headers(vehicle_admin), **idem_headers()}
    payload = make_vehicle_payload()
    resp = await client.post(
        "/v1/vehicles",
        data={"vehicle_data": json.dumps(payload)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]
