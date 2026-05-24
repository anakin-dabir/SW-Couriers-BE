"""Seed rich credit demo data for org 9491bf02-e7bf-413b-98a7-270999f90954.

Populates:
  - OrgCreditAccount          (updates existing: limit, terms, review config)
  - OrgCreditLedgerEntry      (10 consume + 6 repay entries for utilisation history)
  - OrgCreditStatusHistory    (5 status transitions)
  - OrgCreditInternalScoreHistory (12 monthly score snapshots)
  - OrgCreditAlert            (3 active + 3 historical alerts)
  - OrgCreditAlertConfig      (all alert types enabled)
  - OrgCreditReview           (3 past reviews)
  - OrgCreditApplication      (updates existing to APPROVED)
  - OrgCreditReport           (creditsafe data)

Run:
    cd /home/dev/DEV_WORK/SW-clone/SW-Couriers-BE
    poetry run python scripts/seed_credit_overview_demo.py
    poetry run python scripts/seed_credit_overview_demo.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.models  # noqa: F401 — registers all ORM classes
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import get_async_session
from app.modules.org_credit.enums import (
    OrgCreditAdjustmentReason,
    OrgCreditLedgerMovementType,
    OrgCreditLedgerSourceType,
    OrgCreditReviewFrequency,
    OrgCreditAccountStatus,
    internal_credit_score_band,
)
from app.modules.org_credit.models import (
    OrgCreditAccount,
    OrgCreditInternalScoreHistory,
    OrgCreditLedgerEntry,
    OrgCreditStatusHistory,
)
from app.modules.org_credit_alerts.enums import (
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertSeverity,
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.org_credit_alerts.models import OrgCreditAlert, OrgCreditAlertConfig
from app.modules.org_credit_reviews.enums import (
    CreditReviewOutcome,
    CreditReviewReminderPeriod,
    CreditReviewRiskLevel,
)
from app.modules.org_credit_reviews.models import OrgCreditReview

ORG_ID = "9491bf02-e7bf-413b-98a7-270999f90954"
ACCOUNT_ID = "0e81234c-65d3-4058-805b-10a787d6ab3f"
ADMIN_USER_ID = "2fa1e27b-e72d-4148-a78a-046e4327cb07"

NOW = datetime.now(UTC)
TODAY = NOW.date()


def uid() -> str:
    return str(uuid.uuid4())


def months_ago(n: int) -> datetime:
    return NOW - timedelta(days=n * 30)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


async def seed(session, dry_run: bool) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding credit demo data for org {ORG_ID}...")

    # ── 1. Update OrgCreditAccount ──────────────────────────────────────────
    print("  Updating credit account...")
    await session.execute(
        update(OrgCreditAccount)
        .where(OrgCreditAccount.id == ACCOUNT_ID)
        .values(
            credit_limit=Decimal("50000.00"),
            credit_limit_updated_at=days_ago(45),
            used_credit=Decimal("18500.00"),
            payment_terms_days=30,
            payment_terms_updated_at=days_ago(90),
            review_frequency=OrgCreditReviewFrequency.QUARTERLY,
            next_review_date=TODAY + timedelta(days=22),
            last_review_date=TODAY - timedelta(days=68),
            review_reminder_period=CreditReviewReminderPeriod.SEVEN_DAYS,
            assigned_reviewer_user_id=ADMIN_USER_ID,
            review_risk_level=CreditReviewRiskLevel.MEDIUM,
            hold_threshold_pct=85,
            credit_facility_start_date=date(2025, 1, 1),
            credit_facility_end_date=date(2026, 12, 31),
            status=OrgCreditAccountStatus.ACTIVE,
            last_status_change_at=days_ago(14),
            action_by_user_id=ADMIN_USER_ID,
        )
    )

    # ── 2. OrgCreditStatusHistory ───────────────────────────────────────────
    print("  Creating status history...")
    # Clear existing first
    await session.execute(
        delete(OrgCreditStatusHistory).where(
            OrgCreditStatusHistory.organization_id == ORG_ID
        )
    )
    status_rows = [
        OrgCreditStatusHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            from_status=None, to_status=OrgCreditAccountStatus.ACTIVE,
            reason="Credit account created and activated.",
            actor_user_id=ADMIN_USER_ID,
            created_at=months_ago(6),
        ),
        OrgCreditStatusHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            from_status=OrgCreditAccountStatus.ACTIVE, to_status=OrgCreditAccountStatus.ON_HOLD,
            reason="Overdue invoices exceeding 30 days. Risk concern raised.",
            actor_user_id=ADMIN_USER_ID,
            created_at=months_ago(4),
        ),
        OrgCreditStatusHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            from_status=OrgCreditAccountStatus.ON_HOLD, to_status=OrgCreditAccountStatus.ACTIVE,
            reason="Invoices settled. Account reinstated by admin.",
            actor_user_id=ADMIN_USER_ID,
            created_at=months_ago(3),
        ),
        OrgCreditStatusHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            from_status=OrgCreditAccountStatus.ACTIVE, to_status=OrgCreditAccountStatus.SUSPENDED,
            reason="Repeated late payments. Escalated to suspension.",
            actor_user_id=ADMIN_USER_ID,
            created_at=months_ago(2),
        ),
        OrgCreditStatusHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            from_status=OrgCreditAccountStatus.SUSPENDED, to_status=OrgCreditAccountStatus.ACTIVE,
            reason="Payment plan agreed. Account reactivated.",
            actor_user_id=ADMIN_USER_ID,
            created_at=days_ago(14),
        ),
    ]
    session.add_all(status_rows)

    # ── 3. OrgCreditLedgerEntry ─────────────────────────────────────────────
    print("  Creating ledger entries...")
    await session.execute(
        delete(OrgCreditLedgerEntry).where(
            OrgCreditLedgerEntry.organization_id == ORG_ID
        )
    )
    ledger_rows = []
    running_used = Decimal("0.00")
    credit_limit = Decimal("50000.00")

    consume_amounts = [3200, 4500, 2800, 5100, 1900, 4200, 3800, 2500, 6000, 3500]
    repay_amounts   = [3200, 4500, 2800, 5100, 1900, 4200]

    for i, amt in enumerate(consume_amounts):
        running_used += Decimal(str(amt))
        ledger_rows.append(OrgCreditLedgerEntry(
            id=uid(), organization_id=ORG_ID, account_id=ACCOUNT_ID,
            movement_type=OrgCreditLedgerMovementType.CONSUME,
            source_type=OrgCreditLedgerSourceType.ORDER,
            source_id=uid(),
            used_credit_after=running_used,
            available_credit_after=credit_limit - running_used,
            credit_limit_after=credit_limit,
            actor_user_id=None,
            created_at=days_ago(90 - i * 8),
        ))

    for i, amt in enumerate(repay_amounts):
        running_used -= Decimal(str(amt))
        ledger_rows.append(OrgCreditLedgerEntry(
            id=uid(), organization_id=ORG_ID, account_id=ACCOUNT_ID,
            movement_type=OrgCreditLedgerMovementType.REPAY,
            source_type=OrgCreditLedgerSourceType.PAYMENT,
            source_id=uid(),
            used_credit_after=max(running_used, Decimal("0")),
            available_credit_after=credit_limit - max(running_used, Decimal("0")),
            credit_limit_after=credit_limit,
            actor_user_id=ADMIN_USER_ID,
            created_at=days_ago(85 - i * 9),
        ))

    # Final state: used = 18500
    running_used = Decimal("18500.00")
    ledger_rows.append(OrgCreditLedgerEntry(
        id=uid(), organization_id=ORG_ID, account_id=ACCOUNT_ID,
        movement_type=OrgCreditLedgerMovementType.CONSUME,
        source_type=OrgCreditLedgerSourceType.ORDER,
        source_id=uid(),
        used_credit_after=running_used,
        available_credit_after=credit_limit - running_used,
        credit_limit_after=credit_limit,
        actor_user_id=None,
        created_at=days_ago(3),
    ))
    session.add_all(ledger_rows)

    # ── 4. OrgCreditInternalScoreHistory ────────────────────────────────────
    print("  Creating internal score history...")
    await session.execute(
        delete(OrgCreditInternalScoreHistory).where(
            OrgCreditInternalScoreHistory.organization_id == ORG_ID
        )
    )
    scores = [58, 61, 55, 49, 52, 60, 63, 57, 65, 68, 72, 70]
    score_rows = []
    for i, score in enumerate(scores):
        band = internal_credit_score_band(score)
        score_rows.append(OrgCreditInternalScoreHistory(
            id=uid(), organization_id=ORG_ID, credit_account_id=ACCOUNT_ID,
            score=score, label=band.value,
            breakdown={"payment_history": score - 8, "utilisation": score + 5, "age": 30},
            calculated_by_user_id=ADMIN_USER_ID,
            created_at=months_ago(11 - i),
        ))
    session.add_all(score_rows)

    # ── 5. OrgCreditAlertConfig ─────────────────────────────────────────────
    print("  Creating alert configs...")
    for alert_type in CreditAlertType:
        await session.execute(
            text("""
                INSERT INTO org_credit_alert_configs
                    (id, organization_id, alert_type, enabled,
                     warning_threshold_pct, critical_threshold_pct,
                     cooldown_period, delivery_channel, auto_acknowledge,
                     created_at, updated_at)
                VALUES
                    (:id, :org_id, :alert_type, true,
                     :warn, :crit,
                     :cooldown, :channel, false,
                     NOW(), NOW())
                ON CONFLICT (organization_id, alert_type) DO UPDATE
                    SET enabled = true
            """),
            {
                "id": uid(), "org_id": ORG_ID, "alert_type": alert_type.value,
                "warn": "75.00", "crit": "90.00",
                "cooldown": CreditAlertCooldownPeriod.TWENTY_FOUR_HOURS.value,
                "channel": CreditAlertDeliveryChannel.BOTH.value,
            }
        )

    # ── 6. OrgCreditAlert ───────────────────────────────────────────────────
    print("  Creating alerts...")
    await session.execute(
        delete(OrgCreditAlert).where(OrgCreditAlert.organization_id == ORG_ID)
    )
    alert_rows = [
        # Active alerts
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.CREDIT_UTILISATION_MONITORING,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.ACTIVE,
            title="Credit Utilisation at 37%",
            summary="Credit utilisation has reached 37% (£18,500 of £50,000). Monitor closely as it approaches the 85% hold threshold.",
            context={"utilisation_pct": 37, "used": 18500, "limit": 50000},
            triggered_at=days_ago(2),
        ),
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.ACTIVE,
            title="Quarterly Review Due in 22 Days",
            summary="Scheduled credit review is due on " + (TODAY + timedelta(days=22)).strftime("%d %b %Y") + ". Assign reviewer and begin assessment.",
            context={"days_remaining": 22},
            triggered_at=days_ago(8),
        ),
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.LATE_PAYMENT_BEHAVIOUR,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.SNOOZED,
            title="Late Payment Pattern Detected",
            summary="3 invoices in the last 60 days were paid beyond agreed Net 30 terms. Average delay: 12 days.",
            context={"late_count": 3, "avg_delay_days": 12},
            triggered_at=days_ago(15),
            snoozed_until=days_ago(-1),  # snoozed until tomorrow
        ),
        # Historical (acknowledged/resolved)
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.ACCOUNT_ON_HOLD,
            severity=CreditAlertSeverity.CRITICAL,
            status=CreditAlertStatus.RESOLVED,
            title="Credit Account Placed On Hold",
            summary="Account placed on hold due to overdue invoices. No new bookings permitted until resolved.",
            context={},
            triggered_at=months_ago(4),
            resolved_at=months_ago(3),
        ),
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.ACCOUNT_SUSPENDED,
            severity=CreditAlertSeverity.CRITICAL,
            status=CreditAlertStatus.RESOLVED,
            title="Credit Account Suspended",
            summary="Account suspended following repeated late payments. Credit facility frozen.",
            context={},
            triggered_at=months_ago(2),
            resolved_at=days_ago(14),
        ),
        OrgCreditAlert(
            id=uid(), organization_id=ORG_ID,
            alert_type=CreditAlertType.CREDIT_SCORE_DROP,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.ACKNOWLEDGED,
            title="Internal Credit Score Dropped to 49",
            summary="Internal credit score fell from 60 to 49 (POOR) this month. Review payment history.",
            context={"score": 49, "prev_score": 60, "band": "POOR"},
            triggered_at=months_ago(5),
            acknowledged_at=months_ago(5) + timedelta(hours=2),
            acknowledged_by_user_id=ADMIN_USER_ID,
            resolution_notes="Investigated. Decline linked to suspension period. Monitoring.",
        ),
    ]
    session.add_all(alert_rows)

    # ── 7. OrgCreditReview ──────────────────────────────────────────────────
    print("  Creating credit reviews...")
    # Keep any existing, just add new ones if not already present
    existing_reviews = (await session.execute(
        select(OrgCreditReview.id).where(OrgCreditReview.organization_id == ORG_ID)
    )).scalars().all()

    if len(existing_reviews) < 3:
        review_rows = [
            OrgCreditReview(
                id=uid(), organization_id=ORG_ID, account_id=ACCOUNT_ID,
                reviewer_user_id=ADMIN_USER_ID,
                review_date=date(2024, 11, 15),
                review_frequency_at_time=OrgCreditReviewFrequency.QUARTERLY,
                risk_level=CreditReviewRiskLevel.LOW,
                outcome=CreditReviewOutcome.MAINTAIN_CURRENT_TERMS,
                review_notes="Account in good standing. Payment behaviour consistent. No changes recommended.",
                next_review_frequency=OrgCreditReviewFrequency.QUARTERLY,
                recommended_new_limit=None,
                recommended_payment_terms_days=None,
            ),
            OrgCreditReview(
                id=uid(), organization_id=ORG_ID, account_id=ACCOUNT_ID,
                reviewer_user_id=ADMIN_USER_ID,
                review_date=date(2025, 2, 10),
                review_frequency_at_time=OrgCreditReviewFrequency.QUARTERLY,
                risk_level=CreditReviewRiskLevel.MEDIUM,
                outcome=CreditReviewOutcome.INCREASE_LIMIT,
                review_notes="Business growing. Order volumes up 40% YoY. Credit limit increased from £30k to £50k.",
                next_review_frequency=OrgCreditReviewFrequency.QUARTERLY,
                recommended_new_limit=Decimal("50000.00"),
                recommended_payment_terms_days=30,
            ),
        ]
        for r in review_rows:
            r.created_at = datetime.now(UTC)
            r.updated_at = datetime.now(UTC)
        session.add_all(review_rows)

    # ── 8. OrgCreditReport (creditsafe data on the account) ─────────────────
    print("  Creating/updating credit report...")
    existing_report = (await session.execute(
        text("SELECT id FROM org_credit_reports WHERE organization_id = :org_id"),
        {"org_id": ORG_ID}
    )).fetchone()

    report_data = {
        "org_id": ORG_ID,
        "connect_id": "GB-0-12345678",
        "credit_score": 72,
        "credit_score_max": 100,
        "credit_rating": "B",
        "credit_rating_description": "Good credit limit",
        "recommended_credit_limit": "50000.00",
        "recommended_credit_limit_currency": "GBP",
        "previous_credit_rating": "C",
        "previous_rating_changed_at": date(2025, 6, 15),
        "risk_band": "Moderate Risk",
        "probability_of_default_12m": "1.80",
        "assessment_commentary": "The company demonstrates stable financial performance with low risk indicators. Suitable for moderate credit exposure.",
        "company_name": "Trading Name Star Ltd",
        "legal_entity_name": "Trading Name Star Group Ltd",
        "company_status": "Active",
        "company_registration_number": "12345678",
        "date_of_incorporation": date(2018, 4, 14),
        "country": "GB",
        "latest_turnover": "2400000.00",
        "latest_turnover_currency": "GBP",
        "registered_address": "45 Kensington High Street, London, W8 5ED, United Kingdom",
        "industry_code": "47910",
        "industry_description": "Retail sale via mail order or Internet",
        "vat_number": "GB123456789",
        "contact_number": "+44 20 7946 0958",
        "last_checked_at": days_ago(30),
        "checked_by_user_id": ADMIN_USER_ID,
        "directors": json.dumps([
            {"name": "James Carter", "role": "Director", "appointed_on": "2018-04-14", "date_of_birth": "1978-03-22", "flags": []},
            {"name": "Sarah Mitchell", "role": "Director", "appointed_on": "2020-09-01", "date_of_birth": "1985-07-15", "flags": []},
        ]),
        "risk_indicators": json.dumps([
            {"key": "insolvency", "label": "Insolvency", "severity": "OK", "description": "No active insolvency proceedings", "details": []},
            {"key": "ccj", "label": "County Court Judgements", "severity": "OK", "description": "No County Court Judgements (CCJs) recorded", "details": []},
            {"key": "director_linkages", "label": "Director linkages", "severity": "WARNING", "description": "1 historical director linkage to dissolved entity", "details": []},
            {"key": "bankruptcy", "label": "Bankruptcy filings", "severity": "OK", "description": "No bankruptcy filings", "details": []},
        ]),
        "payment_behaviour_description": "The company generally pays suppliers within agreed terms. Minor delays observed in 2 instances over the past 12 months, but no severe delinquencies.",
    }

    if existing_report:
        await session.execute(
            text("""
                UPDATE org_credit_reports SET
                    connect_id = :connect_id, credit_score = :credit_score,
                    credit_score_max = :credit_score_max, credit_rating = :credit_rating,
                    credit_rating_description = :credit_rating_description,
                    recommended_credit_limit = :recommended_credit_limit,
                    recommended_credit_limit_currency = :recommended_credit_limit_currency,
                    previous_credit_rating = :previous_credit_rating,
                    previous_rating_changed_at = :previous_rating_changed_at,
                    risk_band = :risk_band,
                    probability_of_default_12m = :probability_of_default_12m,
                    assessment_commentary = :assessment_commentary,
                    company_name = :company_name, legal_entity_name = :legal_entity_name,
                    company_status = :company_status,
                    company_registration_number = :company_registration_number,
                    date_of_incorporation = :date_of_incorporation, country = :country,
                    latest_turnover = :latest_turnover,
                    latest_turnover_currency = :latest_turnover_currency,
                    registered_address = :registered_address,
                    industry_code = :industry_code, industry_description = :industry_description,
                    vat_number = :vat_number, contact_number = :contact_number,
                    last_checked_at = :last_checked_at,
                    checked_by_user_id = :checked_by_user_id,
                    directors = CAST(:directors AS jsonb),
                    risk_indicators = CAST(:risk_indicators AS jsonb),
                    payment_behaviour_description = :payment_behaviour_description,
                    updated_at = NOW()
                WHERE organization_id = :org_id
            """),
            report_data
        )
    else:
        await session.execute(
            text("""
                INSERT INTO org_credit_reports (
                    id, organization_id, connect_id, credit_score, credit_score_max,
                    credit_rating, credit_rating_description, recommended_credit_limit,
                    recommended_credit_limit_currency, previous_credit_rating,
                    previous_rating_changed_at, risk_band, probability_of_default_12m,
                    assessment_commentary, company_name, legal_entity_name,
                    company_status, company_registration_number, date_of_incorporation,
                    country, latest_turnover, latest_turnover_currency,
                    registered_address, industry_code, industry_description,
                    vat_number, contact_number, last_checked_at, checked_by_user_id,
                    directors, risk_indicators, payment_behaviour_description,
                    created_at, updated_at
                ) VALUES (
                    :id, :org_id, :connect_id, :credit_score, :credit_score_max,
                    :credit_rating, :credit_rating_description, :recommended_credit_limit,
                    :recommended_credit_limit_currency, :previous_credit_rating,
                    :previous_rating_changed_at, :risk_band, :probability_of_default_12m,
                    :assessment_commentary, :company_name, :legal_entity_name,
                    :company_status, :company_registration_number, :date_of_incorporation,
                    :country, :latest_turnover, :latest_turnover_currency,
                    :registered_address, :industry_code, :industry_description,
                    :vat_number, :contact_number, :last_checked_at, :checked_by_user_id,
                    CAST(:directors AS jsonb), CAST(:risk_indicators AS jsonb),
                    :payment_behaviour_description,
                    NOW(), NOW()
                )
            """),
            {"id": uid(), **report_data}
        )

    if not dry_run:
        await session.commit()
        print("Done. All credit demo data committed.")
    else:
        await session.rollback()
        print("Dry run complete — no data written.")


async def main(dry_run: bool) -> None:
    async with get_async_session() as session:
        await seed(session, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed credit demo data")
    parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
