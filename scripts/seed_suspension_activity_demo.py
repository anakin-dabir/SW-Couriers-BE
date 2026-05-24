"""Seed rich suspension activity data for FE activity APIs.

Usage:
    poetry run python scripts/seed_suspension_activity_demo.py
    poetry run python scripts/seed_suspension_activity_demo.py --rows 80
    poetry run python scripts/seed_suspension_activity_demo.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import app.models  # noqa: F401
from sqlalchemy import select

from app.common.enums import UserRole, UserStatus
from app.core.database import get_async_session
from app.modules.organizations.enums import OrganizationStatus, PaymentModel
from app.modules.organizations.models import Organization
from app.modules.suspension_rules.enums import (
    RuleScopeType,
    SuspensionActionTaken,
    SuspensionConditionType,
    SuspensionConnector,
    SuspensionRuleType,
    SuspensionRuleStatus,
)
from app.modules.suspension_rules.models import (
    SuspensionActivity,
    SuspensionEvaluationRun,
    SuspensionNotificationAudit,
    SuspensionRuleCondition,
    SuspensionRuleSet,
)
from app.modules.user.models import User


def _payment_model_for_rule_type(rule_type: str) -> str:
    if rule_type == SuspensionRuleType.CREDIT_CARD.value:
        return PaymentModel.CARD.value
    if rule_type == SuspensionRuleType.BANK_TRANSFER.value:
        return PaymentModel.BANK_TRANSFER.value
    if rule_type == SuspensionRuleType.CASH.value:
        return PaymentModel.CASH.value
    return PaymentModel.CREDIT_ACCOUNT.value


def _default_condition_for_rule_type(rule_type: str) -> tuple[str, Decimal, str]:
    if rule_type == SuspensionRuleType.CREDIT_CARD.value:
        return (SuspensionConditionType.PAYMENT_FAILURE_COUNT.value, Decimal("3.00"), "COUNT")
    if rule_type == SuspensionRuleType.BANK_TRANSFER.value:
        return (SuspensionConditionType.NUMBER_OF_UNPAID_INVOICES.value, Decimal("4.00"), "COUNT")
    if rule_type == SuspensionRuleType.CASH.value:
        return (SuspensionConditionType.MAX_UNPAID_ORDERS.value, Decimal("3.00"), "COUNT")
    return (SuspensionConditionType.TOTAL_OVERDUE_AMOUNT.value, Decimal("1000.00"), "GBP")


def _build_conditions_met(rule_type: str, i: int) -> dict:
    if rule_type == SuspensionRuleType.CREDIT_CARD.value:
        return {
            "payment_failure_count": 2 + (i % 4),
            "payment_retry_failure_count": 1 + (i % 3),
            "chargeback_triggered": i % 7 == 0,
            "window_days": 30,
        }
    if rule_type == SuspensionRuleType.BANK_TRANSFER.value:
        return {
            "number_of_unpaid_invoices": 3 + (i % 5),
            "total_outstanding_amount": 500 + (i * 35),
            "window_days": 45,
        }
    if rule_type == SuspensionRuleType.CASH.value:
        return {
            "max_unpaid_orders": 2 + (i % 4),
            "outstanding_cash_balance": 300 + (i * 20),
            "cash_invoice_overdue_days": 1 + (i % 9),
        }
    return {
        "credit_utilization": 70 + (i % 25),
        "total_overdue_amount": 1200 + (i * 50),
        "invoice_overdue_days": 7 + (i % 20),
    }


async def _get_or_create_rule_set(
    *,
    session,
    org_id: str,
    rule_type: str,
    seed_index: int,
) -> SuspensionRuleSet:
    existing = (
        await session.execute(
            select(SuspensionRuleSet).where(
                SuspensionRuleSet.scope_type == RuleScopeType.GLOBAL.value,
                SuspensionRuleSet.rule_type == rule_type,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    rule = SuspensionRuleSet(
        name=f"Seeded {rule_type.replace('_', ' ').title()} Rule {seed_index}",
        condition_summary="Seeded rich suspension rule for FE activity demo.",
        scope_type=RuleScopeType.GLOBAL.value,
        scope_org_id=None,
        parent_global_rule_set_id=None,
        rule_type=rule_type,
        status=SuspensionRuleStatus.ACTIVE.value,
        notes="[seed-demo] Auto-created for suspension activity demo.",
        auto_suspension_enabled=True,
        pause_new_bookings=seed_index % 2 == 0,
        restrict_portal_login=seed_index % 3 == 0,
        notify_finance_team=True,
        notify_account_manager=seed_index % 2 == 1,
    )
    session.add(rule)
    await session.flush()

    cond_type, threshold, unit = _default_condition_for_rule_type(rule_type)
    session.add(
        SuspensionRuleCondition(
            rule_set_id=rule.id,
            position=1,
            connector=SuspensionConnector.NONE.value,
            condition_type=cond_type,
            threshold_value=threshold,
            unit=unit,
        )
    )
    await session.flush()
    return rule


async def _run(rows: int, dry_run: bool) -> None:
    random.seed(42)
    now = datetime.now(UTC)
    async with get_async_session() as session:
        orgs = (
            await session.execute(
                select(Organization)
                .where(Organization.status == OrganizationStatus.ACTIVE)
                .order_by(Organization.created_at.desc())
                .limit(12)
            )
        ).scalars().all()
        if not orgs:
            raise SystemExit("No ACTIVE organizations found; cannot seed suspension activity.")

        users = (
            await session.execute(
                select(User)
                .where(
                    User.role == UserRole.CUSTOMER_B2B,
                    User.status.in_([UserStatus.ACTIVE, UserStatus.SUSPENDED]),
                    User.organization_id.isnot(None),
                )
                .order_by(User.created_at.desc())
                .limit(80)
            )
        ).scalars().all()
        if not users:
            raise SystemExit("No CUSTOMER_B2B users found with organization_id; cannot seed suspension activity.")

        # Ensure we have at least one rule set per rule type for rich filtering.
        rule_types = [
            SuspensionRuleType.CREDIT_LIMIT.value,
            SuspensionRuleType.BANK_TRANSFER.value,
            SuspensionRuleType.CREDIT_CARD.value,
            SuspensionRuleType.CASH.value,
        ]
        rules: list[SuspensionRuleSet] = []
        for idx, rt in enumerate(rule_types, start=1):
            rules.append(await _get_or_create_rule_set(session=session, org_id=orgs[0].id, rule_type=rt, seed_index=idx))

        run_rows: list[SuspensionEvaluationRun] = []
        for d in range(3):
            run_rows.append(
                SuspensionEvaluationRun(
                    run_date=(now.date() - timedelta(days=d)).isoformat(),
                    started_at=now - timedelta(days=d, minutes=20),
                    completed_at=now - timedelta(days=d, minutes=5),
                    status="COMPLETED",
                    evaluated_count=max(5, len(orgs)),
                    matched_count=max(4, rows // 4),
                    warned_count=max(2, rows // 10),
                    suspended_count=max(2, rows // 8),
                    failed_count=0,
                    notes="[seed-demo] Rich suspension activity run",
                )
            )
        if dry_run:
            print(f"Would seed {rows} suspension_activity rows.")
            print(f"Using {len(orgs)} orgs, {len(users)} users, {len(rules)} rules, {len(run_rows)} evaluation runs.")
            return

        session.add_all(run_rows)
        await session.flush()
        runs = run_rows

        activities: list[SuspensionActivity] = []
        for i in range(rows):
            user = users[i % len(users)]
            org_id = str(user.organization_id)
            rule = rules[i % len(rules)]
            run = runs[i % len(runs)]
            action = (
                SuspensionActionTaken.SUSPENDED.value
                if i % 3 != 0
                else SuspensionActionTaken.WARNING_SENT.value
            )
            notification_status = "SENT" if i % 4 else "QUEUED"
            created_at = now - timedelta(hours=i * 3, minutes=i % 17)
            conditions_met = _build_conditions_met(rule.rule_type, i)
            activity = SuspensionActivity(
                rule_set_id=rule.id,
                rule_name_snapshot=rule.name,
                account_id=user.id,
                organization_id=org_id,
                rule_type=rule.rule_type,
                payment_model=_payment_model_for_rule_type(rule.rule_type),
                run_id=run.id,
                conditions_met=conditions_met,
                action_taken=action,
                notes=f"[seed-demo] Suspension activity sample #{i + 1}",
                evaluated_expression="(cond_1 AND cond_2) OR cond_3",
                group_results=[
                    {"group": 1, "operator": "AND", "result": True},
                    {"group": 2, "operator": "OR", "result": i % 5 != 0},
                ],
                final_result=True,
                notification_status=notification_status,
                created_at=created_at,
            )
            activities.append(activity)
        session.add_all(activities)
        await session.flush()

        audits: list[SuspensionNotificationAudit] = []
        for i, activity in enumerate(activities):
            user = users[i % len(users)]
            audits.append(
                SuspensionNotificationAudit(
                    activity_id=activity.id,
                    channel="EMAIL",
                    recipient=user.email,
                    status="SENT" if i % 4 else "QUEUED",
                    external_id=f"seed-mail-{uuid.uuid4().hex[:12]}",
                    error_message=None,
                    rule_metadata={
                        "seed_demo": True,
                        "recipient_type": "customer",
                        "index": i + 1,
                    },
                )
            )
        session.add_all(audits)

        print(f"Inserted {len(run_rows)} suspension_evaluation_runs.")
        print(f"Inserted {len(activities)} suspension_activity rows.")
        print(f"Inserted {len(audits)} suspension_notification_audit rows.")
        print("Tip: call GET /v1/suspension-rules/activity with filters (rule_type, payment_model, organization_id).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed rich suspension activity demo data for FE APIs.")
    parser.add_argument("--rows", type=int, default=60, help="Number of suspension_activity rows to insert.")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be seeded without DB writes.")
    args = parser.parse_args()
    if args.rows <= 0:
        raise SystemExit("--rows must be > 0")
    asyncio.run(_run(rows=args.rows, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
