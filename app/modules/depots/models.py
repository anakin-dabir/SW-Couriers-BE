"""Depot ORM model. Physical warehouse/depot locations.

Depots belong to a region, have drivers assigned to them, and contain
warehouse zones. They're the hub for package sorting and dispatch.
"""

from geoalchemy2 import Geometry
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class Depot(BaseModel):
    """Physical depot / warehouse location."""

    __tablename__ = "depots"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    # ── Location ─────────────────────────────
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # PostGIS point
    location = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=True,
    )

    # ── Operations ───────────────────────────
    # IANA name (e.g. Europe/London). Used when resolving "today" for drivers assigned to this
    # depot and when planners author RoutePlan.service_date as a depot-local calendar day.
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Europe/London")
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # max packages

    # ── Region ───────────────────────────────
    region_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Depot {self.code}: {self.name}>"
