"""Unit tests for ``ServiceTierService`` validation paths (mocked repository / session)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.exceptions import ValidationError
from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.service import ServiceTierService


def _superfast_tier_mock(**overrides):
    base = SimpleNamespace(
        id="superfast-id",
        tier_name=SUPERFAST_TIER_NAME,
        available_for=ServiceTierAudience.BOTH.value,
        scope_type=ServiceTierScopeType.GLOBAL.value,
        scope_org_id=None,
        status=ServiceTierStatus.ACTIVE.value,
        duration_days=1,
        price_per_package=Decimal("125.00"),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(mock_session: AsyncMock) -> ServiceTierService:
    s = ServiceTierService(mock_session, request=None)
    s._tier_repo = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_list_tiers_rejects_org_scope_without_org_id(service: ServiceTierService) -> None:
    with pytest.raises(ValidationError, match="scope_org_id is required"):
        await service.list_tiers(scope_type=ServiceTierScopeType.ORG, scope_org_id=None)


@pytest.mark.asyncio
async def test_list_tiers_rejects_global_with_scope_org_id(service: ServiceTierService) -> None:
    with pytest.raises(ValidationError, match="scope_org_id must be omitted"):
        await service.list_tiers(
            scope_type=ServiceTierScopeType.GLOBAL,
            scope_org_id="00000000-0000-0000-0000-000000000001",
        )


@pytest.mark.asyncio
async def test_list_tiers_rejects_min_price_gt_max(service: ServiceTierService) -> None:
    with pytest.raises(ValidationError, match="min_price"):
        await service.list_tiers(min_price=100.0, max_price=10.0)


@pytest.mark.asyncio
async def test_list_tiers_rejects_min_days_gt_max(service: ServiceTierService) -> None:
    with pytest.raises(ValidationError, match="min_days"):
        await service.list_tiers(min_days=90, max_days=7)


@pytest.mark.asyncio
async def test_update_tier_rejects_unknown_fields(service: ServiceTierService) -> None:
    tier = MagicMock()
    tier.tier_name = "X"
    tier.available_for = "BOTH"
    tier.scope_type = ServiceTierScopeType.GLOBAL.value
    tier.scope_org_id = None
    service._tier_repo.get_by_id_or_404 = AsyncMock(return_value=tier)

    with pytest.raises(ValidationError, match="unknown field"):
        await service.update_tier(tier_id="tid", data={"tier_name": "Y", "not_a_column": 1})


@pytest.mark.asyncio
async def test_validate_scope_global_rejects_org_id(service: ServiceTierService) -> None:
    with pytest.raises(ValidationError, match="null for GLOBAL"):
        await service._validate_scope(scope_type=ServiceTierScopeType.GLOBAL.value, scope_org_id="x")


@pytest.mark.asyncio
async def test_validate_scope_org_requires_existing_org(service: ServiceTierService) -> None:
    service._session.get = AsyncMock(return_value=None)
    with pytest.raises(ValidationError, match="non-existent organization"):
        await service._validate_scope(scope_type=ServiceTierScopeType.ORG.value, scope_org_id="missing-org-id")


@pytest.mark.asyncio
async def test_create_tier_rejects_superfast_global(service: ServiceTierService) -> None:
    service._validate_scope = AsyncMock()
    with pytest.raises(ValidationError, match="cannot be created"):
        await service.create_tier(
            tier_name=SUPERFAST_TIER_NAME,
            duration_days=1,
            error_margin_kg=0,
            price_per_kg=Decimal("0"),
            price_per_package=Decimal("125"),
            base_price=Decimal("0"),
            available_for=ServiceTierAudience.BOTH,
            scope_type=ServiceTierScopeType.GLOBAL,
        )


@pytest.mark.asyncio
async def test_delete_tier_rejects_superfast(service: ServiceTierService) -> None:
    tier = _superfast_tier_mock()
    service._tier_repo.get_by_id_or_404 = AsyncMock(return_value=tier)
    with pytest.raises(ValidationError, match="cannot be deleted"):
        await service.delete_tier(tier_id=tier.id)


@pytest.mark.asyncio
async def test_update_tier_rejects_superfast_rename(service: ServiceTierService) -> None:
    tier = _superfast_tier_mock()
    service._tier_repo.get_by_id_or_404 = AsyncMock(return_value=tier)
    with pytest.raises(ValidationError, match="name cannot be changed"):
        await service.update_tier(tier_id=tier.id, data={"tier_name": "Fast"})


@pytest.mark.asyncio
async def test_update_tier_rejects_superfast_deactivate(service: ServiceTierService) -> None:
    tier = _superfast_tier_mock()
    service._tier_repo.get_by_id_or_404 = AsyncMock(return_value=tier)
    with pytest.raises(ValidationError, match="cannot be deactivated"):
        await service.update_tier(tier_id=tier.id, data={"status": ServiceTierStatus.INACTIVE})


@pytest.mark.asyncio
async def test_update_tier_allows_superfast_price_change(service: ServiceTierService) -> None:
    tier = _superfast_tier_mock()
    updated = _superfast_tier_mock(price_per_package=Decimal("140.00"))
    service._tier_repo.get_by_id_or_404 = AsyncMock(return_value=tier)
    service._tier_repo.update_by_id = AsyncMock(return_value=updated)
    service._log_audit = AsyncMock()
    result = await service.update_tier(
        tier_id=tier.id,
        data={"price_per_package": Decimal("140.00")},
    )
    assert result.price_per_package == Decimal("140.00")
