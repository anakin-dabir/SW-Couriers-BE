"""Fixtures for vehicle inspection tests."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle


def driver_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="DRIVER",
        region_id=user.region_id,
        organization_id=user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "DRIVER",
    }


def make_checklist() -> list[dict]:
    """Build a valid 3-section checklist payload."""
    return [
        {
            "category": "INSIDE_CABIN",
            "items": [
                {"item": "Seatbelt functional", "checked": True},
                {"item": "Dashboard lights OK", "checked": True},
            ],
        },
        {
            "category": "OUTSIDE_VEHICLE",
            "items": [
                {"item": "Tyres inflated", "checked": True},
                {"item": "No visible damage", "checked": True},
            ],
        },
        {
            "category": "LOAD_EQUIPMENT",
            "items": [
                {"item": "Cargo straps present", "checked": True},
                {"item": "Load area clean", "checked": True},
            ],
        },
    ]


def make_inspection_payload(registration_number: str, **overrides) -> dict:
    """Build a valid CreateInspectionRequest payload."""
    payload = {
        "registration_number": registration_number,
        "inspection_type": "PRE_TRIP",
        "mileage": 12345.0,
        "checklist": make_checklist(),
        "latitude": 51.5074,
        "longitude": -0.1278,
        "notes": "Test inspection",
    }
    payload.update(overrides)
    return payload


@pytest_asyncio.fixture
async def inspection_driver_user(user_factory) -> User:
    """Active driver user for inspection tests."""
    return await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")


@pytest_asyncio.fixture
async def inspection_vehicle(db_session: AsyncSession) -> Vehicle:
    """A vehicle for inspection tests."""
    reg = f"INS{uuid.uuid4().hex[:5].upper()}"
    vehicle = Vehicle(
        registration_number=reg,
        make="Ford",
        model="Transit",
        year=2022,
        vehicle_type="INTERNAL",
        fuel_type="DIESEL",
    )
    db_session.add(vehicle)
    await db_session.flush()
    await db_session.refresh(vehicle)
    return vehicle


@pytest_asyncio.fixture
async def inspection_driver(
    db_session: AsyncSession,
    inspection_driver_user: User,
    inspection_vehicle: Vehicle,
) -> Driver:
    """Driver profile with vehicle assigned."""
    driver = Driver(
        user_id=inspection_driver_user.id,
        driver_code=f"DR-{inspection_driver_user.id[:6].upper()}",
        account_status=DriverAccountStatus.ACTIVE,
        vehicle_id=inspection_vehicle.id,
    )
    db_session.add(driver)
    await db_session.flush()
    await db_session.refresh(driver)
    return driver


@pytest_asyncio.fixture
async def driver_auth_headers(inspection_driver_user: User) -> dict[str, str]:
    return driver_headers(inspection_driver_user)


@pytest_asyncio.fixture
async def created_inspection(
    client: AsyncClient,
    inspection_driver: Driver,
    inspection_vehicle: Vehicle,
    driver_auth_headers: dict[str, str],
) -> dict:
    """Create an inspection via API, return the full response data dict."""
    payload = make_inspection_payload(inspection_vehicle.registration_number)
    resp = await client.post(
        "/v1/vehicle-inspections",
        json=payload,
        headers=driver_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# Second driver (for cross-driver isolation tests)


@pytest_asyncio.fixture
async def other_driver_user(user_factory) -> User:
    return await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")


@pytest_asyncio.fixture
async def other_driver(
    db_session: AsyncSession,
    other_driver_user: User,
) -> Driver:
    driver = Driver(
        user_id=other_driver_user.id,
        driver_code=f"DR-{other_driver_user.id[:6].upper()}",
        account_status=DriverAccountStatus.ACTIVE,
    )
    db_session.add(driver)
    await db_session.flush()
    await db_session.refresh(driver)
    return driver


@pytest_asyncio.fixture
async def other_driver_headers(other_driver_user: User) -> dict[str, str]:
    return driver_headers(other_driver_user)
