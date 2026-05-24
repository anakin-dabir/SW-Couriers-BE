"""Seed all demo actors with hardcoded IDs (idempotent).

This script wires up every prerequisite that ``OrderService.create_order`` needs, so the
follow-up scripts can focus on the route flow instead of the booking flow:

* Depot ``DEMO-LDN-01`` in London (Europe/London).
* Driver user (``UserRole.DRIVER``) + linked ``drivers`` row.
* Vehicle (registered with the demo depot).
* Open Crew pairing the driver user with the vehicle.
* B2B Organization with **pricing_plans** JSON (STANDARD + EXPRESS), in ``ACTIVE`` status.
* ``OrgPaymentConfig`` (VAT 20%, 3 delivery attempts with fee schedule).
* ``OrgPaymentMethod`` ``CREDIT_ACCOUNT`` (default; no Braintree round-trip).
* ``OrgContact`` (ACTIVE, ACCOUNT_OWNER, ``is_primary=True``) linked to the customer user.
* Customer user (``UserRole.CUSTOMER_B2B``) linked to the org.
* 8 pickup addresses across London (one per "client warehouse") for use as ``orders.pickup_address_id``.

Run::

    poetry run python scripts/seed_demo_actors.py

The script is idempotent — re-running keeps the same UUIDs and only patches drifting fields.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — runnable-script pattern (imports below sys.path bootstrap).

import app.models  # noqa: F401
from app.common.enums import UserRole, UserStatus, UserTitle
from app.core.database import get_async_session
from app.core.security import hash_password
from app.modules.crew.models import Crew
from app.modules.depots.models import Depot
from app.modules.drivers.enums import DriverAccountStatus, DriverLiveStatus, DriverType
from app.modules.drivers.models import Driver
from app.modules.organizations.enums import (
    BillingSchedule,
    CompanySize,
    ContactRole,
    ContactStatus,
    IndustryType,
    OrganizationStatus,
    PaymentModel,
    VatRate,
    VatTreatment,
)
from app.modules.organizations.models import Organization, OrgContact, OrgPaymentConfig, OrgPaymentMethod
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.user.models import User
from app.modules.vehicles.enums import (
    FuelType,
    LiveStatus,
    VehicleAvailability,
    VehicleStatus,
    VehicleType,
)
from app.modules.vehicles.models import Vehicle

DEMO_DEPOT_ID = "00000000-0000-4000-8000-000000000107"
DEMO_DRIVER_USER_ID = "00000000-0000-4000-8000-000000000101"
DEMO_DRIVER_ID = "00000000-0000-4000-8000-000000000102"
DEMO_VEHICLE_ID = "00000000-0000-4000-8000-000000000103"
DEMO_CREW_ID = "00000000-0000-4000-8000-000000000104"
DEMO_CUSTOMER_USER_ID = "00000000-0000-4000-8000-000000000105"
DEMO_ORG_ID = "00000000-0000-4000-8000-000000000106"
DEMO_ORG_CONTACT_ID = "00000000-0000-4000-8000-00000000010A"
DEMO_ORG_PAYMENT_CONFIG_ID = "00000000-0000-4000-8000-00000000010B"
DEMO_ORG_PAYMENT_METHOD_ID = "00000000-0000-4000-8000-00000000010C"

DEMO_DRIVER_EMAIL = "demo.driver@swcouriers.invalid"
DEMO_DRIVER_PASSWORD = "DemoDriverPass1!"
DEMO_CUSTOMER_EMAIL = "demo.customer@swcouriers.invalid"
DEMO_CUSTOMER_PASSWORD = "DemoCustomerPass1!"

DEPOT_LAT = 51.5267
DEPOT_LNG = -0.0119

DEMO_PRICING_PLANS: list[dict] = [
    {
        "id_price_tier": "tier-standard-v1",
        "plain_name": "STANDARD",
        "plain_type": "STANDARD",
        "days": 3,
        "base_price": "5.00",
        "price_per_package": "3.50",
        "price_per_kg": "0.40",
    },
    {
        "id_price_tier": "tier-express-v1",
        "plain_name": "EXPRESS",
        "plain_type": "EXPRESS",
        "days": 1,
        "base_price": "9.00",
        "price_per_package": "6.50",
        "price_per_kg": "0.80",
    },
]


PICKUP_ADDRESSES: list[dict[str, object]] = [
    {
        "id": "00000000-0000-4000-8000-000000000201",
        "label": "Canary Wharf Hub",
        "line_1": "1 Canada Square",
        "city": "London",
        "postcode": "E14 5AB",
        "latitude": 51.5054,
        "longitude": -0.0235,
    },
    {
        "id": "00000000-0000-4000-8000-000000000202",
        "label": "Shoreditch Studio",
        "line_1": "1 Spital Square",
        "city": "London",
        "postcode": "E1 6DY",
        "latitude": 51.5215,
        "longitude": -0.0759,
    },
    {
        "id": "00000000-0000-4000-8000-000000000203",
        "label": "Whitechapel Warehouse",
        "line_1": "1 Whitechapel Road",
        "city": "London",
        "postcode": "E1 1DU",
        "latitude": 51.5165,
        "longitude": -0.0613,
    },
    {
        "id": "00000000-0000-4000-8000-000000000204",
        "label": "Stratford Fulfilment",
        "line_1": "2 Stratford Place",
        "city": "London",
        "postcode": "E20 1EJ",
        "latitude": 51.5416,
        "longitude": -0.0042,
    },
    {
        "id": "00000000-0000-4000-8000-000000000205",
        "label": "City Mailroom",
        "line_1": "30 St Mary Axe",
        "city": "London",
        "postcode": "EC3A 8BF",
        "latitude": 51.5144,
        "longitude": -0.0803,
    },
    {
        "id": "00000000-0000-4000-8000-000000000206",
        "label": "Kings Cross Loft",
        "line_1": "1 Granary Square",
        "city": "London",
        "postcode": "N1C 4AA",
        "latitude": 51.5365,
        "longitude": -0.1255,
    },
    {
        "id": "00000000-0000-4000-8000-000000000207",
        "label": "Hackney Print House",
        "line_1": "1 Empson Street",
        "city": "London",
        "postcode": "E3 3LT",
        "latitude": 51.5290,
        "longitude": -0.0186,
    },
    {
        "id": "00000000-0000-4000-8000-000000000208",
        "label": "Bermondsey Depot",
        "line_1": "1 Tower Bridge Road",
        "city": "London",
        "postcode": "SE1 4TR",
        "latitude": 51.5028,
        "longitude": -0.0763,
    },
]


async def _upsert_depot(session) -> Depot:
    depot = await session.get(Depot, DEMO_DEPOT_ID)
    if depot is None:
        depot = Depot(
            id=DEMO_DEPOT_ID,
            name="SWC London Demo Depot",
            code="DEMO-LDN-01",
            address_line_1="1 Bromley-by-Bow",
            city="London",
            postcode="E3 3JJ",
            latitude=DEPOT_LAT,
            longitude=DEPOT_LNG,
            timezone="Europe/London",
            status="active",
        )
        session.add(depot)
        await session.flush()
    return depot


async def _upsert_driver_user_and_profile(session, depot_id: str) -> tuple[User, Driver]:
    user = await session.get(User, DEMO_DRIVER_USER_ID)
    if user is None:
        user = User(
            id=DEMO_DRIVER_USER_ID,
            email=DEMO_DRIVER_EMAIL,
            phone="07700900200",
            first_name="Demo",
            last_name="Driver",
            title=UserTitle.MR,
            password_hash=hash_password(DEMO_DRIVER_PASSWORD),
            role=UserRole.DRIVER,
            status=UserStatus.ACTIVE,
            email_verified=True,
            force_password_change=False,
        )
        session.add(user)
        await session.flush()

    driver = await session.get(Driver, DEMO_DRIVER_ID)
    if driver is None:
        driver = Driver(
            id=DEMO_DRIVER_ID,
            user_id=user.id,
            depot_id=depot_id,
            capacities=["VAN"],
            driver_type=DriverType.INTERNAL.value,
            address_line1="1 Driver Lane",
            city="London",
            postcode="E3 3JJ",
            state="England",
            account_status=DriverAccountStatus.ACTIVE,
            live_status=DriverLiveStatus.OFFLINE,
        )
        session.add(driver)
        await session.flush()
    elif driver.depot_id != depot_id:
        driver.depot_id = depot_id
        await session.flush()
    return user, driver


async def _upsert_vehicle(session, depot_id: str) -> Vehicle:
    vehicle = await session.get(Vehicle, DEMO_VEHICLE_ID)
    if vehicle is None:
        vehicle = Vehicle(
            id=DEMO_VEHICLE_ID,
            registration_number="DEMO-VAN-01",
            depot_id=depot_id,
            make="Ford",
            model="Transit",
            year=datetime.now(UTC).year - 1,
            vehicle_type=VehicleType.INTERNAL,
            fuel_type=FuelType.DIESEL,
            cargo_volume_m3=11.0,
            max_payload_kg=1000.0,
            status=VehicleStatus.ACTIVE,
            availability=VehicleAvailability.ACTIVE,
            live_status=LiveStatus.IDLE,
            current_mileage=12000,
        )
        session.add(vehicle)
        await session.flush()
    elif vehicle.depot_id != depot_id:
        vehicle.depot_id = depot_id
        await session.flush()
    return vehicle


async def _upsert_crew(session, *, driver_user_id: str, vehicle_id: str) -> Crew:
    crew = await session.get(Crew, DEMO_CREW_ID)
    if crew is None:
        crew = Crew(
            id=DEMO_CREW_ID,
            driver_id=driver_user_id,
            vehicle_id=vehicle_id,
            started_at=datetime.now(UTC),
        )
        session.add(crew)
        await session.flush()
    elif crew.ended_at is not None or crew.driver_id != driver_user_id or crew.vehicle_id != vehicle_id:
        crew.driver_id = driver_user_id
        crew.vehicle_id = vehicle_id
        crew.ended_at = None
        await session.flush()
    return crew


async def _upsert_organization(session) -> Organization:
    """Org must be ACTIVE and carry pricing_plans for OrderService.create_order to succeed."""
    org = await session.get(Organization, DEMO_ORG_ID)
    if org is None:
        org = Organization(
            id=DEMO_ORG_ID,
            reference="DEMO-ORG-01",
            trading_name="Demo Senders Ltd",
            legal_entity_name="Demo Senders Ltd",
            industry=IndustryType.ECOMMERCE,
            company_size=CompanySize.EMPLOYEES_11_50,
            reg_address_line_1="1 Demo Quay",
            reg_city="London",
            reg_postcode="E3 3JJ",
            status=OrganizationStatus.ACTIVE,
            pricing_plans=DEMO_PRICING_PLANS,
        )
        session.add(org)
        await session.flush()
    else:
        dirty = False
        if org.status != OrganizationStatus.ACTIVE:
            org.status = OrganizationStatus.ACTIVE
            dirty = True
        if org.pricing_plans != DEMO_PRICING_PLANS:
            org.pricing_plans = DEMO_PRICING_PLANS
            dirty = True
        if dirty:
            await session.flush()
    return org


async def _upsert_org_payment_config(session, org_id: str) -> OrgPaymentConfig:
    pc = await session.get(OrgPaymentConfig, DEMO_ORG_PAYMENT_CONFIG_ID)
    if pc is None:
        pc = OrgPaymentConfig(
            id=DEMO_ORG_PAYMENT_CONFIG_ID,
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
    return pc


async def _upsert_org_payment_method(session, org_id: str) -> OrgPaymentMethod:
    """CREDIT_ACCOUNT default — avoids Braintree round-trip in OrderService.create_order."""
    pm = await session.get(OrgPaymentMethod, DEMO_ORG_PAYMENT_METHOD_ID)
    if pm is None:
        pm = OrgPaymentMethod(
            id=DEMO_ORG_PAYMENT_METHOD_ID,
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
    return pm


async def _upsert_customer_user(session, org_id: str) -> User:
    cust = await session.get(User, DEMO_CUSTOMER_USER_ID)
    if cust is None:
        cust = User(
            id=DEMO_CUSTOMER_USER_ID,
            email=DEMO_CUSTOMER_EMAIL,
            phone="07700900300",
            first_name="Demo",
            last_name="Customer",
            title=UserTitle.MS,
            password_hash=hash_password(DEMO_CUSTOMER_PASSWORD),
            role=UserRole.CUSTOMER_B2B,
            status=UserStatus.ACTIVE,
            organization_id=org_id,
            email_verified=True,
            force_password_change=False,
        )
        session.add(cust)
        await session.flush()
    elif cust.organization_id != org_id:
        cust.organization_id = org_id
        await session.flush()
    return cust


async def _upsert_org_contact(session, *, org_id: str, customer_user_id: str) -> OrgContact:
    """OrgContact (ACCOUNT_OWNER, ACTIVE) — required by admin B2B booking, harmless for self-serve."""
    contact = await session.get(OrgContact, DEMO_ORG_CONTACT_ID)
    if contact is None:
        contact = OrgContact(
            id=DEMO_ORG_CONTACT_ID,
            organization_id=org_id,
            contact_number="OC-DEMO-001",
            contact_role=ContactRole.ACCOUNT_OWNER,
            status=ContactStatus.ACTIVE,
            is_primary=True,
            user_id=customer_user_id,
        )
        session.add(contact)
        await session.flush()
    else:
        dirty = False
        if contact.status != ContactStatus.ACTIVE:
            contact.status = ContactStatus.ACTIVE
            dirty = True
        if contact.user_id != customer_user_id:
            contact.user_id = customer_user_id
            dirty = True
        if dirty:
            await session.flush()
    return contact


async def _upsert_pickup_addresses(session, org_id: str) -> list[PickupAddress]:
    """Upsert all demo pickup addresses keyed by hardcoded UUIDs (idempotent)."""
    out: list[PickupAddress] = []
    for i, defn in enumerate(PICKUP_ADDRESSES):
        pa_id = str(defn["id"])
        pa = await session.get(PickupAddress, pa_id)
        if pa is None:
            pa = PickupAddress(
                id=pa_id,
                organization_id=org_id,
                label=defn["label"],
                line_1=defn["line_1"],
                city=defn["city"],
                postcode=defn["postcode"],
                latitude=defn["latitude"],
                longitude=defn["longitude"],
                is_default=(i == 0),
            )
            session.add(pa)
            await session.flush()
        out.append(pa)
    return out


async def _run() -> None:
    async with get_async_session() as session:
        depot = await _upsert_depot(session)
        user, driver = await _upsert_driver_user_and_profile(session, depot_id=depot.id)
        vehicle = await _upsert_vehicle(session, depot_id=depot.id)
        crew = await _upsert_crew(session, driver_user_id=user.id, vehicle_id=vehicle.id)
        org = await _upsert_organization(session)
        await _upsert_org_payment_config(session, org_id=org.id)
        await _upsert_org_payment_method(session, org_id=org.id)
        cust = await _upsert_customer_user(session, org_id=org.id)
        await _upsert_org_contact(session, org_id=org.id, customer_user_id=cust.id)
        pickups = await _upsert_pickup_addresses(session, org_id=org.id)
        await session.commit()

    print("=" * 72)
    print("Demo actors ready (hardcoded IDs):")
    print(f"  Depot         : {depot.id}  code={depot.code}  ({DEPOT_LAT},{DEPOT_LNG})")
    print(f"  Driver user   : {user.id}   email={user.email}")
    print(f"  Driver        : {driver.id}")
    print(f"  Vehicle       : {vehicle.id}  reg={vehicle.registration_number}")
    print(f"  Crew (open)   : {crew.id}")
    print(f"  Organization  : {org.id}  ref={org.reference}  status={org.status}")
    print(f"  Customer user : {cust.id}  email={cust.email}")
    print(f"  Pickup addrs  : {len(pickups)} (8 client warehouses)")
    print()
    print("Driver login:")
    print("  POST /v1/auth/login  X-Client-Type: DRIVER")
    print(f"  {{ \"email\": {DEMO_DRIVER_EMAIL!r}, \"password\": {DEMO_DRIVER_PASSWORD!r} }}")
    print("Customer login (B2B portal):")
    print(f"  {{ \"email\": {DEMO_CUSTOMER_EMAIL!r}, \"password\": {DEMO_CUSTOMER_PASSWORD!r} }}")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_run())
