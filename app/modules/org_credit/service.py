from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.org_credit.enums import (
    CloseAccountReason,
    HoldReasonCategory,
    OrgCreditAccountStatus,
    OrgCreditAdjustmentReason,
    OrgCreditLedgerMovementType,
    OrgCreditLedgerSourceType,
    OrgCreditReviewFrequency,
    internal_credit_score_band,
)
from app.modules.org_credit.models import (
    OrgCreditAccount,
    OrgCreditLedgerEntry,
    OrgCreditStatusHistory,
)
from app.modules.org_credit.repository import (
    OrgCreditAccountRepository,
    OrgCreditInternalScoreHistoryRepository,
    OrgCreditLedgerRepository,
    OrgCreditReportRepository,
    OrgCreditStatusHistoryRepository,
)
from app.modules.org_credit_alerts.service import OrgCreditAlertService
from app.modules.org_credit_settings.repository import (
    OrgCreditLimitAdjustmentHistoryRepository,
    OrgCreditTermsModificationHistoryRepository,
)
from app.modules.org_credit_suspension.repository import OrgCreditConfigRepository
from app.modules.organizations.repository import OrganizationRepository

logger = structlog.get_logger()

_MOVEMENT_DESCRIPTIONS: dict[str, str] = {
    "CONSUME": "Credit consumed",
    "REPAY": "Credit repaid",
    "MANUAL_ADJUST_USED": "Manual credit adjustment",
}


def _available_credit(account: OrgCreditAccount) -> Decimal:
    if account.credit_limit is None:
        return Decimal("0")
    return account.credit_limit - account.used_credit


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


def _actor_display(first: str | None, last: str | None, email: str | None) -> str | None:
    name = " ".join(p for p in (first, last) if p)
    return name or email


def _format_status_duration_days(start: datetime, end: datetime) -> str:
    secs = max(0, int((end - start).total_seconds()))
    days = secs // 86400
    return f"{days}d"


class _AccountOpsMixin:
    """Shared plumbing for services that need to load & mutate the credit account."""

    _account_repo: OrgCreditAccountRepository
    _ledger_repo: OrgCreditLedgerRepository

    async def _load_account_locked(self, organization_id: str) -> OrgCreditAccount:
        acct = await self._account_repo.get_by_org_id_for_update(organization_id)
        if acct is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)
        return acct

    @staticmethod
    def _user_to_schema_payload(user: Any | None) -> dict[str, Any] | None:
        if user is None:
            return None
        return {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

    async def _append_ledger(
        self,
        *,
        organization_id: str,
        account: OrgCreditAccount,
        movement_type: OrgCreditLedgerMovementType,
        source_type: OrgCreditLedgerSourceType | None,
        source_id: str | None,
        idempotency_key: str | None,
        actor_user_id: str | None,
        adjustment_reason: OrgCreditAdjustmentReason | None,
    ) -> OrgCreditLedgerEntry:
        avail = _available_credit(account)
        return await self._ledger_repo.create({
            "organization_id": organization_id,
            "account_id": account.id,
            "movement_type": movement_type,
            "source_type": source_type,
            "source_id": source_id,
            "idempotency_key": idempotency_key,
            "used_credit_after": account.used_credit,
            "available_credit_after": avail,
            "credit_limit_after": account.credit_limit,
            "actor_user_id": actor_user_id,
            "adjustment_reason": adjustment_reason,
        })


class OrgCreditService(BaseService, _AccountOpsMixin):
    """Public admin operations: provisioning, status transitions, overview reads.

    All actual money-movement (consume / repay / manual-adjust) lives in
    :class:`OrgCreditLedgerService` and is only reachable from inside the
    backend (orders, invoices, payments, background jobs) — never from
    an HTTP route.
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._account_repo = OrgCreditAccountRepository(session)
        self._ledger_repo = OrgCreditLedgerRepository(session)
        self._report_repo = OrgCreditReportRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._credit_config_repo = OrgCreditConfigRepository(session)
        self._limit_hist_repo = OrgCreditLimitAdjustmentHistoryRepository(session)
        self._terms_hist_repo = OrgCreditTermsModificationHistoryRepository(session)
        self._status_repo = OrgCreditStatusHistoryRepository(session)
        self._score_repo = OrgCreditInternalScoreHistoryRepository(session)
        self._audit = AuditService(session)
        self._audit_repo = AuditRepository(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _reload_account_for_read(self, organization_id: str) -> OrgCreditAccount:
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)
        return acct

    def credit_status_payload(self, account: OrgCreditAccount) -> dict[str, Any]:
        last_changed_at = (
            account.last_status_change_at.isoformat() if account.last_status_change_at else None
        )
        return {
            "status": account.status.value,
            "last_changed_at": last_changed_at,
            "reason": account.status_reason,
            "action_by": self._user_to_schema_payload(account.action_by_user),
        }

    async def get_or_create_account(self, organization_id: str) -> OrgCreditAccount:
        await self._org_repo.get_by_id_or_404(organization_id)
        existing = await self._account_repo.get_by_org_id(organization_id)
        if existing:
            return existing
        cfg = await self._credit_config_repo.get_by_org(organization_id)
        initial_limit = cfg.approved_credit_limit if cfg else None
        now = datetime.now(UTC)
        row: dict[str, Any] = {
            "organization_id": organization_id,
            "status": OrgCreditAccountStatus.ACTIVE,
            "status_reason": None,
            "action_by_user_id": None,
            "credit_limit": initial_limit,
            "used_credit": Decimal("0"),
            "credit_facility_start_date": now.date(),
            "last_status_change_at": now,
        }
        if initial_limit is not None:
            row["credit_limit_updated_at"] = now
        return await self._account_repo.create(row)

    async def _record_status_transition(
        self,
        *,
        organization_id: str,
        account: OrgCreditAccount,
        from_status: OrgCreditAccountStatus | None,
        to_status: OrgCreditAccountStatus,
        reason: str | None,
        actor_user_id: str | None,
    ) -> OrgCreditStatusHistory:
        return await self._status_repo.create({
            "organization_id": organization_id,
            "credit_account_id": account.id,
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "actor_user_id": actor_user_id,
        })

    async def record_system_status_transition(
        self,
        *,
        organization_id: str,
        account: OrgCreditAccount,
        from_status: OrgCreditAccountStatus | None,
        to_status: OrgCreditAccountStatus,
        reason: str | None,
    ) -> OrgCreditStatusHistory:
        return await self._record_status_transition(
            organization_id=organization_id,
            account=account,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            actor_user_id=None,
        )

    async def provision_account_on_approval(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        application_id: str,
        approved_credit_limit: Decimal,
        approved_payment_terms_days: int,
        review_frequency_value: str | None,
    ) -> OrgCreditAccount:
        """Create or reactivate the organisation's credit account after an application is approved.

        Sets the approved credit limit, payment terms, review frequency, and
        activation timestamps. Ensures the account status is ACTIVE, records a
        status-history row, and an audit log entry linking the provisioning
        back to the source application.
        """
        await self._org_repo.get_by_id_or_404(organization_id)
        existing = await self._account_repo.get_by_org_id(organization_id)
        is_new = existing is None
        now = datetime.now(UTC)
        if is_new:
            await self._account_repo.create({
                "organization_id": organization_id,
                "status": OrgCreditAccountStatus.ACTIVE,
                "status_reason": "Provisioned on credit application approval",
                "action_by_user_id": caller.id,
                "credit_limit": approved_credit_limit,
                "used_credit": Decimal("0"),
                "credit_facility_start_date": now.date(),
                "last_status_change_at": now,
                "credit_limit_updated_at": now,
            })

        acct = await self._load_account_locked(organization_id)
        old_limit = acct.credit_limit
        old_status = acct.status
        old_payment_terms_days = acct.payment_terms_days
        old_review_frequency = acct.review_frequency

        payment_terms_days = approved_payment_terms_days
        review_frequency_enum: OrgCreditReviewFrequency | None = None
        if review_frequency_value is not None:
            mapped = "ANNUAL" if review_frequency_value == "ANNUALLY" else review_frequency_value
            review_frequency_enum = OrgCreditReviewFrequency(mapped)

        today = datetime.now(UTC).date()
        acct.credit_limit = approved_credit_limit
        acct.credit_limit_updated_at = now
        acct.payment_terms_days = payment_terms_days
        acct.payment_terms_effective_from = today
        acct.payment_terms_updated_at = datetime.now(UTC)
        if review_frequency_enum is not None:
            acct.review_frequency = review_frequency_enum
        status_changed = acct.status != OrgCreditAccountStatus.ACTIVE
        if status_changed:
            acct.status = OrgCreditAccountStatus.ACTIVE
            acct.last_status_change_at = datetime.now(UTC)
            acct.status_reason = "Reactivated on credit application approval"
            acct.action_by_user_id = caller.id
        if acct.credit_facility_start_date is None:
            acct.credit_facility_start_date = datetime.now(UTC).date()

        await self._session.flush()

        if status_changed:
            await self._record_status_transition(
                organization_id=organization_id,
                account=acct,
                from_status=old_status,
                to_status=OrgCreditAccountStatus.ACTIVE,
                reason="Reactivated on credit application approval",
                actor_user_id=caller.id,
            )

        await self._audit.log(
            action="org_credit_account.provisioned" if is_new else "org_credit_account.reactivated_on_approval",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={
                "credit_limit": str(old_limit) if old_limit is not None else None,
                "status": old_status.value if old_status else None,
                "payment_terms_days": old_payment_terms_days,
                "review_frequency": old_review_frequency.value if old_review_frequency else None,
            },
            new_value={
                "credit_limit": str(approved_credit_limit),
                "status": OrgCreditAccountStatus.ACTIVE.value,
                "payment_terms_days": payment_terms_days,
                "review_frequency": review_frequency_enum.value if review_frequency_enum else None,
                "application_id": application_id,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=(
                f"Credit account provisioned on application {application_id} approval"
                if is_new
                else f"Credit account reactivated on application {application_id} approval"
            ),
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_APPROVED,
            severity="NOTICE",
        )
        logger.info(
            "org_credit_account.provisioned",
            organization_id=organization_id,
            account_id=acct.id,
            application_id=application_id,
            is_new=is_new,
        )
        return await self._reload_account_for_read(organization_id)

    async def place_hold(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        hold_reason_category: HoldReasonCategory,
        detailed_reason: str | None = None,
    ) -> OrgCreditAccount:
        await self.get_or_create_account(organization_id)
        acct = await self._load_account_locked(organization_id)
        if acct.status != OrgCreditAccountStatus.ACTIVE:
            raise ValidationError("Hold can only be placed on an active credit account.")
        old_status = acct.status
        acct.status = OrgCreditAccountStatus.ON_HOLD
        acct.last_status_change_at = datetime.now(UTC)
        reason_text = detailed_reason or hold_reason_category.value.replace("_", " ").title()
        acct.status_reason = reason_text
        acct.action_by_user_id = caller.id
        await self._session.flush()
        await self._record_status_transition(
            organization_id=organization_id,
            account=acct,
            from_status=old_status,
            to_status=OrgCreditAccountStatus.ON_HOLD,
            reason=reason_text,
            actor_user_id=caller.id,
        )
        await self._audit.log(
            action="org_credit.hold_placed",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": old_status.value},
            new_value={"status": acct.status.value, "hold_reason_category": hold_reason_category.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Hold placed: {reason_text}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_HOLD_TRIGGERED,
            severity="WARNING",
        )
        try:
            await OrgCreditAlertService(self._session).evaluate_after_status_change(organization_id, acct)
        except Exception:
            logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="hold_placed")
        return await self._reload_account_for_read(organization_id)

    async def release_hold(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        memo: str | None = None,
    ) -> OrgCreditAccount:
        await self.get_or_create_account(organization_id)
        acct = await self._load_account_locked(organization_id)
        if acct.status != OrgCreditAccountStatus.ON_HOLD:
            raise ValidationError("Release hold is only valid when the account is on hold.")
        old_status = acct.status
        acct.status = OrgCreditAccountStatus.ACTIVE
        acct.last_status_change_at = datetime.now(UTC)
        acct.status_reason = memo or "Hold released"
        acct.action_by_user_id = caller.id
        await self._session.flush()
        await self._record_status_transition(
            organization_id=organization_id,
            account=acct,
            from_status=old_status,
            to_status=OrgCreditAccountStatus.ACTIVE,
            reason=memo or "Hold released",
            actor_user_id=caller.id,
        )
        await self._audit.log(
            action="org_credit.hold_released",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": old_status.value},
            new_value={"status": acct.status.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=memo or "Hold released",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_HOLD_REINSTATED,
            severity="NOTICE",
        )
        await self._session.refresh(acct)
        return acct

    async def suspend_account(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        reason: str,
        trigger_payment_acceleration: bool = False,
    ) -> OrgCreditAccount:
        await self.get_or_create_account(organization_id)
        acct = await self._load_account_locked(organization_id)
        if acct.status == OrgCreditAccountStatus.CLOSED:
            raise ValidationError("Cannot suspend a closed credit account.")
        if acct.status == OrgCreditAccountStatus.SUSPENDED:
            return await self._reload_account_for_read(organization_id)
        old_status = acct.status
        acct.status = OrgCreditAccountStatus.SUSPENDED
        acct.last_status_change_at = datetime.now(UTC)
        acct.status_reason = reason
        acct.action_by_user_id = caller.id
        await self._session.flush()
        await self._record_status_transition(
            organization_id=organization_id,
            account=acct,
            from_status=old_status,
            to_status=OrgCreditAccountStatus.SUSPENDED,
            reason=reason,
            actor_user_id=caller.id,
        )
        await self._audit.log(
            action="org_credit.suspended",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": old_status.value},
            new_value={"status": acct.status.value, "trigger_payment_acceleration": trigger_payment_acceleration},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Account suspended: {reason}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SUSPENDED,
            severity="CRITICAL",
        )
        try:
            await OrgCreditAlertService(self._session).evaluate_after_status_change(organization_id, acct)
        except Exception:
            logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="suspended")
        return await self._reload_account_for_read(organization_id)

    async def reactivate_account(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        memo: str | None = None,
    ) -> OrgCreditAccount:
        await self.get_or_create_account(organization_id)
        acct = await self._load_account_locked(organization_id)
        if acct.status not in (OrgCreditAccountStatus.SUSPENDED, OrgCreditAccountStatus.ON_HOLD):
            raise ValidationError("Reactivate is only valid for suspended or on-hold accounts.")
        old_status = acct.status
        acct.status = OrgCreditAccountStatus.ACTIVE
        acct.last_status_change_at = datetime.now(UTC)
        acct.status_reason = memo or "Reactivated"
        acct.action_by_user_id = caller.id
        await self._session.flush()
        await self._record_status_transition(
            organization_id=organization_id,
            account=acct,
            from_status=old_status,
            to_status=OrgCreditAccountStatus.ACTIVE,
            reason=memo or "Reactivated",
            actor_user_id=caller.id,
        )
        await self._audit.log(
            action="org_credit.reactivated",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": old_status.value},
            new_value={"status": acct.status.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=memo or f"Reactivated from {old_status.value}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SUSPENSION_REINSTATED,
            severity="NOTICE",
        )
        return await self._reload_account_for_read(organization_id)

    async def close_account(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        reason_category: CloseAccountReason,
        detailed_reason: str | None = None,
    ) -> OrgCreditAccount:
        await self.get_or_create_account(organization_id)
        acct = await self._load_account_locked(organization_id)
        if acct.status == OrgCreditAccountStatus.CLOSED:
            raise ValidationError("Credit account is already closed.")
        old_status = acct.status
        acct.status = OrgCreditAccountStatus.CLOSED
        acct.last_status_change_at = datetime.now(UTC)
        reason_text = detailed_reason or reason_category.value.replace("_", " ").title()
        acct.status_reason = reason_text
        acct.action_by_user_id = caller.id
        await self._session.flush()
        await self._record_status_transition(
            organization_id=organization_id,
            account=acct,
            from_status=old_status,
            to_status=OrgCreditAccountStatus.CLOSED,
            reason=reason_text,
            actor_user_id=caller.id,
        )
        await self._audit.log(
            action="org_credit.closed",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": old_status.value},
            new_value={"status": acct.status.value, "reason_category": reason_category.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Account closed: {reason_text}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_STATUS_CHANGED,
            severity="CRITICAL",
        )
        logger.info("org_credit.closed", organization_id=organization_id, account_id=acct.id)
        return await self._reload_account_for_read(organization_id)

    async def get_credit_overview(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        account = await self._account_repo.get_by_org_id(organization_id)
        report = await self._report_repo.get_by_org_id(organization_id)
        cfg = await self._credit_config_repo.get_by_org(organization_id)

        null_ob = {
            "total": None,
            "as_of": None,
            "current": None,
            "unpaid_invoice_count": None,
            "overdue_portion": None,
        }
        null_overdue = {
            "total": None,
            "overdue_invoice_count": None,
            "oldest_overdue_days": None,
        }
        null_next_invoice = {
            "due_date": None,
            "days_until_due": None,
        }

        latest_score = None
        if account is not None:
            latest_score = await self._score_repo.latest_for_org(organization_id)

        if account is None:
            return {
                "account": None,
                "utilization_percent": None,
                "available_credit": None,
                "credit_status": None,
                "credit_limit": None,
                "credit_terms": None,
                "next_review": None,
                "outstanding_balance": null_ob,
                "overdue": null_overdue,
                "next_invoice": null_next_invoice,
                "internal_credit_score": None,
                "report_summary": self._report_summary(report) if report else None,
                "config_summary": self._config_summary(cfg),
                "credit_facility_end_date": None,
                "risk_flags": ["NO_CREDIT_ACCOUNT"],
            }

        avail = _available_credit(account)
        util_pct: float | None = None
        if account.credit_limit and account.credit_limit > 0:
            util_pct = float(account.used_credit / account.credit_limit * 100)

        risk_flags: list[str] = []
        if account.status == OrgCreditAccountStatus.ON_HOLD:
            risk_flags.append("ON_HOLD")
        if account.status == OrgCreditAccountStatus.SUSPENDED:
            risk_flags.append("SUSPENDED")
        if cfg and cfg.credit_utilization_warning_pct is not None and util_pct is not None:
            if util_pct >= float(cfg.credit_utilization_warning_pct):
                risk_flags.append("HIGH_UTILIZATION")

        next_review_due: str | None = None
        next_review_days_remaining: int | None = None
        if account.next_review_date:
            next_review_due = account.next_review_date.isoformat()
            delta = account.next_review_date - datetime.now(UTC).date()
            next_review_days_remaining = delta.days

        outstanding_balance = {
            "total": str(account.used_credit),
            "as_of": datetime.now(UTC).isoformat(),
            "current": None,
            "unpaid_invoice_count": None,
            "overdue_portion": None,
        }

        internal_credit_score: dict[str, Any] | None = None
        if latest_score is not None:
            internal_credit_score = {
                "score": latest_score.score,
                "label": internal_credit_score_band(latest_score.score).value,
                "last_recalculated_at": latest_score.created_at.isoformat(),
            }

        credit_status = self.credit_status_payload(account)

        latest_adj = await self._limit_hist_repo.get_latest_applied(organization_id)
        last_limit_adj_at: str | None = None
        if latest_adj is not None:
            if latest_adj.applied_at is not None:
                last_limit_adj_at = latest_adj.applied_at.isoformat()
            else:
                last_limit_adj_at = latest_adj.created_at.isoformat()
        elif account.credit_limit_updated_at is not None:
            last_limit_adj_at = account.credit_limit_updated_at.isoformat()

        credit_limit_block: dict[str, Any] = {
            "amount": str(account.credit_limit) if account.credit_limit is not None else None,
            "last_adjusted_at": last_limit_adj_at,
        }

        terms_days = account.payment_terms_days
        terms_label = f"Net {terms_days}" if terms_days is not None else None
        credit_terms_block: dict[str, Any] = {
            "payment_terms_days": terms_days,
            "terms_label": terms_label,
        }

        next_review_block: dict[str, Any] = {
            "due_date": next_review_due,
            "days_remaining": next_review_days_remaining,
        }

        return {
            "account": self.account_to_public_dict(account),
            "utilization_percent": util_pct,
            "available_credit": str(avail),
            "credit_status": credit_status,
            "credit_limit": credit_limit_block,
            "credit_terms": credit_terms_block,
            "next_review": next_review_block,
            "outstanding_balance": outstanding_balance,
            "overdue": null_overdue,
            "next_invoice": null_next_invoice,
            "internal_credit_score": internal_credit_score,
            "report_summary": self._report_summary(report) if report else None,
            "config_summary": self._config_summary(cfg),
            "credit_facility_end_date": account.credit_facility_end_date.isoformat()
            if account.credit_facility_end_date else None,
            "risk_flags": risk_flags,
        }

    async def get_credit_account_overview(self, organization_id: str) -> dict[str, Any]:
        """Minimal limit/used/available snapshot for the order-creation UI.

        Raises NotFoundError if the org has no credit account so the FE can
        show a "no credit account configured" banner instead of zero figures.
        """
        await self._org_repo.get_by_id_or_404(organization_id)
        account = await self._account_repo.get_by_org_id(organization_id)
        if account is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)

        limit = account.credit_limit or Decimal("0")
        used = account.used_credit or Decimal("0")
        # `_available_credit` clamps to credit_limit - used_credit (can go
        # negative if over-limit consumes were allowed); we hand it through
        # unchanged so the FE can decide whether to block submission.
        available = _available_credit(account)
        percent = float(used / limit * 100) if limit > 0 else 0.0
        return {
            "status": account.status.value,
            "credit_limit": str(limit),
            "outstanding_balance": str(used),
            "available_credit": str(available),
            "credit_limit_used_percent": round(percent, 2),
        }

    async def list_status_history(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._status_repo.list_for_org_with_next_change_at(
            organization_id, page=page, size=size,
        )
        now = datetime.now(UTC)
        items: list[dict[str, Any]] = []
        for entry, next_change_at in rows:
            actor = entry.actor_user
            span_end = next_change_at if next_change_at is not None else now
            duration = _format_status_duration_days(entry.created_at, span_end)
            items.append({
                "id": entry.id,
                "from_status": entry.from_status.value if entry.from_status else None,
                "to_status": entry.to_status.value,
                "reason": entry.reason,
                "duration": duration,
                "created_at": entry.created_at.isoformat(),
                "action_by": {
                    "id": actor.id,
                    "first_name": actor.first_name,
                    "last_name": actor.last_name,
                } if actor else None,
            })
        return items, total

    async def list_credit_activity(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        event_types: list[str] | None = None,
        user_types: list[str] | None = None,
        severities: list[str] | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Unified credit activity feed sourced from ``audit_log``.

        Returns ``CREDIT``-category audit entries for the organisation,
        ordered newest first. Each row is shaped to match
        :class:`CreditActivityEntryResponse` (event type/label, actor bucket,
        severity, reason, etc.) so the timeline stays consistent regardless
        of which credit module emitted the original event.
        """
        await self._org_repo.get_by_id_or_404(organization_id)

        rows, total = await self._audit_repo.get_credit_activity_logs(
            organization_id,
            page=page,
            size=size,
            event_types=event_types,
            user_types=user_types,
            severities=severities,
            search=search,
            from_date=from_date,
            to_date=to_date,
        )

        items = [self._audit_log_to_activity_entry(row) for row in rows]
        return items, total

    @staticmethod
    def _user_type_for(log: Any) -> str:
        if log.user_id is None:
            return "System"
        role = (log.user_role or "").upper()
        if role in ("ADMIN", "SUPER_ADMIN"):
            return "Admin"
        return "Client"

    @staticmethod
    def _event_label(event_type: str | None, action: str) -> str:
        raw = event_type or action or ""
        return raw.replace("_", " ").title() if raw else ""

    @staticmethod
    def _description_from(log: Any) -> str | None:
        if log.reason:
            return log.reason
        if log.new_value and isinstance(log.new_value, dict):
            for key in ("description", "summary", "message", "justification"):
                val = log.new_value.get(key)
                if isinstance(val, str) and val:
                    return val
        return None

    def _audit_log_to_activity_entry(self, log: Any) -> dict[str, Any]:
        actor = log.user
        return {
            "id": log.id,
            "event_type": log.event_type or AuditEventType.SYSTEM_CONFIG_CHANGED.value,
            "event_label": self._event_label(log.event_type, log.action),
            "description": self._description_from(log),
            "user_type": self._user_type_for(log),
            "severity": (log.severity or "INFO").upper(),
            "acted_by": _actor_display(
                actor.first_name if actor else None,
                actor.last_name if actor else None,
                actor.email if actor else None,
            ),
            "acted_by_email": actor.email if actor else None,
            "timestamp": log.created_at,
            "audit_ref": log.audit_ref,
            "entity_ref": log.entity_ref,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "ip_address": log.ip_address,
            "browser": log.browser,
            "device": log.device,
            "os": log.os,
        }

    def _report_summary(self, report: Any) -> dict[str, Any]:
        return {
            "connect_id": report.connect_id,
            "credit_score": report.credit_score,
            "credit_score_max": report.credit_score_max,
            "credit_rating": report.credit_rating,
            "company_name": report.company_name,
            "last_checked_at": report.last_checked_at.isoformat() if report.last_checked_at else None,
        }

    def _config_summary(self, cfg: Any | None) -> dict[str, Any] | None:
        if cfg is None:
            return None
        return {
            "approved_credit_limit": str(cfg.approved_credit_limit) if cfg.approved_credit_limit is not None else None,
            "credit_utilization_warning_pct": cfg.credit_utilization_warning_pct,
            "credit_clearance_period_days": cfg.credit_clearance_period_days,
            "allow_bookings_beyond_limit": cfg.allow_bookings_beyond_limit,
        }

    def account_to_public_dict(self, account: OrgCreditAccount) -> dict[str, Any]:
        avail = _available_credit(account)
        return {
            "id": account.id,
            "organization_id": account.organization_id,
            "status": account.status.value,
            "credit_limit": str(account.credit_limit) if account.credit_limit is not None else None,
            "credit_limit_updated_at": account.credit_limit_updated_at.isoformat() if account.credit_limit_updated_at else None,
            "pending_credit_limit": str(account.pending_credit_limit) if account.pending_credit_limit is not None else None,
            "pending_credit_limit_effective_from": account.pending_credit_limit_effective_from.isoformat()
            if account.pending_credit_limit_effective_from else None,
            "used_credit": str(account.used_credit),
            "available_credit": str(avail),
            "status_reason": account.status_reason,
            "action_by_user_id": account.action_by_user_id,
            "review_frequency": account.review_frequency.value if account.review_frequency else None,
            "review_risk_level": account.review_risk_level.value if account.review_risk_level else None,
            "last_status_change_at": account.last_status_change_at.isoformat() if account.last_status_change_at else None,
            "credit_facility_start_date": account.credit_facility_start_date.isoformat() if account.credit_facility_start_date else None,
            "credit_facility_end_date": account.credit_facility_end_date.isoformat() if account.credit_facility_end_date else None,
            "payment_terms_days": account.payment_terms_days,
            "pending_payment_terms_days": account.pending_payment_terms_days,
            "pending_payment_terms_effective_from": account.pending_payment_terms_effective_from.isoformat()
            if account.pending_payment_terms_effective_from else None,
            "payment_terms_updated_at": account.payment_terms_updated_at.isoformat() if account.payment_terms_updated_at else None,
            "payment_terms_effective_from": account.payment_terms_effective_from.isoformat()
            if account.payment_terms_effective_from else None,
            "created_at": account.created_at.isoformat(),
            "updated_at": account.updated_at.isoformat(),
        }


class OrgCreditLedgerService(BaseService, _AccountOpsMixin):
    """Internal credit ledger operations — not exposed via any HTTP route.

    Callers are in-process services (orders, invoices, payments, jobs). They
    must supply an :class:`AuthUser` captured from their own request context
    for audit attribution, along with an idempotency key when the operation
    should be safe to retry.
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._account_repo = OrgCreditAccountRepository(session)
        self._ledger_repo = OrgCreditLedgerRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _ensure_active(self, acct: OrgCreditAccount, *, action: str) -> None:
        if acct.status != OrgCreditAccountStatus.ACTIVE:
            raise ValidationError(f"{action} requires an active credit account.")

    async def _check_idempotent_dup(
        self, organization_id: str, idempotency_key: str,
    ) -> OrgCreditAccount | None:
        dup = await self._ledger_repo.get_by_org_and_idempotency_key(organization_id, idempotency_key)
        if dup is None:
            return None
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)
        return acct

    async def assert_can_consume(
        self,
        organization_id: str,
        *,
        amount: Decimal,
    ) -> OrgCreditAccount:
        """Pre-flight check before booking on credit.

        Raises :class:`ValidationError` if the org has no account, the account
        is inactive, no credit limit is set, or available credit is below
        ``amount``. Returns the account read (NOT row-locked — this is only a
        fail-fast check; :meth:`consume_credit` re-validates under lock).
        """
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise ValidationError("Organisation has no credit account.")
        await self._ensure_active(acct, action="Credit consumption")
        if acct.credit_limit is None:
            raise ValidationError("Credit limit is not set for this organisation.")
        if _available_credit(acct) < amount:
            raise ValidationError("Insufficient available credit for this order.")
        return acct

    async def consume_credit(
        self,
        organization_id: str,
        *,
        actor: AuthUser | None,
        amount: Decimal,
        source_type: OrgCreditLedgerSourceType,
        source_id: str | None,
        idempotency_key: str,
    ) -> OrgCreditAccount:
        """Deduct ``amount`` from the wallet under a row-level lock.

        The account row is loaded with ``SELECT ... FOR UPDATE`` so concurrent
        callers serialise on the same wallet, then ``used_credit`` is
        incremented atomically inside the surrounding DB transaction. If the
        operation has already been processed under the same idempotency key,
        the existing wallet is returned without any state change.
        """
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
        dup = await self._check_idempotent_dup(organization_id, idempotency_key)
        if dup is not None:
            return dup
        acct = await self._load_account_locked(organization_id)
        await self._ensure_active(acct, action="Credit consumption")
        if acct.credit_limit is None:
            raise ValidationError("Set a credit limit before consuming credit.")
        new_used = acct.used_credit + amount
        if new_used > acct.credit_limit:
            raise ValidationError("Insufficient available credit for the consumption amount.")
        acct.used_credit = new_used
        await self._session.flush()
        ledger_entry = await self._append_ledger(
            organization_id=organization_id, account=acct,
            movement_type=OrgCreditLedgerMovementType.CONSUME,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            actor_user_id=actor.id if actor else None,
            adjustment_reason=None,
        )
        await self._audit.log(
            action="org_credit.consumed",
            entity_type="org_credit_ledger_entry",
            entity_id=ledger_entry.id,
            user_id=actor.id if actor else None,
            user_role=_caller_role_str(actor) if actor else "SYSTEM",
            new_value={
                "amount": str(amount),
                "source_type": source_type.value,
                "source_id": source_id,
                "used_credit_after": str(acct.used_credit),
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Credit consumed via {source_type.value.lower()}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_CONSUMED,
            severity="INFO",
        )
        logger.info("org_credit.consume", organization_id=organization_id, amount=str(amount))
        try:
            await OrgCreditAlertService(self._session).evaluate_after_used_credit_change(
                organization_id, acct,
            )
        except Exception:
            logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="consume")
        await self._session.refresh(acct)
        return acct

    async def repay_credit(
        self,
        organization_id: str,
        *,
        actor: AuthUser | None,
        amount: Decimal,
        source_type: OrgCreditLedgerSourceType,
        source_id: str | None,
        idempotency_key: str,
    ) -> OrgCreditAccount:
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")
        dup = await self._check_idempotent_dup(organization_id, idempotency_key)
        if dup is not None:
            return dup
        acct = await self._load_account_locked(organization_id)
        await self._ensure_active(acct, action="Credit repayment")
        if acct.used_credit < amount:
            raise ValidationError("Cannot repay more than the currently used credit.")
        acct.used_credit -= amount
        await self._session.flush()
        ledger_entry = await self._append_ledger(
            organization_id=organization_id, account=acct,
            movement_type=OrgCreditLedgerMovementType.REPAY,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            actor_user_id=actor.id if actor else None,
            adjustment_reason=None,
        )
        await self._audit.log(
            action="org_credit.repaid",
            entity_type="org_credit_ledger_entry",
            entity_id=ledger_entry.id,
            user_id=actor.id if actor else None,
            user_role=_caller_role_str(actor) if actor else "SYSTEM",
            new_value={
                "amount": str(amount),
                "source_type": source_type.value,
                "source_id": source_id,
                "used_credit_after": str(acct.used_credit),
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Credit repaid via {source_type.value.lower()}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_REPAID,
            severity="INFO",
        )
        await self._session.refresh(acct)
        return acct

    async def manual_adjust_used(
        self,
        organization_id: str,
        *,
        actor: AuthUser,
        delta_used: Decimal,
        reason: OrgCreditAdjustmentReason,
        idempotency_key: str | None,
    ) -> OrgCreditAccount:
        if idempotency_key:
            dup = await self._check_idempotent_dup(organization_id, idempotency_key)
            if dup is not None:
                return dup
        acct = await self._load_account_locked(organization_id)
        await self._ensure_active(acct, action="Manual credit adjustment")
        new_used = acct.used_credit + delta_used
        if new_used < 0:
            raise ValidationError("Adjustment would make used credit negative.")
        if acct.credit_limit is not None and new_used > acct.credit_limit:
            raise ValidationError("Adjustment would exceed the credit limit.")
        acct.used_credit = new_used
        await self._session.flush()
        ledger_entry = await self._append_ledger(
            organization_id=organization_id, account=acct,
            movement_type=OrgCreditLedgerMovementType.MANUAL_ADJUST_USED,
            source_type=OrgCreditLedgerSourceType.MANUAL,
            source_id=None,
            idempotency_key=idempotency_key,
            actor_user_id=actor.id,
            adjustment_reason=reason,
        )
        await self._audit.log(
            action="org_credit.manual_adjustment",
            entity_type="org_credit_ledger_entry",
            entity_id=ledger_entry.id,
            user_id=actor.id,
            user_role=_caller_role_str(actor),
            new_value={
                "delta_used": str(delta_used),
                "adjustment_reason": reason.value,
                "used_credit_after": str(acct.used_credit),
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Manual credit adjustment ({reason.value.replace('_', ' ').lower()})",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_MANUALLY_ADJUSTED,
            severity="NOTICE",
        )
        if delta_used > 0:
            try:
                await OrgCreditAlertService(self._session).evaluate_after_used_credit_change(
                    organization_id, acct,
                )
            except Exception:
                logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="manual_adjust_used")
        await self._session.refresh(acct)
        return acct

