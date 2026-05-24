"""Address ORM model. Addresses with PostGIS point geometry for geospatial queries.

Used by delivery stops (delivery address),
depots, and users. Geocoded via Ideal Postcodes with Redis caching.
"""

from geoalchemy2 import Geometry
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class Address(BaseModel):
    """UK address with PostGIS point and optional UPRN."""

    __tablename__ = "addresses"

    # ── Address lines ────────────────────────
    line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    county: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(10), nullable=False, default="GB")

    # ── Geolocation ──────────────────────────
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # PostGIS point — SRID 4326 (WGS84)
    location = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=True,
    )

    # UK-specific: Unique Property Reference Number
    uprn: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # ── Context ──────────────────────────────
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "Home", "Office"
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Owner (optional — for saved address book) ──
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<Address {self.line_1}, {self.postcode}>"

    @property
    def full_address(self) -> str:
        parts = [self.line_1]
        if self.line_2:
            parts.append(self.line_2)
        parts.extend([self.city, self.postcode])
        return ", ".join(parts)
