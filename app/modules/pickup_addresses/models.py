"""Saved pickup locations for B2B organisations and B2C users.

Exactly one of ``organization_id`` or ``user_id`` is set (enforced in DB + service).
"""

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel


class PickupAddress(BaseModel):
    """Pickup address book entry — org-scoped (B2B) or user-scoped (B2C)."""

    __tablename__ = "pickup_addresses"

    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False, default="United Kingdom")

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=True,
    )

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    organization = relationship(
        "Organization",
        lazy="raise",
        foreign_keys=[organization_id],
        back_populates="pickup_addresses",
    )
    user = relationship("User", lazy="raise", foreign_keys="PickupAddress.user_id")
    created_by = relationship("User", lazy="raise", foreign_keys="PickupAddress.created_by_user_id")

    def __repr__(self) -> str:
        return f"<PickupAddress {self.line_1}, {self.postcode} org={self.organization_id} user={self.user_id}>"

    @property
    def full_address(self) -> str:
        parts = [self.line_1]
        if self.line_2:
            parts.append(self.line_2)
        parts.extend([self.city, self.postcode])
        return ", ".join(parts)
