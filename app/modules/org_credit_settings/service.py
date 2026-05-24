from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import LogEvent
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit.repository import OrgCreditAccountRepository
from app.modules.org_credit.service import _available_credit
from app.modules.org_credit_settings.constants import GLOBAL_CREDIT_COOLDOWN_ROW_ID
from app.modules.org_credit_settings.enums import ScheduledCreditSettingStatus
from app.modules.org_credit_settings.models import (
    GlobalCreditAccountCooldownPeriod,
    OrgCreditCooldownWindow,
)
from app.modules.org_credit_settings.repository import (
    GlobalCreditAccountCooldownPeriodRepository,
    OrgCreditAccountCooldownPeriodRepository,
    OrgCreditCooldownWindowRepository,
    OrgCreditLimitAdjustmentHistoryRepository,
    OrgCreditTermsModificationHistoryRepository,
)
from app.modules.org_credit_settings.resolution import (
    default_triplet,
    global_cooldown_is_configured,
    resolve_cooldown_for_org,
)
from app.modules.org_credit_settings.utils import (
    ends_at_after_duration,
    humanize_cooldown_remaining,
)
from app.modules.organizations.repository import OrganizationRepository

logger = structlog.get_logger()


def _limit_change_meta(
    prev: Decimal | None,
    new: Decimal,
) -> tuple[str | None, str | None, str | None]:
    if prev is None:
        return None, None, None
    change_amount = new - prev
    adj_type = "Increase" if change_amount >= 0 else "Decrease"
    change_pct: str | None = None
    if prev > 0:
        change_pct = f"{float(change_amount / prev * 100):.1f}%"
    return str(change_amount), change_pct, adj_type


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


class OrgCreditSettingsService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._global_repo = GlobalCreditAccountCooldownPeriodRepository(session)
        self._org_repo_cooldown = OrgCreditAccountCooldownPeriodRepository(session)
        self._window_repo = OrgCreditCooldownWindowRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._account_repo = OrgCreditAccountRepository(session)
        self._terms_hist_repo = OrgCreditTermsModificationHistoryRepository(session)
        self._limit_hist_repo = OrgCreditLimitAdjustmentHistoryRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    def _global_effective_triplet(self, global_row: GlobalCreditAccountCooldownPeriod | None) -> tuple[int, int, int]:
        if not global_cooldown_is_configured(global_row) or global_row is None:
            return default_triplet()
        return (
            global_row.months if global_row.months is not None else 0,
            global_row.days if global_row.days is not None else 0,
            global_row.hours if global_row.hours is not None else 0,
        )

    async def get_global_cooldown_payload(self) -> dict[str, Any]:
        row = await self._global_repo.get_singleton()
        m, d, h = self._global_effective_triplet(row)
        return {"months": m, "days": d, "hours": h}

    async def patch_global_cooldown(
        self,
        *,
        caller: AuthUser,
        months: int,
        days: int,
        hours: int,
        reset_to_defaults: bool,
    ) -> dict[str, Any]:
        old_row = await self._global_repo.get_singleton()
        old_snapshot = {
            "months": old_row.months if old_row else None,
            "days": old_row.days if old_row else None,
            "hours": old_row.hours if old_row else None,
        }
        if reset_to_defaults:
            row = await self._global_repo.upsert_singleton(months=None, days=None, hours=None)
        else:
            row = await self._global_repo.upsert_singleton(months=months, days=days, hours=hours)
        eff = self._global_effective_triplet(row)
        new_snapshot = {"months": row.months, "days": row.days, "hours": row.hours}
        await self._audit.log(
            action="global_credit_account_cooldown.updated",
            entity_type="global_credit_account_cooldown",
            entity_id=GLOBAL_CREDIT_COOLDOWN_ROW_ID,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value=old_snapshot,
            new_value=new_snapshot,
            ip_address=self._ip,
            user_agent=self._ua,
            reason=(
                "Reset global cooldown to defaults"
                if reset_to_defaults
                else f"Set global cooldown to {months}m {days}d {hours}h"
            ),
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SETTINGS_UPDATED,
            severity="INFO",
        )
        logger.info(
            "global_credit_account_cooldown.updated",
            user_id=caller.id,
            reset_to_defaults=reset_to_defaults,
        )
        return {"months": eff[0], "days": eff[1], "hours": eff[2]}

    async def get_org_cooldown_resolved_payload(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        org_row = await self._org_repo_cooldown.get_by_org_id(organization_id)
        global_row = await self._global_repo.get_singleton()
        resolved = resolve_cooldown_for_org(org_row, global_row)
        return {"months": resolved.months, "days": resolved.days, "hours": resolved.hours}

    async def get_settings_payload(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        account = await self._account_repo.get_by_org_id(organization_id)
        org_cd = await self._org_repo_cooldown.get_by_org_id(organization_id)
        global_cd = await self._global_repo.get_singleton()
        resolved = resolve_cooldown_for_org(org_cd, global_cd)

        if account is None:
            # No credit facility row yet (e.g. application not approved). UI still needs cooldown rules.
            return {
                "credit_limit_section": {
                    "total_limit": None,
                    "available_credit": "0",
                    "utilisation_pct": None,
                    "credit_facility_start_date": None,
                    "last_updated": "",
                },
                "credit_terms_section": {
                    "payment_terms_days": None,
                    "last_updated": None,
                },
                "risk_controls_section": {
                    "hold_threshold_pct": None,
                },
                "cooldown_section": {
                    "months": resolved.months,
                    "days": resolved.days,
                    "hours": resolved.hours,
                },
            }

        avail = _available_credit(account)
        util_pct: float | None = None
        if account.credit_limit and account.credit_limit > 0:
            util_pct = float(account.used_credit / account.credit_limit * 100)

        return {
            "credit_limit_section": {
                "total_limit": str(account.credit_limit) if account.credit_limit is not None else None,
                "available_credit": str(avail),
                "utilisation_pct": util_pct,
                "credit_facility_start_date": account.credit_facility_start_date.isoformat() if account.credit_facility_start_date else None,
                "last_updated": (
                    account.credit_limit_updated_at.isoformat()
                    if account.credit_limit_updated_at
                    else account.updated_at.isoformat()
                ),
            },
            "credit_terms_section": {
                "payment_terms_days": account.payment_terms_days,
                "last_updated": account.payment_terms_updated_at.isoformat() if account.payment_terms_updated_at else None,
            },
            "risk_controls_section": {
                "hold_threshold_pct": account.hold_threshold_pct,
            },
            "cooldown_section": {
                "months": resolved.months,
                "days": resolved.days,
                "hours": resolved.hours,
            },
        }

    async def post_org_cooldown(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        months: int,
        days: int,
        hours: int,
        reset_to_defaults: bool,
    ) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        if reset_to_defaults:
            await self._org_repo_cooldown.delete_for_org(organization_id)
        else:
            await self._org_repo_cooldown.upsert_for_org(
                organization_id,
                months=months,
                days=days,
                hours=hours,
            )
        return await self.get_org_cooldown_resolved_payload(organization_id)

    async def patch_payment_terms(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        payment_terms_days: int,
        effective_date: date,
        reason: str,
        apply_to_existing_unpaid: bool,
    ) -> OrgCreditAccount:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._account_repo.get_by_org_id_for_update(organization_id)
        if acct is None:
            raise ValidationError(
                "No credit account exists for this organisation. Credit must be assigned before payment terms can be updated.",
            )
        old_days = acct.payment_terms_days
        old_effective_from = acct.payment_terms_effective_from
        today = datetime.now(UTC).date()
        old_label = str(old_days) if old_days is not None else None
        new_label = str(payment_terms_days)

        if effective_date <= today:
            acct.payment_terms_days = payment_terms_days
            acct.payment_terms_effective_from = effective_date
            acct.payment_terms_updated_at = datetime.now(UTC)
            acct.pending_payment_terms_days = None
            acct.pending_payment_terms_effective_from = None
            hist_status = ScheduledCreditSettingStatus.APPLIED
            applied_at = datetime.now(UTC)
        else:
            acct.pending_payment_terms_days = payment_terms_days
            acct.pending_payment_terms_effective_from = effective_date
            acct.payment_terms_updated_at = datetime.now(UTC)
            hist_status = ScheduledCreditSettingStatus.SCHEDULED
            applied_at = None

        await self._session.flush()
        await self._terms_hist_repo.create({
            "organization_id": organization_id,
            "credit_account_id": acct.id,
            "old_payment_terms": old_label,
            "new_payment_terms": new_label,
            "effective_date": effective_date,
            "modified_by_user_id": caller.id,
            "reason": reason,
            "applied_to_unpaid_invoices": apply_to_existing_unpaid,
            "status": hist_status,
            "applied_at": applied_at,
        })
        await self._audit.log(
            action="org_credit.payment_terms_set",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={
                "payment_terms_days": old_days,
                "payment_terms_effective_from": old_effective_from.isoformat() if old_effective_from else None,
            },
            new_value={
                "payment_terms_days": payment_terms_days,
                "payment_terms_effective_from": effective_date.isoformat(),
                "pending": effective_date > today,
                "reason": reason,
                "apply_to_existing_unpaid": apply_to_existing_unpaid,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=reason or f"Payment terms set to {payment_terms_days}d (effective {effective_date.isoformat()})",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
            severity="NOTICE",
        )
        logger.info("org_credit.payment_terms_set", organization_id=organization_id)
        await self._session.refresh(acct)
        return acct

    async def patch_credit_limit(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        credit_limit: Decimal,
        reason_category: str,
        effective_date: date,
        justification: str,
    ) -> OrgCreditAccount:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._account_repo.get_by_org_id_for_update(organization_id)
        if acct is None:
            raise ValidationError(
                "No credit account exists for this organisation. Credit must be assigned before the limit can be updated.",
            )
        old_limit = acct.credit_limit
        today = datetime.now(UTC).date()

        if effective_date <= today:
            acct.credit_limit = credit_limit
            acct.pending_credit_limit = None
            acct.pending_credit_limit_effective_from = None
            hist_status = ScheduledCreditSettingStatus.APPLIED
            applied_at = datetime.now(UTC)
        else:
            acct.pending_credit_limit = credit_limit
            acct.pending_credit_limit_effective_from = effective_date
            hist_status = ScheduledCreditSettingStatus.SCHEDULED
            applied_at = None

        acct.credit_limit_updated_at = datetime.now(UTC)

        await self._session.flush()
        await self._limit_hist_repo.create({
            "organization_id": organization_id,
            "credit_account_id": acct.id,
            "previous_limit": old_limit,
            "new_limit": credit_limit,
            "effective_date": effective_date,
            "reason_category": reason_category,
            "justification": justification,
            "modified_by_user_id": caller.id,
            "status": hist_status,
            "applied_at": applied_at,
        })
        await self._audit.log(
            action="org_credit.credit_limit_set",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"credit_limit": str(old_limit) if old_limit is not None else None},
            new_value={
                "credit_limit": str(credit_limit),
                "reason_category": reason_category,
                "effective_date": effective_date.isoformat(),
                "scheduled": effective_date > today,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=justification or f"Credit limit set to {credit_limit} (effective {effective_date.isoformat()})",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_LIMIT_ADJUSTED,
            severity="NOTICE",
        )
        logger.info(
            "org_credit.credit_limit_set",
            organization_id=organization_id,
            account_id=acct.id,
            scheduled=effective_date > today,
        )
        await self._session.refresh(acct)
        return acct

    async def list_credit_limit_adjustment_history(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._limit_hist_repo.list_by_organization_with_actor(
            organization_id,
            page=page,
            size=size,
        )
        items: list[dict[str, Any]] = []
        for hist in rows:
            actor = hist.modified_by_user
            updated_by = None
            if hist.modified_by_user_id is not None:
                updated_by = {
                    "id": str(hist.modified_by_user_id),
                    "first_name": actor.first_name if actor is not None else None,
                    "last_name": actor.last_name if actor is not None else None,
                }
            chg_amt, chg_pct, adj_type = _limit_change_meta(hist.previous_limit, hist.new_limit)
            items.append({
                "id": str(hist.id),
                "date": hist.created_at.isoformat(),
                "previous_limit": str(hist.previous_limit) if hist.previous_limit is not None else None,
                "new_limit": str(hist.new_limit),
                "change_amount": chg_amt,
                "change_pct": chg_pct,
                "adjustment_type": adj_type,
                "effective_date": hist.effective_date.isoformat(),
                "updated_by": updated_by,
                "reason_category": hist.reason_category,
                "justification": hist.justification,
                "status": hist.status.value,
            })
        return items, total

    async def apply_due_scheduled_credit_and_terms(self, today: date) -> int:
        logger.info(LogEvent.ORG_CREDIT_SCHEDULED_SETTINGS_CRON_STARTED, run_date=today.isoformat())
        org_ids = await self._account_repo.list_org_ids_with_due_pending_settings(today)
        applied = 0
        for organization_id in org_ids:
            acct = await self._account_repo.get_by_org_id_for_update(organization_id)
            if acct is None:
                continue

            if (
                acct.pending_credit_limit is not None
                and acct.pending_credit_limit_effective_from is not None
                and acct.pending_credit_limit_effective_from <= today
            ):
                eff = acct.pending_credit_limit_effective_from
                prev = acct.credit_limit
                new_lim = acct.pending_credit_limit
                acct.credit_limit = new_lim
                acct.pending_credit_limit = None
                acct.pending_credit_limit_effective_from = None
                row = await self._limit_hist_repo.find_scheduled_by_account_and_effective_date(acct.id, eff)
                if row is not None:
                    row.status = ScheduledCreditSettingStatus.APPLIED
                    row.applied_at = datetime.now(UTC)
                await self._audit.log(
                    action="org_credit.credit_limit_effective",
                    entity_type="org_credit_account",
                    entity_id=acct.id,
                    user_id=None,
                    user_role="SYSTEM",
                    old_value={"credit_limit": str(prev) if prev is not None else None},
                    new_value={"credit_limit": str(new_lim), "effective_date": eff.isoformat()},
                    ip_address=None,
                    user_agent=None,
                    reason=f"Scheduled credit limit change applied (now {new_lim})",
                    organization_id=organization_id,
                    category=AuditCategory.CREDIT,
                    event_type=AuditEventType.CREDIT_LIMIT_ADJUSTED,
                    severity="NOTICE",
                )
                logger.info(
                    LogEvent.ORG_CREDIT_SCHEDULED_SETTINGS_APPLIED,
                    kind="credit_limit",
                    organization_id=organization_id,
                    account_id=acct.id,
                )
                applied += 1

            if (
                acct.pending_payment_terms_days is not None
                and acct.pending_payment_terms_effective_from is not None
                and acct.pending_payment_terms_effective_from <= today
            ):
                eff = acct.pending_payment_terms_effective_from
                prev_days = acct.payment_terms_days
                new_days = acct.pending_payment_terms_days
                acct.payment_terms_days = new_days
                acct.payment_terms_effective_from = eff
                acct.payment_terms_updated_at = datetime.now(UTC)
                acct.pending_payment_terms_days = None
                acct.pending_payment_terms_effective_from = None
                row = await self._terms_hist_repo.find_scheduled_by_account_and_effective_date(acct.id, eff)
                if row is not None:
                    row.status = ScheduledCreditSettingStatus.APPLIED
                    row.applied_at = datetime.now(UTC)
                await self._audit.log(
                    action="org_credit.payment_terms_effective",
                    entity_type="org_credit_account",
                    entity_id=acct.id,
                    user_id=None,
                    user_role="SYSTEM",
                    old_value={"payment_terms_days": prev_days},
                    new_value={"payment_terms_days": new_days, "effective_date": eff.isoformat()},
                    ip_address=None,
                    user_agent=None,
                    reason=f"Scheduled payment terms change applied (now {new_days}d)",
                    organization_id=organization_id,
                    category=AuditCategory.CREDIT,
                    event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
                    severity="NOTICE",
                )
                logger.info(
                    LogEvent.ORG_CREDIT_SCHEDULED_SETTINGS_APPLIED,
                    kind="payment_terms",
                    organization_id=organization_id,
                    account_id=acct.id,
                )
                applied += 1

            await self._session.flush()

        logger.info(
            LogEvent.ORG_CREDIT_SCHEDULED_SETTINGS_CRON_COMPLETED,
            organizations_checked=len(org_ids),
            transitions_applied=applied,
            run_date=today.isoformat(),
        )
        return applied

    async def get_risk_controls_payload(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)
        return {"hold_threshold_pct": acct.hold_threshold_pct}

    async def patch_risk_controls(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        hold_threshold_pct: int,
    ) -> OrgCreditAccount:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._account_repo.get_by_org_id_for_update(organization_id)
        if acct is None:
            raise ValidationError(
                "No credit account exists for this organisation. Credit must be assigned before risk controls can be updated.",
            )
        old = acct.hold_threshold_pct
        acct.hold_threshold_pct = hold_threshold_pct
        await self._session.flush()
        await self._audit.log(
            action="org_credit.hold_threshold_set",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"hold_threshold_pct": old},
            new_value={"hold_threshold_pct": hold_threshold_pct},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Hold threshold set to {hold_threshold_pct}%",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SETTINGS_UPDATED,
            severity="INFO",
        )
        logger.info("org_credit.hold_threshold_set", organization_id=organization_id, account_id=acct.id)
        await self._session.refresh(acct)
        return acct

    async def list_terms_modification_history(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._terms_hist_repo.list_by_organization_with_actor(
            organization_id,
            page=page,
            size=size,
        )
        items: list[dict[str, Any]] = []
        for hist in rows:
            actor = hist.modified_by_user
            modified_by = None
            if hist.modified_by_user_id is not None:
                modified_by = {
                    "id": str(hist.modified_by_user_id),
                    "first_name": actor.first_name if actor is not None else None,
                    "last_name": actor.last_name if actor is not None else None,
                }
            items.append({
                "id": str(hist.id),
                "date": hist.created_at.isoformat(),
                "old_terms": hist.old_payment_terms,
                "new_terms": hist.new_payment_terms,
                "effective_date": hist.effective_date.isoformat(),
                "modified_by": modified_by,
                "reason": hist.reason,
                "applied_to_existing": hist.applied_to_unpaid_invoices,
                "status": hist.status.value,
                "applied_at": hist.applied_at.isoformat() if hist.applied_at else None,
            })
        return items, total

    async def get_active_cooldown_public_payload(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        row = await self._window_repo.get_by_org_id(organization_id)
        now = datetime.now(UTC)
        if row is None:
            return {
                "active": False,
                "ends_at": None,
                "remaining_seconds": None,
                "summary": None,
            }
        if now >= row.ends_at:
            return {
                "active": False,
                "ends_at": None,
                "remaining_seconds": None,
                "summary": None,
            }
        remaining = int((row.ends_at - now).total_seconds())
        ends_iso = row.ends_at.isoformat()
        summary = f"Cool-down active until {ends_iso} ({humanize_cooldown_remaining(remaining)})"
        return {
            "active": True,
            "ends_at": ends_iso,
            "remaining_seconds": remaining,
            "summary": summary,
        }

    async def start_cooldown_window(
        self,
        organization_id: str,
        *,
        started_at: datetime | None = None,
    ) -> OrgCreditCooldownWindow:
        """Start or replace the org cool-down window using the resolved policy duration.

        Intended for internal callers (e.g. credit application flows after a qualifying event).
        There is no public HTTP endpoint for creating windows.
        """
        await self._org_repo.get_by_id_or_404(organization_id)
        org_row = await self._org_repo_cooldown.get_by_org_id(organization_id)
        global_row = await self._global_repo.get_singleton()
        resolved = resolve_cooldown_for_org(org_row, global_row)
        if resolved.months == 0 and resolved.days == 0 and resolved.hours == 0:
            raise ValidationError("Resolved cool-down duration is zero; cannot start a window.")
        start = started_at if started_at is not None else datetime.now(UTC)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = ends_at_after_duration(
            start,
            months=resolved.months,
            days=resolved.days,
            hours=resolved.hours,
        )
        row = await self._window_repo.upsert_for_org(
            organization_id,
            started_at=start,
            ends_at=end,
            policy_months=resolved.months,
            policy_days=resolved.days,
            policy_hours=resolved.hours,
        )
        logger.info(
            "org_credit_cooldown_window.started",
            organization_id=organization_id,
            ends_at=end.isoformat(),
        )
        return row
