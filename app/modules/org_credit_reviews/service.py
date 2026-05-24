from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit.enums import OrgCreditReviewFrequency
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditReport
from app.modules.org_credit.repository import OrgCreditAccountRepository, OrgCreditReportRepository
from app.modules.org_credit.service import OrgCreditService
from app.modules.org_credit.v1.schemas import CreditReportResponse
from app.modules.org_credit_reviews.enums import CreditReviewOutcome, CreditReviewReminderPeriod, CreditReviewRiskLevel
from app.modules.org_credit_reviews.models import OrgCreditReview
from app.modules.org_credit_reviews.repository import OrgCreditReviewRepository
from app.modules.organizations.repository import OrganizationRepository

logger = structlog.get_logger()


def _credit_snapshot_from_account(account: OrgCreditAccount) -> dict[str, Any]:
    util_pct: float | None = None
    if account.credit_limit and account.credit_limit > 0:
        util_pct = float(account.used_credit / account.credit_limit * 100)
    return {
        "status": account.status.value,
        "credit_limit": str(account.credit_limit) if account.credit_limit is not None else None,
        "last_review_date": account.last_review_date.isoformat() if account.last_review_date else None,
        "utilization_percent": util_pct,
        "next_review_due": account.next_review_date.isoformat() if account.next_review_date else None,
        "risk_level": account.review_risk_level.value if account.review_risk_level else None,
    }


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


class OrgCreditReviewService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._account_repo = OrgCreditAccountRepository(session)
        self._report_repo = OrgCreditReportRepository(session)
        self._review_repo = OrgCreditReviewRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._audit = AuditService(session)
        self._credit_svc = OrgCreditService(session, request)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _resolve_credit_report_snapshot_id(
        self,
        organization_id: str,
        credit_report_id: str | None,
    ) -> str | None:
        if credit_report_id is not None:
            report = await self._report_repo.get_by_id_and_org(credit_report_id, organization_id)
            if report is None:
                raise ValidationError("No credit report exists for this organisation with the given id.")
            return report.id
        report = await self._report_repo.get_by_org_id(organization_id)
        return report.id if report else None

    async def _load_account_locked(self, organization_id: str) -> OrgCreditAccount:
        acct = await self._account_repo.get_by_org_id_for_update(organization_id)
        if acct is None:
            raise ValidationError(
                "No credit account exists for this organisation. Credit must be assigned before continuing.",
            )
        return acct

    async def configure_review(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        review_frequency: OrgCreditReviewFrequency,
        next_review_date: date,
        reminder_period: CreditReviewReminderPeriod,
        reviewer_user_id: str,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._load_account_locked(organization_id)
        old = {
            "review_frequency": acct.review_frequency.value if acct.review_frequency else None,
            "next_review_date": acct.next_review_date.isoformat() if acct.next_review_date else None,
            "review_reminder_period": acct.review_reminder_period.value if acct.review_reminder_period else None,
            "assigned_reviewer_user_id": acct.assigned_reviewer_user_id,
        }

        acct.review_frequency = review_frequency
        acct.next_review_date = next_review_date
        acct.review_reminder_period = reminder_period
        acct.assigned_reviewer_user_id = reviewer_user_id

        await self._session.flush()

        new = {
            "review_frequency": review_frequency.value,
            "next_review_date": next_review_date.isoformat(),
            "review_reminder_period": reminder_period.value,
            "assigned_reviewer_user_id": reviewer_user_id,
        }

        await self._audit.log(
            action="org_credit.review_configuration_updated",
            entity_type="org_credit_account",
            entity_id=acct.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value=old,
            new_value=new,
            ip_address=self._ip,
            user_agent=self._ua,
            reason=(
                f"Review schedule updated: frequency {review_frequency.value}, "
                f"next review {next_review_date.isoformat()}"
            ),
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_REVIEW_CONFIG_UPDATED,
            severity="INFO",
        )
        logger.info("org_credit.review_configuration_updated", organization_id=organization_id, account_id=acct.id)

    async def submit_review(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        risk_level: CreditReviewRiskLevel,
        outcome: CreditReviewOutcome,
        review_notes: str | None = None,
        next_review_frequency: OrgCreditReviewFrequency | None = None,
        recommended_new_limit: Decimal | None = None,
        recommended_payment_terms_days: int | None = None,
        credit_report_id: str | None = None,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._load_account_locked(organization_id)

        snapshot_id = await self._resolve_credit_report_snapshot_id(organization_id, credit_report_id)
        today = datetime.now(UTC).date()

        review = await self._review_repo.create({
            "organization_id": organization_id,
            "account_id": acct.id,
            "reviewer_user_id": caller.id,
            "review_date": today,
            "review_frequency_at_time": acct.review_frequency,
            "risk_level": risk_level,
            "outcome": outcome,
            "review_notes": review_notes,
            "next_review_frequency": next_review_frequency,
            "recommended_new_limit": recommended_new_limit,
            "recommended_payment_terms_days": recommended_payment_terms_days,
            "credit_report_snapshot_id": snapshot_id,
        })

        acct.last_review_date = today
        acct.review_risk_level = risk_level
        if next_review_frequency is not None:
            acct.review_frequency = next_review_frequency
        acct.next_review_date = self._compute_next_review_date(today, acct.review_frequency)
        await self._session.flush()

        await self._audit.log(
            action="org_credit.review_submitted",
            entity_type="org_credit_review",
            entity_id=review.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "risk_level": risk_level.value,
                "outcome": outcome.value,
                "recommended_new_limit": str(recommended_new_limit) if recommended_new_limit else None,
                "recommended_payment_terms_days": recommended_payment_terms_days,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=review_notes or f"Review completed (risk={risk_level.value}, outcome={outcome.value})",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_REVIEW_COMPLETED,
            severity="NOTICE",
        )
        logger.info("org_credit.review_submitted", organization_id=organization_id, review_id=review.id, outcome=outcome.value)

    def _compute_next_review_date(self, from_date: date, frequency: OrgCreditReviewFrequency | None) -> date | None:
        if frequency is None:
            return None
        from dateutil.relativedelta import relativedelta
        mapping = {
            OrgCreditReviewFrequency.MONTHLY: relativedelta(months=1),
            OrgCreditReviewFrequency.QUARTERLY: relativedelta(months=3),
            OrgCreditReviewFrequency.SEMI_ANNUAL: relativedelta(months=6),
            OrgCreditReviewFrequency.ANNUAL: relativedelta(years=1),
        }
        return from_date + mapping[frequency]

    async def get_reviews_and_status_payload(self, organization_id: str) -> dict[str, Any]:
        account = await self._account_repo.get_by_org_id(organization_id)
        if account is None:
            await self._org_repo.get_by_id_or_404(organization_id)
            return {"snapshot": None}
        return {"snapshot": _credit_snapshot_from_account(account)}

    async def list_review_history(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._review_repo.list_for_org(organization_id, page=page, size=size)
        return [self.review_history_item_to_dict(r) for r in rows], total

    async def get_review_detail(
        self,
        organization_id: str,
        review_id: str,
    ) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        review = await self._review_repo.get_by_id_and_org_with_reviewer(review_id, organization_id)
        if review is None:
            raise NotFoundError(resource="org_credit_review", id=review_id)
        d = self.review_to_dict(review)
        report: OrgCreditReport | None = None
        if review.credit_report_snapshot_id:
            report = await self._report_repo.get_by_id_and_org(
                review.credit_report_snapshot_id,
                organization_id,
            )
        if report is None:
            report = await self._report_repo.get_by_org_id(organization_id)
        if report is not None:
            d["creditsafe"] = CreditReportResponse.from_report(report).model_dump(mode="json")
        else:
            d["creditsafe"] = None
        return d

    def review_history_item_to_dict(self, review: OrgCreditReview) -> dict[str, Any]:
        reviewer_payload: dict[str, Any] | None = None
        u = review.reviewer
        if u is not None:
            reviewer_payload = {"id": u.id, "first_name": u.first_name, "last_name": u.last_name}
        return {
            "id": review.id,
            "review_date": review.review_date.isoformat(),
            "review_frequency_at_time": review.review_frequency_at_time.value if review.review_frequency_at_time else None,
            "reviewer": reviewer_payload,
            "risk_level": review.risk_level.value,
            "outcome": review.outcome.value,
            "review_notes": review.review_notes,
        }

    def review_to_dict(self, review: OrgCreditReview) -> dict[str, Any]:
        reviewer_payload: dict[str, Any] | None = None
        u = review.reviewer
        if u is not None:
            reviewer_payload = {"id": u.id, "first_name": u.first_name, "last_name": u.last_name}

        return {
            "id": review.id,
            "organization_id": review.organization_id,
            "account_id": review.account_id,
            "reviewer": reviewer_payload,
            "review_date": review.review_date.isoformat(),
            "review_frequency_at_time": review.review_frequency_at_time.value if review.review_frequency_at_time else None,
            "risk_level": review.risk_level.value,
            "outcome": review.outcome.value,
            "review_notes": review.review_notes,
            "next_review_frequency": review.next_review_frequency.value if review.next_review_frequency else None,
            "recommended_new_limit": str(review.recommended_new_limit) if review.recommended_new_limit is not None else None,
            "recommended_payment_terms_days": review.recommended_payment_terms_days,
            "created_at": review.created_at.isoformat(),
            "updated_at": review.updated_at.isoformat(),
        }

    def account_to_public_dict(self, account: OrgCreditAccount) -> dict[str, Any]:
        return self._credit_svc.account_to_public_dict(account)
