"""Driver ORM models for driver profiles, drafts, and documents."""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion
from app.modules.drivers.enums import DriverAccountStatus, DriverCapacity, DriverLiveStatus, DriverType, TimeOffType, TrafficViolationStatus, TrafficViolationType

driver_code_seq = Sequence("driver_code_seq", metadata=Base.metadata)
draft_code_seq = Sequence("draft_code_seq", metadata=Base.metadata)


class Driver(BaseModel):
    """Driver profile for admin/ops, linked to a user account.

    Includes driver_code (DR-NNN), basic contact details, type, address,
    profile photo metadata, and link to structured documents.
    """

    __tablename__ = "drivers"

    # ── Identity / code ───────────────────────
    driver_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'DR-' || lpad(nextval('{driver_code_seq.name}')::text, 3, '0')"),
        doc="Human-friendly driver id in format DR-NNN",
    )

    # ── Link to user ─────────────────────────
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )

    # Identity/contact fields are stored on users and accessed via user relation.

    # ── Assignment ───────────────────────────
    depot_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("depots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Operational (static profile data) ────
    license_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    license_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_stops: Mapped[int | None] = mapped_column(Integer, default=30, nullable=True)
    territory_tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    capacities: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(20)),
        nullable=True,
        default=None,
        doc="Vehicle capacities (one or more of VAN/TRUCK).",
    )

    driver_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
        doc="INTERNAL or EXTERNAL (see DriverType enum)",
    )

    # ── Statuses ─────────────────────────────
    account_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DriverAccountStatus.DRAFT,
        doc="Lifecycle / account status (DRAFT, ACTIVE, SUSPENDED, INACTIVE).",
    )

    live_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DriverLiveStatus.OFFLINE,
        doc="Live operational status (ON_ROUTE, ON_BREAK, TIME_OFF, RETURNING, OFFLINE, NON_WORKING_DAY).",
    )

    # ── KPIs / meta ───────────────────────────
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_time_deliveries: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Contact / address ─────────────────────
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Profile photo ─────────────────────────
    profile_photo_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Opaque key / image id stored in Cloudflare Images (private).",
    )

    # ── Mobile onboarding consents/preferences ─────────
    terms_and_conditions_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("driver_terms_and_conditions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    terms_accepted_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    map_preference: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Operational configuration (admin / scheduling) ────────────────────────
    okay_with_layover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    layover_cost_per_night: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        doc="GBP per night when layovers apply.",
    )
    max_layover_nights: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    # ── Relationships ────────────────────────
    user = relationship("User", lazy="raise", foreign_keys=[user_id])
    accepted_terms = relationship("DriverTermsAndConditions", lazy="raise", foreign_keys=[terms_and_conditions_id])
    depot = relationship("Depot", lazy="raise", foreign_keys=[depot_id])
    vehicle = relationship("Vehicle", lazy="raise", foreign_keys=[vehicle_id])
    documents = relationship(
        "DriverDocument",
        back_populates="driver",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    draft = relationship(
        "DriverDraft",
        back_populates="driver",
        cascade="all, delete-orphan",
        lazy="raise",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Driver code={self.driver_code} user={self.user_id}>"


class DriverDocument(BaseModelNoVersion):
    """Documents associated with a driver (licence, CPC, tachograph, custom)."""

    __tablename__ = "driver_documents"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="DRIVING_LICENCE",
        doc="One of DriverDocumentKind values.",
    )

    # Human-friendly title; required for CUSTOM, optional otherwise.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Opaque key to encrypted object stored in R2 (or similar).
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)

    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Mark whether this was part of the initial required set or added later.
    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    driver = relationship("Driver", back_populates="documents", lazy="raise")

    def __repr__(self) -> str:
        return f"<DriverDocument driver={self.driver_id} kind={self.kind} title={self.title}>"


class DriverTermsAndConditions(BaseModelNoVersion):
    """Legal terms document metadata shown/accepted by drivers."""

    __tablename__ = "driver_terms_and_conditions"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clauses = relationship(
        "DriverTermsClause",
        back_populates="terms",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<DriverTermsAndConditions active={self.is_active}>"


class DriverTermsClause(BaseModelNoVersion):
    """Ordered clause/point under a terms document."""

    __tablename__ = "driver_terms_clauses"
    __table_args__ = (UniqueConstraint("terms_id", "clause_order", name="uq_driver_terms_clauses_terms_order"),)

    terms_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("driver_terms_and_conditions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_order: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    terms = relationship("DriverTermsAndConditions", back_populates="clauses", lazy="raise")


class DriverTermsAcceptanceRecord(AppendOnlyModel):
    """Append-only audit row for each driver T&C + location consent acceptance.

    Supports multi-device and re-accept flows: one row per successful ``POST …/onboarding-consents``.
    Request IP and ``User-Agent`` are captured server-side; optional ``device_info`` comes from the client.
    """

    __tablename__ = "driver_terms_acceptance_records"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    terms_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("driver_terms_and_conditions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_info: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    device_installation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    driver = relationship("Driver", lazy="raise", foreign_keys=[driver_id])
    terms = relationship("DriverTermsAndConditions", lazy="raise", foreign_keys=[terms_id])


class DriverDraft(BaseModelNoVersion):
    """Pivot/audit row for a driver draft.

    One row per driver (UNIQUE(driver_id)). draft_id is a stable, human-friendly
    code like DF-001 generated at DB level via draft_code_seq.
    """

    __tablename__ = "driver_drafts"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    draft_id: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'DF-' || lpad(nextval('{draft_code_seq.name}')::text, 3, '0')"),
        doc="Human-friendly draft id in format DF-NNN",
    )

    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_submitted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
        doc="True after final submit completes (user created + driver linked/activated).",
    )

    draft_data: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="JSON payload for draft form state (identity + address/capacities/docs metadata).",
    )

    driver = relationship("Driver", back_populates="draft", lazy="raise")


class DriverTimeOff(BaseModelNoVersion):
    """Planned time off records (annual leave / unpaid) for a driver."""

    __tablename__ = "driver_time_off"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TimeOffType.ANNUAL_LEAVE,
        doc="One of TimeOffType values (ANNUAL_LEAVE, SICK_LEAVE, etc.).",
    )

    days: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="Pre-computed number of days in the range.")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, doc="Whether the leave is paid or unpaid.")

    driver = relationship("Driver", lazy="raise")


class DriverWeeklySchedule(BaseModelNoVersion):
    """Recurring weekly work pattern for a driver (Mon–Sun)."""

    __tablename__ = "driver_weekly_schedule"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 0 = Monday, 6 = Sunday
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    driver = relationship("Driver", lazy="raise")


class DriverTrafficViolation(BaseModel):
    """Traffic violations (tickets) associated with a driver."""

    __tablename__ = "driver_traffic_violations"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    violation_type: Mapped[TrafficViolationType] = mapped_column(
        SAEnum(TrafficViolationType, name="traffic_violation_type_enum"),
        nullable=False,
        doc="One of TrafficViolationType values (SPEEDING, RED_LIGHT, PARKING, BUS_LANE).",
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[TrafficViolationStatus] = mapped_column(
        SAEnum(TrafficViolationStatus, name="traffic_violation_status_enum"),
        nullable=False,
        default=TrafficViolationStatus.UNPAID,
        doc="PAID or UNPAID.",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    driver = relationship("Driver", lazy="raise")
    proofs = relationship(
        "DriverTrafficViolationProof",
        back_populates="violation",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class DriverTrafficViolationProof(BaseModelNoVersion):
    """Proof file for a traffic violation (one violation can have many proofs)."""

    __tablename__ = "driver_traffic_violation_proofs"

    violation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("driver_traffic_violations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    violation = relationship("DriverTrafficViolation", back_populates="proofs", lazy="raise")


class DriverShift(BaseModel):
    """A scheduled shift for a driver on a specific date.

    Migrated from app.modules.shifts.models so schedule lives under drivers.
    Schema (table name + columns) kept identical.
    """

    __tablename__ = "driver_shifts"

    __table_args__ = (UniqueConstraint("driver_id", "shift_date", name="uq_driver_shifts_driver_date"),)

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    shift_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")

    origin: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="MANUAL",
        doc="ShiftOrigin value: WEEKLY_TEMPLATE or MANUAL.",
    )

    driver = relationship("Driver", lazy="raise", foreign_keys=[driver_id])

    def __repr__(self) -> str:
        return f"<DriverShift driver={self.driver_id} date={self.shift_date}>"
