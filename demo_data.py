"""Seed realistic demo data for the drivers module.

Creates the full dependency chain:
  Region → Depot → Vehicle → User (DRIVER role) → Driver → DriverDraft
  → DriverDocument → DriverTermsAndConditions → DriverTermsClause
  → DriverTermsAcceptanceRecord → DriverTimeOff → DriverWeeklySchedule
  → DriverTrafficViolation → DriverTrafficViolationProof → DriverShift

**Idempotent:** safe to run repeatedly. Uses **get-or-create** on region, depot, vehicles,
terms (+ each clause), users, drivers, drafts, documents, schedules, time off, violations,
shifts, route plans, and routes—no duplicate inserts when data already exists.

Usage:
    python demo_data.py
    python demo_data.py --count 5
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
from datetime import UTC, date, datetime, time, timedelta

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.core.security import hash_password
from app.common.enums import UserRole, UserStatus, UserTitle
from app.modules.planning.enums import RoutePlanStatus, RouteStatus
from app.modules.planning.models import Route, RoutePlan
from app.modules.regions.models import Region
from app.modules.depots.models import Depot
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.enums import FuelType, VehicleType, VehicleStatus, VehicleAvailability, LiveStatus
from app.modules.user.models import User
from app.modules.drivers.models import (
    Driver,
    DriverDocument,
    DriverDraft,
    DriverTermsAndConditions,
    DriverTermsClause,
    DriverTermsAcceptanceRecord,
    DriverTimeOff,
    DriverWeeklySchedule,
    DriverTrafficViolation,
    DriverTrafficViolationProof,
    DriverShift,
)
from app.modules.drivers.enums import (
    DriverAccountStatus,
    DriverCapacity,
    DriverDocumentKind,
    DriverLiveStatus,
    DriverType,
    TimeOffType,
    TrafficViolationStatus,
    TrafficViolationType,
)
from sqlalchemy import func, select, text


# ── Static pool data ─────────────────────────────────────────────────────────

DRIVER_PROFILES = [
    {"first_name": "James",   "last_name": "Harper",    "email": "james.harper@swcouriers.co.uk",    "phone": "07700900101", "title": UserTitle.MR},
    {"first_name": "Priya",   "last_name": "Sharma",    "email": "priya.sharma@swcouriers.co.uk",    "phone": "07700900102", "title": UserTitle.MS},
    {"first_name": "Marcus",  "last_name": "Okafor",    "email": "marcus.okafor@swcouriers.co.uk",   "phone": "07700900103", "title": UserTitle.MR},
    {"first_name": "Aoife",   "last_name": "Brennan",   "email": "aoife.brennan@swcouriers.co.uk",   "phone": "07700900104", "title": UserTitle.MS},
    {"first_name": "Daniel",  "last_name": "Kowalski",  "email": "daniel.kowalski@swcouriers.co.uk", "phone": "07700900105", "title": UserTitle.MR},
    {"first_name": "Sunita",  "last_name": "Patel",     "email": "sunita.patel@swcouriers.co.uk",    "phone": "07700900106", "title": UserTitle.MRS},
    {"first_name": "Ryan",    "last_name": "O'Brien",   "email": "ryan.obrien@swcouriers.co.uk",     "phone": "07700900107", "title": UserTitle.MR},
    {"first_name": "Fatima",  "last_name": "Al-Rashid", "email": "fatima.alrashid@swcouriers.co.uk", "phone": "07700900108", "title": UserTitle.MS},
    {"first_name": "Thomas",  "last_name": "Eriksson",  "email": "thomas.eriksson@swcouriers.co.uk", "phone": "07700900109", "title": UserTitle.MR},
    {"first_name": "Chioma",  "last_name": "Eze",       "email": "chioma.eze@swcouriers.co.uk",      "phone": "07700900110", "title": UserTitle.MS},
]

VEHICLES = [
    {"registration_number": "LK72 ABC", "make": "Ford",      "model": "Transit",       "year": 2022, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 9.0,  "max_payload_kg": 1200.0},
    {"registration_number": "BN21 XYZ", "make": "Mercedes",  "model": "Sprinter",      "year": 2021, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 11.5, "max_payload_kg": 1500.0},
    {"registration_number": "YE70 DEF", "make": "Vauxhall",  "model": "Movano",        "year": 2020, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 17.0, "max_payload_kg": 3500.0},
    {"registration_number": "OU23 GHI", "make": "Renault",   "model": "Master",        "year": 2023, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.ELECTRIC,"cargo_volume_m3": 10.8, "max_payload_kg": 1300.0},
    {"registration_number": "SN19 JKL", "make": "Volkswagen","model": "Crafter",       "year": 2019, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 10.0, "max_payload_kg": 1400.0},
    {"registration_number": "WK22 MNO", "make": "Iveco",     "model": "Daily",         "year": 2022, "vehicle_type": VehicleType.EXTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 20.0, "max_payload_kg": 5000.0},
    {"registration_number": "HG20 PQR", "make": "Ford",      "model": "Transit Custom","year": 2020, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.PETROL,  "cargo_volume_m3": 6.0,  "max_payload_kg": 900.0},
    {"registration_number": "FP71 STU", "make": "Mercedes",  "model": "eVito",         "year": 2021, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.ELECTRIC,"cargo_volume_m3": 6.6,  "max_payload_kg": 900.0},
    {"registration_number": "LV68 VWX", "make": "MAN",       "model": "TGE",           "year": 2018, "vehicle_type": VehicleType.EXTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 22.0, "max_payload_kg": 7500.0},
    {"registration_number": "CT24 YZA", "make": "Peugeot",   "model": "Boxer",         "year": 2024, "vehicle_type": VehicleType.INTERNAL, "fuel_type": FuelType.DIESEL,  "cargo_volume_m3": 8.0,  "max_payload_kg": 1150.0},
]

DEMO_TERMS_TITLE = "SW Couriers Driver Terms and Conditions v2.1"

TERMS_CLAUSES = [
    {"clause_order": 1,  "heading": "Acceptance of Terms",             "body": "By registering and using the SW Couriers Driver App you confirm that you have read, understood, and agree to be bound by these Terms and Conditions in full. These terms form a legally binding agreement between you and SW Couriers Ltd."},
    {"clause_order": 2,  "heading": "Eligibility and Licence",         "body": "You must hold a valid UK driving licence appropriate to the vehicle you operate, have the legal right to work in the United Kingdom, and maintain all documents required by DVLA and HMRC throughout your engagement with SW Couriers."},
    {"clause_order": 3,  "heading": "Location Services",               "body": "The App requires continuous access to your device's GPS location during active shifts. This data is used solely for real-time route optimisation, proof-of-delivery timestamping, and fleet safety monitoring. Location data is never shared with third parties for marketing purposes."},
    {"clause_order": 4,  "heading": "Vehicle Safety and Compliance",   "body": "You are responsible for conducting a pre-shift walkaround check before operating any assigned vehicle. Any defects must be reported immediately via the App or to your depot manager. You must not operate a vehicle that you believe to be unsafe."},
    {"clause_order": 5,  "heading": "Delivery Standards",              "body": "Parcels must be handled with reasonable care. Failed delivery attempts must be recorded accurately in the App with the correct status code. Proof-of-delivery photographs must be clear, timestamped, and include the parcel in its final resting position."},
    {"clause_order": 6,  "heading": "Conduct and Professional Image",  "body": "You represent SW Couriers to our clients and their customers. You must wear the issued uniform, maintain a professional manner at all times, and not use a mobile phone while driving. Misconduct may result in immediate suspension pending investigation."},
    {"clause_order": 7,  "heading": "Data Protection",                 "body": "Customer addresses and personal data accessed through the App are processed under UK GDPR. You must not retain, copy, or share any customer data beyond what is strictly necessary to complete your assigned deliveries."},
    {"clause_order": 8,  "heading": "Traffic Violations and Fines",    "body": "Any fixed penalty notices or court summons incurred while driving a SW Couriers vehicle are the responsibility of the driver. You must notify your depot manager within 24 hours of receiving a penalty. Failure to do so may result in disciplinary action."},
    {"clause_order": 9,  "heading": "Amendment of Terms",              "body": "SW Couriers reserves the right to update these Terms and Conditions. You will be notified via the App and required to re-accept before your next shift. Continued use of the App following notification constitutes acceptance of the revised terms."},
    {"clause_order": 10, "heading": "Governing Law",                   "body": "These Terms are governed by and construed in accordance with the laws of England and Wales. Any disputes shall be subject to the exclusive jurisdiction of the courts of England and Wales."},
]


def _content_hash(clauses: list[dict]) -> str:
    raw = "|".join(f"{c['clause_order']}:{c['body']}" for c in sorted(clauses, key=lambda x: x["clause_order"]))
    return hashlib.sha256(raw.encode()).hexdigest()


def _shift_start_end(shift_date: date) -> tuple[datetime, datetime]:
    start = datetime(shift_date.year, shift_date.month, shift_date.day, 7, 30, tzinfo=UTC)
    end = datetime(shift_date.year, shift_date.month, shift_date.day, 18, 0, tzinfo=UTC)
    return start, end


_MAX_DR_NUM = text("""
SELECT COALESCE(MAX(substring(trim(driver_code) from '([0-9]+)$')::bigint), 0)
FROM drivers
WHERE trim(driver_code) ~ '^DR-[0-9]+$'
""")
_MAX_DF_NUM = text("""
SELECT COALESCE(MAX(substring(trim(draft_id) from '([0-9]+)$')::bigint), 0)
FROM driver_drafts
WHERE trim(draft_id) ~ '^DF-[0-9]+$'
""")


def _demo_dr_code(n: int) -> str:
    """Match DB style ``DR-`` + zero-padded numeric (min width 3, grows for large n)."""
    return f"DR-{n:03d}"


def _demo_df_id(n: int) -> str:
    return f"DF-{n:03d}"


async def _next_driver_code_num(session) -> int:
    """Next numeric suffix for DR- (1 + max suffix in table, visible in this transaction)."""
    cur = await session.scalar(_MAX_DR_NUM)
    return int(cur or 0) + 1


async def _next_draft_id_num(session) -> int:
    cur = await session.scalar(_MAX_DF_NUM)
    return int(cur or 0) + 1


DEMO_VIOLATION_NOTES = "Issued by enforcement camera. Driver notified same day."
DEMO_TIMEOFF_NOTE_PAST = "Approved annual leave."
DEMO_TIMEOFF_NOTE_FUTURE = "Pre-approved annual leave."


async def _ensure_terms_clauses(session, terms: DriverTermsAndConditions) -> None:
    for clause_data in TERMS_CLAUSES:
        existing = await session.scalar(
            select(DriverTermsClause).where(
                DriverTermsClause.terms_id == terms.id,
                DriverTermsClause.clause_order == clause_data["clause_order"],
            )
        )
        if existing is None:
            session.add(
                DriverTermsClause(
                    terms_id=terms.id,
                    clause_order=clause_data["clause_order"],
                    heading=clause_data["heading"],
                    body=clause_data["body"],
                )
            )
    await session.flush()


async def _get_or_create_demo_user(session, profile: dict) -> tuple[User, bool]:
    """Return (user, created)."""
    row = await session.scalar(select(User).where(User.email == profile["email"]))
    if row is not None:
        return row, False
    user = User(
        email=profile["email"],
        phone=profile["phone"],
        first_name=profile["first_name"],
        last_name=profile["last_name"],
        title=profile["title"],
        position_role="Delivery Driver",
        password_hash=hash_password("Driver@12345!"),
        role=UserRole.DRIVER,
        status=UserStatus.ACTIVE,
        email_verified=True,
        force_password_change=False,
    )
    session.add(user)
    await session.flush()
    return user, True


async def _get_or_create_demo_driver(
    session,
    *,
    user: User,
    depot: Depot,
    vehicle_ids: list[str],
    profile_idx: int,
    terms: DriverTermsAndConditions,
    content_hash: str,
    now: datetime,
    today: date,
) -> tuple[Driver, bool]:
    """Return (driver, created_new)."""
    existing = await session.scalar(select(Driver).where(Driver.user_id == user.id))
    if existing is not None:
        if existing.vehicle_id is None and profile_idx < len(vehicle_ids):
            existing.vehicle_id = vehicle_ids[profile_idx]
            await session.flush()
        return existing, False

    license_categories = ["B", "B+E", "C1", "C1+E", "C", "C+E"]
    dr_num = await _next_driver_code_num(session)
    driver_code = _demo_dr_code(dr_num)
    driver = Driver(
        user_id=user.id,
        depot_id=depot.id,
        vehicle_id=vehicle_ids[profile_idx] if profile_idx < len(vehicle_ids) else None,
        driver_code=driver_code,
        license_number=f"HARP{random.randint(100000, 999999)}J9IJ",
        license_category=random.choice(license_categories),
        max_stops=random.randint(20, 50),
        territory_tags=["SE1", "SE16", "E1W"] if profile_idx % 2 == 0 else ["N1", "EC1", "WC1"],
        capacities=[DriverCapacity.VAN] if profile_idx < 7 else [DriverCapacity.VAN, DriverCapacity.TRUCK],
        driver_type=DriverType.INTERNAL if profile_idx < 7 else DriverType.EXTERNAL,
        account_status=DriverAccountStatus.ACTIVE,
        live_status=DriverLiveStatus.OFFLINE,
        notes=f"Experienced driver. Based out of {depot.name}.",
        safety_score=random.randint(75, 100),
        on_time_deliveries=random.randint(200, 2000),
        address_line1=f"{random.randint(1, 200)} {random.choice(['High Street', 'Church Road', 'Station Road', 'Mill Lane'])}",
        country="United Kingdom",
        state="England",
        city=random.choice(["London", "Croydon", "Bromley", "Lewisham"]),
        postcode=random.choice(["SE1 1AA", "SE16 4NX", "SW1A 1AA", "E1 6RF"]),
        terms_and_conditions_id=terms.id,
        terms_accepted_content_hash=content_hash,
        terms_accepted_at=now - timedelta(days=random.randint(10, 90)),
        location_consent_at=now - timedelta(days=random.randint(10, 90)),
        map_preference="GOOGLE_MAPS",
    )
    session.add(driver)
    await session.flush()
    await session.execute(text("SELECT setval('driver_code_seq', CAST(:n AS bigint), true)"), {"n": dr_num})
    return driver, True


async def _ensure_driver_draft(
    session,
    *,
    driver: Driver,
    user: User,
    now: datetime,
) -> None:
    draft = await session.scalar(select(DriverDraft).where(DriverDraft.driver_id == driver.id))
    if draft is not None:
        return
    df_num = await _next_draft_id_num(session)
    draft_id = _demo_df_id(df_num)
    draft = DriverDraft(
        driver_id=driver.id,
        draft_id=draft_id,
        created_by=None,
        is_submitted=True,
        draft_data={
            "personal": {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone": user.phone,
            },
            "address": {
                "line1": driver.address_line1,
                "city": driver.city,
                "postcode": driver.postcode,
                "country": driver.country,
            },
            "capacity": {"driver_type": driver.driver_type, "capacities": driver.capacities},
            "documents": ["DRIVING_LICENCE"],
            "submitted_at": now.isoformat(),
        },
    )
    session.add(draft)
    await session.flush()
    await session.execute(text("SELECT setval('draft_code_seq', CAST(:n AS bigint), true)"), {"n": df_num})


async def _ensure_driver_licence_document(session, *, driver: Driver, user: User, today: date) -> None:
    doc = await session.scalar(
        select(DriverDocument).where(
            DriverDocument.driver_id == driver.id,
            DriverDocument.kind == DriverDocumentKind.DRIVING_LICENCE,
        )
    )
    if doc is not None:
        return
    doc = DriverDocument(
        driver_id=driver.id,
        kind=DriverDocumentKind.DRIVING_LICENCE,
        title="Driving Licence",
        file_key=f"drivers/{driver.id}/docs/driving_licence_{user.last_name.lower()}.pdf",
        expiry_date=today + timedelta(days=random.randint(365, 3650)),
        content_type="application/pdf",
        size_bytes=random.randint(80_000, 500_000),
        is_initial=True,
    )
    session.add(doc)
    await session.flush()


async def _ensure_driver_terms_acceptance(
    session,
    *,
    driver: Driver,
    terms: DriverTermsAndConditions,
    content_hash: str,
    user: User,
) -> None:
    n = int(
        await session.scalar(
            select(func.count()).select_from(DriverTermsAcceptanceRecord).where(
                DriverTermsAcceptanceRecord.driver_id == driver.id
            )
        )
        or 0
    )
    if n > 0:
        return
    acceptance = DriverTermsAcceptanceRecord(
        driver_id=driver.id,
        terms_id=terms.id,
        content_hash=content_hash,
        ip_address="10.0.1." + str(random.randint(10, 250)),
        user_agent="SW-Couriers-Driver-App/2.4.1 (Android 14; Samsung Galaxy A54)",
        client_type="DRIVER",
        device_info={
            "platform": "android",
            "os_version": "14",
            "app_version": "2.4.1",
            "device_model": "Samsung Galaxy A54",
        },
        device_installation_id=f"did-{user.id[:8]}-{random.randint(1000, 9999)}",
    )
    session.add(acceptance)
    await session.flush()


async def _ensure_driver_weekly_schedule(session, *, driver: Driver) -> None:
    for day_idx in range(7):
        row = await session.scalar(
            select(DriverWeeklySchedule).where(
                DriverWeeklySchedule.driver_id == driver.id,
                DriverWeeklySchedule.day_of_week == day_idx,
            )
        )
        if row is not None:
            continue
        is_active = day_idx < 5
        session.add(
            DriverWeeklySchedule(
                driver_id=driver.id,
                day_of_week=day_idx,
                is_active=is_active,
                start_time=time(7, 30) if is_active else None,
                end_time=time(18, 0) if is_active else None,
            )
        )
    await session.flush()


async def _ensure_driver_demo_time_off(session, *, driver: Driver, today: date) -> None:
    past_n = int(
        await session.scalar(
            select(func.count()).select_from(DriverTimeOff).where(
                DriverTimeOff.driver_id == driver.id,
                DriverTimeOff.notes == DEMO_TIMEOFF_NOTE_PAST,
            )
        )
        or 0
    )
    if past_n == 0:
        past_start = today - timedelta(days=random.randint(60, 120))
        past_end = past_start + timedelta(days=random.randint(3, 7))
        past_days = (past_end - past_start).days + 1
        session.add(
            DriverTimeOff(
                driver_id=driver.id,
                start_date=past_start,
                end_date=past_end,
                type=TimeOffType.ANNUAL_LEAVE,
                days=past_days,
                notes=DEMO_TIMEOFF_NOTE_PAST,
                is_paid=True,
            )
        )
    fut_n = int(
        await session.scalar(
            select(func.count()).select_from(DriverTimeOff).where(
                DriverTimeOff.driver_id == driver.id,
                DriverTimeOff.notes == DEMO_TIMEOFF_NOTE_FUTURE,
            )
        )
        or 0
    )
    if fut_n == 0:
        future_start = today + timedelta(days=random.randint(14, 60))
        future_end = future_start + timedelta(days=random.randint(2, 5))
        future_days = (future_end - future_start).days + 1
        session.add(
            DriverTimeOff(
                driver_id=driver.id,
                start_date=future_start,
                end_date=future_end,
                type=TimeOffType.ANNUAL_LEAVE,
                days=future_days,
                notes=DEMO_TIMEOFF_NOTE_FUTURE,
                is_paid=True,
            )
        )
    await session.flush()


async def _ensure_driver_demo_violation(session, *, driver: Driver, now: datetime) -> None:
    violation = await session.scalar(
        select(DriverTrafficViolation)
        .where(
            DriverTrafficViolation.driver_id == driver.id,
            DriverTrafficViolation.notes == DEMO_VIOLATION_NOTES,
        )
        .limit(1)
    )
    if violation is None:
        viol_date = now - timedelta(days=random.randint(30, 180))
        violation = DriverTrafficViolation(
            driver_id=driver.id,
            occurred_at=viol_date,
            violation_type=random.choice(list(TrafficViolationType)),
            amount=random.choice(["60.00", "100.00", "130.00", "200.00"]),
            status=random.choice([TrafficViolationStatus.PAID, TrafficViolationStatus.UNPAID]),
            notes=DEMO_VIOLATION_NOTES,
        )
        session.add(violation)
        await session.flush()

    proof = await session.scalar(
        select(DriverTrafficViolationProof).where(
            DriverTrafficViolationProof.violation_id == violation.id,
            DriverTrafficViolationProof.file_key == f"drivers/{driver.id}/violations/{violation.id}/notice.pdf",
        )
    )
    if proof is None:
        proof = DriverTrafficViolationProof(
            violation_id=violation.id,
            file_key=f"drivers/{driver.id}/violations/{violation.id}/notice.pdf",
            content_type="application/pdf",
            size_bytes=random.randint(50_000, 200_000),
        )
        session.add(proof)
        await session.flush()


def _demo_shift_dates(*, today: date) -> list[date]:
    shift_days: list[date] = []
    d = today
    for _ in range(10):
        d -= timedelta(days=1)
        if d.weekday() < 5:
            shift_days.append(d)
        if len(shift_days) == 5:
            break
    shift_days.reverse()
    for _ in range(5):
        for offset in range(1, 14):
            candidate = today + timedelta(days=offset)
            if candidate.weekday() < 5 and candidate not in shift_days:
                shift_days.append(candidate)
                break
    return shift_days


async def _ensure_driver_demo_shifts(session, *, driver: Driver, today: date) -> None:
    for sd in _demo_shift_dates(today=today):
        existing = await session.scalar(
            select(DriverShift).where(DriverShift.driver_id == driver.id, DriverShift.shift_date == sd)
        )
        if existing is not None:
            continue
        start_dt, end_dt = _shift_start_end(sd)
        if sd < today:
            status = "completed"
        elif sd == today:
            status = "in_progress"
        else:
            status = "scheduled"
        session.add(
            DriverShift(
                driver_id=driver.id,
                shift_date=sd,
                start_time=start_dt,
                end_time=end_dt,
                status=status,
            )
        )
    await session.flush()


async def seed(count: int) -> None:
    count = min(count, len(DRIVER_PROFILES))
    today = date.today()
    now = datetime.now(UTC)

    async with get_async_session() as session:

        # ── 0. Ensure driver_code_seq exists (may be missing if migration didn't apply it) ─
        await session.execute(text("CREATE SEQUENCE IF NOT EXISTS driver_code_seq START WITH 1 INCREMENT BY 1"))
        await session.execute(text(
            "ALTER TABLE drivers ALTER COLUMN driver_code SET DEFAULT 'DR-' || lpad(nextval('driver_code_seq')::text, 3, '0')"
        ))
        await session.execute(text("CREATE SEQUENCE IF NOT EXISTS draft_code_seq START WITH 1 INCREMENT BY 1"))
        await session.execute(text(
            "ALTER TABLE driver_drafts ALTER COLUMN draft_id SET DEFAULT 'DF-' || lpad(nextval('draft_code_seq')::text, 3, '0')"
        ))
        await session.commit()

        # ── 1. Region ────────────────────────────────────────────────────────
        region_code = "LDN-CENTRAL"
        existing_region = await session.scalar(select(Region).where(Region.code == region_code))
        if existing_region:
            region = existing_region
            print(f"  [skip] Region {region_code} already exists")
        else:
            region = Region(
                name="London Central",
                code=region_code,
                description="Central London operational zone covering zones 1–3.",
                status="active",
            )
            session.add(region)
            await session.flush()
            print(f"  [+] Region: {region.name} ({region.id})")

        # ── 2. Depot ─────────────────────────────────────────────────────────
        depot_code = "LDN-001"
        existing_depot = await session.scalar(select(Depot).where(Depot.code == depot_code))
        if existing_depot:
            depot = existing_depot
            print(f"  [skip] Depot {depot_code} already exists")
        else:
            depot = Depot(
                name="Bermondsey Distribution Centre",
                code=depot_code,
                address_line_1="12 Crimscott Street",
                address_line_2=None,
                city="London",
                postcode="SE1 5TE",
                latitude=51.4981,
                longitude=-0.0783,
                timezone="Europe/London",
                capacity=5000,
                region_id=region.id,
                status="active",
                notes="Primary hub for south-east London last-mile delivery.",
            )
            session.add(depot)
            await session.flush()
            print(f"  [+] Depot: {depot.name} ({depot.id})")

        # ── 3. Vehicles ──────────────────────────────────────────────────────
        vehicle_ids: list[str] = []
        for v_data in VEHICLES[:count]:
            existing_v = await session.scalar(
                select(Vehicle).where(Vehicle.registration_number == v_data["registration_number"])
            )
            if existing_v:
                vehicle_ids.append(existing_v.id)
                print(f"  [skip] Vehicle {v_data['registration_number']} already exists")
                continue
            vehicle = Vehicle(
                registration_number=v_data["registration_number"],
                make=v_data["make"],
                model=v_data["model"],
                year=v_data["year"],
                vehicle_type=v_data["vehicle_type"],
                fuel_type=v_data["fuel_type"],
                cargo_volume_m3=v_data["cargo_volume_m3"],
                max_payload_kg=v_data["max_payload_kg"],
                current_mileage=random.randint(5000, 120000),
                service_interval_miles=10000,
                service_interval_months=12,
                next_service_due=today + timedelta(days=random.randint(14, 180)),
                mot_expiry=today + timedelta(days=random.randint(30, 365)),
                tax_due_date=today + timedelta(days=random.randint(30, 365)),
                insurance_expiry=today + timedelta(days=random.randint(60, 365)),
                depot_id=depot.id,
                status=VehicleStatus.ACTIVE,
                availability=VehicleAvailability.ACTIVE,
                live_status=LiveStatus.IDLE,
            )
            session.add(vehicle)
            await session.flush()
            vehicle_ids.append(vehicle.id)
            print(f"  [+] Vehicle: {vehicle.registration_number} {vehicle.make} {vehicle.model} ({vehicle.id})")

        # ── 4. Terms & Conditions ────────────────────────────────────────────
        terms = await session.scalar(
            select(DriverTermsAndConditions)
            .where(DriverTermsAndConditions.title == DEMO_TERMS_TITLE)
            .order_by(DriverTermsAndConditions.created_at.asc())
            .limit(1)
        )
        terms_created = False
        if terms is None:
            terms = DriverTermsAndConditions(
                title=DEMO_TERMS_TITLE,
                is_active=True,
                effective_from=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            )
            session.add(terms)
            await session.flush()
            terms_created = True
        await _ensure_terms_clauses(session, terms)
        if terms_created:
            print(f"  [+] DriverTermsAndConditions: '{terms.title}' ({terms.id}); clauses ensured")
        else:
            print(f"  [=] DriverTermsAndConditions '{terms.title}' ({terms.id}); clauses ensured")

        content_hash = _content_hash(TERMS_CLAUSES)

        # ── 5. Drivers — get-or-create user, driver, and all demo children ────
        ensured_profiles = 0
        for i, profile in enumerate(DRIVER_PROFILES[:count]):
            user, user_created = await _get_or_create_demo_user(session, profile)
            if user_created:
                print(f"  [+] User: {user.first_name} {user.last_name} <{user.email}> ({user.id})")
            else:
                print(f"  [=] User exists: {profile['email']}")

            driver, driver_created = await _get_or_create_demo_driver(
                session,
                user=user,
                depot=depot,
                vehicle_ids=vehicle_ids,
                profile_idx=i,
                terms=terms,
                content_hash=content_hash,
                now=now,
                today=today,
            )
            if driver_created:
                print(f"    [+] Driver: {driver.driver_code} ({driver.id})")
            else:
                print(f"    [=] Driver exists: {driver.driver_code} ({driver.id})")

            await _ensure_driver_draft(session, driver=driver, user=user, now=now)
            await _ensure_driver_licence_document(session, driver=driver, user=user, today=today)
            await _ensure_driver_terms_acceptance(session, driver=driver, terms=terms, content_hash=content_hash, user=user)
            await _ensure_driver_weekly_schedule(session, driver=driver)
            await _ensure_driver_demo_time_off(session, driver=driver, today=today)
            await _ensure_driver_demo_violation(session, driver=driver, now=now)
            await _ensure_driver_demo_shifts(session, driver=driver, today=today)
            ensured_profiles += 1
            print(f"    [=] Demo graph ensured for {profile['email']}")

        # ── 6. RoutePlans + Routes (one plan per working day, one route per driver) ─
        # Load all drivers for this depot (covers pre-existing ones too)
        all_drivers = list((await session.execute(
            select(Driver).where(Driver.depot_id == depot.id)
        )).scalars().all())
        if all_drivers:
            shift_dates = set(_demo_shift_dates(today=today))

            plans_by_date: dict[date, RoutePlan] = {}
            plans_created = 0
            for svc_date in sorted(shift_dates):
                existing_plan = await session.scalar(
                    select(RoutePlan).where(
                        RoutePlan.depot_id == depot.id,
                        RoutePlan.service_date == svc_date,
                    )
                )
                if existing_plan is not None:
                    plans_by_date[svc_date] = existing_plan
                else:
                    plan_status = (
                        RoutePlanStatus.READY.value if svc_date <= today else RoutePlanStatus.DRAFT.value
                    )
                    plan = RoutePlan(
                        service_date=svc_date,
                        depot_id=depot.id,
                        status=plan_status,
                    )
                    session.add(plan)
                    await session.flush()
                    plans_by_date[svc_date] = plan
                    plans_created += 1

            routes_added = 0
            for driver in all_drivers:
                for svc_date, plan in plans_by_date.items():
                    exists = await session.scalar(
                        select(Route.id).where(Route.plan_id == plan.id, Route.driver_id == driver.id).limit(1)
                    )
                    if exists is not None:
                        continue
                    if svc_date < today:
                        r_status = RouteStatus.COMPLETED.value
                    elif svc_date == today:
                        r_status = RouteStatus.ACTIVE.value
                    else:
                        r_status = RouteStatus.ASSIGNED.value
                    route = Route(
                        plan_id=plan.id,
                        driver_id=driver.id,
                        vehicle_id=driver.vehicle_id,
                        status=r_status,
                        total_stops=random.randint(8, 25),
                    )
                    session.add(route)
                    routes_added += 1
            await session.flush()
            if plans_created or routes_added:
                print(
                    f"  [+] RoutePlans: {plans_created} new, {len(plans_by_date)} total dates; "
                    f"Routes: +{routes_added} inserted"
                )
            else:
                print("  [skip] Demo route plans/routes already present for all drivers on seeded dates")

        await session.commit()

    print()
    print("=" * 60)
    print(f"Demo data seeded successfully.")
    print(f"Profiles ensured : {ensured_profiles}")
    print(f"Default password: Driver@12345!")
    print("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed realistic driver demo data for every driver table.")
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of drivers to seed (max 10, default 5)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(seed(count=args.count))
