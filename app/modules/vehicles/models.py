from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.enums.sequence import SequentialPrefix
from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion
from app.modules.vehicles.enums import (
    DefectSeverity,
    DefectStatus,
    DocumentType,
    LiveStatus,
    MaintenanceProviderType,
    ScheduleEntrySource,
    ScheduleEventType,
    ServiceStatus,
    VehicleStatus,
    VehicleType,
)

if TYPE_CHECKING:
    from app.modules.user.models import User

fleet_number_seq = Sequence("fleet_number_seq", metadata=Base.metadata)
maintenance_ref_seq = Sequence("maintenance_ref_seq", metadata=Base.metadata)
defect_ref_seq = Sequence("defect_ref_seq", metadata=Base.metadata)
draft_number_seq = Sequence("draft_number_seq", metadata=Base.metadata)


class Vehicle(BaseModel):
    __tablename__ = "vehicles"

    # Identity
    registration_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        unique=True,
        index=True,
    )
    fleet_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.VEHICLE}-' || lpad(nextval('{fleet_number_seq.name}')::text, 3, '0')"),
    )
    fleet_custom_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Classification
    vehicle_type: Mapped[VehicleType] = mapped_column(
        Enum(VehicleType, native_enum=False),
        nullable=True,
    )
    fuel_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Capacity & performance
    cargo_volume_m3: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_payload_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_mpg: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_miles: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Odometer
    current_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0, server_default="0")

    # Service intervals
    service_interval_miles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_service_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_service_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    driver_service_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_continuous_driving_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    break_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Compliance (indexed for dashboard MOT/Tax filters: compare to today)
    mot_expiry: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    tax_due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    insurance_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Assignment
    depot_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("depots.id", name="vehicles_depot_id_fkey", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    preferred_driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="vehicles_preferred_driver_id_fkey", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Lifecycle status — DRAFT until published, then ACTIVE forever
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus, native_enum=False),
        nullable=False,
        default=VehicleStatus.ACTIVE,
        server_default=VehicleStatus.ACTIVE,
        index=True,
    )
    # Operational availability — user-controlled
    availability: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
        index=True,
    )
    live_status: Mapped[LiveStatus] = mapped_column(
        Enum(LiveStatus, native_enum=False),
        nullable=False,
        default=LiveStatus.IDLE,
        server_default=LiveStatus.IDLE,
    )
    availability_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    availability_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    images: Mapped[list["VehicleImage"]] = relationship(
        "VehicleImage",
        back_populates="vehicle",
        order_by="VehicleImage.created_at",
        lazy="raise",
        passive_deletes=True,
    )
    preferred_driver: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[preferred_driver_id],
        lazy="raise",
    )

    __table_args__ = (Index("ix_vehicles_created_at", "created_at"),)

    def __repr__(self) -> str:
        return f"<Vehicle {self.registration_number}>"


class VehicleDeletionLog(AppendOnlyModel):
    __tablename__ = "vehicle_deletions"

    vehicle_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    registration_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    deletion_reason: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="vehicle_deletions_deleted_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )

    deleted_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[deleted_by_id],
        lazy="raise",
    )

    __table_args__ = (Index("ix_vehicle_deletions_created_at", "created_at"),)


class VehicleMaintenanceRecord(BaseModelNoVersion):
    __tablename__ = "vehicle_maintenance_records"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
    )
    reference: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.MAINTENANCE}-' || lpad(nextval('{maintenance_ref_seq.name}')::text, 5, '0')"),
    )
    maintenance_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    provider_type: Mapped[MaintenanceProviderType] = mapped_column(
        Enum(MaintenanceProviderType, native_enum=False),
        nullable=False,
        default=MaintenanceProviderType.EXTERNAL,
        server_default=MaintenanceProviderType.EXTERNAL,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    garage: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (Index("ix_vehicle_maintenance_records_vehicle_id_created_at", "vehicle_id", "created_at"),)


class VehicleDefect(BaseModel):
    __tablename__ = "vehicle_defects"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.DEFECT}-' || lpad(nextval('{defect_ref_seq.name}')::text, 5, '0')"),
    )
    inspection_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicle_inspections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    route_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    reported_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    severity: Mapped[DefectSeverity] = mapped_column(
        Enum(DefectSeverity, native_enum=False),
        nullable=False,
    )
    status: Mapped[DefectStatus] = mapped_column(
        Enum(DefectStatus, native_enum=False),
        nullable=False,
        default=DefectStatus.PENDING,
        server_default=DefectStatus.PENDING,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_to_drive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    images: Mapped[list["VehicleDefectImage"]] = relationship(
        "VehicleDefectImage",
        back_populates="defect",
        order_by="VehicleDefectImage.created_at",
        lazy="raise",
    )
    reported_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reported_by_id],
        lazy="raise",
    )


class VehicleDefectImage(AppendOnlyModel):
    __tablename__ = "vehicle_defect_images"

    defect_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicle_defects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    defect: Mapped["VehicleDefect"] = relationship("VehicleDefect", back_populates="images")
    uploaded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class VehicleServiceRecord(BaseModelNoVersion):
    __tablename__ = "vehicle_service_records"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    service_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    next_service_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    mileage_at_service: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, native_enum=False),
        nullable=False,
        default=ServiceStatus.COMPLETED,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class VehicleDocument(BaseModelNoVersion):
    __tablename__ = "vehicle_documents"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uploaded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (Index("ix_vehicle_documents_vehicle_id_created_at", "vehicle_id", "created_at"),)


class VehicleImage(AppendOnlyModel):
    __tablename__ = "vehicle_images"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="images")
    uploaded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class VehicleDraft(BaseModelNoVersion):
    """Pivot table linking a draft number (DR-001) to a vehicle in DRAFT status."""

    __tablename__ = "vehicle_drafts"

    draft_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.DRAFT}-' || lpad(nextval('{draft_number_seq.name}')::text, 3, '0')"),
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    published_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="vehicle_drafts_published_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )

    vehicle: Mapped["Vehicle"] = relationship("Vehicle", lazy="raise")


class VehicleScheduleEntry(BaseModelNoVersion):
    """One date range on the vehicle calendar (one row per block, not per day). See SCHEDULE_SPEC.md for full rules.

    How scheduling works:
    - Each row is a range (date_from, date_to) with a type and source. date_to can be null = indefinite from date_from.
    - Log maintenance → one row for that window (source=MAINTENANCE).
    - Change availability to UNAVAILABLE: one row. If effective_to is omitted, date_to is null (indefinitely unavailable).
    - Change availability back to e.g. ACTIVE ("mark available"): we close open AVAILABILITY ranges (set date_to = today), preserving history; no rows deleted.
    - GET schedule: loads overlapping ranges, expands to per-day (indefinite = from date_from to end of requested range), merges by priority, returns events + utilization.
    """

    __tablename__ = "vehicle_schedule_entries"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    type: Mapped[ScheduleEventType] = mapped_column(
        Enum(ScheduleEventType, native_enum=False),
        nullable=False,
        index=True,
    )
    source: Mapped[ScheduleEntrySource] = mapped_column(
        Enum(ScheduleEntrySource, native_enum=False),
        nullable=False,
    )
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    