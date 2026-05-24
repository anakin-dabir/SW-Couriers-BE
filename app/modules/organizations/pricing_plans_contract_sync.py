"""Persist organisations.pricing_plans into org_service_tier_contract_lines.

Called after org create / update when pricing_plans is present and validated.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.modules.organizations.superfast_tier import (
    ensure_superfast_contract_line,
    ensure_superfast_in_pricing_plans,
    get_superfast_global_tier,
    reject_superfast_deselect,
    validate_superfast_plan_constraints,
)
from app.modules.service_tiers.constants import is_superfast_global_tier
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType
from app.modules.service_tiers.models import ServiceTier
from app.modules.service_tiers.service import ServiceTierService


def _norm_default_flags(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure exactly one default among permitted (first permitted if none marked; first marked if many)."""
    out = deepcopy(plans)
    perm_ix = [i for i, p in enumerate(out) if p.get("permitted", True)]
    if not perm_ix:
        return out
    default_ix = [i for i in perm_ix if out[i].get("is_default") or out[i].get("selected")]
    for p in out:
        p["is_default"] = False
    if not default_ix:
        out[perm_ix[0]]["is_default"] = True
    else:
        out[default_ix[0]]["is_default"] = True
    return out


def _as_decimal(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


async def replace_org_contract_from_pricing_plans(
    session: AsyncSession,
    *,
    organization_id: str,
    plans: list[dict[str, Any]],
) -> None:
    """Replace all contract lines for the org from enriched pricing_plans JSON.

    For each plan row, ``id_price_tier`` must reference a **GLOBAL** ``service_tier``.
    """
    from app.modules.organizations.org_service_tier_contract_repository import (  # noqa: PLC0415
        OrgServiceTierContractRepository,
    )

    if not plans:
        await ensure_superfast_contract_line(session, organization_id=organization_id)
        return

    superfast = await get_superfast_global_tier(session)
    reject_superfast_deselect(plans, superfast_id=str(superfast.id))
    plans = ensure_superfast_in_pricing_plans(plans, superfast)
    validate_superfast_plan_constraints(plans, superfast_id=str(superfast.id))

    plans = _norm_default_flags(plans)
    tier_service = ServiceTierService(session, request=None)
    contract_repo = OrgServiceTierContractRepository(session)
    await contract_repo.delete_for_organization(organization_id)

    for sort_idx, plan in enumerate(plans):
        tier_id = str(plan.get("id_price_tier") or "")
        if not tier_id:
            raise ValidationError("Each pricing plan must include id_price_tier")
        global_row = await session.get(ServiceTier, tier_id)
        if global_row is None:
            raise ValidationError(f"Pricing tier '{tier_id}' does not exist.")
        if str(global_row.scope_type) != ServiceTierScopeType.GLOBAL.value or global_row.scope_org_id is not None:
            raise ValidationError("id_price_tier must reference a GLOBAL service_tier row for contract sync.")

        permitted = bool(plan.get("permitted", True))
        if is_superfast_global_tier(global_row) and not permitted:
            raise ValidationError("Superfast cannot be deselected for any organisation.")
        is_default = bool(plan.get("is_default") or plan.get("selected"))
        mode = str(plan.get("plain_type", "standard"))
        if mode not in ("standard", "custom"):
            raise ValidationError("plain_type must be standard or custom")

        org_tier_id: str | None = None
        if mode == "custom":
            audience = (
                global_row.available_for
                if isinstance(global_row.available_for, str)
                else str(global_row.available_for)
            )
            day_val = int(plan.get("days", global_row.duration_days))
            p_kg = plan.get("price_per_kg")
            payload = {
                "description": (plan.get("plain_name") and str(plan.get("plain_name"))) or global_row.description,
                "duration_days": day_val,
                "error_margin_kg": global_row.error_margin_kg,
                "price_per_kg": _as_decimal(p_kg) if p_kg is not None else global_row.price_per_kg,
                "price_per_package": _as_decimal(plan["price_per_package"]),
                "base_price": _as_decimal(plan.get("base_price", global_row.base_price)),
                "color": plan.get("color") or global_row.color,
                "icon": plan.get("icon") or global_row.icon,
            }
            ot = await tier_service.upsert_org_tier_override(
                organization_id=organization_id,
                tier_name=global_row.tier_name,
                available_for=ServiceTierAudience(audience),
                payload=payload,
                expected_version=None,
                audit_user_id=None,
                audit_user_role=None,
            )
            org_tier_id = ot.id
        # standard: org_tier_id stays None

        await contract_repo.create(
            {
                "organization_id": organization_id,
                "global_template_id": global_row.id,
                "mode": mode,
                "permitted": permitted,
                "is_default": is_default and permitted,
                "org_tier_id": org_tier_id,
                "sort_order": sort_idx,
            }
        )
