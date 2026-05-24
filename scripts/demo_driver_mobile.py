"""Seed / clear rich demo data for the **driver mobile app** only.

Targets the FE integration account (``GET /v1/driver-profile/me``):

  - ``data.user_id`` — auth user id (JWT subject); seed matches this.
  - ``data.id`` — driver row primary key (use for anything keyed by ``driver_id``).
  - ``data.driver_code`` — e.g. ``DR-043``.

Expected ids for Ryan O'Brien (integration env):

  User (JWT)   : 02590528-b113-4267-bcf9-aef4a3343bfe
  Driver (PK)  : 449b0693-b1ad-4714-944f-082464098e89

Requires:
  - Ryan already has a ``drivers`` row linked to this user (e.g. ``python demo_data.py --count 8``).
  - Depot ``LDN-001`` (from ``demo_data.py``) so depot-local dates match Bermondsey.

Usage (from repo root):

  poetry run python scripts/demo_driver_mobile.py seed
  poetry run python scripts/demo_driver_mobile.py seed --force    # optional; seed is idempotent
  poetry run python scripts/demo_driver_mobile.py clear

Subcommand ``seed`` or ``clear`` is required (running without them only prints argparse help).

``seed`` is **idempotent**: it removes any prior demo created by this script (via manifest
if present, plus stable tagged rows) and re-inserts fresh data. Safe to run repeatedly.

After ``seed`` succeeds, the manifest is written to ``demo_driver_mobile_manifest.json``
in the **repository root** (same folder as ``demo_data.py``).

``clear`` removes only IDs recorded in ``demo_driver_mobile_manifest.json`` at the **repo root**
(does not delete Ryan). You usually do not need it unless you want to wipe demo data
without re-seeding.

Today's ACTIVE route (``TD``) includes one **RETURN** stop (``stop_flow_type`` = RETURN,
packages in ``RETURN_IN_TRANSIT``) for failed-delivery parcels routed back to the sender.
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

from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError

from app.core.database import get_async_session
from app.core.security import hash_password
from app.common.enums import UserRole, UserStatus, UserTitle
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package, StopNote, StopNoteImage
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RoutePlanStatus
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

from scripts.fe_demo_lib import append_return_route_stop


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_FILENAME = "demo_driver_mobile_manifest.json"
# Written here after `seed` so it appears next to `demo_data.py` in the IDE.
MANIFEST_PATH = REPO_ROOT / MANIFEST_FILENAME
# Older layout (script directory); still read for load/clear if present.
LEGACY_MANIFEST_PATH = Path(__file__).resolve().parent / MANIFEST_FILENAME

RYAN_USER_ID = "02590528-b113-4267-bcf9-aef4a3343bfe"
# GET /driver-profile/me → data.id (driver primary key). Warn if DB differs (other envs may vary).
RYAN_DRIVER_ID_EXPECTED = "449b0693-b1ad-4714-944f-082464098e89"
RYAN_EMAIL = "ryan.obrien@swcouriers.co.uk"

# Stable tag so every run targets the same org / route_code / order_id prefix (idempotent re-seed).
DEMO_MOBILE_SUFFIX = "DMOBSEED"
DEMO_CUSTOMER_EMAIL = "demo.cust.dm-mobile@swcouriers.invalid"

# Demo org is tagged in trading_name; ``organizations.reference`` is VARCHAR(20).

# DB column limits we must respect (PostgreSQL / SQLAlchemy models).
_LEN_ORG_REFERENCE = 20
_LEN_ORDER_ID = 32
_LEN_MASTER_LABEL_ID = 40
_LEN_TRACKING_ID = 40
_LEN_ROUTE_CODE = 20
_LEN_USER_EMAIL = 255


def _money(value: str) -> Decimal:
    return Decimal(value)


def _fit(field: str, value: str, max_len: int) -> str:
    if len(value) > max_len:
        raise SystemExit(f"demo_driver_mobile seed: {field} is {len(value)} chars (max {max_len}): {value!r}")
    return value


def _incorporation_years_ago(today: date, *, years: int = 3) -> date:
    y = today.year - years
    try:
        return today.replace(year=y)
    except ValueError:
        return today.replace(year=y, day=28)


def _load_manifest() -> dict | None:
    for path in (MANIFEST_PATH, LEGACY_MANIFEST_PATH):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_manifest(data: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


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


async def _purge_stable_driver_mobile_demo(
    session,
    *,
    depot_id: str,
    driver_id: str,
    suffix: str,
) -> None:
    """Remove rows from a previous run of this script (no manifest required).

    Deletes routes whose ``route_code`` matches ``RT-DMR-%`` for this driver and depot
    (all seeds from this script use that prefix), drops route plans that become empty,
    removes deterministic demo orders (``DMO-{suffix[:8]}-%``), removes org ``DM-{suffix[:8]}``,
    and deletes the fixed demo customer user by email.
    """
    like_pattern = f"RT-DMR-{suffix[:8]}-%"
    routes = (
        await session.scalars(
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                RoutePlan.depot_id == depot_id,
                Route.route_code.like(like_pattern),
            )
        )
    ).all()
    plan_ids: set[str] = {str(r.plan_id) for r in routes}
    for r in routes:
        await session.delete(r)
    if routes:
        await session.flush()

    # Orphan orders from crashed runs (routes gone but orders/order_ids still present).
    order_pat = f"DMO-{suffix[:8]}-%"
    await session.execute(delete(Order).where(Order.order_id.like(order_pat)))
    await session.flush()

    for pid in plan_ids:
        remaining = await session.scalar(select(func.count()).select_from(Route).where(Route.plan_id == pid))
        if remaining == 0:
            plan = await session.get(RoutePlan, pid)
            if plan is not None:
                await session.delete(plan)
    if plan_ids:
        await session.flush()

    org_ref = _fit("organizations.reference (purge)", f"DM-{suffix[:8]}", _LEN_ORG_REFERENCE)
    org = await session.scalar(select(Organization).where(Organization.reference == org_ref))
    if org is not None:
        await session.delete(org)
        await session.flush()

    await session.execute(delete(User).where(User.email == DEMO_CUSTOMER_EMAIL))
    await session.flush()


async def _delete_route_if_exists_by_code(session, *, route_code: str) -> None:
    """``routes.route_code`` is globally unique — remove any leftover row before re-seeding."""
    row = await session.scalar(select(Route).where(Route.route_code == route_code))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def clear_demo_data() -> None:
    manifest = _load_manifest()
    if not manifest:
        print(
            "No manifest found. Look for "
            f"{MANIFEST_PATH.resolve()} (or legacy {LEGACY_MANIFEST_PATH.resolve()}) "
            "after a successful `poetry run python scripts/demo_driver_mobile.py seed`."
        )
        return

    async with get_async_session() as session:
        route_ids = manifest.get("route_ids") or []
        plan_ids = manifest.get("route_plan_ids") or []
        org_id = manifest.get("organization_id")
        customer_id = manifest.get("demo_customer_user_id")

        for rid in route_ids:
            row = await session.get(Route, rid)
            if row:
                await session.delete(row)
        # Flush routes before plans: Route.plan_id FK references route_plans.id.
        # Explicit flush guarantees DB sees route deletions (+ cascade to route_stops/route_events)
        # before plan rows are touched, regardless of UoW topological sort.
        await session.flush()

        for pid in plan_ids:
            row = await session.get(RoutePlan, pid)
            if row:
                await session.delete(row)
        await session.flush()

        # Belt-and-suspenders: wipe demo orders by stable id pattern so they never orphan if
        # the org_id is stale or missing from an older manifest.
        suffix = DEMO_MOBILE_SUFFIX
        await session.execute(delete(Order).where(Order.order_id.like(f"DMO-{suffix[:8]}-%")))
        await session.flush()

        if org_id:
            row = await session.get(Organization, org_id)
            if row:
                await session.delete(row)

        if customer_id:
            await session.execute(delete(User).where(User.id == customer_id))

        await session.commit()

    MANIFEST_PATH.unlink(missing_ok=True)
    LEGACY_MANIFEST_PATH.unlink(missing_ok=True)
    print(f"Cleared demo_driver_mobile data (manifest removed from repo root if present).")


def _report_seed_failure(exc: BaseException) -> None:
    print("\n[!] Driver mobile seed failed.", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < 6:
        if isinstance(cur, DBAPIError):
            orig = cur.orig
            if orig is not None:
                print(f"  DB driver detail: {orig}", file=sys.stderr)
        cur = cur.__cause__ if cur.__cause__ is not None else getattr(cur, "__context__", None)
        depth += 1


async def seed_demo_data(*, force: bool) -> None:
    if force:
        print("[i] --force is optional: seed always replaces prior driver-mobile demo data.")

    if _load_manifest():
        print("[=] Replacing prior seed (manifest on disk)…")
        await clear_demo_data()

    suffix = DEMO_MOBILE_SUFFIX
    now = datetime.now(UTC)

    async with get_async_session() as session:
        try:
            # ── Depot timezone → local calendar dates ───────────────────────────
            depot = await session.scalar(select(Depot).where(Depot.code == "LDN-001"))
            if depot is None:
                raise SystemExit(
                    "Depot LDN-001 not found. Run `python demo_data.py` first to create region/depot."
                )
            tz_name = depot.timezone or "Europe/London"
            tz = ZoneInfo(tz_name)
            today_local = datetime.now(tz).date()
            past_day = _prev_weekday(today_local, steps=1)
            tomorrow_day = _next_weekday(today_local, steps=1)
    
            # ── Ryan user (fixed ID for FE JWT / fixtures) ───────────────────────
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
                print(f"[+] Created user {RYAN_EMAIL} ({RYAN_USER_ID})")
            else:
                user.email = RYAN_EMAIL
                user.first_name = "Ryan"
                user.last_name = "O'Brien"
                user.phone = user.phone or "07700900107"
                user.role = UserRole.DRIVER
                user.status = UserStatus.ACTIVE
                print(f"[=] Updated existing user {RYAN_EMAIL}")
            await session.flush()

            driver = await session.scalar(select(Driver).where(Driver.user_id == RYAN_USER_ID))
            if driver is None:
                raise SystemExit(
                    "No driver row for Ryan's user_id. Create one via admin onboarding or "
                    "`python demo_data.py --count 8` so Ryan exists before running this seed."
                )
    
            if str(driver.id) != RYAN_DRIVER_ID_EXPECTED:
                print(
                    f"[!] Driver id is {driver.id} (expected {RYAN_DRIVER_ID_EXPECTED} for integration FE). "
                    "Seeding still attaches routes to this driver row."
                )
    
            driver.depot_id = depot.id
            if driver.vehicle_id is None:
                veh = await session.scalar(
                    select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1)
                )
                if veh:
                    driver.vehicle_id = veh.id
            await session.flush()
    
            vehicle_id = driver.vehicle_id
            if vehicle_id is None:
                raise SystemExit("Driver has no vehicle and depot has no vehicles; assign a vehicle first.")

            await _purge_stable_driver_mobile_demo(session, depot_id=depot.id, driver_id=driver.id, suffix=suffix)

            # ── Demo org + customer (get-or-create after purge; belts-and-suspenders) ──
            # organizations.reference is VARCHAR(20); keep total length ≤ 20.
            org_ref = _fit("organizations.reference", f"DM-{suffix[:8]}", _LEN_ORG_REFERENCE)
            org = await session.scalar(select(Organization).where(Organization.reference == org_ref))
            if org is None:
                org = Organization(
                    reference=org_ref,
                    trading_name=f"Demo Mobile Routes Ltd ({suffix})",
                    legal_entity_name=f"Demo Mobile Routes Ltd ({suffix})",
                    companies_house_number=_fit("companies_house_number", f"DM{suffix[:8]}", 100),
                    vat_number=_fit("vat_number", f"GBDM{suffix[:8]}", 50),
                    date_of_incorporation=_incorporation_years_ago(today_local, years=3),
                    industry=IndustryType.OTHER,
                    company_size=CompanySize.EMPLOYEES_1_10,
                    reg_address_line_1="88 Demo Wharf Road",
                    reg_city="London",
                    reg_postcode="SE16 7FZ",
                    status=OrganizationStatus.ACTIVE,
                )
                session.add(org)
                await session.flush()
            else:
                org.trading_name = f"Demo Mobile Routes Ltd ({suffix})"
                org.status = OrganizationStatus.ACTIVE
                await session.flush()

            cust_email = _fit("demo customer email", DEMO_CUSTOMER_EMAIL, _LEN_USER_EMAIL)
            customer = await session.scalar(select(User).where(User.email == cust_email))
            if customer is None:
                customer = User(
                    email=cust_email,
                    phone="07700900999",
                    first_name="Alex",
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
            else:
                customer.role = UserRole.CUSTOMER_B2B
                customer.status = UserStatus.ACTIVE
                await session.flush()

            pickup_addr = await session.scalar(
                select(PickupAddress).where(
                    PickupAddress.organization_id == org.id,
                    PickupAddress.label == "DM Return Sender",
                )
            )
            if pickup_addr is None:
                pickup_addr = PickupAddress(
                    organization_id=org.id,
                    label="DM Return Sender",
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
                ("Jordan", "Park", "71 Bermondsey Wall East", None, "London", "SE16 4TY", 51.4979, -0.0748),
                ("Samira", "Hassan", "14 Tooley Street", "Bankside Arches", "London", "SE1 2TU", 51.5045, -0.0865),
                ("Chris", "Walsh", "125 The Highway", None, "London", "E1W 2BQ", 51.5089, -0.0547),
                ("Priya", "Nair", "30 Swan Road", None, "London", "SE16 4JW", 51.4912, -0.0467),
                ("Tom", "Greenwood", "22 Horsleydown Lane", None, "London", "SE1 2LN", 51.5034, -0.0742),
                ("Nina", "Kravitz", "199 Jamaica Road", None, "London", "SE16 4TT", 51.4988, -0.0699),
                ("Marcus", "Bell", "40 Abbey Street", None, "London", "SE1 3LX", 51.4985, -0.0825),
                ("Helena", "Costa", "9 Dockhead Court", None, "London", "SE16 7FJ", 51.4938, -0.0725),
                ("Wei", "Chen", "150 Tower Bridge Road", None, "London", "SE1 3LW", 51.5032, -0.0799),
            ]
    
            manifest: dict = {
                "seeded_at": now.isoformat(),
                "ryan_user_id": RYAN_USER_ID,
                "ryan_driver_id": str(driver.id),
                "demo_customer_user_id": customer.id,
                "organization_id": org.id,
                "route_plan_ids": [],
                "route_ids": [],
            }
    
            def _mk_order(prefix: str, idx: int) -> Order:
                # Shorter than SWC-DMR-* so we stay ≤32 / ≤40 even with large idx.
                oid = _fit("orders.order_id", f"DMO-{suffix[:8]}-{prefix}-{idx}", _LEN_ORDER_ID)
                ml = _fit("orders.master_label_id", f"ML-{suffix[:8]}-{prefix}-{idx}", _LEN_MASTER_LABEL_ID)
                return Order(
                    order_id=oid,
                    master_label_id=ml,
                    organization_id=org.id,
                    customer_id=customer.id,
                    subtotal=_money("42.50"),
                    vat_amount=_money("8.50"),
                    total_amount=_money("51.00"),
                    status=OrderStatus.DELIVERY_IN_PROGRESS,
                )

            def _mk_delivery_stop(order: Order, prefix: str, idx: int) -> DeliveryStop:
                """Build stop after ``order`` has been flushed so ``order.id`` (FK target) is set."""
                trk = _fit("delivery_stops.tracking_id", f"TRK-{suffix[:8]}-{prefix}-{idx}", _LEN_TRACKING_ID)
                fn, ln, l1, l2, city, pc, lat, lng = recipients[idx % len(recipients)]
                return DeliveryStop(
                    order_id=order.id,
                    tracking_id=trk,
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{idx + 100:05d}",
                    recipient_email=_fit(
                        "delivery_stops.recipient_email",
                        f"{fn.lower()}.{ln.lower()}-{suffix.lower()}@example.com",
                        255,
                    ),
                    line_1=l1,
                    line_2=l2,
                    city=city,
                    postcode=pc,
                    latitude=lat,
                    longitude=lng,
                    service_tier=DeliveryServiceTier.STANDARD,
                    signature_required=(prefix == "TD" and idx == 3),
                    safe_place_allowed=True,
                    status=DeliveryStopStatus.OUT_FOR_DELIVERY,
                    scheduled_for=today_local if prefix == "TD" else (past_day if prefix == "PD" else tomorrow_day),
                )
    
            async def _build_route(
                *,
                tag: str,
                service_date,
                route_status: str,
                stop_specs: list[tuple[str, int]],
                completed_sequences: set[int],
                actual_drive_min: float | None,
                distance_km: float | None,
                seed_notes_on_sequence: int | None,
                seed_pkg_issue_note: bool,
                include_return_stop: bool = False,
            ) -> Route:
                # One plan per depot per calendar day (uq_route_plans_depot_date). Reuse if
                # demo_data.py or an earlier run already created it — append another Route for Ryan.
                plan = await session.scalar(
                    select(RoutePlan).where(
                        RoutePlan.depot_id == depot.id,
                        RoutePlan.service_date == service_date,
                    )
                )
                created_new_plan = plan is None
                if created_new_plan:
                    plan = RoutePlan(
                        service_date=service_date,
                        depot_id=depot.id,
                        status=RoutePlanStatus.READY.value,
                    )
                    session.add(plan)
                    await session.flush()
                    # Only track plans we own so ``clear`` does not delete a shared depot-day plan.
                    manifest["route_plan_ids"].append(plan.id)

                route_code = _fit("routes.route_code", f"RT-DMR-{suffix[:8]}-{tag}", _LEN_ROUTE_CODE)
                await _delete_route_if_exists_by_code(session, route_code=route_code)

                route = Route(
                    plan_id=plan.id,
                    driver_id=driver.id,
                    vehicle_id=vehicle_id,
                    route_code=route_code,
                    route_type="DELIVERY",
                    total_stops=len(stop_specs),
                    status=route_status,
                    estimated_drive_time_min=float(len(stop_specs)) * 18.5,
                    actual_drive_time_min=actual_drive_min,
                    total_distance_km=distance_km,
                    total_duration_min=actual_drive_min,
                    navigation_encoded_polyline="xPoly_demo_encoded_polyline_placeholder",
                    navigation_meta={"demo": True, "provider": "seed_script"},
                    navigation_fingerprint="pending",
                )
                session.add(route)
                await session.flush()
                manifest["route_ids"].append(route.id)
    
                route_stops: list[RouteStop] = []
                seq_delivery: dict[int, DeliveryStop] = {}
                seq_packages: dict[int, list[Package]] = {}
    
                for seq, (prefix, idx) in enumerate(stop_specs, start=1):
                    order = _mk_order(prefix, idx)
                    session.add(order)
                    await session.flush()
                    dstop = _mk_delivery_stop(order, prefix, idx)
                    session.add(dstop)
                    await session.flush()
    
                    st_completed = seq in completed_sequences
                    if st_completed:
                        dstop.status = DeliveryStopStatus.DELIVERED
    
                    rs_status = "COMPLETED" if st_completed else "READY"
                    rs = RouteStop(
                        route_id=route.id,
                        delivery_stop_id=dstop.id,
                        sequence=seq,
                        estimated_arrival=datetime(
                            service_date.year,
                            service_date.month,
                            service_date.day,
                            (8 + seq) % 24,
                            (15 * seq) % 60,
                            tzinfo=UTC,
                        ),
                        actual_arrival=datetime.now(UTC) - timedelta(minutes=30 * seq) if st_completed else None,
                        distance_from_prev_km=2.4 + seq * 0.35,
                        duration_from_prev_min=6.0 + seq,
                        status=rs_status,
                        stop_flow_type="DELIVERY",
                        notes=None if seq != seed_notes_on_sequence else "Knock loudly — concierge desk.",
                    )
                    session.add(rs)
                    await session.flush()
                    route_stops.append(rs)
                    seq_delivery[seq] = dstop
    
                    pkgs: list[Package] = []
                    for p_idx in range(2):
                        pkgs.append(
                            Package(
                                order_id=order.id,
                                delivery_stop_id=dstop.id,
                                length_cm=45 + p_idx * 5,
                                width_cm=30,
                                height_cm=22,
                                weight_kg=3.2 + p_idx * 1.1,
                                declared_weight_kg=3.5,
                                declared_value=_money("120.00"),
                                status=(
                                    PackageStatus.DELIVERED_TO_CUSTOMER
                                    if st_completed
                                    else PackageStatus.OUT_FOR_DELIVERY
                                ),
                                is_damaged=False,
                                price_breakdown={"linehaul": "12.00", "fuel": "1.50"},
                            )
                        )
                    session.add_all(pkgs)
                    await session.flush()
                    seq_packages[seq] = pkgs
    
                if include_return_stop:
                    ret_seq = len(route_stops) + 1
                    await append_return_route_stop(
                        session,
                        route=route,
                        route_stops=route_stops,
                        organization_id=org.id,
                        customer_id=customer.id,
                        pickup_address=pickup_addr,
                        order_id=_fit(
                            "orders.order_id",
                            f"DMO-{suffix[:8]}-RET-{tag}",
                            _LEN_ORDER_ID,
                        ),
                        master_label_id=_fit(
                            "orders.master_label_id",
                            f"ML-{suffix[:8]}-RET-{tag}",
                            _LEN_MASTER_LABEL_ID,
                        ),
                        tracking_id=_fit(
                            "delivery_stops.tracking_id",
                            f"TRK-{suffix[:8]}-RET-{tag}",
                            _LEN_TRACKING_ID,
                        ),
                        sequence=ret_seq,
                        service_date=service_date,
                        route_stop_status="READY",
                        route_stop_completed=False,
                        notes="Previously failed delivery — return to sender (cost-efficient on this route).",
                    )

                fp = compute_route_navigation_fingerprint(
                    sequences_and_route_stop_ids=[(rs.sequence, rs.id) for rs in route_stops]
                )
                route.navigation_fingerprint = fp
    
                # Stop notes on chosen sequence (blocking ADMIN + CUSTOMER + optional PACKAGE_ISSUE)
                if seed_notes_on_sequence is not None and seed_notes_on_sequence in seq_delivery:
                    ds = seq_delivery[seed_notes_on_sequence]
                    n_customer = StopNote(
                        delivery_stop_id=ds.id,
                        note_type="CUSTOMER",
                        message="Leave with reception if no answer — signed parcel cage.",
                        is_blocking=False,
                        sort_order=0,
                    )
                    n_admin = StopNote(
                        delivery_stop_id=ds.id,
                        note_type="ADMIN",
                        message="URGENT: Fragile clinical supplies — photograph crate label before handing over.",
                        is_blocking=True,
                        sort_order=1,
                    )
                    session.add_all([n_customer, n_admin])
                    await session.flush()
                    if seed_pkg_issue_note and seq_packages.get(seed_notes_on_sequence):
                        pkg = seq_packages[seed_notes_on_sequence][0]
                        n_pkg = StopNote(
                            delivery_stop_id=ds.id,
                            note_type="PACKAGE_ISSUE_NOTE",
                            message="Corner crush noted at warehouse — inspect before scan.",
                            is_blocking=False,
                            sort_order=2,
                            package_ids=[pkg.id],
                        )
                        session.add(n_pkg)
                        await session.flush()
                        img = StopNoteImage(
                            stop_note_id=n_pkg.id,
                            image_key=f"demo/stop-notes/{n_pkg.id}/crush.jpg",
                            sort_order=1,
                        )
                        session.add(img)
                        await session.flush()

                # Telematics: only on today's active-ish route tag TD
                if tag == "TD":
                    pings = [
                        (51.4981, -0.0783, now - timedelta(minutes=55)),
                        (51.4995, -0.0765, now - timedelta(minutes=40)),
                        (51.5020, -0.0740, now - timedelta(minutes=25)),
                        (51.5055, -0.0720, now - timedelta(minutes=12)),
                        (51.5080, -0.0695, now - timedelta(minutes=3)),
                    ]
                    for lat, lng, ts in pings:
                        session.add(
                            RouteEvent(
                                route_id=route.id,
                                driver_id=driver.id,
                                event_type="LOCATION_PING",
                                occurred_at=ts,
                                lat=lat,
                                lng=lng,
                                event_metadata={"source": "demo_seed"},
                            )
                        )
                    session.add_all(
                        [
                            RouteEvent(
                                route_id=route.id,
                                driver_id=driver.id,
                                event_type="SPEEDING",
                                occurred_at=now - timedelta(minutes=48),
                                lat=51.5002,
                                lng=-0.0758,
                                event_metadata={
                                    "speed_mph": 76.0,
                                    "limit_mph": 70.0,
                                    "speed_over_mph": 6.0,
                                },
                            ),
                            RouteEvent(
                                route_id=route.id,
                                driver_id=driver.id,
                                event_type="SPEEDING",
                                occurred_at=now - timedelta(minutes=22),
                                lat=51.5068,
                                lng=-0.0715,
                                event_metadata={"speed_mph": 73.5, "limit_mph": 70.0, "speed_over_mph": 3.5},
                            ),
                            RouteEvent(
                                route_id=route.id,
                                driver_id=driver.id,
                                event_type="HARSH_BRAKING",
                                occurred_at=now - timedelta(minutes=35),
                                lat=51.5038,
                                lng=-0.0735,
                                event_metadata={"deceleration_mps2": -6.8, "speed_before_mph": 42.0},
                            ),
                            RouteEvent(
                                route_id=route.id,
                                driver_id=driver.id,
                                event_type="HARSH_BRAKING",
                                occurred_at=now - timedelta(minutes=18),
                                lat=51.5075,
                                lng=-0.0705,
                                event_metadata={"deceleration_mps2": -7.2, "speed_before_mph": 38.0},
                            ),
                        ]
                    )
    
                return route
    
            # Past — all stops completed (Routes board **past** tab + history KPI windows)
            await _build_route(
                tag="PD",
                service_date=past_day,
                route_status="COMPLETED",
                stop_specs=[("PD", i) for i in range(4)],
                completed_sequences={1, 2, 3, 4},
                actual_drive_min=145.0,
                distance_km=92.5,
                seed_notes_on_sequence=None,
                seed_pkg_issue_note=False,
            )
    
            # Today — ACTIVE, partial progress + notes + telemetry + POD-relevant stops ahead
            await _build_route(
                tag="TD",
                service_date=today_local,
                route_status="ACTIVE",
                stop_specs=[("TD", i) for i in range(5)],
                completed_sequences={1, 2},
                actual_drive_min=55.0,
                distance_km=36.2,
                seed_notes_on_sequence=3,
                seed_pkg_issue_note=True,
                include_return_stop=True,
            )
    
            # Tomorrow — ASSIGNED queue
            await _build_route(
                tag="TM",
                service_date=tomorrow_day,
                route_status="ASSIGNED",
                stop_specs=[("TM", i + 10) for i in range(3)],
                completed_sequences=set(),
                actual_drive_min=None,
                distance_km=None,
                seed_notes_on_sequence=None,
                seed_pkg_issue_note=False,
            )
    
            await session.commit()
        except Exception as e:
            await session.rollback()
            _report_seed_failure(e)
            raise SystemExit(1) from e

    _save_manifest(manifest)
    print()
    print("=" * 72)
    print("Driver mobile demo seed complete.")
    print(f"  User (JWT)   : {RYAN_USER_ID}  ← GET /me data.user_id")
    print(f"  Driver (PK)  : {manifest.get('ryan_driver_id')}  ← GET /me data.id")
    print(f"  Email        : {RYAN_EMAIL}")
    print(f"  Manifest     : {MANIFEST_PATH.resolve()}")
    print(f"  Local dates    : past={past_day} today={today_local} tomorrow={tomorrow_day} ({tz_name})")
    print("  Return stop    : on today's ACTIVE route (stop_flow_type=RETURN, RETURN_IN_TRANSIT)")
    print("  Password       : Driver@12345! (unchanged if user already existed)")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear driver-mobile demo data for Ryan's account.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seed = sub.add_parser("seed", help="Insert demo routes/stops/orders for Ryan")
    p_seed.add_argument(
        "--force",
        action="store_true",
        help="Optional; seed is idempotent and replaces prior demo data automatically",
    )

    sub.add_parser("clear", help="Remove demo rows listed in demo_driver_mobile_manifest.json")

    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed_demo_data(force=args.force))
    else:
        asyncio.run(clear_demo_data())


if __name__ == "__main__":
    main()
