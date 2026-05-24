"""Pydantic schemas for Service Tiers v1 API."""

from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from app.common.schemas import BaseResponseSchema, BaseSchema
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus

HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
TierName = Annotated[str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)]
IconKey = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]

# GBP amounts: non-negative, bounded to avoid float overflow / absurd payloads.
_TierMoney = Annotated[float, Field(ge=0, le=999_999.99)]


class ServiceTierBase(BaseSchema):
    tier_name: TierName = Field(..., description="Display name of the tier.")
    description: str | None = Field(None, description="Optional description of the tier.")
    duration_days: Annotated[int, Field(ge=1, le=3650)] = Field(..., description="Duration of the service tier in days.")
    error_margin_kg: Annotated[int, Field(ge=0, le=100_000)] = Field(
        0,
        description="Weight error margin in kilograms (whole kg).",
    )
    price_per_kg: _TierMoney = Field(0, description="Price per 1 kg in GBP.")
    price_per_package: _TierMoney = Field(..., description="Price per package in GBP.")
    base_price: _TierMoney = Field(0, description="Base price component in GBP.")
    available_for: ServiceTierAudience = Field(..., description="CUSTOMER_B2B, CUSTOMER_B2C, or BOTH.")
    scope_type: ServiceTierScopeType = Field(
        ServiceTierScopeType.GLOBAL,
        description=(
            "`GLOBAL`: platform default; `scope_org_id` must be null. "
            "`ORG`: row applies to one organisation; `scope_org_id` is required."
        ),
    )
    scope_org_id: str | None = Field(
        None,
        description="Required when `scope_type` is `ORG` (organisation UUID). Must be null or omitted for `GLOBAL`.",
    )
    color: HexColor | None = Field(None, description="Hex color code, e.g. #FF0000.")
    icon: IconKey | None = Field(None, description="Icon key used by the frontend.")
    status: ServiceTierStatus = Field(ServiceTierStatus.ACTIVE, description="ACTIVE or INACTIVE.")

    @model_validator(mode="after")
    def _validate_scope(self) -> "ServiceTierBase":
        if self.scope_type == ServiceTierScopeType.ORG and not self.scope_org_id:
            raise ValueError("scope_org_id is required when scope_type=ORG")
        if self.scope_type == ServiceTierScopeType.GLOBAL and self.scope_org_id is not None:
            raise ValueError("scope_org_id must be null for GLOBAL tiers")
        return self


class ServiceTierCreateRequest(ServiceTierBase):
    """Create a new service tier."""

    model_config = ConfigDict(extra="forbid")


class ServiceTierUpdateRequest(BaseSchema):
    """Partial update for an existing service tier."""

    model_config = ConfigDict(extra="forbid")

    tier_name: TierName | None = None
    description: str | None = None
    duration_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    error_margin_kg: Annotated[int, Field(ge=0, le=100_000)] | None = None
    price_per_kg: _TierMoney | None = None
    price_per_package: _TierMoney | None = None
    base_price: _TierMoney | None = None
    available_for: ServiceTierAudience | None = None
    color: HexColor | None = None
    icon: IconKey | None = None
    status: ServiceTierStatus | None = None
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking. If omitted, update is non-atomic.",
    )


class ServiceTierResponse(ServiceTierBase, BaseResponseSchema):
    """Single tier row as stored (list/get)."""

    is_override: bool = False
    source_scope_type: ServiceTierScopeType | None = None
    global_tier_id: str | None = Field(
        None,
        description="When is_override, the id of the global tier this org row replaces.",
    )
    # Populated only by effective-for-org; None on all other tier endpoints.
    permitted: bool | None = Field(None, description="Whether this tier is permitted for booking by this org.")
    is_default: bool | None = Field(None, description="Whether this tier is the default for this org.")
    plain_type: str | None = Field(None, description="Contract mode: 'standard' or 'custom'.")
    is_system_tier: bool = Field(False, description="System-owned tier (e.g. Superfast); immutable name and lifecycle.")
    tier_name_locked: bool = Field(False, description="When true, tier_name cannot be changed.")
    permitted_locked: bool = Field(False, description="When true, tier cannot be deselected for an organisation.")


class ServiceTierListResponse(BaseSchema):
    items: list[ServiceTierResponse]
    total: int


class OrgServiceTierOverrideUpsertRequest(BaseSchema):
    """Upsert body for PUT …/orgs/{org_id}/overrides — org-scoped tier for a global name + audience."""

    model_config = ConfigDict(extra="forbid")

    tier_name: TierName
    available_for: ServiceTierAudience
    description: str | None = None
    duration_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    error_margin_kg: Annotated[int, Field(ge=0, le=100_000)] | None = None
    price_per_kg: _TierMoney | None = None
    price_per_package: _TierMoney | None = None
    base_price: _TierMoney | None = None
    color: HexColor | None = None
    icon: IconKey | None = None
    status: ServiceTierStatus | None = None
    version: int | None = None
