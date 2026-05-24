"""Resolve booking-facing service tiers for an organisation (permitted + effective values)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.organizations.org_service_tier_contract_repository import OrgServiceTierContractRepository
from app.modules.service_tiers.enums import ServiceTierScopeType
from app.modules.service_tiers.models import ServiceTier
from app.modules.service_tiers.repository import ServiceTierRepository


def _audience_match(tier: ServiceTier, available_for: str) -> bool:
    af = tier.available_for if isinstance(tier.available_for, str) else str(tier.available_for)
    if af == available_for:
        return True
    return af == "BOTH"


@dataclass(frozen=True)
class BookingServiceTierItem:
    """One row for the booking UI and order pricing."""

    id: str
    global_template_id: str
    org_tier_id: str | None
    mode: str
    is_default: bool
    tier_name: str
    description: str | None
    duration_days: int
    error_margin_kg: int
    price_per_kg: Decimal
    price_per_package: Decimal
    base_price: Decimal
    available_for: str
    color: str | None
    icon: str | None
    source: str  # global | org_row


class BookingServiceTierResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._contract_repo = OrgServiceTierContractRepository(session)
        self._tier_repo = ServiceTierRepository(session)

    async def list_booking_tiers(
        self,
        *,
        organization_id: str,
        available_for: str,
    ) -> tuple[list[BookingServiceTierItem], bool]:
        """Return permitted tiers and whether the org uses contract rows (else global fallback).

        ``available_for`` should be ``CUSTOMER_B2B`` or ``CUSTOMER_B2C`` (``BOTH`` tiers match either).
        """
        org = await self._session.get(Organization, organization_id)
        if org is None:
            from app.common.exceptions import NotFoundError

            raise NotFoundError(resource="organization", id=organization_id)

        lines = await self._contract_repo.list_for_organization(organization_id)
        lines = [ln for ln in lines if ln.permitted]

        if not lines:
            return await self._fallback_global(available_for=available_for), False

        items: list[BookingServiceTierItem] = []
        for ln in lines:
            global_row = await self._session.get(ServiceTier, ln.global_template_id)
            if global_row is None:
                continue
            if not _audience_match(global_row, available_for):
                continue

            if ln.mode == "custom" and ln.org_tier_id:
                eff = await self._session.get(ServiceTier, ln.org_tier_id)
                if eff is None or str(eff.scope_type) != ServiceTierScopeType.ORG.value:
                    continue
                items.append(self._to_item(eff, ln, source="org_row"))
            else:
                if str(global_row.scope_type) != ServiceTierScopeType.GLOBAL.value:
                    continue
                items.append(self._to_item(global_row, ln, source="global"))

        return items, True

    def _to_item(self, tier: ServiceTier, ln: Any, *, source: str) -> BookingServiceTierItem:
        af = tier.available_for if isinstance(tier.available_for, str) else str(tier.available_for)
        return BookingServiceTierItem(
            id=tier.id,
            global_template_id=ln.global_template_id,
            org_tier_id=ln.org_tier_id,
            mode=ln.mode,
            is_default=bool(ln.is_default),
            tier_name=tier.tier_name,
            description=tier.description,
            duration_days=tier.duration_days,
            error_margin_kg=tier.error_margin_kg,
            price_per_kg=tier.price_per_kg,
            price_per_package=tier.price_per_package,
            base_price=tier.base_price,
            available_for=af,
            color=tier.color,
            icon=tier.icon,
            source=source,
        )

    async def _fallback_global(self, *, available_for: str) -> list[BookingServiceTierItem]:
        globals_ = await self._tier_repo.list_active_global()
        items: list[BookingServiceTierItem] = []
        matching = [g for g in globals_ if _audience_match(g, available_for)]
        for i, g in enumerate(matching):
            ln = SimpleNamespace(
                global_template_id=g.id,
                org_tier_id=None,
                mode="standard",
                is_default=(i == 0),
            )
            items.append(self._to_item(g, ln, source="global"))
        if len(items) > 1 and not any(x.is_default for x in items):
            items[0] = replace(items[0], is_default=True)
        return items
