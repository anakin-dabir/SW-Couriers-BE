"""API tests for GET /driver-profile/me/home/summary date windows."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.depots.models import Depot
from app.modules.drivers.service import DriverService
from app.modules.planning.models import Route, RoutePlan, RouteStop
from app.modules.vehicles.models import Vehicle
from tests.drivers.test_driver_self_api import DRIVER_PROFILE, _create_driver_and_headers


async def _seed_route_with_completed_stop(
    db_session: AsyncSession,
    *,
    driver_id: str,
    service_date: date,
    suffix: str,
    actual_drive_time_min: float | None = 60.0,
    total_distance_km: float | None = 40.0,
) -> Route:
    depot = Depot(
        name=f"Home Summary Depot {suffix}",
        code=f"DP-HS-{suffix}",
        address_line_1="1 Summary Street",
        city="London",
        postcode="SW1A 1AA",
    )
    db_session.add(depot)
    await db_session.flush()
    vehicle = Vehicle(registration_number=f"HS-{suffix}", depot_id=depot.id)
    db_session.add(vehicle)
    await db_session.flush()
    plan = RoutePlan(service_date=service_date, depot_id=depot.id, status="READY")
    db_session.add(plan)
    await db_session.flush()
    route = Route(
        plan_id=plan.id,
        driver_id=driver_id,
        vehicle_id=vehicle.id,
        route_code=f"RT-HS-{suffix}",
        route_type="DELIVERY",
        total_stops=1,
        total_distance_km=total_distance_km,
        actual_drive_time_min=actual_drive_time_min,
        status="COMPLETED",
    )
    db_session.add(route)
    await db_session.flush()
    db_session.add(
        RouteStop(
            route_id=route.id,
            delivery_stop_id=None,
            sequence=1,
            status="COMPLETED",
        )
    )
    await db_session.flush()
    return route


@pytest.mark.asyncio
async def test_home_summary_last_month(client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_route_with_completed_stop(
        db_session,
        driver_id=created["id"],
        service_date=last_month_start + timedelta(days=2),
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"period": "last_month"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["addresses_attended"] == 1
    assert data["average_speed_mph"] == 24.9


@pytest.mark.asyncio
async def test_home_summary_explicit_dates_end_on_today(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_route_with_completed_stop(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={
            "start_date": (today - timedelta(days=6)).isoformat(),
            "end_date": today.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["addresses_attended"] == 1
    assert data["average_speed_mph"] == 24.9


@pytest.mark.asyncio
async def test_home_summary_month_to_date_end_on_today(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    """App often sends start_date=1st of month and end_date=today (month-to-date)."""
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_route_with_completed_stop(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["addresses_attended"] == 1
    assert data["average_speed_mph"] == 24.9


@pytest.mark.asyncio
async def test_home_summary_start_and_end_both_today(client: AsyncClient, user_factory) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_home_summary_this_week_end_on_today(client: AsyncClient, user_factory) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"period": "this_week"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert "addresses_attended" in body
    assert "average_speed_mph" in body


@pytest.mark.asyncio
async def test_home_summary_tolerates_non_finite_route_metrics(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_route_with_completed_stop(
        db_session,
        driver_id=created["id"],
        service_date=today.replace(day=1) - timedelta(days=5),
        suffix=suffix,
        actual_drive_time_min=math.nan,
        total_distance_km=math.nan,
    )
    await db_session.commit()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"period": "last_month"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["addresses_attended"] == 1
    assert data["average_speed_mph"] is None


@pytest.mark.asyncio
async def test_home_summary_rejects_inverted_dates(client: AsyncClient, user_factory) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={
            "start_date": today.isoformat(),
            "end_date": (today - timedelta(days=3)).isoformat(),
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_home_summary_rejects_unknown_period(client: AsyncClient, user_factory) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"period": "not_a_period"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_home_summary_requires_both_dates_without_period(client: AsyncClient, user_factory) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        f"{DRIVER_PROFILE}/home/summary",
        headers=headers,
        params={"start_date": today.isoformat()},
    )
    assert resp.status_code == 422


def test_resolve_home_summary_windows_with_end_on_today() -> None:
    today = date(2026, 5, 22)
    start, end, prev_start, prev_end = DriverService.resolve_home_summary_windows(
        period=None,
        start_date=date(2026, 5, 1),
        end_date=today,
        today=today,
    )
    assert start == date(2026, 5, 1)
    assert end == today
    assert prev_end == date(2026, 4, 30)
    assert prev_start == date(2026, 4, 9)
