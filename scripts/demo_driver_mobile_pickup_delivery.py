"""Seed / clear rich demo data for driver mobile: pickup + delivery flows.

Usage:
  poetry run python scripts/demo_driver_mobile_pickup_delivery.py seed
  poetry run python scripts/demo_driver_mobile_pickup_delivery.py clear

Behavior:
  - Idempotent: `seed` removes prior rows created by this script and recreates fresh data.
  - Re-running on a new day automatically creates routes with updated service dates.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.common.enums import UserRole, UserStatus, UserTitle
from app.core.database import get_async_session
from app.core.security import hash_password
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package, StopNote, StopNoteImage
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RoutePlanStatus, RouteStatus, RouteStopFlowType, RouteType

from scripts.fe_demo_lib import append_return_route_stop
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

RYAN_USER_ID = "02590528-b113-4267-bcf9-aef4a3343bfe"
RYAN_EMAIL = "ryan.obrien@swcouriers.co.uk"
DEMO_SUFFIX = "DMPDSEED"
ROUTE_PREFIX = "RT-DMPD-"
ORDER_PREFIX = "DMPD-ORD-"
TRACK_PREFIX = "DMPD-TRK-"


def _money(v: str) -> Decimal:
    return Decimal(v)


def _prev_weekday(d: date, *, steps: int = 1) -> date:
    cur = d
    for _ in range(steps):
        cur -= timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
    return cur


def _next_weekday(d: date, *, steps: int = 1) -> date:
    cur = d
    for _ in range(steps):
        cur += timedelta(days=1)
        while cur.weekday() >= 5:
            cur += timedelta(days=1)
    return cur


def _route_code(day: date, leg: str, tag: str) -> str:
    # <= 20 chars for routes.route_code
    # e.g. RT-DMPD-260511-PU-TD
    return f"{ROUTE_PREFIX}{day.strftime('%y%m%d')}-{leg}-{tag}"


def _order_code(day: date, leg: str, idx: int, tag: str) -> str:
    # <= 32 chars for orders.order_id
    return f"{ORDER_PREFIX}{day.strftime('%y%m%d')}-{leg}-{tag}-{idx}"


def _tracking_code(day: date, leg: str, idx: int, tag: str) -> str:
    # <= 40 chars for delivery_stops.tracking_id
    return f"{TRACK_PREFIX}{day.strftime('%y%m%d')}-{leg}-{tag}-{idx}"


async def _purge_seeded_data() -> None:
    async with get_async_session() as session:
        route_ids = list(
            (
                await session.execute(
                    select(Route.id).where(Route.route_code.ilike(f"{ROUTE_PREFIX}%"))
                )
            )
            .scalars()
            .all()
        )

        if route_ids:
            await session.execute(delete(RouteEvent).where(RouteEvent.route_id.in_(route_ids)))
            await session.execute(delete(Route).where(Route.id.in_(route_ids)))

        await session.execute(delete(Order).where(Order.order_id.ilike(f"{ORDER_PREFIX}%")))
        await session.commit()


async def seed_demo_data() -> None:
    await _purge_seeded_data()

    async with get_async_session() as session:
        depot = await session.scalar(select(Depot).where(Depot.code == "LDN-001"))
        if depot is None:
            raise SystemExit("Depot LDN-001 not found. Run `python demo_data.py` first.")

        tz_name = depot.timezone or "Europe/London"
        tz = ZoneInfo(tz_name)
        today = datetime.now(tz).date()
        past_day = _prev_weekday(today, steps=1)
        tomorrow = _next_weekday(today, steps=1)
        day_after_tomorrow = _next_weekday(today, steps=2)

        user = await session.get(User, RYAN_USER_ID)
        if user is None:
            user = User(
                id=RYAN_USER_ID,
                email=RYAN_EMAIL,
                phone="07700900107",
                first_name="Ryan",
                last_name="O'Brien",
                title=UserTitle.MR,
                position_role="Delivery Driver",
                password_hash=hash_password("Driver@12345!"),
                role=UserRole.DRIVER,
                status=UserStatus.ACTIVE,
                email_verified=True,
                force_password_change=False,
            )
            session.add(user)
            await session.flush()

        driver = await session.scalar(select(Driver).where(Driver.user_id == user.id))
        if driver is None:
            raise SystemExit("No driver row linked to Ryan. Create it first via onboarding or `python demo_data.py --count 8`.")
        driver.depot_id = depot.id

        if driver.vehicle_id is None:
            v = await session.scalar(select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1))
            if v is None:
                raise SystemExit("No vehicle found for depot LDN-001.")
            driver.vehicle_id = v.id
        await session.flush()

        org_ref = f"DM-{DEMO_SUFFIX[:8]}"
        org = await session.scalar(select(Organization).where(Organization.reference == org_ref))
        if org is None:
            org = Organization(
                reference=org_ref,
                trading_name=f"Demo Mobile Pickup+Delivery ({DEMO_SUFFIX})",
                legal_entity_name=f"Demo Mobile Pickup+Delivery ({DEMO_SUFFIX})",
                companies_house_number=f"DM{DEMO_SUFFIX[:8]}",
                vat_number=f"GBDM{DEMO_SUFFIX[:8]}",
                industry=IndustryType.OTHER,
                company_size=CompanySize.EMPLOYEES_1_10,
                reg_address_line_1="88 Demo Wharf Road",
                reg_city="London",
                reg_postcode="SE16 7FZ",
                status=OrganizationStatus.ACTIVE,
            )
            session.add(org)
            await session.flush()

        customer_email = "demo.cust.pickup.delivery@swcouriers.invalid"
        customer = await session.scalar(select(User).where(User.email == customer_email))
        if customer is None:
            customer = User(
                email=customer_email,
                phone="07700900998",
                first_name="Taylor",
                last_name="Merchant",
                title=UserTitle.MS,
                password_hash=hash_password("UnusedDemoCustomer9!"),
                role=UserRole.CUSTOMER_B2B,
                status=UserStatus.ACTIVE,
                email_verified=True,
                force_password_change=False,
            )
            session.add(customer)
            await session.flush()

        pickup_addr = await session.scalar(
            select(PickupAddress).where(
                PickupAddress.organization_id == org.id,
                PickupAddress.label == "DM Pickup Main",
            )
        )
        if pickup_addr is None:
            pickup_addr = PickupAddress(
                organization_id=org.id,
                label="DM Pickup Main",
                line_1="5 Dockside Business Park",
                line_2="Unit 12",
                city="London",
                state="Greater London",
                postcode="SE16 3LN",
                country="United Kingdom",
                latitude=51.4972,
                longitude=-0.0619,
                is_default=True,
                created_by_user_id=customer.id,
            )
            session.add(pickup_addr)
            await session.flush()

        recipients = [
            ("Jordan", "Park", "71 Bermondsey Wall East", "London", "SE16 4TY", 51.4979, -0.0748),
            ("Samira", "Hassan", "14 Tooley Street", "London", "SE1 2TU", 51.5045, -0.0865),
            ("Chris", "Walsh", "125 The Highway", "London", "E1W 2BQ", 51.5089, -0.0547),
            ("Priya", "Nair", "30 Swan Road", "London", "SE16 4JW", 51.4912, -0.0467),
            ("Nina", "Kravitz", "199 Jamaica Road", "London", "SE16 4TT", 51.4988, -0.0699),
            ("Wei", "Chen", "150 Tower Bridge Road", "London", "SE1 3LW", 51.5032, -0.0799),
        ]

        async def get_or_create_plan(service_date: date) -> RoutePlan:
            plan = await session.scalar(
                select(RoutePlan).where(RoutePlan.depot_id == depot.id, RoutePlan.service_date == service_date)
            )
            if plan is None:
                plan = RoutePlan(service_date=service_date, depot_id=depot.id, status=RoutePlanStatus.READY.value)
                session.add(plan)
                await session.flush()
            return plan

        async def build_route(
            *,
            service_date: date,
            leg: RouteType,
            tag: str,
            status: RouteStatus,
            total_stops: int,
            completed_sequences: set[int],
            include_return_stop: bool = False,
        ) -> Route:
            plan = await get_or_create_plan(service_date)
            route = Route(
                plan_id=plan.id,
                driver_id=driver.id,
                vehicle_id=driver.vehicle_id,
                route_code=_route_code(service_date, "PU" if leg == RouteType.PICKUP else "DL", tag),
                route_type=leg.value,
                total_stops=total_stops,
                status=status.value,
                estimated_drive_time_min=float(total_stops) * 16.0,
                actual_drive_time_min=65.0 if status == RouteStatus.ACTIVE else None,
                total_distance_km=32.5 if status == RouteStatus.ACTIVE else None,
                total_duration_min=70.0 if status == RouteStatus.ACTIVE else None,
                navigation_encoded_polyline="xPoly_demo_encoded_polyline_placeholder",
                navigation_meta={"demo": True, "seed": "pickup_delivery_mobile"},
                navigation_fingerprint="pending",
            )
            session.add(route)
            await session.flush()

            route_stops: list[RouteStop] = []
            for seq in range(1, total_stops + 1):
                fn, ln, line1, city, pc, lat, lng = recipients[(seq - 1) % len(recipients)]
                leg_code = "PU" if leg == RouteType.PICKUP else "DL"
                order = Order(
                    order_id=_order_code(service_date, leg_code, seq, tag),
                    master_label_id=f"ML-{service_date.strftime('%y%m%d')}-{leg_code}-{tag}-{seq}",
                    organization_id=org.id,
                    customer_id=customer.id,
                    pickup_address_id=pickup_addr.id,
                    requested_pickup_date=service_date if leg == RouteType.PICKUP else None,
                    subtotal=_money("42.50"),
                    vat_amount=_money("8.50"),
                    total_amount=_money("51.00"),
                    status=(
                        OrderStatus.ENROUTE_PICKUP
                        if leg == RouteType.PICKUP and seq not in completed_sequences
                        else OrderStatus.AT_WAREHOUSE
                        if leg == RouteType.PICKUP
                        else OrderStatus.DELIVERY_IN_PROGRESS
                    ),
                )
                session.add(order)
                await session.flush()

                dstop = DeliveryStop(
                    order_id=order.id,
                    tracking_id=_tracking_code(service_date, leg_code, seq, tag),
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{seq + 100:05d}",
                    recipient_email=f"{fn.lower()}.{ln.lower()}.{leg_code.lower()}.{tag.lower()}@example.com",
                    line_1=line1,
                    city=city,
                    postcode=pc,
                    latitude=lat,
                    longitude=lng,
                    service_tier=DeliveryServiceTier.STANDARD,
                    signature_required=(leg == RouteType.DELIVERY and seq == 2),
                    safe_place_allowed=True,
                    status=(
                        DeliveryStopStatus.ENROUTE_PICKUP
                        if leg == RouteType.PICKUP and seq not in completed_sequences
                        else DeliveryStopStatus.AT_WAREHOUSE
                        if leg == RouteType.PICKUP
                        else DeliveryStopStatus.DELIVERED
                        if seq in completed_sequences
                        else DeliveryStopStatus.OUT_FOR_DELIVERY
                    ),
                    scheduled_for=service_date,
                )
                session.add(dstop)
                await session.flush()

                rs = RouteStop(
                    route_id=route.id,
                    delivery_stop_id=dstop.id,
                    sequence=seq,
                    estimated_arrival=datetime(service_date.year, service_date.month, service_date.day, 8 + seq, 10, tzinfo=UTC),
                    actual_arrival=datetime.now(UTC) - timedelta(minutes=15 * seq) if seq in completed_sequences else None,
                    distance_from_prev_km=2.1 + seq * 0.4,
                    duration_from_prev_min=5.0 + seq,
                    status="COMPLETED" if seq in completed_sequences else "READY",
                    stop_flow_type=RouteStopFlowType.PICKUP.value if leg == RouteType.PICKUP else RouteStopFlowType.DELIVERY.value,
                    notes="Confirm pallet count with shipper." if (leg == RouteType.PICKUP and seq == 1) else None,
                )
                session.add(rs)
                await session.flush()
                route_stops.append(rs)

                pkg_status = (
                    PackageStatus.ENROUTE_PICKUP
                    if leg == RouteType.PICKUP and seq not in completed_sequences
                    else PackageStatus.AT_WAREHOUSE
                    if leg == RouteType.PICKUP
                    else PackageStatus.DELIVERED_TO_CUSTOMER
                    if seq in completed_sequences
                    else PackageStatus.OUT_FOR_DELIVERY
                )
                packages = [
                    Package(
                        order_id=order.id,
                        delivery_stop_id=dstop.id,
                        length_cm=45 + i * 3,
                        width_cm=30,
                        height_cm=22,
                        weight_kg=3.2 + i,
                        declared_weight_kg=3.5 + i,
                        declared_value=_money("110.00"),
                        status=pkg_status,
                        is_damaged=False,
                        price_breakdown={"linehaul": "12.00", "fuel": "1.50"},
                    )
                    for i in range(2)
                ]
                session.add_all(packages)
                await session.flush()

                if seq == 2:
                    note = StopNote(
                        delivery_stop_id=dstop.id,
                        note_type="CUSTOMER",
                        message=(
                            "Pickup gate code: 2468."
                            if leg == RouteType.PICKUP
                            else "Leave with concierge if recipient unavailable."
                        ),
                        is_blocking=False,
                        sort_order=0,
                    )
                    session.add(note)
                    await session.flush()
                    session.add(
                        StopNoteImage(
                            stop_note_id=note.id,
                            image_key=f"demo/stop-notes/{note.id}/context.jpg",
                            sort_order=1,
                        )
                    )
                    await session.flush()

            if include_return_stop:
                ret_seq = len(route_stops) + 1
                leg_code = "PU" if leg == RouteType.PICKUP else "DL"
                await append_return_route_stop(
                    session,
                    route=route,
                    route_stops=route_stops,
                    organization_id=org.id,
                    customer_id=customer.id,
                    pickup_address=pickup_addr,
                    order_id=_order_code(service_date, leg_code, ret_seq, f"{tag}-RET"),
                    master_label_id=f"ML-{service_date.strftime('%y%m%d')}-{leg_code}-{tag}-RET",
                    tracking_id=_tracking_code(service_date, leg_code, ret_seq, f"{tag}-RET"),
                    sequence=ret_seq,
                    service_date=service_date,
                    route_stop_status="READY",
                    route_stop_completed=False,
                    notes=(
                        "Failed delivery return — placed on pickup route (cost-efficient)."
                        if leg == RouteType.PICKUP
                        else "Failed delivery return — placed on delivery route (cost-efficient)."
                    ),
                )

            route.navigation_fingerprint = compute_route_navigation_fingerprint(
                sequences_and_route_stop_ids=[(s.sequence, s.id) for s in route_stops]
            )

            if status == RouteStatus.ACTIVE:
                now = datetime.now(UTC)
                session.add_all(
                    [
                        RouteEvent(
                            route_id=route.id,
                            driver_id=driver.id,
                            event_type="LOCATION_PING",
                            occurred_at=now - timedelta(minutes=35),
                            lat=51.4995,
                            lng=-0.0765,
                            event_metadata={"source": "demo_seed"},
                        ),
                        RouteEvent(
                            route_id=route.id,
                            driver_id=driver.id,
                            event_type="LOCATION_PING",
                            occurred_at=now - timedelta(minutes=18),
                            lat=51.5055,
                            lng=-0.0720,
                            event_metadata={"source": "demo_seed"},
                        ),
                        RouteEvent(
                            route_id=route.id,
                            driver_id=driver.id,
                            event_type="HARSH_BRAKING",
                            occurred_at=now - timedelta(minutes=22),
                            lat=51.5038,
                            lng=-0.0735,
                            event_metadata={"deceleration_mps2": -6.5, "speed_before_mph": 39.0},
                        ),
                    ]
                )

            return route

        for day, tag, st, completed in [
            (past_day, "PD", RouteStatus.COMPLETED, {1, 2, 3}),
            (today, "TD", RouteStatus.ACTIVE, {1}),
            (tomorrow, "TM", RouteStatus.ASSIGNED, set()),
            (day_after_tomorrow, "T2", RouteStatus.ASSIGNED, set()),
        ]:
            await build_route(
                service_date=day,
                leg=RouteType.PICKUP,
                tag=tag,
                status=st,
                total_stops=3,
                completed_sequences=completed,
                include_return_stop=(tag == "TD"),
            )
            await build_route(
                service_date=day,
                leg=RouteType.DELIVERY,
                tag=tag,
                status=st,
                total_stops=4,
                completed_sequences=completed,
                include_return_stop=(tag == "TD"),
            )

        await session.commit()
        print("=" * 72)
        print("Driver mobile pickup+delivery seed complete.")
        print(f"Driver user   : {RYAN_USER_ID} ({RYAN_EMAIL})")
        print(f"Depot / tz    : LDN-001 / {tz_name}")
        print(
            f"Dates         : past={past_day} today={today} tomorrow={tomorrow} "
            f"day_after_tomorrow={day_after_tomorrow}"
        )
        print(
            "Routes created: 8 total (pickup+delivery across past/today/tomorrow/day_after_tomorrow); "
            "today's routes each include 1 RETURN stop (failed delivery → sender)"
        )
        print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear pickup+delivery demo routes for mobile app testing.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Insert fresh pickup+delivery demo data")
    sub.add_parser("clear", help="Delete demo data created by this script")

    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed_demo_data())
    else:
        asyncio.run(_purge_seeded_data())
        print("Cleared demo pickup+delivery data created by this script.")


if __name__ == "__main__":
    main()
