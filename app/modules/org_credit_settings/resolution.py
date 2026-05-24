from __future__ import annotations

from dataclasses import dataclass

from app.modules.org_credit_settings.constants import (
    DEFAULT_CREDIT_ACCOUNT_COOLDOWN_DAYS,
    DEFAULT_CREDIT_ACCOUNT_COOLDOWN_HOURS,
    DEFAULT_CREDIT_ACCOUNT_COOLDOWN_MONTHS,
)
from app.modules.org_credit_settings.enums import CooldownResolutionSource
from app.modules.org_credit_settings.models import GlobalCreditAccountCooldownPeriod, OrgCreditAccountCooldownPeriod


def global_cooldown_is_configured(global_row: GlobalCreditAccountCooldownPeriod | None) -> bool:
    if global_row is None:
        return False
    return (
        global_row.months is not None
        or global_row.days is not None
        or global_row.hours is not None
    )


def _global_effective_triplet(global_row: GlobalCreditAccountCooldownPeriod | None) -> tuple[int, int, int]:
    if not global_cooldown_is_configured(global_row) or global_row is None:
        return (
            DEFAULT_CREDIT_ACCOUNT_COOLDOWN_MONTHS,
            DEFAULT_CREDIT_ACCOUNT_COOLDOWN_DAYS,
            DEFAULT_CREDIT_ACCOUNT_COOLDOWN_HOURS,
        )
    return (
        global_row.months if global_row.months is not None else 0,
        global_row.days if global_row.days is not None else 0,
        global_row.hours if global_row.hours is not None else 0,
    )


@dataclass(frozen=True, slots=True)
class ResolvedCooldown:
    months: int
    days: int
    hours: int
    source: CooldownResolutionSource


def resolve_cooldown_for_org(
    org_row: OrgCreditAccountCooldownPeriod | None,
    global_row: GlobalCreditAccountCooldownPeriod | None,
) -> ResolvedCooldown:
    if org_row is not None:
        return ResolvedCooldown(
            months=org_row.months,
            days=org_row.days,
            hours=org_row.hours,
            source=CooldownResolutionSource.ORG,
        )
    if global_cooldown_is_configured(global_row):
        m, d, h = _global_effective_triplet(global_row)
        return ResolvedCooldown(months=m, days=d, hours=h, source=CooldownResolutionSource.GLOBAL)
    return ResolvedCooldown(
        months=DEFAULT_CREDIT_ACCOUNT_COOLDOWN_MONTHS,
        days=DEFAULT_CREDIT_ACCOUNT_COOLDOWN_DAYS,
        hours=DEFAULT_CREDIT_ACCOUNT_COOLDOWN_HOURS,
        source=CooldownResolutionSource.DEFAULT,
    )


def default_triplet() -> tuple[int, int, int]:
    return (
        DEFAULT_CREDIT_ACCOUNT_COOLDOWN_MONTHS,
        DEFAULT_CREDIT_ACCOUNT_COOLDOWN_DAYS,
        DEFAULT_CREDIT_ACCOUNT_COOLDOWN_HOURS,
    )
