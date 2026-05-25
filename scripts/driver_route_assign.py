"""Assign depot-local **today** routes to a driver for mobile QA / demos.

This is **script infrastructure**, not an application domain service. The planning module has
no write API yet (``PlanningService`` is read-only); driver mobile reads routes via
``Route.driver_id`` on rows created the same way as ``seed_driver_mobile_pickup_route.py``.

``seed`` bootstraps depot ``LDN-001``, a vehicle, and the driver user/``drivers`` row when
missing — no ``demo_data.py`` prerequisite.

After seeding, visibility is checked with the real ``DriverService`` read paths used by
``GET /v1/driver-profile/me/routes/today``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

import app.models  # noqa: F401
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole, UserStatus, UserTitle
from app.core.database import get_async_session
from app.core.security import hash_password
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.depots.models import Depot
from app.modules.drivers.enums import DriverAccountStatus, DriverLiveStatus, DriverType
from app.modules.drivers.models import Driver
from app.modules.drivers.service import DriverService
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, Package
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RoutePlanStatus, RouteStatus, RouteStopFlowType, RouteType
from app.modules.planning.models import Route, RoutePlan, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.models import User
from app.modules.vehicles.enums import (
    FuelType,
    LiveStatus,
    VehicleAvailability,
    VehicleStatus,
    VehicleType,
)
from app.modules.vehicles.models import Vehicle

from scripts.fe_demo_lib import (
    DEFAULT_DEPOT_CODES,
    _REPO_ROOT,
    append_return_route_stop,
    depot_today,
    money,
)

_LEN_ORDER_ID = 32
_LEN_MASTER_LABEL_ID = 40
_LEN_TRACKING_ID = 40
_LEN_ROUTE_CODE = 20
_LEN_ORG_REF = 20

_ASSIGN_DEPOT_CODE = "LDN-001"
_ASSIGN_VEHICLE_REG = "ASG-LDN-VAN"
_DEFAULT_DRIVER_PASSWORD = "Driver@12345!"

_KNOWN_DRIVER_PROFILES: dict[str, dict[str, object]] = {
    "ryan.obrien@swcouriers.co.uk": {
        "first_name": "Ryan",
        "last_name": "O'Brien",
        "phone": "07700900107",
        "title": UserTitle.MR,
    },
    "fatima.alrashid@swcouriers.co.uk": {
        "first_name": "Fatima",
        "last_name": "Al-Rashid",
        "phone": "07700900108",
        "title": UserTitle.MS,
    },
}

_RECIPIENTS = [
    ("Casey", "Ng", "10 Demo Lane", None, "London", "SE1 2AA", 51.5045, -0.0865),
    ("Riley", "Fox", "88 Warehouse Row", "Unit B", "London", "E1W 3NQ", 51.5089, -0.0547),
    ("Jamie", "Reed", "200 Jamaica Road", None, "London", "SE16 4TT", 51.4988, -0.0699),
]


class AssignScenarioKey(StrEnum):
    PICKUP = "pickup"
    DELIVERY = "delivery"
    PICKUP_RETURN = "pickup_return"
    DELIVERY_RETURN = "delivery_return"


@dataclass(frozen=True, slots=True)
class _Scenario:
    key: AssignScenarioKey
    tag: str
    route_type: RouteType
    include_return: bool
    order_status: OrderStatus
    stop_status: DeliveryStopStatus
    package_status: PackageStatus
    stop_flow_type: RouteStopFlowType
    return_notes: str | None = None


_SCENARIOS: dict[AssignScenarioKey, _Scenario] = {
    AssignScenarioKey.PICKUP: _Scenario(
        key=AssignScenarioKey.PICKUP,
        tag="ATP",
        route_type=RouteType.PICKUP,
        include_return=False,
        order_status=OrderStatus.ENROUTE_PICKUP,
        stop_status=DeliveryStopStatus.ENROUTE_PICKUP,
        package_status=PackageStatus.ENROUTE_PICKUP,
        stop_flow_type=RouteStopFlowType.PICKUP,
    ),
    AssignScenarioKey.DELIVERY: _Scenario(
        key=AssignScenarioKey.DELIVERY,
        tag="ATD",
        route_type=RouteType.DELIVERY,
        include_return=False,
        order_status=OrderStatus.DELIVERY_IN_PROGRESS,
        stop_status=DeliveryStopStatus.OUT_FOR_DELIVERY,
        package_status=PackageStatus.OUT_FOR_DELIVERY,
        stop_flow_type=RouteStopFlowType.DELIVERY,
    ),
    AssignScenarioKey.PICKUP_RETURN: _Scenario(
        key=AssignScenarioKey.PICKUP_RETURN,
        tag="ATPR",
        route_type=RouteType.PICKUP,
        include_return=True,
        order_status=OrderStatus.ENROUTE_PICKUP,
        stop_status=DeliveryStopStatus.ENROUTE_PICKUP,
        package_status=PackageStatus.ENROUTE_PICKUP,
        stop_flow_type=RouteStopFlowType.PICKUP,
        return_notes="Failed delivery return — placed on today's pickup route.",
    ),
    AssignScenarioKey.DELIVERY_RETURN: _Scenario(
        key=AssignScenarioKey.DELIVERY_RETURN,
        tag="ATDR",
        route_type=RouteType.DELIVERY,
        include_return=True,
        order_status=OrderStatus.DELIVERY_IN_PROGRESS,
        stop_status=DeliveryStopStatus.OUT_FOR_DELIVERY,
        package_status=PackageStatus.OUT_FOR_DELIVERY,
        stop_flow_type=RouteStopFlowType.DELIVERY,
        return_notes="Failed delivery return — placed on today's delivery route.",
    ),
}


@dataclass(frozen=True, slots=True)
class AssignResult:
    scenario: AssignScenarioKey
    driver_email: str
    driver_id: str
    route_id: str
    route_code: str
    service_date: date
    depot_timezone: str
    stop_count: int
    includes_return: bool
    verified_on_today_dashboard: bool


def _fit(field: str, value: str, max_len: int) -> str:
    if len(value) > max_len:
        raise SystemExit(f"{field} is {len(value)} chars (max {max_len}): {value!r}")
    return value


def _route_code(scenario: _Scenario, service_date: date) -> str:
    return _fit("routes.route_code", f"RT-{scenario.tag}-{service_date.strftime('%y%m%d')}", _LEN_ROUTE_CODE)


def _order_id(scenario: _Scenario, service_date: date, seq: int, *, suffix: str = "") -> str:
    return _fit("orders.order_id", f"ORD-{scenario.tag}-{service_date.strftime('%y%m%d')}-{seq:02d}{suffix}", _LEN_ORDER_ID)


def _master_label(scenario: _Scenario, service_date: date, seq: int, *, suffix: str = "") -> str:
    return _fit(
        "orders.master_label_id",
        f"ML-{scenario.tag}-{service_date.strftime('%y%m%d')}-{seq:02d}{suffix}",
        _LEN_MASTER_LABEL_ID,
    )


def _tracking_id(scenario: _Scenario, service_date: date, seq: int, *, suffix: str = "") -> str:
    return _fit(
        "delivery_stops.tracking_id",
        f"TRK-{scenario.tag}-{service_date.strftime('%y%m%d')}-{seq:02d}{suffix}",
        _LEN_TRACKING_ID,
    )


def _manifest_path(scenario: _Scenario):
    return _REPO_ROOT / f"driver_assign_{scenario.tag.lower()}_manifest.json"


def _incorporation_years_ago(today: date, *, years: int = 2) -> date:
    y = today.year - years
    try:
        return today.replace(year=y)
    except ValueError:
        return today.replace(year=y, day=28)


def _driver_profile_from_email(email: str) -> dict[str, object]:
    key = email.strip().lower()
    if key in _KNOWN_DRIVER_PROFILES:
        return _KNOWN_DRIVER_PROFILES[key]
    local = key.split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").split(".") if p]
    first_name = parts[0].title() if parts else "Assign"
    last_name = parts[1].title() if len(parts) > 1 else "Driver"
    return {
        "first_name": first_name,
        "last_name": last_name,
        "phone": "07700900400",
        "title": UserTitle.MR,
    }


async def _resolve_assign_depot(session: AsyncSession) -> Depot | None:
    for code in DEFAULT_DEPOT_CODES:
        depot = await session.scalar(select(Depot).where(Depot.code == code))
        if depot is not None:
            return depot
    return None


async def _get_or_create_assign_depot(session: AsyncSession) -> Depot:
    depot = await _resolve_assign_depot(session)
    if depot is not None:
        return depot

    depot = Depot(
        name="Bermondsey Distribution Centre",
        code=_ASSIGN_DEPOT_CODE,
        address_line_1="12 Crimscott Street",
        city="London",
        postcode="SE1 5TE",
        latitude=51.4981,
        longitude=-0.0783,
        timezone="Europe/London",
        capacity=5000,
        status="active",
        notes="Created by assign_today_* scripts (standalone bootstrap).",
    )
    session.add(depot)
    await session.flush()
    print(f"[+] Created depot {depot.code} ({depot.id}).")
    return depot


async def _get_or_create_assign_vehicle(session: AsyncSession, *, depot: Depot) -> Vehicle:
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.registration_number == _ASSIGN_VEHICLE_REG))
    if vehicle is not None:
        if vehicle.depot_id != depot.id:
            vehicle.depot_id = depot.id
            await session.flush()
        return vehicle

    vehicle = await session.scalar(select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1))
    if vehicle is not None:
        return vehicle

    today = datetime.now(UTC).date()
    vehicle = Vehicle(
        registration_number=_ASSIGN_VEHICLE_REG,
        depot_id=depot.id,
        make="Ford",
        model="Transit",
        year=datetime.now(UTC).year - 2,
        vehicle_type=VehicleType.INTERNAL,
        fuel_type=FuelType.DIESEL,
        cargo_volume_m3=9.0,
        max_payload_kg=1200.0,
        current_mileage=15000,
        service_interval_miles=10000,
        service_interval_months=12,
        next_service_due=today + timedelta(days=90),
        mot_expiry=today + timedelta(days=180),
        tax_due_date=today + timedelta(days=120),
        insurance_expiry=today + timedelta(days=200),
        status=VehicleStatus.ACTIVE,
        availability=VehicleAvailability.ACTIVE,
        live_status=LiveStatus.IDLE,
    )
    session.add(vehicle)
    await session.flush()
    print(f"[+] Created vehicle {vehicle.registration_number} in depot {depot.code}.")
    return vehicle


async def _get_or_create_assign_driver(
    session: AsyncSession,
    *,
    driver_email: str,
    depot: Depot,
    vehicle: Vehicle,
) -> tuple[User, Driver]:
    email = driver_email.strip()
    profile = _driver_profile_from_email(email)

    user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is None:
        user = User(
            email=email,
            phone=str(profile["phone"]),
            first_name=str(profile["first_name"]),
            last_name=str(profile["last_name"]),
            title=profile["title"],  # type: ignore[arg-type]
            password_hash=hash_password(_DEFAULT_DRIVER_PASSWORD),
            role=UserRole.DRIVER,
            status=UserStatus.ACTIVE,
            email_verified=True,
            force_password_change=False,
        )
        session.add(user)
        await session.flush()
        print(f"[+] Created driver user {email} ({user.id}).")

    driver = await session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        driver = Driver(
            user_id=user.id,
            depot_id=depot.id,
            vehicle_id=vehicle.id,
            capacities=["VAN"],
            driver_type=DriverType.INTERNAL.value,
            address_line1="1 Assign Lane",
            city="London",
            postcode="SE1 5TE",
            state="England",
            account_status=DriverAccountStatus.ACTIVE,
            live_status=DriverLiveStatus.OFFLINE,
        )
        session.add(driver)
        await session.flush()
        print(f"[+] Created drivers row {driver.driver_code} for {email}.")

    driver.depot_id = depot.id
    if driver.vehicle_id is None:
        driver.vehicle_id = vehicle.id
    await session.flush()
    return user, driver


async def ensure_assign_prerequisites(
    session: AsyncSession,
    driver_email: str,
) -> tuple[Depot, User, Driver]:
    """Ensure depot, vehicle, user, and drivers row exist (idempotent bootstrap)."""
    depot = await _get_or_create_assign_depot(session)
    vehicle = await _get_or_create_assign_vehicle(session, depot=depot)
    user, driver = await _get_or_create_assign_driver(
        session,
        driver_email=driver_email,
        depot=depot,
        vehicle=vehicle,
    )
    return depot, user, driver


async def _demote_other_active_routes(
    session: AsyncSession,
    *,
    driver_id: str,
    service_date: date,
    depot_id: str,
    keep_route_code: str,
) -> int:
    subq = (
        select(Route.id)
        .join(RoutePlan, Route.plan_id == RoutePlan.id)
        .where(
            Route.driver_id == driver_id,
            Route.status == RouteStatus.ACTIVE.value,
            Route.route_code != keep_route_code,
            RoutePlan.depot_id == depot_id,
            RoutePlan.service_date == service_date,
        )
    )
    res = await session.execute(update(Route).where(Route.id.in_(subq)).values(status=RouteStatus.ASSIGNED.value))
    return int(res.rowcount or 0)


async def _delete_scenario_seed_rows(
    session: AsyncSession,
    *,
    scenario: _Scenario,
    service_date: date,
) -> None:
    """Remove prior assign-demo graph for this scenario/day (safe to call repeatedly).

    Route first (cascade ``route_stops`` / ``route_events``), then orders by stable id prefix
    (cascade ``delivery_stops`` / ``packages``). Org/customer rows are reused on re-seed.
    """
    route_code = _route_code(scenario, service_date)
    route = await session.scalar(select(Route).where(Route.route_code == route_code))
    if route is not None:
        await session.delete(route)
        await session.flush()

    order_prefix = f"ORD-{scenario.tag}-{service_date.strftime('%y%m%d')}-"
    await session.execute(delete(Order).where(Order.order_id.like(f"{order_prefix}%")))
    await session.flush()


async def _ensure_org_customer_pickup(
    session: AsyncSession,
    *,
    scenario: _Scenario,
    service_date: date,
) -> tuple[Organization, User, PickupAddress]:
    org_ref = _fit("organizations.reference", f"DM-{scenario.tag}", _LEN_ORG_REF)
    org = await session.scalar(select(Organization).where(Organization.reference == org_ref))
    if org is None:
        org = Organization(
            reference=org_ref,
            trading_name=f"Assign Demo Org ({scenario.tag})",
            legal_entity_name=f"Assign Demo Org ({scenario.tag})",
            companies_house_number=_fit("companies_house_number", f"AS{scenario.tag}", 100),
            vat_number=_fit("vat_number", f"GBAS{scenario.tag}", 50),
            date_of_incorporation=_incorporation_years_ago(service_date),
            industry=IndustryType.OTHER,
            company_size=CompanySize.EMPLOYEES_1_10,
            reg_address_line_1="42 Assign Wharf",
            reg_city="London",
            reg_postcode="SE16 7FZ",
            status=OrganizationStatus.ACTIVE,
        )
        session.add(org)
        await session.flush()

    cust_email = f"assign.{scenario.tag.lower()}@swcouriers.invalid"
    cust = await session.scalar(select(User).where(func.lower(User.email) == cust_email.lower()))
    if cust is None:
        cust = User(
            email=cust_email,
            phone="07700900401",
            first_name="Assign",
            last_name=scenario.tag,
            title=UserTitle.MS,
            password_hash=hash_password("UnusedAssignDemo9!"),
            role=UserRole.CUSTOMER_B2B,
            status=UserStatus.ACTIVE,
            email_verified=True,
            force_password_change=False,
            organization_id=org.id,
        )
        session.add(cust)
        await session.flush()

    pickup_addr = await session.scalar(
        select(PickupAddress).where(
            PickupAddress.organization_id == org.id,
            PickupAddress.label == "Assign Return Sender",
        )
    )
    if pickup_addr is None:
        pickup_addr = PickupAddress(
            organization_id=org.id,
            label="Assign Return Sender",
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

    return org, cust, pickup_addr


async def _get_or_create_plan(session: AsyncSession, *, depot: Depot, service_date: date) -> tuple[RoutePlan, bool]:
    plan = await session.scalar(
        select(RoutePlan).where(RoutePlan.depot_id == depot.id, RoutePlan.service_date == service_date)
    )
    if plan is not None:
        return plan, False
    plan = RoutePlan(service_date=service_date, depot_id=depot.id, status=RoutePlanStatus.READY.value)
    session.add(plan)
    await session.flush()
    return plan, True


async def _verify_driver_can_see_route(
    session: AsyncSession,
    *,
    driver_id: str,
    route_id: str,
    service_date: date,
    route_status: str,
) -> bool:
    driver_svc = DriverService(session)
    if route_status == RouteStatus.ACTIVE.value:
        dashboard = await driver_svc.get_driver_today_route_dashboard_payload(
            driver_id=driver_id,
            explicit_service_date=service_date,
        )
        return dashboard is not None and str(dashboard.get("route_id")) == str(route_id)
    assigned_rows, _total = await driver_svc.list_driver_assigned_routes_payload(driver_id=driver_id, page=1, size=50)
    return any(str(row.get("route_id")) == str(route_id) for row in assigned_rows)


async def assign_today_driver_route(
    session: AsyncSession,
    *,
    scenario_key: AssignScenarioKey,
    driver_email: str,
    stops: int = 4,
    route_status: str = RouteStatus.ACTIVE.value,
    service_date: date | None = None,
    demote_conflicts: bool = True,
) -> AssignResult:
    """Idempotent: removes prior route for this scenario/day, then creates a fresh graph."""
    scenario = _SCENARIOS[scenario_key]
    depot, _user, driver = await ensure_assign_prerequisites(session, driver_email)
    target_day = service_date or depot_today(depot)
    tz_name = depot.timezone or "Europe/London"

    route_code = _route_code(scenario, target_day)
    await _delete_scenario_seed_rows(session, scenario=scenario, service_date=target_day)

    if demote_conflicts and route_status == RouteStatus.ACTIVE.value:
        demoted = await _demote_other_active_routes(
            session,
            driver_id=driver.id,
            service_date=target_day,
            depot_id=depot.id,
            keep_route_code=route_code,
        )
        if demoted:
            print(f"[i] Demoted {demoted} other ACTIVE route(s) for this driver on {target_day}.")

    plan, plan_created = await _get_or_create_plan(session, depot=depot, service_date=target_day)
    org, customer, pickup_addr = await _ensure_org_customer_pickup(session, scenario=scenario, service_date=target_day)

    route = Route(
        plan_id=plan.id,
        driver_id=driver.id,
        vehicle_id=driver.vehicle_id,
        route_code=route_code,
        route_type=scenario.route_type.value,
        total_stops=stops + (1 if scenario.include_return else 0),
        status=route_status,
        estimated_drive_time_min=float(stops) * 20.0,
        total_distance_km=10.0 + stops * 3.0,
        navigation_encoded_polyline="xPoly_assign_demo_placeholder",
        navigation_meta={"demo": True, "assign_scenario": scenario.key.value},
        navigation_fingerprint="pending",
    )
    session.add(route)
    await session.flush()

    route_stops: list[RouteStop] = []
    for seq in range(1, stops + 1):
        fn, ln, l1, l2, city, pc, lat, lng = _RECIPIENTS[(seq - 1) % len(_RECIPIENTS)]
        order = Order(
            order_id=_order_id(scenario, target_day, seq),
            master_label_id=_master_label(scenario, target_day, seq),
            organization_id=org.id,
            customer_id=customer.id,
            subtotal=money("35.00"),
            vat_amount=money("7.00"),
            total_amount=money("42.00"),
            status=scenario.order_status,
        )
        session.add(order)
        await session.flush()

        dstop = DeliveryStop(
            order_id=order.id,
            tracking_id=_tracking_id(scenario, target_day, seq),
            recipient_first_name=fn,
            recipient_last_name=ln,
            recipient_phone=f"077009{seq + 400:05d}",
            recipient_email=f"{fn.lower()}.{ln.lower()}.{scenario.tag.lower()}@example.com",
            line_1=l1,
            line_2=l2,
            city=city,
            postcode=pc,
            latitude=lat,
            longitude=lng,
            service_tier=DeliveryServiceTier.STANDARD,
            signature_required=False,
            safe_place_allowed=False,
            status=scenario.stop_status,
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
            distance_from_prev_km=2.0 + seq * 0.5,
            duration_from_prev_min=8.0 + seq * 2,
            status="READY",
            stop_flow_type=scenario.stop_flow_type.value,
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
                    declared_value=money("85.00"),
                    status=scenario.package_status,
                    is_damaged=False,
                    price_breakdown={"assign_demo": "10.00"},
                )
            )
        await session.flush()

    if scenario.include_return:
        ret_seq = len(route_stops) + 1
        await append_return_route_stop(
            session,
            route=route,
            route_stops=route_stops,
            organization_id=org.id,
            customer_id=customer.id,
            pickup_address=pickup_addr,
            order_id=_order_id(scenario, target_day, ret_seq, suffix="-RET"),
            master_label_id=_master_label(scenario, target_day, ret_seq, suffix="-RET"),
            tracking_id=_tracking_id(scenario, target_day, ret_seq, suffix="-RET"),
            sequence=ret_seq,
            service_date=target_day,
            route_stop_status="READY",
            notes=scenario.return_notes,
        )

    route.navigation_fingerprint = compute_route_navigation_fingerprint(
        sequences_and_route_stop_ids=[(rs.sequence, rs.id) for rs in route_stops]
    )
    await session.flush()

    verified = await _verify_driver_can_see_route(
        session,
        driver_id=driver.id,
        route_id=route.id,
        service_date=target_day,
        route_status=route_status,
    )

    _manifest_path(scenario).write_text(
        json.dumps(
            {
                "scenario": scenario.key.value,
                "route_code": route_code,
                "route_id": str(route.id),
                "plan_id": str(plan.id),
                "plan_created_by_script": plan_created,
                "service_date": target_day.isoformat(),
                "driver_email": driver_email,
                "driver_id": str(driver.id),
                "organization_id": str(org.id),
                "depot_timezone": tz_name,
                "verified_on_today_dashboard": verified,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return AssignResult(
        scenario=scenario_key,
        driver_email=driver_email,
        driver_id=str(driver.id),
        route_id=str(route.id),
        route_code=route_code,
        service_date=target_day,
        depot_timezone=tz_name,
        stop_count=len(route_stops),
        includes_return=scenario.include_return,
        verified_on_today_dashboard=verified,
    )


async def clear_today_driver_route(
    session: AsyncSession,
    *,
    scenario_key: AssignScenarioKey,
    service_date: date | None = None,
) -> None:
    scenario = _SCENARIOS[scenario_key]
    depot = await _resolve_assign_depot(session)
    if depot is None and service_date is None:
        raise SystemExit(
            f"No depot found ({', '.join(DEFAULT_DEPOT_CODES)}). "
            "Run `seed` once to bootstrap, or pass `--service-date YYYY-MM-DD`."
        )
    target_day = service_date or depot_today(depot)  # type: ignore[arg-type]
    await _delete_scenario_seed_rows(session, scenario=scenario, service_date=target_day)

    manifest_path = _manifest_path(scenario)
    plan_id: str | None = None
    plan_owned = False
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            plan_id = data.get("plan_id")
            plan_owned = bool(data.get("plan_created_by_script"))
        except (json.JSONDecodeError, OSError):
            pass

    if plan_id and plan_owned:
        remaining = await session.scalar(select(func.count()).select_from(Route).where(Route.plan_id == plan_id))
        if remaining == 0:
            plan = await session.get(RoutePlan, plan_id)
            if plan is not None:
                await session.delete(plan)

    manifest_path.unlink(missing_ok=True)


async def run_assign(**kwargs) -> AssignResult:
    async with get_async_session() as session:
        try:
            result = await assign_today_driver_route(session, **kwargs)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def run_clear(**kwargs) -> None:
    async with get_async_session() as session:
        await clear_today_driver_route(session, **kwargs)
        await session.commit()


def build_arg_parser(*, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seed = sub.add_parser("seed", help="Assign today's route (idempotent)")
    p_seed.add_argument("--driver-email", required=True, help="Driver login email")
    p_seed.add_argument("--stops", type=int, default=4, help="Operational stops before RETURN (default 4)")
    p_seed.add_argument(
        "--route-status",
        default=RouteStatus.ACTIVE.value,
        choices=[RouteStatus.ACTIVE.value, RouteStatus.ASSIGNED.value],
    )
    p_seed.add_argument("--service-date", type=lambda s: date.fromisoformat(s), default=None, metavar="YYYY-MM-DD")
    p_seed.add_argument(
        "--no-demote-conflicts",
        action="store_true",
        help="Do not demote other ACTIVE routes for this driver on the same day",
    )

    p_clear = sub.add_parser("clear", help="Remove today's assigned route for this scenario")
    p_clear.add_argument(
        "--service-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        metavar="YYYY-MM-DD",
    )
    return parser


def print_assign_result(result: AssignResult, *, title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print(f"  Scenario       : {result.scenario.value}")
    print(f"  Driver email   : {result.driver_email}")
    print(f"  Route code     : {result.route_code}")
    print(f"  Service date   : {result.service_date} ({result.depot_timezone})")
    print(f"  Stops          : {result.stop_count}" + (" + RETURN" if result.includes_return else ""))
    print(f"  Dashboard OK   : {result.verified_on_today_dashboard}")
    print()
    print("Try:")
    print("  GET /v1/driver-profile/me/routes/today")
    print(f"  GET /v1/driver-profile/me/routes/{result.route_id}/stops")
    print("=" * 72)


def main_for_scenario(*, scenario_key: AssignScenarioKey, description: str, title: str) -> None:
    parser = build_arg_parser(description=description)
    args = parser.parse_args()
    if args.cmd == "seed":
        try:
            result = asyncio.run(
                run_assign(
                    scenario_key=scenario_key,
                    driver_email=args.driver_email.strip(),
                    stops=max(1, min(args.stops, 8)),
                    route_status=args.route_status,
                    service_date=args.service_date,
                    demote_conflicts=not args.no_demote_conflicts,
                )
            )
        except Exception as exc:
            print(f"\n[!] Assign failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print_assign_result(result, title=title)
    else:
        asyncio.run(run_clear(scenario_key=scenario_key, service_date=args.service_date))
        print(f"Cleared {scenario_key.value} assignment.")
