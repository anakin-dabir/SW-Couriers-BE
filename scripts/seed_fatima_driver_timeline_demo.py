"""Seed/clear Fatima (DR-044) driver timeline demo data.

Creates a separate driver-focused flow:
- pickups on depot-local today and tomorrow
- deliveries on depot-local day+2 and day+3 (weekday-aware)

Usage:
  poetry run python scripts/seed_fatima_driver_timeline_demo.py seed
  poetry run python scripts/seed_fatima_driver_timeline_demo.py clear
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
from app.modules.drivers.enums import DriverAccountStatus, DriverLiveStatus
from app.modules.drivers.models import Driver
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
from app.modules.vehicles.models import Vehicle

FATIMA_DRIVER_ID = "1f39f9ac-eff3-4a65-8254-cae9107b1e10"
FATIMA_USER_ID = "7d5cdf99-fe38-4637-8b41-fee1a9af4894"
FATIMA_DRIVER_CODE = "DR-044"
FATIMA_FIRST_NAME = "Fatima"
FATIMA_LAST_NAME = "Al-Rashid"
FATIMA_PHONE = "07700900108"

SEED_TAG = "FATDM01"
ROUTE_PREFIX = "RT-FATDM-"
ORDER_PREFIX = "FATDM-ORD-"
TRACK_PREFIX = "FATDM-TRK-"
ORG_REF = "DM-FATDM01"
CUSTOMER_EMAIL = "fatima.demo.customer@swcouriers.invalid"
DEFAULT_DEPOT_CODE = "LDN-001"


def _money(v: str) -> Decimal:
    return Decimal(v)


def _next_weekday(d: date, *, steps: int = 1) -> date:
    cur = d
    for _ in range(steps):
        cur += timedelta(days=1)
        while cur.weekday() >= 5:
            cur += timedelta(days=1)
    return cur


def _route_code(service_date: date, leg: str, suffix: str) -> str:
    # routes.route_code is VARCHAR(20); keep generated codes comfortably within limit
    # while preserving day/leg/suffix readability.
    return f"RT-FDM-{service_date.strftime('%y%m%d')}-{leg}{suffix}"


def _order_code(service_date: date, leg: str, idx: int) -> str:
    return f"{ORDER_PREFIX}{service_date.strftime('%y%m%d')}-{leg}-{idx}"


def _tracking_code(service_date: date, leg: str, idx: int) -> str:
    return f"{TRACK_PREFIX}{service_date.strftime('%y%m%d')}-{leg}-{idx}"


async def _clear_seed_data() -> None:
    async with get_async_session() as session:
        route_ids = list((await session.execute(select(Route.id).where(Route.route_code.ilike(f"{ROUTE_PREFIX}%")))).scalars().all())
        if route_ids:
            await session.execute(delete(RouteEvent).where(RouteEvent.route_id.in_(route_ids)))
            await session.execute(delete(Route).where(Route.id.in_(route_ids)))

        await session.execute(delete(Order).where(Order.order_id.ilike(f"{ORDER_PREFIX}%")))

        org = await session.scalar(select(Organization).where(Organization.reference == ORG_REF))
        if org is not None:
            await session.execute(delete(PickupAddress).where(PickupAddress.organization_id == org.id))
            await session.execute(delete(User).where(User.organization_id == org.id, User.email == CUSTOMER_EMAIL))
            await session.delete(org)

        await session.commit()
        print("Cleared Fatima driver timeline demo data.")


async def seed_demo_data() -> None:
    await _clear_seed_data()

    async with get_async_session() as session:
        depot = await session.scalar(select(Depot).where(Depot.code == DEFAULT_DEPOT_CODE))
        if depot is None:
            raise SystemExit(f"Depot {DEFAULT_DEPOT_CODE} not found. Run demo data bootstrap first.")

        tz_name = depot.timezone or "Europe/London"
        tz = ZoneInfo(tz_name)
        today = datetime.now(tz).date()
        tomorrow = _next_weekday(today, steps=1)
        day_plus_2 = _next_weekday(today, steps=2)
        day_plus_3 = _next_weekday(today, steps=3)

        fatima_user = await session.get(User, FATIMA_USER_ID)
        if fatima_user is None:
            fatima_user = User(
                id=FATIMA_USER_ID,
                email="fatima.al-rashid@swcouriers.co.uk",
                phone=FATIMA_PHONE,
                first_name=FATIMA_FIRST_NAME,
                last_name=FATIMA_LAST_NAME,
                title=UserTitle.MS,
                password_hash=hash_password("Driver@12345!"),
                role=UserRole.DRIVER,
                status=UserStatus.ACTIVE,
                email_verified=True,
                force_password_change=False,
            )
            session.add(fatima_user)
            await session.flush()

        fatima_driver = await session.get(Driver, FATIMA_DRIVER_ID)
        if fatima_driver is None:
            fatima_driver = await session.scalar(select(Driver).where(Driver.user_id == FATIMA_USER_ID))
        if fatima_driver is None:
            fatima_driver = Driver(
                id=FATIMA_DRIVER_ID,
                driver_code=FATIMA_DRIVER_CODE,
                user_id=FATIMA_USER_ID,
                account_status=DriverAccountStatus.ACTIVE.value,
                live_status=DriverLiveStatus.OFFLINE.value,
                depot_id=depot.id,
                max_stops=30,
            )
            session.add(fatima_driver)
            await session.flush()
        fatima_driver.depot_id = depot.id

        if fatima_driver.vehicle_id is None:
            vehicle = await session.scalar(select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1))
            if vehicle is None:
                raise SystemExit(f"No vehicle found in depot {DEFAULT_DEPOT_CODE}.")
            fatima_driver.vehicle_id = vehicle.id
        await session.flush()

        org = Organization(
            reference=ORG_REF,
            trading_name="Fatima Driver Demo Org",
            legal_entity_name="Fatima Driver Demo Org Ltd",
            industry=IndustryType.OTHER,
            company_size=CompanySize.EMPLOYEES_11_50,
            date_of_incorporation=today - timedelta(days=365 * 2),
            companies_house_number="FAT123456",
            vat_number="GBFAT123456",
            reg_address_line_1="90 Driver Demo Street",
            reg_city="London",
            reg_postcode="E1 8AA",
            status=OrganizationStatus.ACTIVE,
        )
        session.add(org)
        await session.flush()

        customer = User(
            email=CUSTOMER_EMAIL,
            first_name="Fatima",
            last_name="DemoCustomer",
            phone="07700900118",
            password_hash=hash_password("UnusedFatimaDemo9!"),
            role=UserRole.CUSTOMER_B2B,
            status=UserStatus.ACTIVE,
            email_verified=True,
            organization_id=org.id,
        )
        session.add(customer)
        await session.flush()

        pickup_addr = PickupAddress(
            organization_id=org.id,
            label="Fatima Demo Pickup Hub",
            line_1="15 Cargo Yard",
            city="London",
            postcode="E1W 2BB",
            country="United Kingdom",
            latitude=51.5084,
            longitude=-0.0594,
            is_default=True,
            created_by_user_id=customer.id,
        )
        session.add(pickup_addr)
        await session.flush()

        recipients = [
            ("Yusuf", "Khan", "10 Trinity Square", "London", "EC3N 4AA", 51.5098, -0.0767),
            ("Maya", "Patel", "220 Cable Street", "London", "E1 0BL", 51.5092, -0.0493),
            ("Omar", "Saeed", "61 Commercial Road", "London", "E1 1LP", 51.5123, -0.0656),
            ("Leila", "Noor", "32 Dock Street", "London", "E1 8JP", 51.5074, -0.0640),
        ]

        async def get_or_create_plan(service_date: date) -> RoutePlan:
            plan = await session.scalar(select(RoutePlan).where(RoutePlan.depot_id == depot.id, RoutePlan.service_date == service_date))
            if plan is None:
                plan = RoutePlan(service_date=service_date, depot_id=depot.id, status=RoutePlanStatus.READY.value)
                session.add(plan)
                await session.flush()
            return plan

        async def build_route(*, service_date: date, route_type: RouteType, status: RouteStatus, suffix: str, stops: int) -> Route:
            leg_code = "PU" if route_type == RouteType.PICKUP else "DL"
            plan = await get_or_create_plan(service_date)
            route = Route(
                plan_id=plan.id,
                driver_id=fatima_driver.id,
                vehicle_id=fatima_driver.vehicle_id,
                route_code=_route_code(service_date, leg_code, suffix),
                route_type=route_type.value,
                total_stops=stops,
                status=status.value,
                estimated_drive_time_min=float(stops) * 17.0,
                actual_drive_time_min=55.0 if status == RouteStatus.ACTIVE else None,
                total_distance_km=24.0 if status == RouteStatus.ACTIVE else 18.5,
                navigation_encoded_polyline="xPoly_fatima_demo_polyline",
                navigation_meta={"seed": SEED_TAG, "timeline": "fatima"},
                navigation_fingerprint="pending",
            )
            session.add(route)
            await session.flush()

            route_stops: list[RouteStop] = []
            completed_sequences = {1} if status == RouteStatus.ACTIVE else set()
            for seq in range(1, stops + 1):
                fn, ln, line_1, city, postcode, lat, lng = recipients[(seq - 1) % len(recipients)]
                order = Order(
                    order_id=_order_code(service_date, leg_code, seq),
                    master_label_id=f"ML-FATDM-{service_date.strftime('%y%m%d')}-{leg_code}-{seq}",
                    organization_id=org.id,
                    customer_id=customer.id,
                    pickup_address_id=pickup_addr.id,
                    requested_pickup_date=service_date if route_type == RouteType.PICKUP else None,
                    subtotal=_money("52.50"),
                    vat_amount=_money("10.50"),
                    total_amount=_money("63.00"),
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
                    tracking_id=_tracking_code(service_date, leg_code, seq),
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{seq + 300:05d}",
                    recipient_email=f"{fn.lower()}.{ln.lower()}.fatima.{leg_code.lower()}@example.com",
                    line_1=line_1,
                    city=city,
                    postcode=postcode,
                    latitude=lat,
                    longitude=lng,
                    service_tier=DeliveryServiceTier.STANDARD,
                    signature_required=route_type == RouteType.DELIVERY and seq == 2,
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

                route_stop = RouteStop(
                    route_id=route.id,
                    delivery_stop_id=dstop.id,
                    sequence=seq,
                    estimated_arrival=datetime(service_date.year, service_date.month, service_date.day, 8 + seq, 0, tzinfo=UTC),
                    actual_arrival=datetime.now(UTC) - timedelta(minutes=seq * 12) if seq in completed_sequences else None,
                    distance_from_prev_km=2.0 + seq * 0.5,
                    duration_from_prev_min=5.0 + seq * 2,
                    status="COMPLETED" if seq in completed_sequences else "READY",
                    stop_flow_type=RouteStopFlowType.PICKUP.value if route_type == RouteType.PICKUP else RouteStopFlowType.DELIVERY.value,
                )
                session.add(route_stop)
                await session.flush()
                route_stops.append(route_stop)

                package_status = (
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
                        height_cm=25,
                        weight_kg=3.4,
                        declared_weight_kg=3.6,
                        declared_value=_money("95.00"),
                        status=package_status,
                        is_damaged=False,
                        price_breakdown={"linehaul": "14.00", "fuel": "1.80"},
                    )
                )

            route.navigation_fingerprint = compute_route_navigation_fingerprint(
                sequences_and_route_stop_ids=[(s.sequence, s.id) for s in route_stops]
            )
            if status == RouteStatus.ACTIVE:
                now = datetime.now(UTC)
                session.add(
                    RouteEvent(
                        route_id=route.id,
                        driver_id=fatima_driver.id,
                        event_type="LOCATION_PING",
                        occurred_at=now - timedelta(minutes=10),
                        lat=51.5079,
                        lng=-0.0612,
                        event_metadata={"seed": SEED_TAG},
                    )
                )

            return route

        created_routes = []
        created_routes.append(await build_route(service_date=today, route_type=RouteType.PICKUP, status=RouteStatus.ACTIVE, suffix="D0", stops=3))
        created_routes.append(await build_route(service_date=tomorrow, route_type=RouteType.PICKUP, status=RouteStatus.ASSIGNED, suffix="D1", stops=3))
        created_routes.append(await build_route(service_date=day_plus_2, route_type=RouteType.DELIVERY, status=RouteStatus.ASSIGNED, suffix="D2", stops=4))
        created_routes.append(await build_route(service_date=day_plus_3, route_type=RouteType.DELIVERY, status=RouteStatus.ASSIGNED, suffix="D3", stops=4))

        await session.commit()

        print("=" * 72)
        print("Fatima driver timeline demo seed complete.")
        print(f"Driver ID      : {FATIMA_DRIVER_ID}")
        print(f"User ID        : {FATIMA_USER_ID}")
        print(f"Driver code    : {FATIMA_DRIVER_CODE}")
        print(f"Driver name    : {FATIMA_FIRST_NAME} {FATIMA_LAST_NAME}")
        print(f"Phone          : {FATIMA_PHONE}")
        print(f"Depot / tz     : {DEFAULT_DEPOT_CODE} / {tz_name}")
        print(f"Pickup dates   : {today}, {tomorrow}")
        print(f"Delivery dates : {day_plus_2}, {day_plus_3}")
        print("Route codes    : " + ", ".join(r.route_code for r in created_routes))
        print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear Fatima-specific driver timeline demo data.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Insert Fatima pickup/delivery timeline demo data")
    sub.add_parser("clear", help="Delete Fatima pickup/delivery timeline demo data")
    args = parser.parse_args()

    if args.cmd == "seed":
        asyncio.run(seed_demo_data())
    else:
        asyncio.run(_clear_seed_data())


if __name__ == "__main__":
    main()
