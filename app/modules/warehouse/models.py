"""Warehouse zone model. Physical zones within a depot for package handling."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel


class WarehouseZone(BaseModel):
    """A physical zone within a depot (inbound, sorting, outbound, staging)."""

    __tablename__ = "warehouse_zones"

    depot_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("depots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(30), nullable=False)  # inbound, sorting, outbound, staging
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ────────────────────────
    depot = relationship("Depot", lazy="selectin", foreign_keys=[depot_id])

    def __repr__(self) -> str:
        return f"<WarehouseZone {self.name} type={self.zone_type}>"
