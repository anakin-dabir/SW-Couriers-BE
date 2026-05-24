"""Vehicle inspection ORM model.

Defects reported during an inspection are VehicleDefect records with inspection_id FK.
"""


from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel
from app.modules.vehicle_inspections.enums import (
    InspectionResult,
    InspectionStatus,
    InspectionType,
)


class VehicleInspection(BaseModel):
    """A pre-trip inspection submitted by a driver.

    Flow:
    1. POST /vehicle-inspections — creates record with checklist (status=IN_PROGRESS)
    2. POST /vehicle-inspections/{id}/defects — report defects (optional, repeatable)
    3. GET /vehicle-inspections/{id} — view summary
    4. POST /vehicle-inspections/{id}/sign — declaration + signature → finalizes
       - no defects → COMPLETED
       - has defects → AWAITING_RESOLUTION
    5. GET /vehicle-inspections/{id}/status — poll until RESOLVED
    """

    __tablename__ = "vehicle_inspections"

    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    inspection_type: Mapped[InspectionType] = mapped_column(
        Enum(InspectionType, native_enum=False),
        nullable=False,
        default=InspectionType.PRE_TRIP,
    )
    result: Mapped[InspectionResult | None] = mapped_column(
        Enum(InspectionResult, native_enum=False),
        nullable=True,  # set on sign
    )
    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus, native_enum=False),
        nullable=False,
        default=InspectionStatus.IN_PROGRESS,
        index=True,
    )

    mileage: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Checklist JSONB: {"INSIDE_CABIN": [{"item": "...", "checked": true}], ...}
    checklist: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Location at submission
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Declaration + signature (set on sign)
    declaration_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signature_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    vehicle = relationship("Vehicle", lazy="raise", foreign_keys=[vehicle_id])
    driver = relationship("Driver", lazy="raise", foreign_keys=[driver_id])

    __table_args__ = (
        Index("ix_vehicle_inspections_vehicle_driver", "vehicle_id", "driver_id"),
        Index("ix_vehicle_inspections_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<VehicleInspection vehicle={self.vehicle_id} status={self.status}>"
