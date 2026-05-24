"""Unit tests for org service tier contract sync helpers."""

import pytest
from sqlalchemy import select

from app.common.exceptions import ValidationError
from app.modules.organizations.models import OrgServiceTierContractLine
from app.modules.organizations.pricing_plans_contract_sync import _norm_default_flags, replace_org_contract_from_pricing_plans
from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME
from app.modules.service_tiers.enums import ServiceTierScopeType
from app.modules.service_tiers.repository import ServiceTierRepository


def test_norm_default_assigns_first_permitted_when_none_marked() -> None:
    plans = [
        {"id_price_tier": "a", "permitted": True, "selected": False},
        {"id_price_tier": "b", "permitted": True, "selected": False},
    ]
    out = _norm_default_flags(plans)
    assert out[0]["is_default"] is True
    assert out[1]["is_default"] is False


def test_norm_default_keeps_first_marked() -> None:
    plans = [
        {"id_price_tier": "a", "permitted": True, "is_default": False, "selected": True},
        {"id_price_tier": "b", "permitted": True, "is_default": True},
    ]
    out = _norm_default_flags(plans)
    assert out[0]["is_default"] is True
    assert out[1]["is_default"] is False


@pytest.mark.asyncio
async def test_replace_contract_rejects_superfast_deselect(db_session, org_factory, superfast_global_tier) -> None:
    org = await org_factory()
    plans = [
        {
            "id_price_tier": superfast_global_tier.id,
            "plain_name": SUPERFAST_TIER_NAME,
            "plain_type": "standard",
            "price_per_package": "125.00",
            "days": 1,
            "permitted": False,
        }
    ]
    with pytest.raises(ValidationError, match="cannot be deselected"):
        await replace_org_contract_from_pricing_plans(db_session, organization_id=org.id, plans=plans)


@pytest.mark.asyncio
async def test_replace_contract_auto_appends_superfast(db_session, org_factory, superfast_global_tier) -> None:
    org = await org_factory()
    all_ids = [
        row.id
        for row in (
            await ServiceTierRepository(db_session).list_by_filters(scope_type=ServiceTierScopeType.GLOBAL.value)
        )
        if row.id != superfast_global_tier.id
    ]
    if not all_ids:
        pytest.skip("No non-Superfast global tier available")

    plans = [
        {
            "id_price_tier": all_ids[0],
            "plain_name": "Other",
            "plain_type": "standard",
            "price_per_package": "50.00",
            "days": 30,
            "permitted": True,
        }
    ]

    await replace_org_contract_from_pricing_plans(db_session, organization_id=org.id, plans=plans)
    await db_session.flush()

    lines = (
        await db_session.execute(
            select(OrgServiceTierContractLine).where(OrgServiceTierContractLine.organization_id == org.id)
        )
    ).scalars().all()
    template_ids = {line.global_template_id for line in lines}
    assert superfast_global_tier.id in template_ids
