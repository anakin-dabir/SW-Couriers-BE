"""Seed Ryan (pickups) and Fatima (deliveries) for today + the next 5 calendar days.

Independent of ``demo_driver_mobile.py`` / ``seed_fe_driver_schedules.py`` (own route/order prefixes).

Ryan O'Brien      — ``ryan.obrien@swcouriers.co.uk`` — PICKUP routes (3 stops + 1 RETURN each day).
Fatima Al-Rashid  — ``fatima.alrashid@swcouriers.co.uk`` — DELIVERY routes (4 stops + 1 RETURN each day).

Today is ACTIVE with one completed stop; later days are ASSIGNED.

Usage (from repo root)::

  poetry run python scripts/seed_ryan_fatima_mobile_week.py seed
  poetry run python scripts/seed_ryan_fatima_mobile_week.py clear

Requires depot ``LDN-001`` (or ``DEMO-LDN-01``) and driver rows for both emails.

Return stops match driver execution tests: ``stop_flow_type`` RETURN, packages
``RETURN_IN_TRANSIT``. Mobile can finalize via ``PATCH …/packages/{id}/status`` with
``RETURNED_TO_SENDER`` (POD required before complete), ``SENDER_NOT_HOME``, or ``DISPOSED``,
or ``POST …/packages/batch-status`` for all packages on the stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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
    FATIMA_EMAIL,
    RYAN_EMAIL,
    append_return_route_stop,
    depot_today,
    ensure_driver_vehicle,
    money,
    resolve_depot,
    resolve_driver_by_email,
)

SEED_TAG = "RF6_MOBILE_WEEK"
ROUTE_PREFIX = "RT-RF6-"
ORDER_PREFIX = "RF6-ORD-"
TRACK_PREFIX = "RF6-TRK-"
ORG_REF = "RF6-DEMO-ORG"
CUSTOMER_EMAIL = "ryan.fatima.week.demo@swcouriers.invalid"

SCHEDULE_DAYS = 6  # today + next 5 calendar days

_LEN_ROUTE_CODE = 20
_LEN_ORDER_ID = 32
_LEN_MASTER_LABEL_ID = 40
_LEN_TRACKING_ID = 40

RECIPIENTS = [
    ("Alex", "Morris", "14 Tooley Street", "London", "SE1 2TU", 51.5045, -0.0865),
    ("Beth", "Clarke", "71 Bermondsey Wall East", "London", "SE16 4TY", 51.4979, -0.0748),
    ("Carlos", "Diaz", "125 The Highway", "London", "E1W 2BQ", 51.5089, -0.0547),
    ("Dana", "Iqbal", "30 Swan Road", "London", "SE16 4JW", 51.4912, -0.0467),
    ("Elena", "Voss", "199 Jamaica Road", "London", "SE16 4TT", 51.4988, -0.0699),
    ("Frank", "Lowe", "22 Horsleydown Lane", "London", "SE1 2LN", 51.5034, -0.0742),
]


def _schedule_dates(base: date, *, count: int = SCHEDULE_DAYS) -> list[date]:
    return [base + timedelta(days=i) for i in range(count)]


def _fit(field: str, value: str, max_len: int) -> str:
    if len(value) > max_len:
        raise SystemExit(f"seed_ryan_fatima_mobile_week: {field} is {len(value)} chars (max {max_len}): {value!r}")
    return value


def _route_code(service_date: date, leg: str, day_tag: str) -> str:
    return _fit(
        "routes.route_code",
        f"{ROUTE_PREFIX}{service_date.strftime('%y%m%d')}-{leg}-{day_tag}",
        _LEN_ROUTE_CODE,
    )


def _order_code(ord_tag: str, seq: int) -> str:
    return _fit("orders.order_id", f"{ORDER_PREFIX}{ord_tag}-{seq:02d}", _LEN_ORDER_ID)


def _tracking_code(ord_tag: str, seq: int) -> str:
    return _fit("delivery_stops.tracking_id", f"{TRACK_PREFIX}{ord_tag}-{seq:02d}", _LEN_TRACKING_ID)


def _master_label(ord_tag: str, seq: int) -> str:
    return _fit("orders.master_label_id", f"ML-RF6-{ord_tag}-{seq}", _LEN_MASTER_LABEL_ID)


async def _clear_seeded_rows() -> None:
    async with get_async_session() as session:
        route_ids = list(
            (await session.execute(select(Route.id).where(Route.route_code.ilike(f"{ROUTE_PREFIX}%")))).scalars().all()
        )
        if route_ids:
            await session.execute(delete(RouteEvent).where(RouteEvent.route_id.in_(route_ids)))
            await session.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
            await session.execute(delete(Route).where(Route.id.in_(route_ids)))

        await session.execute(delete(Order).where(Order.order_id.ilike(f"{ORDER_PREFIX}%")))

        await session.execute(delete(User).where(User.email == CUSTOMER_EMAIL))
        await session.flush()

        org = await session.scalar(select(Organization).where(Organization.reference == ORG_REF))
        if org is not None:
            await session.delete(org)

        await session.commit()
        print("Cleared Ryan/Fatima 6-day mobile week seed (RT-RF6-*, RF6-ORD-*).")


async def _ensure_demo_org(session, *, today: date) -> tuple[Organization, User, PickupAddress]:
    org = await session.scalar(select(Organization).where(Organization.reference == ORG_REF))
    if org is None:
        org = Organization(
            reference=ORG_REF,
            trading_name="Ryan/Fatima Week Demo Org",
            legal_entity_name="Ryan/Fatima Week Demo Org Ltd",
            industry=IndustryType.OTHER,
            company_size=CompanySize.EMPLOYEES_1_10,
            date_of_incorporation=today - timedelta(days=400),
            companies_house_number="RF612345678",
            vat_number="GBRF6123456",
            reg_address_line_1="40 RF6 Demo Wharf",
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
            first_name="Week",
            last_name="DemoCustomer",
            phone="07700900902",
            password_hash=hash_password("UnusedRf6Demo9!"),
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
            PickupAddress.label == "RF6 Return Sender Hub",
        )
    )
    if pickup is None:
        pickup = PickupAddress(
            organization_id=org.id,
            label="RF6 Return Sender Hub",
            line_1="8 Dockside Park",
            line_2="Unit 4",
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


def _report_seed_failure(exc: BaseException) -> None:
    print("\n[!] Ryan/Fatima week seed failed.", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


async def seed_week() -> None:
    await _clear_seeded_rows()

    depot = None
    today = None
    schedule: list[date] = []
    created: list[Route] = []

    async with get_async_session() as session:
        try:
            depot = await resolve_depot(session)
            today = depot_today(depot)
            schedule = _schedule_dates(today)

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
                    route_code=_route_code(service_date, leg, day_tag),
                    route_type=route_type.value,
                    total_stops=stops,
                    status=status.value,
                    estimated_drive_time_min=float(stops) * 15.0,
                    actual_drive_time_min=52.0 if status == RouteStatus.ACTIVE else None,
                    total_distance_km=24.0 if status == RouteStatus.ACTIVE else 18.0,
                    navigation_encoded_polyline="xPoly_rf6_mobile_week",
                    navigation_meta={"seed": SEED_TAG, "leg": leg, "day": day_tag},
                    navigation_fingerprint="pending",
                )
                session.add(route)
                await session.flush()

                route_stops: list[RouteStop] = []
                ord_tag = f"DRV-{leg}-{day_tag}"
                for seq in range(1, stops + 1):
                    fn, ln, line_1, city, postcode, lat, lng = RECIPIENTS[(seq - 1) % len(RECIPIENTS)]
                    order = Order(
                        order_id=_order_code(ord_tag, seq),
                        master_label_id=_master_label(ord_tag, seq),
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
                        tracking_id=_tracking_code(ord_tag, seq),
                        recipient_first_name=fn,
                        recipient_last_name=ln,
                        recipient_phone=f"077009{seq + 900:05d}",
                        recipient_email=f"{fn.lower()}.{ln.lower()}.rf6.{leg.lower()}@example.com",
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
                        estimated_arrival=datetime(
                            service_date.year, service_date.month, service_date.day, 8 + seq, 15, tzinfo=UTC
                        ),
                        actual_arrival=datetime.now(UTC) - timedelta(minutes=12 * seq)
                        if seq in completed_sequences
                        else None,
                        distance_from_prev_km=2.0 + seq * 0.4,
                        duration_from_prev_min=5.0 + seq,
                        status="COMPLETED" if seq in completed_sequences else "READY",
                        stop_flow_type=(
                            RouteStopFlowType.PICKUP.value
                            if route_type == RouteType.PICKUP
                            else RouteStopFlowType.DELIVERY.value
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
                    await session.flush()

                ret_seq = len(route_stops) + 1
                ret_ord_tag = f"RET-{leg}-{day_tag}"
                await append_return_route_stop(
                    session,
                    route=route,
                    route_stops=route_stops,
                    organization_id=org.id,
                    customer_id=customer.id,
                    pickup_address=pickup_addr,
                    order_id=_fit("orders.order_id", f"{ORDER_PREFIX}{ret_ord_tag}", _LEN_ORDER_ID),
                    master_label_id=_fit("orders.master_label_id", f"ML-RF6-{ret_ord_tag}", _LEN_MASTER_LABEL_ID),
                    tracking_id=_fit("delivery_stops.tracking_id", f"{TRACK_PREFIX}{ret_ord_tag}", _LEN_TRACKING_ID),
                    sequence=ret_seq,
                    service_date=service_date,
                    package_count=1,
                    notes=(
                        "Failed delivery return on pickup route (cost-efficient)."
                        if route_type == RouteType.PICKUP
                        else "Failed delivery return on delivery route (cost-efficient)."
                    ),
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

            for day_idx, service_date in enumerate(schedule):
                day_tag = f"D{day_idx}"
                is_today = day_idx == 0
                route_status = RouteStatus.ACTIVE if is_today else RouteStatus.ASSIGNED
                completed = {1} if is_today else set()

                created.append(
                    await build_route(
                        driver=ryan_driver,
                        service_date=service_date,
                        route_type=RouteType.PICKUP,
                        day_tag=day_tag,
                        status=route_status,
                        stops=3,
                        completed_sequences=completed,
                    )
                )
                created.append(
                    await build_route(
                        driver=fatima_driver,
                        service_date=service_date,
                        route_type=RouteType.DELIVERY,
                        day_tag=day_tag,
                        status=route_status,
                        stops=4,
                        completed_sequences=completed,
                    )
                )

            await session.commit()
        except Exception as exc:
            await session.rollback()
            _report_seed_failure(exc)
            raise SystemExit(1) from exc

    last_day = schedule[-1]
    print("=" * 72)
    print("Ryan/Fatima mobile week seed complete.")
    print(f"Depot / timezone day : {depot.code} / today={today} through {last_day}")
    print(f"Ryan  ({RYAN_EMAIL})")
    print(f"  PICKUP routes      : {SCHEDULE_DAYS} days (3 stops + 1 RETURN / 1 pkg each)")
    print(f"Fatima ({FATIMA_EMAIL})")
    print(f"  DELIVERY routes    : {SCHEDULE_DAYS} days (4 stops + 1 RETURN / 1 pkg each)")
    print(f"Today ({today})      : ACTIVE, stop 1 completed on each route")
    print("Routes (sample):")
    for r in created[:4]:
        print(f"  {r.route_code}  driver_route_type={r.route_type}  status={r.status}")
    print(f"  … and {len(created) - 4} more")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Ryan pickup + Fatima delivery routes for today and the next 5 days."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Insert 6 days of routes for Ryan and Fatima")
    sub.add_parser("clear", help="Remove rows created by this script only")
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed_week())
    else:
        asyncio.run(_clear_seeded_rows())


if __name__ == "__main__":
    main()
