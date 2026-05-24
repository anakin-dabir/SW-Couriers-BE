"""API tests for GET /driver-profile/me/reports/average-speed date windows."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.depots.models import Depot
from app.modules.planning.models import Route, RouteEvent, RoutePlan
from app.modules.vehicles.models import Vehicle
from tests.drivers.test_driver_self_api import DRIVER_PROFILE, _create_driver_and_headers

AVERAGE_SPEED_REPORT = f"{DRIVER_PROFILE}/reports/average-speed"


async def _seed_average_speed_route(
    db_session: AsyncSession,
    *,
    driver_id: str,
    service_date,
    suffix: str,
    total_distance_km: float | None = 40.0,
    actual_drive_time_min: float | None = 60.0,
    include_events: bool = True,
) -> Route:
    depot = Depot(
        name=f"Avg Speed Depot {suffix}",
        code=f"DP-AVG-{suffix}",
        address_line_1="1 Speed Street",
        city="London",
        postcode="SW1A 1AA",
    )
    db_session.add(depot)
    await db_session.flush()
    vehicle = Vehicle(registration_number=f"AVG-{suffix}", depot_id=depot.id)
    db_session.add(vehicle)
    await db_session.flush()
    plan = RoutePlan(service_date=service_date, depot_id=depot.id, status="READY")
    db_session.add(plan)
    await db_session.flush()
    route = Route(
        plan_id=plan.id,
        driver_id=driver_id,
        vehicle_id=vehicle.id,
        route_code=f"RT-AVG-{suffix}",
        route_type="DELIVERY",
        total_stops=2,
        total_distance_km=total_distance_km,
        actual_drive_time_min=actual_drive_time_min,
        status="COMPLETED",
    )
    db_session.add(route)
    await db_session.flush()
    if include_events:
        db_session.add_all(
            [
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="LOCATION_PING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=2),
                    event_metadata={"speed_mph": 38.0},
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="LOCATION_PING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=1),
                    event_metadata={"speed_mph": 47.0},
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=1),
                    event_metadata={"speed_over_mph": 6.0},
                ),
            ]
        )
        await db_session.flush()
    return route


@pytest.mark.asyncio
async def test_average_speed_report_end_date_on_today(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_average_speed_route(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": (today - timedelta(days=6)).isoformat(),
            "end_date": today.isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]["table"]
    assert data["total"] >= 1
    row = next(item for item in data["items"] if item["route_code"] == f"RT-AVG-{suffix}")
    assert row["average_speed_mph"] == 24.9
    assert row["speed_range_min_mph"] == 38.0
    assert row["speed_range_max_mph"] == 47.0


@pytest.mark.asyncio
async def test_average_speed_report_month_to_date_end_on_today(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_average_speed_route(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["table"]["total"] >= 1


@pytest.mark.asyncio
async def test_average_speed_report_same_start_and_end_today(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_average_speed_route(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
    )
    await db_session.commit()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat(), "page": 1, "size": 20},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_average_speed_report_tolerates_non_finite_route_metrics(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    await _seed_average_speed_route(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
        total_distance_km=math.nan,
        actual_drive_time_min=math.nan,
    )
    await db_session.commit()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    row = next(
        item for item in resp.json()["data"]["table"]["items"] if item["route_code"] == f"RT-AVG-{suffix}"
    )
    assert row["average_speed_mph"] == 42.5


@pytest.mark.asyncio
async def test_average_speed_report_rejects_inverted_dates(
    client: AsyncClient,
    user_factory,
) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": today.isoformat(),
            "end_date": (today - timedelta(days=7)).isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_average_speed_report_tolerates_bad_event_metadata(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    headers, created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()
    suffix = uuid.uuid4().hex[:8].upper()

    route = await _seed_average_speed_route(
        db_session,
        driver_id=created["id"],
        service_date=today,
        suffix=suffix,
        include_events=False,
    )
    db_session.add_all(
        [
            RouteEvent(
                route_id=route.id,
                driver_id=created["id"],
                event_type="LOCATION_PING",
                occurred_at=datetime.now(UTC) - timedelta(minutes=2),
                event_metadata={"speed_mph": "not-a-number", "speed_over_mph": "NaN"},
            ),
            RouteEvent(
                route_id=route.id,
                driver_id=created["id"],
                event_type="LOCATION_PING",
                occurred_at=datetime.now(UTC),
                event_metadata={"speed_mph": 44.0},
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    row = next(
        item for item in resp.json()["data"]["table"]["items"] if item["route_code"] == f"RT-AVG-{suffix}"
    )
    assert row["speed_range_min_mph"] == 44.0
    assert row["speed_range_max_mph"] == 44.0


@pytest.mark.asyncio
async def test_average_speed_report_empty_window_returns_zero_total(
    client: AsyncClient,
    user_factory,
) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=20)).isoformat(),
            "page": 1,
            "size": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["table"]["total"] == 0


@pytest.mark.asyncio
async def test_average_speed_report_requires_both_dates_without_period(
    client: AsyncClient,
    user_factory,
) -> None:
    headers, _created = await _create_driver_and_headers(client, user_factory)
    today = datetime.now(UTC).date()

    resp = await client.get(
        AVERAGE_SPEED_REPORT,
        headers=headers,
        params={"end_date": today.isoformat(), "page": 1, "size": 20},
    )
    assert resp.status_code == 422
