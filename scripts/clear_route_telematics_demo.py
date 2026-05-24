"""Remove demo route + telematics data seeded by seed_route_telematics_demo.

Usage:

    poetry run python -m scripts.clear_route_telematics_demo

This deletes:
- Any RouteEvent rows with event_metadata.demo == true
- Any Route rows whose route_code starts with 'RT-DEMO-'
- Any RoutePlan rows linked to demo depots
- Clears driver.depot_id for any drivers assigned to demo depot
- Any Depot rows with code == 'DEPOT-DEMO'
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.modules.planning.models import Route, RouteEvent, RoutePlan


async def main() -> None:
    async with get_async_session() as session:  # type: ignore[assignment]
        # Find demo depot first (we'll need its ID for several operations)
        demo_depot = (
            await session.execute(select(Depot).where(Depot.code == "DEPOT-DEMO").limit(1))
        ).scalars().first()

        # Delete demo events first (FK to routes).
        # event_metadata is stored as JSONB; we match on the "demo" flag.
        demo_events_count = (
            await session.execute(
                delete(RouteEvent).where(RouteEvent.event_metadata["demo"].as_boolean() == True)  # type: ignore[comparison-overlap]
            )
        ).rowcount

        # Delete demo routes based on the RT-DEMO- prefix.
        demo_routes = (
            await session.execute(
                select(Route.id).where(Route.route_code.like("RT-DEMO-%"))
            )
        ).scalars().all()
        demo_routes_count = 0
        if demo_routes:
            demo_routes_count = (
                await session.execute(delete(Route).where(Route.id.in_(demo_routes)))
            ).rowcount

        # Delete RoutePlans linked to demo depot.
        demo_plans_count = 0
        if demo_depot:
            demo_plans_count = (
                await session.execute(delete(RoutePlan).where(RoutePlan.depot_id == demo_depot.id))
            ).rowcount

        # Clear driver.depot_id for any drivers assigned to demo depot
        # (so we don't leave orphaned FK references)
        demo_drivers_count = 0
        if demo_depot:
            demo_drivers_count = (
                await session.execute(
                    update(Driver)
                    .where(Driver.depot_id == demo_depot.id)
                    .values(depot_id=None)
                )
            ).rowcount
            if demo_drivers_count > 0:
                print(f"Cleared depot_id for {demo_drivers_count} driver(s)")

        # Delete demo depot
        demo_depot_count = 0
        if demo_depot:
            demo_depot_count = (
                await session.execute(delete(Depot).where(Depot.code == "DEPOT-DEMO"))
            ).rowcount

        await session.commit()
        print(
            f"Deleted {demo_events_count} demo route_events, "
            f"{demo_routes_count} demo routes, "
            f"{demo_plans_count} demo route_plans, "
            f"and {demo_depot_count} demo depot(s)."
        )


if __name__ == "__main__":
    asyncio.run(main())

