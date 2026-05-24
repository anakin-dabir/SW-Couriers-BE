"""Seed a **PICKUP** route with orders, delivery stops, and packages in pickup-leg states.

Use this so the driver mobile app and ``/v1/driver-profile/me/*`` APIs can exercise the
collection flow (``stop_flow_type`` = PICKUP, pre-pickup package statuses, master-label scan)
and one **RETURN** stop (failed delivery parcels routed back to sender on the pickup leg).

Prerequisites (same as ``scripts/demo_driver_mobile.py``):

- Depot ``LDN-001`` exists (e.g. from ``python demo_data.py``).
- A driver user to attach the route to (default: Ryan integration account).

Usage (from repo root)::

  poetry run python scripts/seed_driver_mobile_pickup_route.py seed
  poetry run python scripts/seed_driver_mobile_pickup_route.py seed --today --demote-demo-delivery
  poetry run python scripts/seed_driver_mobile_pickup_route.py clear

``seed`` is **idempotent**: stable route/org/order prefixes are removed and re-inserted each run.
``clear`` deletes the same rows **without** requiring a manifest (manifest is optional bookkeeping).

``--demote-demo-delivery`` sets ``demo_driver_mobile`` routes (``route_code`` LIKE ``RT-DMR-%``) on the
**same** ``service_date`` from ACTIVE → ASSIGNED so ``GET .../routes/today`` prefers this pickup route
when both would otherwise be ACTIVE.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import app.models  # noqa: F401

from sqlalchemy import delete, func, select, update

from app.core.database import get_async_session
from app.common.enums import UserRole, UserStatus, UserTitle
from app.core.security import hash_password
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RoutePlanStatus, RouteStatus, RouteStopStatus
from app.modules.planning.models import Route, RoutePlan, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

from scripts.fe_demo_lib import append_return_route_stop

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "driver_mobile_pickup_seed_manifest.json"

# Stable ids across runs (column limits respected).
_SEED_TAG = "PKUDM01"
_ROUTE_CODE = "RT-PKU-PKUDM01"
_ORG_REF = "DM-PKU-PKUDM01"
_ORDER_PREFIX = "DMO-PKU-PKUDM01"
_CUSTOMER_EMAIL = "pickup.demo.customer@swcouriers.invalid"

RYAN_EMAIL_DEFAULT = "ryan.obrien@swcouriers.co.uk"

_LEN_ORDER_ID = 32
_LEN_MASTER_LABEL_ID = 40
_LEN_TRACKING_ID = 40


def _money(value: str) -> Decimal:
    return Decimal(value)


def _fit(field: str, value: str, max_len: int) -> str:
    if len(value) > max_len:
        raise SystemExit(f"{field} is {len(value)} chars (max {max_len}): {value!r}")
    return value


def _incorporation_years_ago(today: date, *, years: int = 2) -> date:
    y = today.year - years
    try:
        return today.replace(year=y)
    except ValueError:
        return today.replace(year=y, day=28)


def _report_failure(exc: BaseException) -> None:
    print("\n[!] Pickup route seed failed.", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


async def _ensure_depot(session) -> Depot:
    depot = await session.scalar(select(Depot).where(Depot.code == "LDN-001"))
    if depot is None:
        raise SystemExit("Depot LDN-001 not found. Run `python demo_data.py` (or equivalent) first.")
    return depot


async def _resolve_driver(session, *, driver_email: str) -> Driver:
    user = await session.scalar(select(User).where(User.email == driver_email))
    if user is None:
        raise SystemExit(f"No user with email {driver_email!r}. Create/link a driver first.")
    driver = await session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        raise SystemExit(f"No drivers row for user {driver_email!r}.")
    return driver


async def _delete_pickup_seed_rows(session) -> None:
    """Remove prior pickup seed data (stable codes). Safe to call repeatedly.

    Order matters: ``routes`` first (cascade deletes ``route_stops`` / ``route_events``), then
    ``orders`` (cascade deletes ``delivery_stops`` / ``packages``), then ``organizations``.
    """
    route = await session.scalar(select(Route).where(Route.route_code == _ROUTE_CODE))
    if route is not None:
        await session.delete(route)
        await session.flush()

    await session.execute(delete(Order).where(Order.order_id.like(f"{_ORDER_PREFIX}-%")))
    await session.flush()

    org = await session.scalar(select(Organization).where(Organization.reference == _ORG_REF))
    if org is not None:
        await session.delete(org)
        await session.flush()


async def _ensure_vehicle_for_driver(session, *, driver: Driver, depot: Depot) -> None:
    """Assign an existing depot vehicle or create a minimal seed vehicle (no terminal failure)."""
    if driver.vehicle_id:
        return

    reg = _fit("vehicles.registration_number", f"PKU-{_SEED_TAG}", 20)
    existing_reg = await session.scalar(select(Vehicle).where(Vehicle.registration_number == reg))
    if existing_reg is not None:
        driver.vehicle_id = existing_reg.id
        await session.flush()
        return

    any_depot = await session.scalar(select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1))
    if any_depot is not None:
        driver.vehicle_id = any_depot.id
        await session.flush()
        return

    vehicle = Vehicle(
        registration_number=reg,
        depot_id=depot.id,
        make="Ford",
        model="Transit",
        year=datetime.now(UTC).year - 2,
    )
    session.add(vehicle)
    await session.flush()
    driver.vehicle_id = vehicle.id
    await session.flush()


async def _demote_demo_delivery_routes(session, *, driver_id: str, service_date: date, depot_id: str) -> int:
    """ACTIVE → ASSIGNED for demo_driver_mobile routes on this depot day (same driver)."""
    subq = (
        select(Route.id)
        .join(RoutePlan, Route.plan_id == RoutePlan.id)
        .where(
            Route.driver_id == driver_id,
            Route.status == RouteStatus.ACTIVE.value,
            Route.route_code.like("RT-DMR-%"),
            RoutePlan.depot_id == depot_id,
            RoutePlan.service_date == service_date,
        )
    )
    res = await session.execute(
        update(Route).where(Route.id.in_(subq)).values(status=RouteStatus.ASSIGNED.value)
    )
    return res.rowcount or 0


async def seed(
    *,
    driver_email: str,
    service_date: date | None,
    use_today: bool,
    use_tomorrow: bool,
    demote_demo_delivery: bool,
    stops: int,
    route_status: str,
) -> None:
    manifest_data: dict | None = None
    async with get_async_session() as session:
        try:
            depot = await _ensure_depot(session)
            tz_name = depot.timezone or "Europe/London"
            tz = ZoneInfo(tz_name)
            today_local = datetime.now(tz).date()

            if service_date is not None:
                target_day = service_date
            elif use_today:
                target_day = today_local
            elif use_tomorrow:
                target_day = today_local + timedelta(days=1)
            else:
                target_day = today_local + timedelta(days=1)

            driver = await _resolve_driver(session, driver_email=driver_email)
            driver.depot_id = depot.id
            await session.flush()

            await _delete_pickup_seed_rows(session)
            await _ensure_vehicle_for_driver(session, driver=driver, depot=depot)

            if demote_demo_delivery:
                n = await _demote_demo_delivery_routes(
                    session, driver_id=driver.id, service_date=target_day, depot_id=depot.id
                )
                if n:
                    print(f"[i] Demoted {n} ACTIVE demo delivery route(s) (RT-DMR-*) to ASSIGNED on {target_day}.")

            plan = await session.scalar(
                select(RoutePlan).where(
                    RoutePlan.depot_id == depot.id,
                    RoutePlan.service_date == target_day,
                )
            )
            plan_owned = False
            if plan is None:
                plan = RoutePlan(
                    service_date=target_day,
                    depot_id=depot.id,
                    status=RoutePlanStatus.READY.value,
                )
                session.add(plan)
                await session.flush()
                plan_owned = True

            org = Organization(
                reference=_fit("organizations.reference", _ORG_REF, 20),
                trading_name=f"Pickup Demo Org ({_SEED_TAG})",
                legal_entity_name=f"Pickup Demo Org ({_SEED_TAG})",
                companies_house_number=_fit("companies_house_number", f"PK{_SEED_TAG}", 100),
                vat_number=_fit("vat_number", f"GBPK{_SEED_TAG}", 50),
                date_of_incorporation=_incorporation_years_ago(target_day),
                industry=IndustryType.OTHER,
                company_size=CompanySize.EMPLOYEES_1_10,
                reg_address_line_1="42 Pickup Wharf",
                reg_city="London",
                reg_postcode="SE16 7FZ",
                status=OrganizationStatus.ACTIVE,
            )
            session.add(org)
            await session.flush()

            cust = await session.scalar(select(User).where(User.email == _CUSTOMER_EMAIL))
            if cust is None:
                cust = User(
                    email=_CUSTOMER_EMAIL,
                    phone="07700900101",
                    first_name="Morgan",
                    last_name="Pickup",
                    title=UserTitle.MS,
                    password_hash=hash_password("UnusedPickupDemo9!"),
                    role=UserRole.CUSTOMER_B2B,
                    status=UserStatus.ACTIVE,
                    email_verified=True,
                    force_password_change=False,
                )
                session.add(cust)
                await session.flush()

            pickup_addr = await session.scalar(
                select(PickupAddress).where(
                    PickupAddress.organization_id == org.id,
                    PickupAddress.label == "PKU Return Sender",
                )
            )
            if pickup_addr is None:
                pickup_addr = PickupAddress(
                    organization_id=org.id,
                    label="PKU Return Sender",
                    line_1="5 Dockside Business Park",
                    line_2="Unit 12",
                    city="London",
                    state="Greater London",
                    postcode="SE16 3LN",
                    country="United Kingdom",
                    latitude=51.4972,
                    longitude=-0.0619,
                    is_default=True,
                    created_by_user_id=cust.id,
                )
                session.add(pickup_addr)
                await session.flush()

            recipients = [
                ("Casey", "Ng", "10 Pickup Lane", None, "London", "SE1 2AA", 51.5045, -0.0865),
                ("Riley", "Fox", "88 Warehouse Row", "Unit B", "London", "E1W 3NQ", 51.5089, -0.0547),
                ("Jamie", "Reed", "200 Jamaica Road", None, "London", "SE16 4TT", 51.4988, -0.0699),
            ]

            route = Route(
                plan_id=plan.id,
                driver_id=driver.id,
                vehicle_id=driver.vehicle_id,
                route_code=_ROUTE_CODE,
                route_type="PICKUP",
                total_stops=stops,
                status=route_status,
                estimated_drive_time_min=float(stops) * 22.0,
                actual_drive_time_min=None,
                total_distance_km=12.5 + stops * 3.2,
                navigation_encoded_polyline="xPoly_pickup_demo_placeholder",
                navigation_meta={"demo": True, "seed": "pickup_route"},
                navigation_fingerprint="pending",
            )
            session.add(route)
            await session.flush()

            route_stops: list[RouteStop] = []
            for seq in range(1, stops + 1):
                oid = _fit("orders.order_id", f"{_ORDER_PREFIX}-{seq}", _LEN_ORDER_ID)
                ml = _fit("orders.master_label_id", f"ML-{_SEED_TAG}-{seq}", _LEN_MASTER_LABEL_ID)
                order = Order(
                    order_id=oid,
                    master_label_id=ml,
                    organization_id=org.id,
                    customer_id=cust.id,
                    subtotal=_money("35.00"),
                    vat_amount=_money("7.00"),
                    total_amount=_money("42.00"),
                    status=OrderStatus.ENROUTE_PICKUP,
                )
                session.add(order)
                await session.flush()

                fn, ln, l1, l2, city, pc, lat, lng = recipients[(seq - 1) % len(recipients)]
                trk = _fit("delivery_stops.tracking_id", f"TRK-{_SEED_TAG}-{seq}", _LEN_TRACKING_ID)
                dstop = DeliveryStop(
                    order_id=order.id,
                    tracking_id=trk,
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{seq + 200:05d}",
                    recipient_email=_fit(
                        "delivery_stops.recipient_email",
                        f"{fn.lower()}.{ln.lower()}-{_SEED_TAG.lower()}@example.com",
                        255,
                    ),
                    line_1=l1,
                    line_2=l2,
                    city=city,
                    postcode=pc,
                    latitude=lat,
                    longitude=lng,
                    service_tier=DeliveryServiceTier.STANDARD,
                    signature_required=False,
                    safe_place_allowed=False,
                    status=DeliveryStopStatus.ENROUTE_PICKUP,
                    scheduled_for=target_day,
                )
                session.add(dstop)
                await session.flush()

                rs = RouteStop(
                    route_id=route.id,
                    delivery_stop_id=dstop.id,
                    sequence=seq,
                    estimated_arrival=datetime(
                        target_day.year,
                        target_day.month,
                        target_day.day,
                        min(8 + seq, 20),
                        (10 * seq) % 60,
                        tzinfo=UTC,
                    ),
                    distance_from_prev_km=2.1 + seq * 0.4,
                    duration_from_prev_min=8.0 + seq * 2,
                    status=RouteStopStatus.READY.value,
                    stop_flow_type="PICKUP",
                    notes=None,
                )
                session.add(rs)
                await session.flush()
                route_stops.append(rs)

                for _ in range(2):
                    session.add(
                        Package(
                            order_id=order.id,
                            delivery_stop_id=dstop.id,
                            length_cm=40,
                            width_cm=30,
                            height_cm=25,
                            weight_kg=4.0,
                            declared_weight_kg=4.2,
                            declared_value=_money("85.00"),
                            status=PackageStatus.ENROUTE_PICKUP,
                            is_damaged=False,
                            price_breakdown={"pickup_line": "10.00"},
                        )
                    )
                await session.flush()

            ret_seq = len(route_stops) + 1
            await append_return_route_stop(
                session,
                route=route,
                route_stops=route_stops,
                organization_id=org.id,
                customer_id=cust.id,
                pickup_address=pickup_addr,
                order_id=_fit("orders.order_id", f"{_ORDER_PREFIX}-RET", _LEN_ORDER_ID),
                master_label_id=_fit("orders.master_label_id", f"ML-{_SEED_TAG}-RET", _LEN_MASTER_LABEL_ID),
                tracking_id=_fit("delivery_stops.tracking_id", f"TRK-{_SEED_TAG}-RET", _LEN_TRACKING_ID),
                sequence=ret_seq,
                service_date=target_day,
                route_stop_status=RouteStopStatus.READY.value,
                notes="Failed delivery return — cost-efficient on today's pickup route.",
            )

            fp = compute_route_navigation_fingerprint(
                sequences_and_route_stop_ids=[(rs.sequence, rs.id) for rs in route_stops]
            )
            route.navigation_fingerprint = fp

            await session.commit()

            manifest_data = {
                "seeded_at": datetime.now(UTC).isoformat(),
                "route_code": _ROUTE_CODE,
                "route_id": str(route.id),
                "plan_id": str(plan.id),
                "plan_created_by_script": plan_owned,
                "service_date": target_day.isoformat(),
                "driver_id": str(driver.id),
                "driver_email": driver_email,
                "organization_id": str(org.id),
                "customer_user_id": str(cust.id),
                "depot_timezone": tz_name,
            }
        except Exception as e:
            await session.rollback()
            _report_failure(e)
            raise SystemExit(1) from e

    if manifest_data is None:
        raise SystemExit(1)

    MANIFEST_PATH.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    print()
    print("=" * 72)
    print("Pickup route seed complete.")
    print(f"  Route code     : {_ROUTE_CODE}  (route_type=PICKUP)")
    print(f"  Service date   : {manifest_data['service_date']} ({manifest_data['depot_timezone']})")
    print(f"  Route status   : {route_status}")
    print(
        f"  Stops          : {stops} pickup + 1 RETURN (failed delivery → sender, RETURN_IN_TRANSIT)"
    )
    print(f"  Driver email   : {driver_email}")
    print(f"  Manifest       : {MANIFEST_PATH.resolve()}")
    print()
    print("Try:")
    print("  GET /v1/driver-profile/me/routes/today")
    print("  GET /v1/driver-profile/me/routes/board?tab=upcoming&type=PICKUP")
    print("  GET /v1/driver-profile/me/routes/{route_id}/stops")
    print("=" * 72)


async def clear_seed() -> None:
    """Remove stable pickup seed rows. Idempotent: works with or without ``manifest`` file."""
    manifest_plan_id: str | None = None
    manifest_plan_owned: bool = False
    if MANIFEST_PATH.is_file():
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            manifest_plan_id = data.get("plan_id")
            manifest_plan_owned = bool(data.get("plan_created_by_script"))
        except (json.JSONDecodeError, OSError):
            pass

    async with get_async_session() as session:
        await _delete_pickup_seed_rows(session)

        if manifest_plan_id and manifest_plan_owned:
            remaining = await session.scalar(
                select(func.count()).select_from(Route).where(Route.plan_id == manifest_plan_id)
            )
            if remaining == 0:
                plan = await session.get(RoutePlan, manifest_plan_id)
                if plan is not None:
                    await session.delete(plan)

        await session.commit()

    MANIFEST_PATH.unlink(missing_ok=True)
    print("Cleared pickup seed data (stable route / orders / org). Manifest removed if present.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed or clear a dummy PICKUP route for driver mobile / API testing.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seed = sub.add_parser("seed", help="Insert pickup route + orders/stops/packages")
    p_seed.set_defaults(use_today=False, use_tomorrow=False)
    p_seed.add_argument(
        "--driver-email",
        default=RYAN_EMAIL_DEFAULT,
        help=f"Driver login email (default: {RYAN_EMAIL_DEFAULT})",
    )
    g = p_seed.add_mutually_exclusive_group()
    g.add_argument(
        "--service-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        metavar="YYYY-MM-DD",
        help="Plan service date (overrides --today / default)",
    )
    g.add_argument("--today", dest="use_today", action="store_true", help="Use depot-local today")
    g_seed = p_seed.add_mutually_exclusive_group()
    g_seed.add_argument(
        "--tomorrow",
        dest="use_tomorrow",
        action="store_true",
        help="Use depot-local tomorrow (default if no --service-date / --today)",
    )
    p_seed.add_argument(
        "--demote-demo-delivery",
        action="store_true",
        help="Set ACTIVE demo_driver_mobile routes (RT-DMR-*) on same day to ASSIGNED",
    )
    p_seed.add_argument("--stops", type=int, default=2, help="Number of pickup stops (default 2)")
    p_seed.add_argument(
        "--route-status",
        default=RouteStatus.ACTIVE.value,
        choices=[RouteStatus.ACTIVE.value, RouteStatus.ASSIGNED.value],
        help="Route row status (default ACTIVE so /routes/today picks it when alone)",
    )

    sub.add_parser("clear", help="Remove data from last manifest")

    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(
            seed(
                driver_email=args.driver_email,
                service_date=args.service_date,
                use_today=getattr(args, "use_today", False),
                use_tomorrow=getattr(args, "use_tomorrow", False),
                demote_demo_delivery=args.demote_demo_delivery,
                stops=max(1, min(args.stops, 8)),
                route_status=args.route_status,
            )
        )
    else:
        asyncio.run(clear_seed())


if __name__ == "__main__":
    main()
