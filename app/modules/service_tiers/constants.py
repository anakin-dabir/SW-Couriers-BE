"""Constants for system-owned service tiers."""

from __future__ import annotations

from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType
from app.modules.service_tiers.models import ServiceTier

SUPERFAST_TIER_NAME = "Superfast"
SUPERFAST_AVAILABLE_FOR = ServiceTierAudience.BOTH.value


def is_superfast_tier_name(name: str | None) -> bool:
    return (name or "").strip() == SUPERFAST_TIER_NAME


def is_superfast_global_tier(tier: ServiceTier) -> bool:
    scope = tier.scope_type if isinstance(tier.scope_type, str) else str(tier.scope_type)
    audience = tier.available_for if isinstance(tier.available_for, str) else str(tier.available_for)
    return (
        scope == ServiceTierScopeType.GLOBAL.value
        and tier.scope_org_id is None
        and is_superfast_tier_name(tier.tier_name)
        and audience == SUPERFAST_AVAILABLE_FOR
    )
