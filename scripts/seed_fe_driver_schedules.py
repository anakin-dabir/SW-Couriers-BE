"""Seed Ryan (pickups) and Fatima (deliveries) for today + next two calendar days.

Ryan O'Brien  — pickup routes on today, tomorrow, day-after-tomorrow.
Fatima Al-Rashid — delivery routes on today, tomorrow, day-after-tomorrow.

Usage:
  poetry run python scripts/seed_fe_driver_schedules.py seed
  poetry run python scripts/seed_fe_driver_schedules.py clear

Clears only rows tagged by this suite (RT-FE-*, FE-DEMO-ORD-* for driver org).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.common.enums import UserRole, UserStatus
from app.core.database import get_async_session
from app.core.security import hash_password
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RoutePlanStatus, RouteStatus, RouteStopFlowType, RouteType
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.models import User
from scripts.fe_demo_lib import (
    CUSTOMER_EMAIL,
    FATIMA_EMAIL,
    ORG_REF,
    RYAN_EMAIL,
    SEED_TAG,
    calendar_day_offsets,
    depot_today,
    ensure_driver_vehicle,
    money,
    order_code,
    resolve_depot,
    resolve_driver_by_email,
    route_code,
    tracking_code,
)


RECIPIENTS = [
    ("Alex", "Morris", "14 Tooley Street", "London", "SE1 2TU", 51.5045, -0.0865),
    ("Beth", "Clarke", "71 Bermondsey Wall East", "London", "SE16 4TY", 51.4979, -0.0748),
    ("Carlos", "Diaz", "125 The Highway", "London", "E1W 2BQ", 51.5089, -0.0547),
    ("Dana", "Iqbal", "30 Swan Road", "London", "SE16 4JW", 51.4912, -0.0467),
    ("Elena", "Voss", "199 Jamaica Road", "London", "SE16 4TT", 51.4988, -0.0699),
]


async def _clear_driver_routes_only() -> None:
    from scripts.fe_demo_lib import ROUTE_PREFIX

    async with get_async_session() as session:
        route_ids = list(
            (await session.execute(select(Route.id).where(Route.route_code.ilike(f"{ROUTE_PREFIX}%")))).scalars().all()
        )
        if route_ids:
            await session.execute(delete(RouteEvent).where(RouteEvent.route_id.in_(route_ids)))
            await session.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
            await session.execute(delete(Route).where(Route.id.in_(route_ids)))
        await session.execute(delete(Order).where(Order.order_id.ilike("FE-DEMO-ORD-DRV-%")))
        await session.commit()
        print("Cleared FE driver schedule routes and linked orders.")


async def _ensure_demo_org(session, *, today: date) -> tuple[Organization, User, PickupAddress]:
    org = await session.scalar(select(Organization).where(Organization.reference == ORG_REF))
    if org is None:
        org = Organization(
            reference=ORG_REF,
            trading_name="FE Driver Demo Org",
            legal_entity_name="FE Driver Demo Org Ltd",
            industry=IndustryType.OTHER,
            company_size=CompanySize.EMPLOYEES_1_10,
            date_of_incorporation=today - timedelta(days=400),
            companies_house_number="FE12345678",
            vat_number="GBFE12345678",
            reg_address_line_1="40 FE Demo Wharf",
            reg_city="London",
            reg_postcode="SE16 7FZ",
            status=OrganizationStatus.ACTIVE,
        )
        session.add(org)
        await session.flush()

    customer = await session.scalar(select(User).where(User.email == CUSTOMER_EMAIL))
    if customer is None:
        customer = User(
            email=CUSTOMER_EMAIL,
            first_name="FE",
            last_name="DemoCustomer",
            phone="07700900901",
            password_hash=hash_password("UnusedFeDemo9!"),
            role=UserRole.CUSTOMER_B2B,
            status=UserStatus.ACTIVE,
            email_verified=True,
            organization_id=org.id,
        )
        session.add(customer)
        await session.flush()

    pickup = await session.scalar(
        select(PickupAddress).where(
            PickupAddress.organization_id == org.id,
            PickupAddress.label == "FE Driver Pickup Hub",
        )
    )
    if pickup is None:
        pickup = PickupAddress(
            organization_id=org.id,
            label="FE Driver Pickup Hub",
            line_1="8 Dockside Park",
            city="London",
            postcode="SE16 3LN",
            country="United Kingdom",
            latitude=51.4972,
            longitude=-0.0619,
            is_default=True,
            created_by_user_id=customer.id,
        )
        session.add(pickup)
        await session.flush()
    return org, customer, pickup


async def seed_driver_schedules() -> None:
    await _clear_driver_routes_only()

    async with get_async_session() as session:
        depot = await resolve_depot(session)
        today = depot_today(depot)
        tomorrow, day_after = calendar_day_offsets(today)[1:]

        _, ryan_driver = await resolve_driver_by_email(session, RYAN_EMAIL)
        _, fatima_driver = await resolve_driver_by_email(session, FATIMA_EMAIL)
        await ensure_driver_vehicle(session, ryan_driver, depot)
        await ensure_driver_vehicle(session, fatima_driver, depot)

        org, customer, pickup_addr = await _ensure_demo_org(session, today=today)

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
            driver,
            service_date: date,
            route_type: RouteType,
            day_tag: str,
            status: RouteStatus,
            stops: int,
            completed_sequences: set[int],
        ) -> Route:
            leg = "PU" if route_type == RouteType.PICKUP else "DL"
            plan = await get_or_create_plan(service_date)
            route = Route(
                plan_id=plan.id,
                driver_id=driver.id,
                vehicle_id=driver.vehicle_id,
                route_code=route_code(service_date, leg, day_tag),
                route_type=route_type.value,
                total_stops=stops,
                status=status.value,
                estimated_drive_time_min=float(stops) * 15.0,
                actual_drive_time_min=50.0 if status == RouteStatus.ACTIVE else None,
                total_distance_km=22.0 if status == RouteStatus.ACTIVE else 18.0,
                navigation_encoded_polyline="xPoly_fe_driver_demo",
                navigation_meta={"seed": SEED_TAG, "leg": leg},
                navigation_fingerprint="pending",
            )
            session.add(route)
            await session.flush()

            route_stops: list[RouteStop] = []
            for seq in range(1, stops + 1):
                fn, ln, line_1, city, postcode, lat, lng = RECIPIENTS[(seq - 1) % len(RECIPIENTS)]
                ord_tag = f"DRV-{leg}-{day_tag}"
                order = Order(
                    order_id=order_code(ord_tag, seq),
                    master_label_id=f"ML-FE-{ord_tag}-{seq}",
                    organization_id=org.id,
                    customer_id=customer.id,
                    pickup_address_id=pickup_addr.id,
                    requested_pickup_date=service_date if route_type == RouteType.PICKUP else None,
                    subtotal=money("45.00"),
                    vat_amount=money("9.00"),
                    total_amount=money("54.00"),
                    status=(
                        OrderStatus.ENROUTE_PICKUP
                        if route_type == RouteType.PICKUP and seq not in completed_sequences
                        else OrderStatus.AT_WAREHOUSE
                        if route_type == RouteType.PICKUP
                        else OrderStatus.DELIVERY_IN_PROGRESS
                    ),
                )
                session.add(order)
                await session.flush()

                dstop = DeliveryStop(
                    order_id=order.id,
                    tracking_id=tracking_code(ord_tag, seq),
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{seq + 800:05d}",
                    recipient_email=f"{fn.lower()}.{ln.lower()}.fe.{leg.lower()}@example.com",
                    line_1=line_1,
                    city=city,
                    postcode=postcode,
                    latitude=lat,
                    longitude=lng,
                    service_tier=DeliveryServiceTier.STANDARD,
                    signature_required=route_type == RouteType.DELIVERY and seq == 1,
                    safe_place_allowed=True,
                    status=(
                        DeliveryStopStatus.ENROUTE_PICKUP
                        if route_type == RouteType.PICKUP and seq not in completed_sequences
                        else DeliveryStopStatus.AT_WAREHOUSE
                        if route_type == RouteType.PICKUP
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
                    estimated_arrival=datetime(service_date.year, service_date.month, service_date.day, 8 + seq, 15, tzinfo=UTC),
                    actual_arrival=datetime.now(UTC) - timedelta(minutes=12 * seq) if seq in completed_sequences else None,
                    distance_from_prev_km=2.0 + seq * 0.4,
                    duration_from_prev_min=5.0 + seq,
                    status="COMPLETED" if seq in completed_sequences else "READY",
                    stop_flow_type=(
                        RouteStopFlowType.PICKUP.value if route_type == RouteType.PICKUP else RouteStopFlowType.DELIVERY.value
                    ),
                )
                session.add(rs)
                await session.flush()
                route_stops.append(rs)

                pkg_status = (
                    PackageStatus.ENROUTE_PICKUP
                    if route_type == RouteType.PICKUP and seq not in completed_sequences
                    else PackageStatus.AT_WAREHOUSE
                    if route_type == RouteType.PICKUP
                    else PackageStatus.DELIVERED_TO_CUSTOMER
                    if seq in completed_sequences
                    else PackageStatus.OUT_FOR_DELIVERY
                )
                session.add(
                    Package(
                        order_id=order.id,
                        delivery_stop_id=dstop.id,
                        length_cm=40,
                        width_cm=30,
                        height_cm=22,
                        weight_kg=3.0,
                        declared_weight_kg=3.2,
                        declared_value=money("85.00"),
                        status=pkg_status,
                        is_damaged=False,
                        price_breakdown={"linehaul": "11.00", "fuel": "1.40"},
                    )
                )

            route.navigation_fingerprint = compute_route_navigation_fingerprint(
                sequences_and_route_stop_ids=[(s.sequence, s.id) for s in route_stops]
            )
            if status == RouteStatus.ACTIVE:
                session.add(
                    RouteEvent(
                        route_id=route.id,
                        driver_id=driver.id,
                        event_type="LOCATION_PING",
                        occurred_at=datetime.now(UTC) - timedelta(minutes=8),
                        lat=51.4990,
                        lng=-0.0700,
                        event_metadata={"seed": SEED_TAG},
                    )
                )
            return route

        created: list[Route] = []
        for day, tag, st, completed in [
            (today, "D0", RouteStatus.ACTIVE, {1}),
            (tomorrow, "D1", RouteStatus.ASSIGNED, set()),
            (day_after, "D2", RouteStatus.ASSIGNED, set()),
        ]:
            created.append(
                await build_route(
                    driver=ryan_driver,
                    service_date=day,
                    route_type=RouteType.PICKUP,
                    day_tag=tag,
                    status=st,
                    stops=3,
                    completed_sequences=completed,
                )
            )
            created.append(
                await build_route(
                    driver=fatima_driver,
                    service_date=day,
                    route_type=RouteType.DELIVERY,
                    day_tag=tag,
                    status=st,
                    stops=4,
                    completed_sequences=completed,
                )
            )

        await session.commit()

        print("=" * 72)
        print("FE driver schedule seed complete.")
        print(f"Depot / today     : {depot.code} / {today}")
        print(f"Ryan ({RYAN_EMAIL})")
        print(f"  Pickups         : {today}, {tomorrow}, {day_after}")
        print(f"Fatima ({FATIMA_EMAIL})")
        print(f"  Deliveries      : {today}, {tomorrow}, {day_after}")
        print("Routes:")
        for r in created:
            print(f"  {r.route_code}  type={r.route_type}  status={r.status}")
        print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Ryan pickup + Fatima delivery schedules (3 days).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("clear")
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed_driver_schedules())
    else:
        asyncio.run(_clear_driver_routes_only())


if __name__ == "__main__":
    main()
