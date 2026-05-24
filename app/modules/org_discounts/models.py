"""ORM model for per-org, per-service-tier, per-discount-type configuration rows."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel
from app.modules.org_discounts.enums import DiscountType


class OrgDiscountConfig(BaseModel):
    """Discount config row — unique per (organization_id, service_tier_id, discount_type)."""

    __tablename__ = "org_discount_configs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "service_tier_id",
            "discount_type",
            name="uq_org_discount_org_tier_type",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # FK to service_tier — the tier this discount row applies to.
    service_tier_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("service_tier.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, native_enum=False),
        nullable=False,
        index=True,
    )

    # Temporarily disable this discount without deleting the row.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── PERCENTAGE / FIXED fields ─────────────────────────────────────────────
    # Used by PERCENTAGE (0.00–100.00) and FIXED_PER_BOOKING (GBP amount).
    # Null when discount_type = VOLUME_TIERED.
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)  # optional end date

    # ── VOLUME_TIERED field ───────────────────────────────────────────────────
    # JSONB array: [{"min_bookings": int, "max_bookings": int|null, "discount_pct": "0.00"}, ...]
    # The last tier always has max_bookings=null (open-ended, e.g. 201+).
    # Tiers must be non-overlapping. Any range is valid; no gap or open-ended requirement.
    # Null when discount_type != VOLUME_TIERED.
    volume_tiers: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<OrgDiscountConfig org={self.organization_id} "
            f"tier={self.service_tier_id} type={self.discount_type} enabled={self.is_enabled}>"
        )
