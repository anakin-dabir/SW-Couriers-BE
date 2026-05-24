"""Schemas for per-organisation, per-service-tier discount configuration."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.org_discounts.enums import DiscountType


# ── Volume tier item ───────────────────────────────────────────────────────────


class VolumeTierItem(BaseModel):
    """One booking-volume bracket with its discount percentage."""

    min_bookings: int = Field(..., ge=1)
    max_bookings: int | None = Field(None, ge=1, description="None means open-ended (e.g. 201+)")
    discount_pct: Decimal = Field(..., ge=0, le=100, decimal_places=2)


def _serialize_volume_tiers(tiers: list[VolumeTierItem]) -> list[dict]:
    """Serialize tier items to JSONB-safe dicts (Decimal → str for precision)."""
    return [
        {
            "min_bookings": t.min_bookings,
            "max_bookings": t.max_bookings,
            "discount_pct": str(t.discount_pct),
        }
        for t in tiers
    ]


def _validate_volume_tiers(tiers: list[VolumeTierItem]) -> list[VolumeTierItem]:
    """Validate tier list: non-overlapping only. Any range is allowed."""
    if not tiers:
        raise ValueError("volume_tiers must contain at least one tier.")

    sorted_tiers = sorted(tiers, key=lambda t: t.min_bookings)

    for i, tier in enumerate(sorted_tiers[:-1]):
        next_tier = sorted_tiers[i + 1]
        if tier.max_bookings is None or next_tier.min_bookings <= tier.max_bookings:
            raise ValueError(
                f"Volume tiers must not overlap. "
                f"Tier starting at {tier.min_bookings} overlaps with "
                f"tier starting at {next_tier.min_bookings}."
            )

    return sorted_tiers


# ── Input schemas ──────────────────────────────────────────────────────────────


class OrgDiscountConfigItem(BaseModel):
    """One discount entry for a single (service_tier_id, discount_type) pair."""

    service_tier_id: str = Field(..., description="UUID of the permitted ServiceTier for this org")
    discount_type: DiscountType
    is_enabled: bool = True

    # PERCENTAGE / FIXED_PER_BOOKING
    value: Decimal | None = Field(None, gt=0, decimal_places=2)
    valid_from: date | None = None
    valid_until: date | None = None

    # VOLUME_TIERED
    volume_tiers: list[VolumeTierItem] | None = None

    @model_validator(mode="after")
    def validate_discount_item(self) -> "OrgDiscountConfigItem":
        dt = self.discount_type

        if dt == DiscountType.PERCENTAGE:
            if self.value is None:
                raise ValueError("value is required for PERCENTAGE discount.")
            if self.value > 100:
                raise ValueError("value must be <= 100 for PERCENTAGE discount.")
            if self.valid_from is None:
                raise ValueError("valid_from is required for PERCENTAGE discount.")
            if self.valid_until and self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be after valid_from.")

        elif dt == DiscountType.FIXED_PER_BOOKING:
            if self.value is None:
                raise ValueError("value is required for FIXED_PER_BOOKING discount.")
            if self.valid_from is None:
                raise ValueError("valid_from is required for FIXED_PER_BOOKING discount.")
            if self.valid_until and self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be after valid_from.")

        elif dt == DiscountType.VOLUME_TIERED:
            if not self.volume_tiers:
                raise ValueError("volume_tiers is required for VOLUME_TIERED discount.")
            _validate_volume_tiers(self.volume_tiers)

        return self


class OrgDiscountConfigInput(BaseModel):
    """Discount config payload for org creation — no reason required."""

    discounts: list[OrgDiscountConfigItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> "OrgDiscountConfigInput":
        seen: set[tuple[str, str]] = set()
        for item in self.discounts:
            key = (item.service_tier_id, item.discount_type.value)
            if key in seen:
                raise ValueError(
                    f"Duplicate discount entry for service_tier_id={item.service_tier_id} "
                    f"and discount_type={item.discount_type}."
                )
            seen.add(key)
        return self


class OrgDiscountConfigUpsert(OrgDiscountConfigInput):
    """Standalone PUT endpoint payload — reason required for audit trail."""

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for the update (audit trail)")


# ── Response schema ────────────────────────────────────────────────────────────


class OrgDiscountConfigItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    service_tier_id: str
    discount_type: DiscountType
    is_enabled: bool

    value: Decimal | None
    valid_from: date | None
    valid_until: date | None

    volume_tiers: list[VolumeTierItem] | None

    created_at: datetime
    updated_at: datetime
    version: int


class OrgDiscountConfigResponse(BaseModel):
    """Full discount configuration for an org — all discount rows grouped."""

    organization_id: str
    discounts: list[OrgDiscountConfigItemResponse]
