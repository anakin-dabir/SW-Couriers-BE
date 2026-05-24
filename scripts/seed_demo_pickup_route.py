"""Seed a single PICKUP route covering the 32 demo orders (idempotent).

Depends on::

    poetry run python scripts/seed_demo_actors.py
    poetry run python scripts/seed_demo_orders.py

Creates / refreshes the following with stable IDs:

* 1 route ``RT-DEMO-PKU`` with ``plan_id = NULL``, ``route_type=PICKUP``, ``status=ASSIGNED``.
* 32 route stops — **one per order** — with ``route_stops.order_id = order.id`` and
  ``delivery_stop_id = NULL``. PICKUP-flow stops resolve their coordinates and packages through
  the order (``orders.pickup_address_id`` + ``packages.order_id``).
* Stops grouped by ``pickup_address_id`` so that all 4 orders at a warehouse are visited
  consecutively (8 warehouses × 4 orders each).
* An open ``route_crew_assignments`` row linking the demo crew to the route.
* Warms the Redis planned-geometry cache by calling OSRM ``/route`` once.

Run::

    poetry run python scripts/seed_demo_pickup_route.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — runnable-script pattern (imports below sys.path bootstrap).

from sqlalchemy import delete, select

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.core.redis import close_redis, init_redis
from app.modules.crew.models import RouteCrewAssignment
from app.modules.orders.models import Order
from app.modules.planning.enums import RouteStatus, RouteStopFlowType, RouteStopStatus, RouteType
from app.modules.planning.models import Route, RouteStop
from app.modules.planning.route_geometry_service import RouteGeometryService
from scripts.seed_demo_actors import (
    DEMO_CREW_ID,
    DEMO_DRIVER_ID,
    DEMO_VEHICLE_ID,
    PICKUP_ADDRESSES,
)
from scripts.seed_demo_orders import DEMO_ORDER_PREFIX

DEMO_ROUTE_ID = "00000000-0000-4000-8000-000000000109"
DEMO_RCA_ID = "00000000-0000-4000-8000-000000000110"

ROUTE_CODE = "RT-DEMO-PKU"


async def _purge_prior_demo_route(session) -> None:
    """Delete previous demo route + crew assignment so this seed is idempotent."""
    await session.execute(delete(RouteCrewAssignment).where(RouteCrewAssignment.route_id == DEMO_ROUTE_ID))
    route = await session.get(Route, DEMO_ROUTE_ID)
    if route is not None:
        await session.delete(route)
        await session.flush()


async def _load_orders_grouped_by_pickup(session) -> list[Order]:
    """Return every demo order sorted by (pickup_address sequence, order_id) so warehouses cluster."""
    stmt = (
        select(Order)
        .where(Order.order_id.like(f"{DEMO_ORDER_PREFIX}-%"))
        .order_by(Order.created_at.asc())
    )
    orders = list((await session.execute(stmt)).scalars().all())
    pickup_priority: dict[str, int] = {str(pa["id"]): i for i, pa in enumerate(PICKUP_ADDRESSES)}
    orders.sort(key=lambda o: (pickup_priority.get(o.pickup_address_id or "", 9999), o.order_id))
    return orders


async def _create_route_with_stops(
    session,
    *,
    orders: list[Order],
) -> tuple[Route, list[RouteStop]]:
    route = Route(
        id=DEMO_ROUTE_ID,
        plan_id=None,
        driver_id=DEMO_DRIVER_ID,
        vehicle_id=DEMO_VEHICLE_ID,
        route_code=ROUTE_CODE,
        route_type=RouteType.PICKUP,
        total_stops=len(orders),
        status=RouteStatus.ASSIGNED,
    )
    session.add(route)
    await session.flush()

    base_time = datetime.now(UTC).replace(microsecond=0)
    route_stops: list[RouteStop] = []
    for seq, order in enumerate(orders, start=1):
        rs = RouteStop(
            route_id=route.id,
            delivery_stop_id=None,
            order_id=order.id,
            sequence=seq,
            stop_flow_type=RouteStopFlowType.PICKUP,
            status=RouteStopStatus.READY,
            estimated_arrival=base_time + timedelta(minutes=12 * seq),
        )
        session.add(rs)
        await session.flush()
        route_stops.append(rs)
    return route, route_stops


async def _assign_crew_to_route(session, route_id: str) -> RouteCrewAssignment:
    rca = RouteCrewAssignment(
        id=DEMO_RCA_ID,
        route_id=route_id,
        crew_id=DEMO_CREW_ID,
        assigned_at=datetime.now(UTC),
    )
    session.add(rca)
    await session.flush()
    return rca


async def _warm_planned_geometry(session, route_id: str) -> None:
    """Call OSRM ``/route`` once so the Redis cache is hot for ``GET /v1/routes/{id}/map``.

    Gracefully degrades: if Redis or OSRM is offline, the seed still succeeds — the ``/map``
    endpoint will compute (and cache) the geometry on the first read instead.
    """
    from app.common.exceptions import ValidationError

    redis_up = False
    try:
        await init_redis()
        redis_up = True
    except Exception as exc:
        print(f"[!] Redis not available; skipping planned-cache warm-up ({exc})")
        return
    try:
        service = RouteGeometryService.for_session(session)
        try:
            result = await service.get_or_compute_planned_geometry(route_id)
        except ValidationError as exc:
            print(f"[!] OSRM not available; planned geometry will be computed on first /map call ({exc})")
            return
        print(
            "  Planned geometry computed: "
            f"distance={result.distance_m} m, duration={result.duration_s} s, "
            f"fingerprint={result.fingerprint[:12]}…"
        )
    finally:
        if redis_up:
            await close_redis()


async def _run() -> None:
    async with get_async_session() as session:
        await _purge_prior_demo_route(session)
        orders = await _load_orders_grouped_by_pickup(session)
        if not orders:
            raise SystemExit(
                "No demo orders found. Run scripts/seed_demo_orders.py before this script."
            )
        route, route_stops = await _create_route_with_stops(session, orders=orders)
        rca = await _assign_crew_to_route(session, route_id=route.id)
        await session.commit()
        await _warm_planned_geometry(session, route_id=route.id)

    print("=" * 72)
    print("Demo pickup route seeded.")
    print(f"  Route ID          : {DEMO_ROUTE_ID}  code={ROUTE_CODE}")
    print("  Route type/status : PICKUP / ASSIGNED  (plan_id = NULL)")
    print(f"  Stops             : {len(route_stops)} (8 warehouses × 4 orders each)")
    print(f"  Crew assignment   : {rca.id}")
    print()
    print("Try the map endpoint:")
    print(f"  GET /v1/routes/{DEMO_ROUTE_ID}/map")
    print()
    print("Next:")
    print("  poetry run python scripts/run_demo_pickup_drive.py")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_run())
