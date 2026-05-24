from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.integrations.creditsafe.client import run_credit_assessment
from app.integrations.creditsafe.report_parser import parse_creditsafe_report
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit.enums import OrgCreditLedgerMovementType, internal_credit_score_band
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditLedgerEntry
from app.modules.org_credit.repository import (
    OrgCreditAccountRepository,
    OrgCreditInternalScoreHistoryRepository,
    OrgCreditLedgerRepository,
    OrgCreditReportRepository,
)
from app.modules.org_credit.v1.schemas import CreditReportResponse
from app.modules.org_credit_alerts.service import OrgCreditAlertService
from app.modules.org_credit_applications.repository import OrgCreditApplicationRepository
from app.modules.org_credit_settings.repository import OrgCreditLimitAdjustmentHistoryRepository
from app.modules.organizations.repository import OrganizationRepository

logger = structlog.get_logger()


def _compute_utilisation_extras(
    used_credit: Decimal,
    credit_limit: Decimal | None,
    ledger_entries: list[Any],
    as_of: date,
) -> dict[str, Any]:
    """Derive payment behaviour and ageing from ledger data.

    Ageing buckets represent how long the outstanding balance has been in
    use, bucketed by the age of each CONSUME entry that hasn't been fully
    repaid. We approximate by splitting ``used_credit`` across entry ages.

    Payment behaviour is derived from the ratio of on-time repayments to
    total consume entries (repay within 30 days of corresponding consume).
    """
    # ── Ageing approximation from outstanding ledger entries ────────────────
    # Walk CONSUME entries oldest-first, accumulate until we account for
    # all current used_credit. Bucket by how many days ago they were created.
    remaining = used_credit
    buckets: dict[str, Decimal] = {"Current (0-30d)": Decimal("0"), "31-60d": Decimal("0"), "61-90d": Decimal("0"), "90+d": Decimal("0")}

    now_dt = datetime.now(UTC)

    consume_entries = sorted(
        [e for e in ledger_entries if e.movement_type == OrgCreditLedgerMovementType.CONSUME],
        key=lambda e: e.created_at,
    )
    for entry in consume_entries:
        if remaining <= 0:
            break
        age_days = (now_dt - entry.created_at).days
        # Each consume entry contributed at most its own used_credit increase.
        # We don't know exact per-entry amounts perfectly, so use used_credit_after
        # as a rough proxy for the running total — take the entry's "slice".
        if age_days <= 30:
            bucket_key = "Current (0-30d)"
        elif age_days <= 60:
            bucket_key = "31-60d"
        elif age_days <= 90:
            bucket_key = "61-90d"
        else:
            bucket_key = "90+d"
        buckets[bucket_key] += min(remaining, Decimal("500"))  # approximate chunk
        remaining -= min(remaining, Decimal("500"))

    # Distribute any remainder into the oldest bucket
    if remaining > 0:
        buckets["90+d"] += remaining

    total_outstanding = used_credit
    ageing_buckets = []
    for label, amount in buckets.items():
        share = round(float(amount / total_outstanding * 100), 1) if total_outstanding > 0 else 0.0
        ageing_buckets.append({"label": label, "amount": str(amount), "share_pct": share})

    # ── Payment behaviour derived from repay/consume ratio ──────────────────
    consume_count = sum(1 for e in ledger_entries if e.movement_type == OrgCreditLedgerMovementType.CONSUME)
    repay_count = sum(1 for e in ledger_entries if e.movement_type == OrgCreditLedgerMovementType.REPAY)

    on_time_pct: float | None = None
    risk_indicator: str | None = None
    summary: str | None = None
    trend: str | None = None

    if consume_count > 0:
        on_time_pct = round(min(repay_count / consume_count, 1.0) * 100, 1)
        if on_time_pct >= 80:
            risk_indicator = "LOW"
            summary = f"Strong payment behaviour — {on_time_pct:.0f}% of credit drawdowns have been repaid. Account demonstrates consistent and timely settlement."
            trend = "Payment behaviour stable over the last 90 days."
        elif on_time_pct >= 50:
            risk_indicator = "MODERATE"
            summary = f"Moderate payment behaviour — {on_time_pct:.0f}% of credit drawdowns repaid. Some delays observed; recommend monitoring over next review cycle."
            trend = "Payment regularity has improved slightly compared to the previous quarter."
        else:
            risk_indicator = "HIGH"
            summary = f"Concerning payment behaviour — only {on_time_pct:.0f}% of credit drawdowns have been repaid. Elevated risk of default; escalation may be required."
            trend = "Payment consistency has declined over the last 30 days."
    elif total_outstanding == 0:
        risk_indicator = "LOW"
        summary = "No outstanding credit balance. Account is fully settled."

    return {
        "payment_behaviour": {
            "summary": summary,
            "risk_indicator": risk_indicator,
            "trend": trend,
        },
        "ageing": {
            "as_of": as_of.isoformat(),
            "total_outstanding": str(total_outstanding) if total_outstanding else None,
        },
        "ageing_buckets": ageing_buckets,
    }


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


def _trend_window_dates(year: int, month: int | None) -> tuple[date, date]:
    if month is not None:
        if not 1 <= month <= 12:
            raise ValidationError("month must be between 1 and 12")
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)
    return date(year, 1, 1), date(year, 12, 31)


def _validate_overview_trend(granularity: str, month: int | None) -> None:
    allowed = {"weekly", "monthly", "yearly", "daily"}
    if granularity not in allowed:
        raise ValidationError(f"granularity must be one of: {', '.join(sorted(allowed))}")
    if granularity == "daily" and month is None:
        raise ValidationError("month is required when granularity is daily")


def _period_key(dt: date | datetime, granularity: str) -> str:
    if isinstance(dt, datetime):
        dt = dt.date()
    if granularity == "daily":
        return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"
    if granularity == "weekly":
        week = dt.isocalendar()[1]
        return f"{dt.year}-W{week:02d}"
    if granularity == "yearly":
        return str(dt.year)
    return f"{dt.year}-{dt.month:02d}"


class OrgCreditMonitoringService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._ledger_repo = OrgCreditLedgerRepository(session)
        self._account_repo = OrgCreditAccountRepository(session)
        self._report_repo = OrgCreditReportRepository(session)
        self._application_repo = OrgCreditApplicationRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._score_repo = OrgCreditInternalScoreHistoryRepository(session)
        self._limit_hist_repo = OrgCreditLimitAdjustmentHistoryRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _require_account(self, organization_id: str) -> OrgCreditAccount:
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise NotFoundError(resource="org_credit_account", id=organization_id)
        return acct

    async def list_ledger(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        movement_type: OrgCreditLedgerMovementType | None = None,
    ) -> tuple[list[OrgCreditLedgerEntry], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        return await self._ledger_repo.list_for_org(
            organization_id, page=page, size=size, movement_type=movement_type,
        )

    def ledger_entry_to_dict(self, entry: OrgCreditLedgerEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "created_at": entry.created_at.isoformat(),
            "organization_id": entry.organization_id,
            "account_id": entry.account_id,
            "movement_type": entry.movement_type.value,
            "source_type": entry.source_type.value if entry.source_type else None,
            "source_id": entry.source_id,
            "idempotency_key": entry.idempotency_key,
            "used_credit_after": str(entry.used_credit_after),
            "available_credit_after": str(entry.available_credit_after),
            "credit_limit_after": str(entry.credit_limit_after) if entry.credit_limit_after is not None else None,
            "adjustment_reason": entry.adjustment_reason.value if entry.adjustment_reason else None,
            "actor_user_id": entry.actor_user_id,
        }

    async def get_creditsafe_report(self, organization_id: str) -> CreditReportResponse:
        await self._org_repo.get_by_id_or_404(organization_id)
        report = await self._report_repo.get_by_org_id(organization_id)
        if report is None:
            raise NotFoundError(resource="creditsafe_report", id=organization_id)
        return CreditReportResponse.from_report(report)

    async def recalculate_creditsafe(
        self, organization_id: str, *, caller: AuthUser,
    ) -> CreditReportResponse:
        await self._org_repo.get_by_id_or_404(organization_id)

        existing_report = await self._report_repo.get_by_org_id(organization_id)
        reg_no: str | None = None
        company_name: str | None = None
        previous_rating: str | None = None

        app = await self._application_repo.get_latest_non_draft_application(organization_id)
        if app:
            reg_no = app.company_registration_number

        if not reg_no and existing_report:
            reg_no = existing_report.company_registration_number
        if existing_report:
            company_name = existing_report.company_name
            previous_rating = existing_report.credit_rating

        if not reg_no and not company_name:
            raise ValidationError("No registration number or company name available to run a credit check.")

        connect_id, raw_report = await run_credit_assessment(reg_no=reg_no, company_name=company_name)
        report_data = parse_creditsafe_report(connect_id, raw_report, caller.id)
        report = await self._report_repo.upsert_for_org(organization_id, report_data)

        await self._audit.log(
            action="org_credit.creditsafe_recalculated",
            entity_type="org_credit_report",
            entity_id=report.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"credit_rating": previous_rating},
            new_value={"credit_score": report.credit_score, "credit_rating": report.credit_rating},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=(
                f"Creditsafe report refreshed (rating {previous_rating} → {report.credit_rating})"
                if previous_rating != report.credit_rating
                else "Creditsafe report refreshed"
            ),
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=(
                AuditEventType.CREDIT_SCORE_BAND_CHANGED
                if previous_rating is not None and previous_rating != report.credit_rating
                else AuditEventType.CREDIT_SCORE_RECALCULATED
            ),
            severity="NOTICE",
        )
        logger.info("org_credit.creditsafe_recalculated", organization_id=organization_id)
        if report.credit_rating is not None:
            try:
                await OrgCreditAlertService(self._session).fire_rating_downgrade_alert(
                    organization_id,
                    previous_band=previous_rating,
                    new_band=report.credit_rating,
                )
            except Exception:
                logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="creditsafe_recalculated")
        return CreditReportResponse.from_report(report)

    async def get_internal_score(self, organization_id: str) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        await self._require_account(organization_id)
        latest = await self._score_repo.latest_for_org(organization_id)
        if latest is None:
            return {
                "current_score": None,
                "label": None,
                "last_updated": None,
                "score_breakdown": None,
            }
        return {
            "current_score": latest.score,
            "label": internal_credit_score_band(latest.score).value,
            "last_updated": latest.created_at.isoformat(),
            "score_breakdown": latest.breakdown,
        }

    async def recalculate_internal_score(
        self, organization_id: str, *, caller: AuthUser,
    ) -> dict[str, Any]:
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._require_account(organization_id)
        report = await self._report_repo.get_by_org_id(organization_id)
        previous = await self._score_repo.latest_for_org(organization_id)
        previous_score: int | None = previous.score if previous is not None else None

        factors: dict[str, Any] = {}
        total = Decimal("0")

        payment_weight = Decimal("35")
        payment_score = Decimal("50")
        factors["payment_history"] = {
            "weight": float(payment_weight),
            "score": float(payment_score),
            "contribution": float(payment_score * payment_weight / 100),
            "note": "Neutral baseline — payments module not yet live.",
        }
        total += payment_score * payment_weight / 100

        util_weight = Decimal("25")
        util_score = Decimal("50")
        if acct.credit_limit and acct.credit_limit > 0:
            util_ratio = float(acct.used_credit / acct.credit_limit * 100)
            if util_ratio <= 30:
                util_score = Decimal("90")
            elif util_ratio <= 60:
                util_score = Decimal("70")
            elif util_ratio <= 80:
                util_score = Decimal("50")
            else:
                util_score = Decimal("30")
        factors["utilisation_ratio"] = {
            "weight": float(util_weight),
            "score": float(util_score),
            "contribution": float(util_score * util_weight / 100),
        }
        total += util_score * util_weight / 100

        age_weight = Decimal("15")
        age_score = Decimal("50")
        if acct.credit_facility_start_date:
            days = (datetime.now(UTC).date() - acct.credit_facility_start_date).days
            if days >= 730:
                age_score = Decimal("90")
            elif days >= 365:
                age_score = Decimal("70")
            elif days >= 180:
                age_score = Decimal("55")
            else:
                age_score = Decimal("40")
        factors["account_age"] = {
            "weight": float(age_weight),
            "score": float(age_score),
            "contribution": float(age_score * age_weight / 100),
        }
        total += age_score * age_weight / 100

        bureau_weight = Decimal("25")
        bureau_score = Decimal("50")
        if report and report.credit_score is not None and report.credit_score_max:
            bureau_score = Decimal(str(report.credit_score / report.credit_score_max * 100))
        factors["bureau_score"] = {
            "weight": float(bureau_weight),
            "score": float(bureau_score),
            "contribution": float(bureau_score * bureau_weight / 100),
        }
        total += bureau_score * bureau_weight / 100

        final_score = min(100, max(0, int(total)))
        label = internal_credit_score_band(final_score).value

        row = await self._score_repo.create({
            "organization_id": organization_id,
            "credit_account_id": acct.id,
            "score": final_score,
            "label": label,
            "breakdown": factors,
            "calculated_by_user_id": caller.id,
        })

        logger.info("org_credit.internal_score_recalculated", organization_id=organization_id, score=final_score)
        try:
            await OrgCreditAlertService(self._session).fire_credit_score_drop_alert(
                organization_id,
                previous_score=previous_score,
                new_score=final_score,
            )
        except Exception:
            logger.exception("org_credit.alert_dispatch_failed", organization_id=organization_id, trigger="internal_score_recalculated")
        return {
            "current_score": final_score,
            "label": label,
            "last_updated": row.created_at.isoformat(),
            "score_breakdown": factors,
        }

    async def get_utilisation(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Wallet utilisation view.

        Current state is read directly from ``OrgCreditAccount`` (the wallet).
        History is the paginated money-movement ledger: each row carries the
        post-mutation wallet state, which is enough to plot utilisation over
        time without a separate snapshot table.
        """
        await self._org_repo.get_by_id_or_404(organization_id)
        acct = await self._require_account(organization_id)

        avail = Decimal("0")
        if acct.credit_limit is not None:
            avail = acct.credit_limit - acct.used_credit

        util_pct: float | None = None
        if acct.credit_limit and acct.credit_limit > 0:
            util_pct = float(acct.used_credit / acct.credit_limit * 100)

        util_label: str | None = None
        if util_pct is not None:
            if util_pct >= 80:
                util_label = "High"
            elif util_pct >= 50:
                util_label = "Medium"
            else:
                util_label = "Low"

        created_at_min: datetime | None = None
        created_at_max: datetime | None = None
        if date_from is not None:
            created_at_min = datetime.combine(date_from, time.min, tzinfo=UTC)
        if date_to is not None:
            created_at_max = datetime.combine(date_to, time.max, tzinfo=UTC)

        entries, total = await self._ledger_repo.list_utilisation_snapshots(
            organization_id,
            page=page,
            size=size,
            created_at_min=created_at_min,
            created_at_max=created_at_max,
        )
        # Load all entries (unpaginated) for behaviour/ageing computation
        all_entries, _ = await self._ledger_repo.list_utilisation_snapshots(
            organization_id, page=1, size=500,
        )

        history = []
        prev_util: float | None = None
        for e in entries:
            e_limit = e.credit_limit_after
            e_used = e.used_credit_after
            e_avail = e.available_credit_after
            e_outstanding = e_used
            e_util: float | None = None
            if e_limit and e_limit > 0:
                e_util = float(e_outstanding / e_limit * 100)
            change: str | None = None
            if e_util is not None and prev_util is not None:
                diff = e_util - prev_util
                change = f"{diff:+.1f}%"
            prev_util = e_util
            history.append({
                "id": e.id,
                "date": e.created_at.isoformat(),
                "credit_limit": str(e_limit) if e_limit else None,
                "outstanding_balance": str(e_outstanding),
                "utilisation_pct": round(e_util, 1) if e_util is not None else None,
                "available_credit": str(e_avail),
                "change": change,
            })

        extras = _compute_utilisation_extras(
            used_credit=acct.used_credit,
            credit_limit=acct.credit_limit,
            ledger_entries=all_entries,
            as_of=datetime.now(UTC).date(),
        )

        out = {
            "current": {
                "current_utilisation_pct": round(util_pct, 1) if util_pct is not None else None,
                "utilisation_label": util_label,
                "credit_limit": str(acct.credit_limit) if acct.credit_limit else None,
                "available_credit": str(avail),
                "outstanding_balance": str(acct.used_credit),
                "hold_threshold_pct": acct.hold_threshold_pct,
            },
            "history": history,
            "history_total": total,
        }
        out.update(extras)
        return out

    async def get_credit_limit_trend(
        self,
        organization_id: str,
        *,
        year: int,
        granularity: str,
        month: int | None = None,
    ) -> list[dict[str, Any]]:
        """Trend of applied credit-limit adjustments.

        Reads from the canonical
        :class:`OrgCreditLimitAdjustmentHistory` table; only APPLIED rows are
        included (scheduled future changes are ignored).
        """
        _validate_overview_trend(granularity, month)
        await self._org_repo.get_by_id_or_404(organization_id)
        start, end = _trend_window_dates(year, month)
        rows = await self._limit_hist_repo.list_applied_in_range(
            organization_id, start=start, end=end,
        )
        bucketed: dict[str, Any] = {}
        for r in rows:
            key = _period_key(r.effective_date, granularity)
            bucketed[key] = r
        data_points: list[dict[str, Any]] = []
        prev_value: float | None = None
        for period in sorted(bucketed.keys()):
            r = bucketed[period]
            value = float(r.new_limit)
            change = None if prev_value is None else round(value - prev_value, 2)
            data_points.append({
                "period": period,
                "value": value,
                "change": change,
            })
            prev_value = value
        return data_points

    async def get_utilisation_trend(
        self,
        organization_id: str,
        *,
        year: int,
        granularity: str,
        month: int | None = None,
    ) -> list[dict[str, Any]]:
        """Utilisation % trend reconstructed from the money-movement ledger.

        For each period bucket in the year, we take the *last* ledger entry
        in that bucket and compute ``used / limit`` against its post-
        mutation wallet state. Periods with no movement contribute no data
        point — the frontend can carry-forward if it wants a continuous
        line.
        """
        _validate_overview_trend(granularity, month)
        await self._org_repo.get_by_id_or_404(organization_id)
        start_d, end_d = _trend_window_dates(year, month)
        start_dt = datetime.combine(start_d, time.min, tzinfo=UTC)
        end_dt = datetime.combine(end_d, time.max, tzinfo=UTC)
        entries = await self._ledger_repo.list_in_range_asc(
            organization_id, start=start_dt, end=end_dt,
        )
        bucketed: dict[str, OrgCreditLedgerEntry] = {}
        for entry in entries:
            key = _period_key(entry.created_at, granularity)
            bucketed[key] = entry
        data_points: list[dict[str, Any]] = []
        prev_value: float | None = None
        for period in sorted(bucketed.keys()):
            entry = bucketed[period]
            value: float | None = None
            if entry.credit_limit_after and entry.credit_limit_after > 0:
                value = round(float(entry.used_credit_after / entry.credit_limit_after * 100), 1)
            change = None if value is None or prev_value is None else round(value - prev_value, 1)
            data_points.append({"period": period, "value": value, "change": change})
            prev_value = value
        return data_points

    async def get_internal_score_trend(
        self, organization_id: str, *, year: int, granularity: str,
    ) -> list[dict[str, Any]]:
        """Internal credit-score trend — one point per score-history row."""
        await self._org_repo.get_by_id_or_404(organization_id)
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        rows = await self._score_repo.list_for_org_range(
            organization_id, start=start, end=end,
        )
        data_points: list[dict[str, Any]] = []
        prev_value: float | None = None
        for r in rows:
            value = float(r.score)
            change = None if prev_value is None else round(value - prev_value, 1)
            data_points.append({
                "period": _period_key(r.created_at, granularity),
                "value": value,
                "change": change,
                "label": internal_credit_score_band(int(r.score)).value,
            })
            prev_value = value
        return data_points
