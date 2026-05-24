"""SQLAlchemy models for service tier configuration."""

from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus


class ServiceTier(BaseModel):
    """Admin-configurable service tier (pricing, audience, and optional org override).

    Uniqueness is enforced in the database via partial unique indexes (see migration
    ``0087_service_tier_scope``): one global row per (tier_name, available_for), and
    one org row per (scope_org_id, tier_name, available_for).
    """

    __tablename__ = "service_tier"

    tier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    error_margin_kg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    price_per_package: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))

    scope_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ServiceTierScopeType.GLOBAL.value,
    )
    scope_org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # CUSTOMER_B2B / CUSTOMER_B2C / BOTH
    available_for: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ServiceTierAudience.BOTH,
    )

    # Hex color string such as "#FF0000"
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Frontend icon key, e.g. "truck", "clock"
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[ServiceTierStatus] = mapped_column(
        Enum(ServiceTierStatus, native_enum=False),
        nullable=False,
        default=ServiceTierStatus.ACTIVE,
    )

    def __repr__(self) -> str:
        return (
            f"<ServiceTier name={self.tier_name!r} scope={self.scope_type!r} "
            f"duration_days={self.duration_days} available_for={self.available_for!r}>"
        )
