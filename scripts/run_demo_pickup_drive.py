"""Simulate the demo pickup route end-to-end through the real ``DriverService`` API surface.

This script drives the seeded pickup route (``RT-DEMO-PKU``) the same way the mobile app would:

#. ``driver_set_route_status`` — flip the route to ``ACTIVE`` (writes ``ROUTE_STARTED`` event).
#. For each ordered ``route_stops`` row (PICKUP-flow; ``order_id`` set, ``delivery_stop_id`` null):

   - Generate synthetic GPS pings from the previous coordinate (depot or last stop) to the next
     pickup-address coordinate. Use the OSRM road polyline if available; otherwise straight-line
     interpolation with jitter. A deliberate slow-down near the stop triggers ``HARSH_BRAKING``.
   - Stream the pings via :meth:`DriverService.ingest_driver_telematics_batch` (this is the same
     endpoint the mobile app posts to). A real wall-clock sleep separates legs unless ``--fast``.
   - Call :meth:`DriverService.driver_update_stop_status` with ``ARRIVED`` (which also stamps
     ``actual_arrival``) and writes the ``STOP_ARRIVED`` route event.
   - Call :meth:`DriverService.pickup_scan_packages_for_order` — narrow service path that resolves
     packages via ``packages.order_id`` (sidesteps the ``delivery_stop_id``-gated scan code path).
     Every package on the order flips to ``LOADED_FOR_DELIVERY`` with a scan log row.
   - Call :meth:`DriverService.driver_update_stop_status` with ``COMPLETED``.

#. ``driver_set_route_status`` — flip the route to ``COMPLETED`` (writes ``ROUTE_COMPLETED`` event).
#. :meth:`RouteGeometryService.compute_traveled_history` — for each leg, pull ``LOCATION_PING``
   events between the previous and current arrival, run OSRM ``/match``, and persist the encoded
   polyline + summary on the leg's ``route_stops`` row for the ``/map`` history view.

Run::

    poetry run python scripts/run_demo_pickup_drive.py             # ~15s pacing between legs
    poetry run python scripts/run_demo_pickup_drive.py --fast      # no sleeping
    poetry run python scripts/run_demo_pickup_drive.py --reset     # rewind, then drive
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — runnable-script pattern (imports below sys.path bootstrap).

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

import app.models  # noqa: F401
from app.common.exceptions import ValidationError
from app.core.database import get_async_session
from app.core.redis import close_redis, init_redis
from app.integrations.osrm import route as osrm_route
from app.modules.depots.models import Depot
from app.modules.drivers.service import DriverService
from app.modules.orders.enums import OrderStatus, PackageStatus
from app.modules.orders.models import Order, Package
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RouteStatus, RouteStopStatus
from app.modules.planning.models import Route, RouteEvent, RouteStop
from app.modules.planning.route_geometry_service import RouteGeometryService
from scripts.seed_demo_actors import DEMO_DEPOT_ID, DEMO_DRIVER_ID
from scripts.seed_demo_pickup_route import DEMO_ROUTE_ID

PINGS_PER_LEG = 8
DEFAULT_LEG_WALL_SECONDS = 8.0
CRUISE_SPEED_MPH = 28.0
APPROACH_SPEED_MPH = 8.0
HARSH_BRAKE_DROP_MPH = 22.0


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in metres between two ``(lon, lat)`` points."""
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _linear_path(start: tuple[float, float], end: tuple[float, float], points: int) -> list[tuple[float, float]]:
    if points <= 1:
        return [end]
    out: list[tuple[float, float]] = []
    for i in range(points):
        t = i / (points - 1)
        lon = start[0] + (end[0] - start[0]) * t
        lat = start[1] + (end[1] - start[1]) * t
        out.append((lon, lat))
    return out


async def _osrm_leg_path(
    start: tuple[float, float],
    end: tuple[float, float],
    points: int,
) -> list[tuple[float, float]] | None:
    """Ask OSRM for the road polyline between two points and downsample to ``points`` coords."""
    try:
        resp = await osrm_route([start, end], overview="full", geometries="geojson")
    except ValidationError:
        return None
    routes = resp.get("routes") or []
    if not routes or not isinstance(routes[0], dict):
        return None
    geom = routes[0].get("geometry") or {}
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    raw: list[tuple[float, float]] = [
        (float(c[0]), float(c[1])) for c in coords if isinstance(c, list) and len(c) >= 2
    ]
    if len(raw) <= points:
        return raw
    step = (len(raw) - 1) / (points - 1)
    return [raw[min(int(round(i * step)), len(raw) - 1)] for i in range(points)]


def _jitter(coord: tuple[float, float], scale_m: float = 5.0) -> tuple[float, float]:
    lat_jitter = (random.random() - 0.5) * (scale_m / 111_320.0) * 2
    lon_jitter = (
        (random.random() - 0.5)
        * (scale_m / (111_320.0 * math.cos(math.radians(coord[1])) or 1.0))
        * 2
    )
    return (coord[0] + lon_jitter, coord[1] + lat_jitter)


async def _load_route_with_pickup_stops(
    session,
) -> tuple[Route, list[tuple[RouteStop, Order, PickupAddress]], Depot]:
    """Load the route + ordered (route_stop, order, pickup_address) triples for the pickup flow."""
    route = await session.get(Route, DEMO_ROUTE_ID)
    if route is None:
        raise SystemExit(
            "Demo route not found. Run scripts/seed_demo_pickup_route.py before this script."
        )

    stmt = (
        select(RouteStop, Order, PickupAddress)
        .join(Order, Order.id == RouteStop.order_id)
        .join(PickupAddress, PickupAddress.id == Order.pickup_address_id)
        .where(RouteStop.route_id == route.id)
        .order_by(RouteStop.sequence.asc())
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        raise SystemExit("Demo route has no order-linked stops.")
    depot = await session.get(Depot, DEMO_DEPOT_ID)
    if depot is None or depot.latitude is None or depot.longitude is None:
        raise SystemExit("Demo depot is missing or has no coordinates.")
    return route, [(rs, order, pa) for rs, order, pa in rows], depot


async def _reset_route(
    session, route: Route, rows: list[tuple[RouteStop, Order, PickupAddress]]
) -> None:
    """Bring the route back to ASSIGNED with empty history so the simulation can re-run cleanly."""
    await session.execute(sa_delete(RouteEvent).where(RouteEvent.route_id == route.id))

    for rs, order, _pa in rows:
        rs.status = RouteStopStatus.READY
        rs.actual_arrival = None
        rs.traveled_encoded_polyline = None
        rs.traveled_distance_m = None
        rs.traveled_duration_s = None
        rs.traveled_started_at = None
        rs.traveled_ended_at = None
        rs.traveled_meta = None
        await session.execute(
            update(Package).where(Package.order_id == order.id).values(status=PackageStatus.ENROUTE_PICKUP)
        )
        await session.execute(
            update(Order).where(Order.id == order.id).values(status=OrderStatus.ENROUTE_PICKUP)
        )

    route.status = RouteStatus.ASSIGNED
    route.actual_drive_time_min = None
    await session.commit()


async def _start_route_via_service(
    *, driver_svc: DriverService, route: Route
) -> datetime:
    """Real driver-app path: POST /v1/driver-profile/me/routes/{id}/start → driver_set_route_status."""
    await driver_svc.driver_set_route_status(
        route_id=route.id,
        driver_id=DEMO_DRIVER_ID,
        status=RouteStatus.ACTIVE,
        event_type="ROUTE_STARTED",
        metadata={"source": "demo_drive_script"},
    )
    started_at = datetime.now(UTC)
    print(f"[i] Route {route.route_code} → ACTIVE at {started_at.isoformat()}")
    return started_at


async def _drive_one_leg(
    *,
    driver_svc: DriverService,
    route_id: str,
    leg_index: int,
    start_coord: tuple[float, float],
    end_coord: tuple[float, float],
    started_at: datetime,
    pings_per_leg: int,
    leg_wall_seconds: float,
    osrm_path: bool,
) -> datetime:
    """Emit telematics pings between two coords via DriverService.ingest_driver_telematics_batch."""
    path: list[tuple[float, float]] | None = None
    if osrm_path:
        path = await _osrm_leg_path(start_coord, end_coord, pings_per_leg)
    if path is None:
        path = _linear_path(start_coord, end_coord, pings_per_leg)

    total_distance_m = _haversine_m(start_coord, end_coord)
    occurred_at = started_at
    items: list[dict[str, object]] = []
    last_lat: float | None = None
    last_lng: float | None = None

    for i, (lon, lat) in enumerate(path):
        is_approach = i >= len(path) - 2
        is_brake = i == len(path) - 2
        if is_brake:
            speed_mph = APPROACH_SPEED_MPH
        elif is_approach:
            speed_mph = APPROACH_SPEED_MPH + HARSH_BRAKE_DROP_MPH
        else:
            speed_mph = CRUISE_SPEED_MPH + random.uniform(-3.0, 4.0)

        jittered = _jitter((lon, lat), scale_m=4.0)
        items.append(
            {
                "route_id": route_id,
                "occurred_at": occurred_at,
                "lat": jittered[1],
                "lng": jittered[0],
                "speed_mph": round(speed_mph, 1),
                "heading": None,
                "accuracy_m": round(random.uniform(3.0, 9.0), 1),
                "source": "demo_drive_script",
            }
        )
        last_lat = jittered[1]
        last_lng = jittered[0]
        occurred_at = occurred_at + timedelta(seconds=max(1.0, leg_wall_seconds / pings_per_leg))

    accepted = await driver_svc.ingest_driver_telematics_batch(
        driver_id=DEMO_DRIVER_ID,
        items=items,
    )
    print(
        f"  Leg {leg_index:>2}: {len(path):>2} pings, {total_distance_m:>6.0f} m "
        f"(accepted={accepted}, last=({last_lat:.5f},{last_lng:.5f}))"
    )
    if leg_wall_seconds > 0:
        await asyncio.sleep(min(leg_wall_seconds, 30.0))
    return occurred_at


async def _arrive_collect_complete(
    *,
    driver_svc: DriverService,
    route_id: str,
    route_stop: RouteStop,
    order: Order,
    arrived_at: datetime,
) -> None:
    """Real driver-app path: ARRIVE → load packages by order → COMPLETE."""
    await driver_svc.driver_update_stop_status(
        stop_id=route_stop.id,
        driver_id=DEMO_DRIVER_ID,
        status=RouteStopStatus.ARRIVED,
        notes=None,
    )
    route_stop.actual_arrival = arrived_at

    result = await driver_svc.pickup_scan_packages_for_order(
        route_id=route_id,
        stop_id=route_stop.id,
        driver_id=DEMO_DRIVER_ID,
    )

    await driver_svc.driver_update_stop_status(
        stop_id=route_stop.id,
        driver_id=DEMO_DRIVER_ID,
        status=RouteStopStatus.COMPLETED,
        notes=None,
    )
    print(
        f"  ✓ Stop seq={route_stop.sequence} order={order.order_id} "
        f"scanned={result['scanned_count']} skipped={result['skipped_count']} "
        f"@ {arrived_at.isoformat()}"
    )


async def _complete_route_via_service(
    *,
    driver_svc: DriverService,
    route: Route,
    route_started_at: datetime,
    simulated_finish_at: datetime,
) -> None:
    finished_at = simulated_finish_at
    duration_min = round((finished_at - route_started_at).total_seconds() / 60.0, 2)
    await driver_svc.driver_set_route_status(
        route_id=route.id,
        driver_id=DEMO_DRIVER_ID,
        status=RouteStatus.COMPLETED,
        event_type="ROUTE_COMPLETED",
        metadata={"source": "demo_drive_script", "duration_min": duration_min},
    )
    route.actual_drive_time_min = duration_min
    print(f"[i] Route {route.route_code} → COMPLETED at {finished_at.isoformat()} ({duration_min} min)")


async def _materialise_history(session, *, route_id: str) -> None:
    service = RouteGeometryService.for_session(session)
    outcome = await service.compute_traveled_history(route_id)
    print(
        "[i] Traveled history materialised: "
        f"{len(outcome.legs)} legs, {outcome.total_distance_m} m, "
        f"{outcome.total_duration_s} s, {outcome.total_points} pings."
    )


async def _run(*, fast: bool, reset: bool, leg_wall_seconds: float, pings_per_leg: int) -> None:
    redis_ready = False
    try:
        await init_redis()
        redis_ready = True
    except Exception as exc:
        print(f"[!] Redis unavailable; planned-geometry caching disabled this run ({exc})")

    try:
        async with get_async_session() as session:
            driver_svc = DriverService(session)
            route, rows, depot = await _load_route_with_pickup_stops(session)

            if reset:
                await _reset_route(session, route, rows)
                print("[i] Route reset to ASSIGNED with empty history.")

            if route.status == RouteStatus.COMPLETED:
                raise SystemExit("Demo route is already COMPLETED. Use --reset to re-run.")

            started_at = await _start_route_via_service(driver_svc=driver_svc, route=route)
            await session.commit()

            cursor_time = started_at
            prev_coord: tuple[float, float] = (float(depot.longitude), float(depot.latitude))

            effective_sleep = 0.0 if fast else leg_wall_seconds
            try_osrm = True
            for i, (rs, order, pa) in enumerate(rows, start=1):
                if pa.latitude is None or pa.longitude is None:
                    print(f"[!] Stop sequence={rs.sequence} pickup-address has no coords; skipping leg.")
                    continue
                end_coord = (float(pa.longitude), float(pa.latitude))
                cursor_time = await _drive_one_leg(
                    driver_svc=driver_svc,
                    route_id=route.id,
                    leg_index=i,
                    start_coord=prev_coord,
                    end_coord=end_coord,
                    started_at=cursor_time,
                    pings_per_leg=pings_per_leg,
                    leg_wall_seconds=effective_sleep,
                    osrm_path=try_osrm,
                )
                await _arrive_collect_complete(
                    driver_svc=driver_svc,
                    route_id=route.id,
                    route_stop=rs,
                    order=order,
                    arrived_at=cursor_time,
                )
                await session.commit()
                prev_coord = end_coord

            await _complete_route_via_service(
                driver_svc=driver_svc,
                route=route,
                route_started_at=started_at,
                simulated_finish_at=cursor_time,
            )
            await session.commit()
            await _materialise_history(session, route_id=route.id)
    finally:
        if redis_ready:
            await close_redis()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate the demo pickup route via real DriverService methods.")
    parser.add_argument("--fast", action="store_true", help="Skip per-leg sleep (instant run).")
    parser.add_argument("--reset", action="store_true", help="Reset the demo route to ASSIGNED before driving.")
    parser.add_argument(
        "--leg-seconds",
        type=float,
        default=DEFAULT_LEG_WALL_SECONDS,
        help=f"Approx. wall-clock seconds per leg (default {DEFAULT_LEG_WALL_SECONDS}).",
    )
    parser.add_argument(
        "--pings-per-leg",
        type=int,
        default=PINGS_PER_LEG,
        help=f"Number of pings emitted per leg (default {PINGS_PER_LEG}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        _run(
            fast=args.fast,
            reset=args.reset,
            leg_wall_seconds=max(0.0, args.leg_seconds),
            pings_per_leg=max(2, args.pings_per_leg),
        )
    )
