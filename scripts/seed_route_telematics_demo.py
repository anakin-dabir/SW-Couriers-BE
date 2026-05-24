"""Seed demo route + telematics data for staging/dev.

Usage (from repo root, with DATABASE_URL configured):

    poetry run python -m scripts.seed_route_telematics_demo

This creates:
- One demo route (if none exists yet) for an arbitrary driver with a vehicle.
- A few SPEEDING and HARSH_BRAKING RouteEvent rows for that route.

Identifiable by:
- route_code starting with 'RT-DEMO-'
- RouteEvent.event_metadata.demo == true
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from random import randint

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.modules.depots.models import Depot
from app.modules.drivers.service import DriverService
from app.modules.drivers.models import Driver
from app.modules.planning.enums import RouteType
from app.modules.planning.models import Route, RouteEvent, RoutePlan
from app.modules.vehicles.models import Vehicle


async def _get_any_driver_with_vehicle(session: AsyncSession) -> tuple[Driver | None, Vehicle | None]:
    driver = (await session.execute(select(Driver).limit(1))).scalars().first()
    if driver is None:
        return None, None
    vehicle = None
    if driver.vehicle_id:
        vehicle = await session.get(Vehicle, driver.vehicle_id)
    if vehicle is None:
        vehicle = (await session.execute(select(Vehicle).limit(1))).scalars().first()
    return driver, vehicle


async def _ensure_demo_depot(session: AsyncSession) -> Depot:
    """Ensure a demo depot exists for route + plan creation. Required because RoutePlan.depot_id is NOT NULL."""
    # Check if demo depot already exists
    existing = (
        await session.execute(select(Depot).where(Depot.code == "DEPOT-DEMO").limit(1))
    ).scalars().first()
    if existing:
        print(f"Using existing demo depot {existing.code} ({existing.id})")
        return existing

    # Create a new demo depot
    demo_depot = Depot(
        name="Demo Depot",
        code="DEPOT-DEMO",
        address_line_1="123 Demo Street",
        city="Demo City",
        postcode="DEM0 001",
        timezone="Europe/London",
        status="active",
        notes="[DEMO] Created by seed_route_telematics_demo.py - Safe to delete",
    )
    session.add(demo_depot)
    await session.flush()
    # Ensure ID is populated after flush
    await session.refresh(demo_depot)
    print(f"Created demo depot {demo_depot.code} ({demo_depot.id})")
    return demo_depot


async def _ensure_demo_route(session: AsyncSession) -> Route | None:
    # Reuse an existing demo route if present.
    existing = (
        await session.execute(
            select(Route).where(Route.route_code.like("RT-DEMO-%")).order_by(Route.created_at.desc()).limit(1)
        )
    ).scalars().first()
    if existing:
        return existing

    driver, vehicle = await _get_any_driver_with_vehicle(session)
    if driver is None or vehicle is None:
        print("No driver/vehicle found; skipping demo seed.")
        return None

    # Ensure we have a demo depot and assign the driver to it if needed
    demo_depot = await _ensure_demo_depot(session)
    if not demo_depot or not demo_depot.id:
        print(f"ERROR: Failed to create or retrieve demo depot (id={getattr(demo_depot, 'id', None)})")
        return None

    # Assign driver to demo depot if not already assigned
    if not driver.depot_id:
        driver.depot_id = demo_depot.id
        await session.flush()
        print(f"Assigned driver {driver.id} to demo depot {demo_depot.id}")

    # Pick or create a simple RoutePlan for "today" using the same depot-local calendar rule as
    # GET /v1/driver-profile/me/routes/today (DriverService._calendar_date_in_zone).
    driver_for_plan_day = (
        await session.execute(select(Driver).options(selectinload(Driver.depot)).where(Driver.id == driver.id))
    ).scalars().first()
    tz_name, _ = DriverService._depot_timezone_name_for_driver(driver_for_plan_day or driver)
    today = DriverService._calendar_date_in_zone(utc_now=datetime.now(UTC), tz_name=tz_name)
    plan = (
        await session.execute(
            select(RoutePlan).where(
                RoutePlan.depot_id == driver.depot_id,
                RoutePlan.service_date == today,
            )
        )
    ).scalars().first()
    if plan is None:
        plan = RoutePlan(
            depot_id=driver.depot_id,
            service_date=today,
            status="locked",
        )
        session.add(plan)
        await session.flush()
        print(f"Created demo route plan {plan.id} for depot {driver.depot_id}")

    route = Route(
        plan_id=plan.id,
        driver_id=driver.id,
        vehicle_id=vehicle.id,
        total_distance_km=42.0,
        total_duration_min=95.0,
        total_stops=18,
        total_weight_kg=250.0,
        total_volume_m3=3.5,
        status="completed",
        route_type=RouteType.DELIVERY.value,
        # Use a stable demo prefix; DB default will still apply if omitted elsewhere.
        route_code=f"RT-DEMO-{randint(100, 999)}",
        estimated_drive_time_min=100.0,
        actual_drive_time_min=95.0,
    )
    session.add(route)
    await session.flush()
    print(f"Created demo route {route.route_code} ({route.id}) for driver {driver.id}")
    return route


async def _seed_events(session: AsyncSession, route: Route) -> None:
    now = datetime.now(UTC)

    demo_events: list[RouteEvent] = []
    # Two SPEEDING events
    for i, over in enumerate((5, 8), start=1):
        demo_events.append(
            RouteEvent(
                route_id=route.id,
                driver_id=route.driver_id,
                event_type="SPEEDING",
                occurred_at=now - timedelta(minutes=30 - i * 5),
                lat=51.5074,
                lng=-0.1278,
                event_metadata={
                    "demo": True,
                    "kind": "speeding",
                    "speed_mph": 30 + over,
                    "limit_mph": 30,
                },
            )
        )

    # One HARSH_BRAKING event
    demo_events.append(
        RouteEvent(
            route_id=route.id,
            driver_id=route.driver_id,
            event_type="HARSH_BRAKING",
            occurred_at=now - timedelta(minutes=10),
            lat=51.509,
            lng=-0.08,
            event_metadata={
                "demo": True,
                "kind": "harsh_braking",
                "severity": "HIGH",
                "start_speed_mph": 32,
                "end_speed_mph": 5,
            },
        )
    )

    for ev in demo_events:
        session.add(ev)
    await session.flush()
    print(f"Inserted {len(demo_events)} demo route_events for route {route.id}")


async def main() -> None:
    async with get_async_session() as session:  # type: ignore[assignment]
        route = await _ensure_demo_route(session)
        if route is None:
            return
        await _seed_events(session, route)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())

