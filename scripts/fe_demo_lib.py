"""Shared helpers for FE / driver demo seed scripts."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole, UserStatus
from app.core.database import get_async_session
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, OrderDraft, Package
from app.modules.planning.enums import RouteStopFlowType
from app.modules.planning.models import Route, RouteEvent, RouteStop
from app.modules.organizations.enums import (
    BillingSchedule,
    ContactStatus,
    OrganizationStatus,
    PaymentModel,
    VatRate,
    VatTreatment,
)
from app.modules.organizations.models import OrgContact, Organization, OrgPaymentConfig, OrgPaymentMethod
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.organizations.pricing_plans_contract_sync import replace_org_contract_from_pricing_plans
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier as ServiceTierModel
from app.modules.service_tiers.repository import ServiceTierRepository
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

SEED_TAG = "FE_DEMO_V1"
ORDER_PREFIX = "FE-DEMO-ORD-"
ROUTE_PREFIX = "RT-FE-"
TRACK_PREFIX = "FE-DEMO-TRK-"
ORG_REF = "FE-DEMO-ORG"
CUSTOMER_EMAIL = "fe.demo.customer@swcouriers.invalid"

RYAN_EMAIL = "ryan.obrien@swcouriers.co.uk"
FATIMA_EMAIL = "fatima.alrashid@swcouriers.co.uk"

DEFAULT_DEPOT_CODES = ("LDN-001", "DEMO-LDN-01")

# Frontend billing + order demo org (invoices, payments, B2B orders share this tenant).
BILLING_DEMO_ORG_ID = "a2953dc2-6be4-4bf7-857a-3f76fca7a714"
PREFERRED_B2B_CUSTOMER_EMAIL = "obaid.tariq+swclient@shiftopus.com"
BILLING_DEMO_PICKUP_LABEL = "Frontend Demo Pickup"

# GLOBAL service tiers + org pricing_plans for FE order seeds (STANDARD + EXPRESS).
FE_DEMO_TIER_AUDIENCE = ServiceTierAudience.CUSTOMER_B2B
FE_DEMO_TIER_SPECS: tuple[dict, ...] = (
    {
        "tier_name": "STANDARD",
        "plain_name": "STANDARD",
        "duration_days": 3,
        "base_price": Decimal("5.00"),
        "price_per_package": Decimal("3.50"),
        "price_per_kg": Decimal("0.40"),
    },
    {
        "tier_name": "EXPRESS",
        "plain_name": "EXPRESS",
        "duration_days": 1,
        "base_price": Decimal("9.00"),
        "price_per_package": Decimal("6.50"),
        "price_per_kg": Decimal("0.80"),
    },
)


def money(v: str) -> Decimal:
    return Decimal(v)


def calendar_day_offsets(base: date) -> tuple[date, date, date]:
    """Today, tomorrow, and the day after (calendar days, includes weekends)."""
    return base, base + timedelta(days=1), base + timedelta(days=2)


async def resolve_depot(session: AsyncSession) -> Depot:
    for code in DEFAULT_DEPOT_CODES:
        depot = await session.scalar(select(Depot).where(Depot.code == code))
        if depot is not None:
            return depot
    raise SystemExit(f"No depot found ({', '.join(DEFAULT_DEPOT_CODES)}). Run demo_data.py or seed_demo_actors.py first.")


def depot_today(depot: Depot) -> date:
    tz = ZoneInfo(depot.timezone or "Europe/London")
    return datetime.now(tz).date()


async def resolve_driver_by_email(session: AsyncSession, email: str) -> tuple[User, Driver]:
    user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is None:
        raise SystemExit(f"Driver user not found for email {email!r}. Run demo_data.py first.")
    driver = await session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        raise SystemExit(f"No drivers row for {email!r}. Complete driver onboarding first.")
    return user, driver


def _user_linked_to_org(user: User, org_id: str) -> bool:
    return str(user.organization_id or "") == str(org_id)


async def _user_has_active_org_contact(session: AsyncSession, *, user_id: str, org_id: str) -> bool:
    contact = await session.scalar(
        select(OrgContact).where(
            OrgContact.organization_id == org_id,
            OrgContact.user_id == user_id,
            OrgContact.status == ContactStatus.ACTIVE,
        )
    )
    return contact is not None


async def _find_global_tier(repo: ServiceTierRepository, tier_name: str) -> ServiceTierModel | None:
    for audience in (FE_DEMO_TIER_AUDIENCE.value, ServiceTierAudience.BOTH.value):
        tier = await repo.find_global_by_name_audience(tier_name=tier_name, available_for=audience)
        if tier is not None:
            return tier
    return None


async def _ensure_global_service_tier(session: AsyncSession, spec: dict) -> ServiceTierModel:
    repo = ServiceTierRepository(session)
    tier = await _find_global_tier(repo, spec["tier_name"])
    if tier is None:
        tier = ServiceTierModel(
            tier_name=spec["tier_name"],
            description=f"FE demo {spec['tier_name']} delivery",
            duration_days=spec["duration_days"],
            error_margin_kg=0,
            price_per_kg=spec["price_per_kg"],
            price_per_package=spec["price_per_package"],
            base_price=spec["base_price"],
            scope_type=ServiceTierScopeType.GLOBAL.value,
            scope_org_id=None,
            available_for=FE_DEMO_TIER_AUDIENCE.value,
            status=ServiceTierStatus.ACTIVE,
        )
        session.add(tier)
        await session.flush()
        print(f"Created global service tier {spec['tier_name']} ({tier.id}).")
        return tier
    if tier.status != ServiceTierStatus.ACTIVE:
        tier.status = ServiceTierStatus.ACTIVE
        await session.flush()
        print(f"Reactivated global service tier {spec['tier_name']} ({tier.id}).")
    return tier


def _tier_list_price(tier: ServiceTierModel) -> Decimal:
    return (tier.base_price + tier.price_per_package).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pricing_plan_from_tier(tier: ServiceTierModel, spec: dict, *, is_default: bool) -> dict:
    list_price = _tier_list_price(tier)
    return {
        "id_price_tier": tier.id,
        "plain_name": spec["plain_name"],
        "plain_type": "standard",
        "days": tier.duration_days,
        "base_price": str(list_price),
        "price_per_package": str(list_price),
        "price_per_kg": str(tier.price_per_kg),
        "permitted": True,
        "is_default": is_default,
        "selected": is_default,
    }


def _org_pricing_plans_match_tiers(org: Organization, tiers: dict[str, ServiceTierModel]) -> bool:
    plans = org.pricing_plans or []
    if not plans:
        return False
    by_name = {str(p.get("plain_name") or "").upper(): p for p in plans if isinstance(p, dict)}
    for name, tier in tiers.items():
        plan = by_name.get(name.upper())
        if not plan or str(plan.get("id_price_tier") or "") != tier.id:
            return False
    return True


async def ensure_org_pricing_plans(session: AsyncSession, org: Organization) -> None:
    """Ensure GLOBAL STANDARD/EXPRESS tiers exist and org.pricing_plans reference them."""
    tiers: dict[str, ServiceTierModel] = {}
    for spec in FE_DEMO_TIER_SPECS:
        tiers[spec["tier_name"]] = await _ensure_global_service_tier(session, spec)

    if _org_pricing_plans_match_tiers(org, tiers):
        return

    plans = [
        _pricing_plan_from_tier(tiers[FE_DEMO_TIER_SPECS[0]["tier_name"]], FE_DEMO_TIER_SPECS[0], is_default=True),
        _pricing_plan_from_tier(tiers[FE_DEMO_TIER_SPECS[1]["tier_name"]], FE_DEMO_TIER_SPECS[1], is_default=False),
    ]
    org.pricing_plans = plans
    await session.flush()
    await replace_org_contract_from_pricing_plans(session, organization_id=org.id, plans=plans)
    print(f"Set pricing_plans (STANDARD + EXPRESS) on org {org.id}.")


async def ensure_org_payment_config(session: AsyncSession, org_id: str) -> OrgPaymentConfig:
    pc = await session.scalar(select(OrgPaymentConfig).where(OrgPaymentConfig.organization_id == org_id))
    if pc is not None:
        return pc
    pc = OrgPaymentConfig(
        organization_id=org_id,
        vat_rate=VatRate.STANDARD_20,
        vat_treatment=VatTreatment.UK,
        max_delivery_attempts=3,
        delivery_attempt_fees=[
            {"attempt": 1, "fee": "0.00"},
            {"attempt": 2, "fee": "2.50"},
            {"attempt": 3, "fee": "5.00"},
        ],
    )
    session.add(pc)
    await session.flush()
    print(f"Created org payment config for org {org_id}.")
    return pc


async def ensure_org_default_payment_method(session: AsyncSession, org_id: str) -> OrgPaymentMethod:
    pm = await session.scalar(
        select(OrgPaymentMethod).where(
            OrgPaymentMethod.organization_id == org_id,
            OrgPaymentMethod.is_default.is_(True),
        )
    )
    if pm is None:
        pm = await session.scalar(
            select(OrgPaymentMethod).where(OrgPaymentMethod.organization_id == org_id).limit(1)
        )
        if pm is not None:
            pm.is_default = True
            await session.flush()
            print(f"Marked existing payment method {pm.id} as default for org {org_id}.")
    if pm is None:
        pm = OrgPaymentMethod(
            organization_id=org_id,
            payment_model=PaymentModel.CREDIT_ACCOUNT,
            billing_schedule=BillingSchedule.DAYS_AFTER_ORDER,
            billing_days_after_order=14,
            credit_limit=Decimal("10000.00"),
            credit_utilization_warning_pct=80,
            is_default=True,
        )
        session.add(pm)
        await session.flush()
        print(f"Created default CREDIT_ACCOUNT payment method {pm.id} for org {org_id}.")
    return pm


async def ensure_org_default_pickup(
    session: AsyncSession,
    org_id: str,
    *,
    created_by_user_id: str,
) -> PickupAddress:
    pickup = await session.scalar(
        select(PickupAddress).where(
            PickupAddress.organization_id == org_id,
            PickupAddress.is_default.is_(True),
        )
    )
    if pickup is None:
        pickup = await session.scalar(
            select(PickupAddress).where(
                PickupAddress.organization_id == org_id,
                PickupAddress.label == BILLING_DEMO_PICKUP_LABEL,
            )
        )
    if pickup is None:
        pickup = await session.scalar(select(PickupAddress).where(PickupAddress.organization_id == org_id).limit(1))
    if pickup is None:
        pickup = PickupAddress(
            organization_id=org_id,
            label=BILLING_DEMO_PICKUP_LABEL,
            line_1="77 Frontend Quay",
            city="London",
            postcode="SE1 7AA",
            country="United Kingdom",
            latitude=51.5037,
            longitude=-0.0828,
            is_default=True,
            created_by_user_id=created_by_user_id,
        )
        session.add(pickup)
        await session.flush()
        print(f"Created default pickup address {pickup.id} for org {org_id}.")
        return pickup
    if not pickup.is_default:
        pickup.is_default = True
        await session.flush()
        print(f"Marked pickup {pickup.id} as default for org {org_id}.")
    return pickup


async def prepare_fe_order_booking_context(
    session: AsyncSession,
    organization_id: str = BILLING_DEMO_ORG_ID,
) -> tuple[Organization, User, PickupAddress, OrgPaymentMethod]:
    """Load target org and ensure all prerequisites for OrderService.create_order."""
    org = await session.get(Organization, organization_id)
    if org is None:
        raise SystemExit(f"Organization {organization_id} not found.")
    if org.status != OrganizationStatus.ACTIVE:
        raise SystemExit(f"Organization {organization_id} is not ACTIVE (status={org.status}).")

    await ensure_org_pricing_plans(session, org)
    await ensure_org_payment_config(session, org.id)

    customer = await resolve_org_b2b_customer(session, org.id)
    pm = await ensure_org_default_payment_method(session, org.id)
    pickup = await ensure_org_default_pickup(session, org.id, created_by_user_id=customer.id)
    return org, customer, pickup, pm


async def resolve_org_b2b_customer(
    session: AsyncSession,
    org_id: str,
    *,
    preferred_email: str | None = PREFERRED_B2B_CUSTOMER_EMAIL,
) -> User:
    """Resolve the org's portal B2B user (role CUSTOMER_B2B), not a synthetic seed account."""

    async def _is_member(user: User) -> bool:
        if user.role != UserRole.CUSTOMER_B2B or user.status != UserStatus.ACTIVE:
            return False
        if _user_linked_to_org(user, org_id):
            return True
        return await _user_has_active_org_contact(session, user_id=user.id, org_id=org_id)

    if preferred_email:
        preferred = await session.scalar(
            select(User).where(func.lower(User.email) == preferred_email.lower())
        )
        if preferred is not None and await _is_member(preferred):
            return preferred

    primary_contact = await session.scalar(
        select(OrgContact).where(
            OrgContact.organization_id == org_id,
            OrgContact.is_primary.is_(True),
            OrgContact.status == ContactStatus.ACTIVE,
            OrgContact.user_id.is_not(None),
        )
    )
    if primary_contact is not None:
        primary_user = await session.get(User, primary_contact.user_id)
        if primary_user is not None and await _is_member(primary_user):
            return primary_user

    direct_members = list(
        (
            await session.execute(
                select(User).where(
                    User.organization_id == org_id,
                    User.role == UserRole.CUSTOMER_B2B,
                    User.status == UserStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )
    if direct_members:
        return direct_members[0]

    contact_user_ids = list(
        (
            await session.execute(
                select(OrgContact.user_id).where(
                    OrgContact.organization_id == org_id,
                    OrgContact.status == ContactStatus.ACTIVE,
                    OrgContact.user_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for user_id in contact_user_ids:
        user = await session.get(User, user_id)
        if user is not None and await _is_member(user):
            return user

    hint = f" (expected e.g. {preferred_email})" if preferred_email else ""
    raise SystemExit(
        f"No active CUSTOMER_B2B user linked to organization {org_id}{hint}. "
        "Ensure the B2B portal user exists with role CUSTOMER_B2B on this org."
    )


async def ensure_driver_vehicle(session: AsyncSession, driver: Driver, depot: Depot) -> None:
    driver.depot_id = depot.id
    if driver.vehicle_id is None:
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.depot_id == depot.id).limit(1))
        if vehicle is None:
            raise SystemExit(f"No vehicle in depot {depot.code}.")
        driver.vehicle_id = vehicle.id
    await session.flush()


def _billing_demo_org_id() -> str:
    return BILLING_DEMO_ORG_ID


async def clear_fe_demo_drafts(session: AsyncSession, org_id: str) -> None:
    """Remove only drafts created by FE demo seeds (payload.seed = SEED_TAG)."""
    await session.execute(
        delete(OrderDraft).where(
            OrderDraft.organization_id == org_id,
            OrderDraft.payload.contains({"seed": SEED_TAG}),
        )
    )


async def purge_fe_demo_data(session: AsyncSession) -> None:
    route_ids = list(
        (await session.execute(select(Route.id).where(Route.route_code.ilike(f"{ROUTE_PREFIX}%")))).scalars().all()
    )
    if route_ids:
        await session.execute(delete(RouteEvent).where(RouteEvent.route_id.in_(route_ids)))
        await session.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
        await session.execute(delete(Route).where(Route.id.in_(route_ids)))

    await session.execute(delete(Order).where(Order.order_id.ilike(f"{ORDER_PREFIX}%")))

    await clear_fe_demo_drafts(session, _billing_demo_org_id())
    fe_org = await session.scalar(select(Organization).where(Organization.reference == ORG_REF))
    if fe_org is not None:
        await clear_fe_demo_drafts(session, fe_org.id)

    await session.commit()


async def run_purge_fe_demo_data() -> None:
    """Delete all FE demo routes, orders, and drafts (standalone entrypoint)."""
    async with get_async_session() as session:
        await purge_fe_demo_data(session)


def route_code(service_date: date, leg: str, tag: str) -> str:
    return f"{ROUTE_PREFIX}{service_date.strftime('%y%m%d')}-{leg}-{tag}"


def order_code(tag: str, idx: int) -> str:
    return f"{ORDER_PREFIX}{tag}-{idx:02d}"


def tracking_code(tag: str, idx: int) -> str:
    return f"{TRACK_PREFIX}{tag}-{idx:02d}"


async def append_return_route_stop(
    session: AsyncSession,
    *,
    route: Route,
    route_stops: list[RouteStop],
    organization_id: str,
    customer_id: str,
    pickup_address: PickupAddress | None,
    order_id: str,
    master_label_id: str,
    tracking_id: str,
    sequence: int,
    service_date: date,
    route_stop_status: str = "READY",
    route_stop_completed: bool = False,
    package_count: int = 2,
    notes: str | None = "Failed delivery — return parcels to sender at this stop.",
) -> None:
    """Append a RETURN leg stop (failed delivery parcels heading back to sender).

    Uses the org pickup address when provided; otherwise falls back to the org registered address.
    """
    if pickup_address is not None:
        line_1 = pickup_address.line_1
        line_2 = pickup_address.line_2
        city = pickup_address.city
        postcode = pickup_address.postcode
        lat = pickup_address.latitude
        lng = pickup_address.longitude
        recipient_first = "Return"
        recipient_last = "Sender"
    else:
        org = await session.get(Organization, organization_id)
        line_1 = (org.reg_address_line_1 if org else None) or "88 Demo Wharf Road"
        line_2 = None
        city = (org.reg_city if org else None) or "London"
        postcode = (org.reg_postcode if org else None) or "SE16 7FZ"
        lat = 51.4972
        lng = -0.0619
        recipient_first = "Return"
        recipient_last = "Sender"

    order = Order(
        order_id=order_id,
        master_label_id=master_label_id,
        organization_id=organization_id,
        customer_id=customer_id,
        pickup_address_id=pickup_address.id if pickup_address is not None else None,
        subtotal=money("38.00"),
        vat_amount=money("7.60"),
        total_amount=money("45.60"),
        status=OrderStatus.RETURN_IN_TRANSIT,
    )
    session.add(order)
    await session.flush()

    dstop = DeliveryStop(
        order_id=order.id,
        tracking_id=tracking_id,
        recipient_first_name=recipient_first,
        recipient_last_name=recipient_last,
        recipient_phone="07700900888",
        recipient_email="return.sender@swcouriers.invalid",
        line_1=line_1,
        line_2=line_2,
        city=city,
        postcode=postcode,
        latitude=lat,
        longitude=lng,
        service_tier=DeliveryServiceTier.STANDARD,
        signature_required=False,
        safe_place_allowed=False,
        status=DeliveryStopStatus.RETURN_IN_TRANSIT,
        scheduled_for=service_date,
    )
    session.add(dstop)
    await session.flush()

    rs_status = "COMPLETED" if route_stop_completed else route_stop_status
    rs = RouteStop(
        route_id=route.id,
        delivery_stop_id=dstop.id,
        sequence=sequence,
        estimated_arrival=datetime(
            service_date.year,
            service_date.month,
            service_date.day,
            min(8 + sequence, 20),
            (15 * sequence) % 60,
            tzinfo=UTC,
        ),
        actual_arrival=datetime.now(UTC) - timedelta(minutes=20) if route_stop_completed else None,
        distance_from_prev_km=2.8,
        duration_from_prev_min=9.0,
        status=rs_status,
        stop_flow_type=RouteStopFlowType.RETURN.value,
        notes=notes,
    )
    session.add(rs)
    await session.flush()
    route_stops.append(rs)

    for _ in range(package_count):
        session.add(
            Package(
                order_id=order.id,
                delivery_stop_id=dstop.id,
                length_cm=42,
                width_cm=32,
                height_cm=24,
                weight_kg=3.8,
                declared_weight_kg=4.0,
                declared_value=money("95.00"),
                status=PackageStatus.RETURN_IN_TRANSIT,
                is_damaged=False,
                price_breakdown={"return_linehaul": "8.00"},
            )
        )
    await session.flush()

    route.total_stops = int(route.total_stops or 0) + 1
