"""Superfast system tier helpers for organisation pricing plans and contracts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.modules.organizations.models import OrgServiceTierContractLine
from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME
from app.modules.service_tiers.models import ServiceTier
from app.modules.service_tiers.repository import ServiceTierRepository


def _tier_reference_price(global_tier: ServiceTier) -> str:
    reference = (global_tier.base_price + global_tier.price_per_package).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return str(reference)


def build_standard_superfast_plan_entry(global_tier: ServiceTier) -> dict[str, Any]:
    """Build a standard pricing_plans row for the global Superfast tier."""
    tier_price = _tier_reference_price(global_tier)
    return {
        "id_price_tier": global_tier.id,
        "plain_type": "standard",
        "plain_name": SUPERFAST_TIER_NAME,
        "base_price": tier_price,
        "price_per_package": tier_price,
        "price_per_kg": str(global_tier.price_per_kg.quantize(Decimal("0.01"))),
        "days": global_tier.duration_days,
        "permitted": True,
        "selected": False,
        "is_default": False,
    }


def reject_superfast_deselect(plans: list[dict[str, Any]], *, superfast_id: str) -> None:
    """Reject explicit attempts to turn off Superfast for an organisation."""
    for plan in plans:
        if str(plan.get("id_price_tier") or "") == superfast_id and not bool(plan.get("permitted", True)):
            raise ValidationError("Superfast cannot be deselected for any organisation.")


def ensure_superfast_in_pricing_plans(plans: list[dict[str, Any]], global_tier: ServiceTier) -> list[dict[str, Any]]:
    """Ensure Superfast is present and always permitted."""
    superfast_id = str(global_tier.id)
    out = [dict(p) for p in plans]
    found = False
    for entry in out:
        if str(entry.get("id_price_tier") or "") == superfast_id:
            entry["permitted"] = True
            found = True
    if not found:
        out.append(build_standard_superfast_plan_entry(global_tier))
    return out


def validate_superfast_plan_constraints(plans: list[dict[str, Any]], *, superfast_id: str) -> None:
    """Reject attempts to omit Superfast after ensure step."""
    matches = [p for p in plans if str(p.get("id_price_tier") or "") == superfast_id]
    if not matches:
        raise ValidationError("Superfast service tier must be included for every organisation.")
    for plan in matches:
        if not bool(plan.get("permitted", True)):
            raise ValidationError("Superfast cannot be deselected for any organisation.")


async def get_superfast_global_tier(session: AsyncSession) -> ServiceTier:
    repo = ServiceTierRepository(session)
    tier = await repo.find_global_superfast()
    if tier is None:
        raise ValidationError("System tier Superfast is not configured.")
    return tier


async def ensure_superfast_contract_line(session: AsyncSession, *, organization_id: str) -> None:
    """Ensure org has a permitted Superfast contract line (standard mode)."""
    global_tier = await get_superfast_global_tier(session)
    existing = (
        await session.execute(
            select(OrgServiceTierContractLine).where(
                OrgServiceTierContractLine.organization_id == organization_id,
                OrgServiceTierContractLine.global_template_id == global_tier.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.permitted:
            existing.permitted = True
            await session.flush()
        return

    max_sort = (
        await session.execute(
            select(OrgServiceTierContractLine.sort_order)
            .where(OrgServiceTierContractLine.organization_id == organization_id)
            .order_by(OrgServiceTierContractLine.sort_order.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    sort_order = int(max_sort or -1) + 1

    line = OrgServiceTierContractLine(
        organization_id=organization_id,
        global_template_id=global_tier.id,
        mode="standard",
        permitted=True,
        is_default=False,
        org_tier_id=None,
        sort_order=sort_order,
    )
    session.add(line)
    await session.flush()


def superfast_plan_present_in_json(pricing_plans: list | None, *, superfast_id: str) -> bool:
    if not pricing_plans:
        return False
    for plan in pricing_plans:
        if isinstance(plan, dict) and str(plan.get("id_price_tier") or "") == superfast_id:
            return True
    return False


def merge_superfast_into_pricing_plans_json(
    pricing_plans: list | None,
    *,
    global_tier: ServiceTier,
) -> list[dict[str, Any]]:
    raw = pricing_plans if isinstance(pricing_plans, list) else []
    dict_plans = [dict(item) for item in raw if isinstance(item, dict)]
    return ensure_superfast_in_pricing_plans(dict_plans, global_tier)
