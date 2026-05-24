"""Seed complete billing demo data for org 9491bf02-e7bf-413b-98a7-270999f90954.

Populates invoices, payments (multi-provider), allocations, refunds, credit notes,
account statements, and audit field-history rows — everything the admin billing
overview / payment history / account-statement APIs need for FE integration testing.

Requires an active CUSTOMER_B2B user on the org (see ``resolve_org_b2b_customer``).

Usage:
  poetry run python scripts/seed_org_billing_complete_demo.py seed
  poetry run python scripts/seed_org_billing_complete_demo.py clear
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.common.enums import UserRole, UserStatus
from app.common.types import AuditContext
from app.core.database import get_async_session
from app.modules.account_statements.enums import StatementCreatedByType
from app.modules.account_statements.service import AccountStatementService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.models import AuditLog
from app.modules.billing.enums import (
    PaymentProvider,
    PaymentRecordStatus,
    RefundMethod,
    RefundReasonCategory,
    RefundType,
)
from app.modules.billing.models import BillingPayment, Refund
from app.modules.billing.service import BillingService
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.organizations.models import Organization
from app.modules.user.models import User
from scripts.fe_demo_lib import resolve_org_b2b_customer

TARGET_ORG_ID = "9491bf02-e7bf-413b-98a7-270999f90954"
SEED_TAG = "BILL9491"
INV_PREFIX = "INV-B9491-"


def _money(v: str | float | int) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _vat(subtotal: Decimal, rate: Decimal = Decimal("20")) -> tuple[Decimal, Decimal]:
    vat = (subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))
    return vat, subtotal + vat


async def _resolve_admin(session) -> User:
    admin = await session.scalar(
        select(User)
        .where(User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE)
        .order_by(User.created_at.asc())
        .limit(1)
    )
    if admin is None:
        raise SystemExit("No active ADMIN user found — create one before seeding.")
    return admin


async def _clear_seed_data() -> None:
    async with get_async_session() as session:
        org = await session.get(Organization, TARGET_ORG_ID)
        if org is None:
            raise SystemExit(f"Organization {TARGET_ORG_ID} not found.")

        org_id = org.id

        await session.execute(
            delete(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action.like("seed.bill9491.%"),
            )
        )

        await session.execute(
            delete(Refund).where(
                Refund.organization_id == org_id,
                Refund.idempotency_key.like(f"{SEED_TAG}-%"),
            )
        )
        await session.execute(
            delete(BillingPayment).where(
                BillingPayment.organization_id == org_id,
                BillingPayment.notes.like(f"{SEED_TAG}:%"),
            )
        )

        invoice_ids = list(
            (
                await session.execute(
                    select(Invoice.id).where(
                        Invoice.organization_id == org_id,
                        Invoice.invoice_number.like(f"{INV_PREFIX}%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        credit_note_ids = list(
            (
                await session.execute(
                    select(CreditNote.id).where(
                        CreditNote.organization_id == org_id,
                        CreditNote.reason.like(f"{SEED_TAG}:%"),
                    )
                )
            )
            .scalars()
            .all()
        )

        if invoice_ids:
            await session.execute(
                delete(InvoiceCreditApplication).where(InvoiceCreditApplication.invoice_id.in_(invoice_ids))
            )
        if credit_note_ids:
            await session.execute(
                delete(InvoiceCreditApplication).where(
                    InvoiceCreditApplication.credit_note_id.in_(credit_note_ids)
                )
            )

        await session.execute(
            delete(CreditNote).where(
                CreditNote.organization_id == org_id,
                CreditNote.reason.like(f"{SEED_TAG}:%"),
            )
        )
        await session.execute(
            delete(Invoice).where(
                Invoice.organization_id == org_id,
                Invoice.invoice_number.like(f"{INV_PREFIX}%"),
            )
        )

        from app.modules.account_statements.models import AccountStatement

        stmt_ids = list(
            (
                await session.execute(
                    select(AccountStatement.id).where(
                        AccountStatement.organization_id == org_id,
                        AccountStatement.snapshot_json["seed_tag"].as_string() == SEED_TAG,
                    )
                )
            )
            .scalars()
            .all()
        )
        if stmt_ids:
            await session.execute(delete(AccountStatement).where(AccountStatement.id.in_(stmt_ids)))

        await session.commit()
        print(f"Cleared {SEED_TAG} billing demo data for org {org_id}.")


async def _seed_audit_field_history(session, *, organization_id: str, actor_id: str) -> None:
    now = datetime.now(UTC)
    credit_limits = [5000, 10000, 15000, 20000, 30000, 50000]
    for idx in range(1, len(credit_limits)):
        log = AuditLog(
            action="seed.bill9491.credit_limit_updated",
            category=AuditCategory.CREDIT.value,
            event_type=AuditEventType.CREDIT_LIMIT_UPDATED.value,
            severity="NOTICE",
            entity_type="organization_billing_profile",
            entity_id=organization_id,
            user_id=actor_id,
            user_role=UserRole.ADMIN.value,
            old_value={"credit_limit": f"{credit_limits[idx - 1]}.00"},
            new_value={"credit_limit": f"{credit_limits[idx]}.00"},
            reason=f"Seeded credit limit step #{idx}",
            organization_id=organization_id,
            created_at=now - timedelta(days=idx * 45),
        )
        session.add(log)

    for idx, (old_status, new_status) in enumerate(
        [("UNDER_REVIEW", "APPROVED"), ("APPROVED", "ACTIVE"), ("ACTIVE", "ACTIVE")],
        start=1,
    ):
        session.add(
            AuditLog(
                action="seed.bill9491.billing_status_changed",
                category=AuditCategory.BILLING.value,
                event_type=AuditEventType.BILLING_CONFIG_CHANGED.value,
                severity="INFO",
                entity_type="organization_billing_profile",
                entity_id=organization_id,
                user_id=actor_id,
                user_role=UserRole.ADMIN.value,
                old_value={"billing_status": old_status},
                new_value={"billing_status": new_status},
                reason=f"Seeded billing status step #{idx}",
                organization_id=organization_id,
                created_at=now - timedelta(days=idx * 14),
            )
        )


def _month_period(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


async def seed_demo_data() -> None:
    await _clear_seed_data()

    async with get_async_session() as session:
        today = date.today()
        org = await session.get(Organization, TARGET_ORG_ID)
        if org is None:
            raise SystemExit(f"Organization {TARGET_ORG_ID} not found.")

        admin = await _resolve_admin(session)
        customer = await resolve_org_b2b_customer(session, org.id)

        service = BillingService(session)
        stmt_service = AccountStatementService(session)
        audit_ctx = AuditContext(
            user_id=admin.id,
            user_role=UserRole.ADMIN.value,
            ip_address="127.0.0.1",
            user_agent="seed_org_billing_complete_demo",
        )

        # ── Invoices (spread over ~14 months) ─────────────────────────────────
        invoice_specs: list[dict] = []
        subtotals = [120, 185, 240, 310, 95, 420, 275, 360, 150, 520, 198, 640, 88, 410, 330, 175, 290, 455, 220, 380, 165, 490, 205, 350]
        for i, subtotal in enumerate(subtotals):
            days_ago = 420 - (i * 17)
            issue = today - timedelta(days=max(days_ago, 5))
            due = issue + timedelta(days=30)
            vat_amt, total = _vat(_money(subtotal))
            invoice_specs.append(
                {
                    "number": f"{INV_PREFIX}{1001 + i:04d}",
                    "issue_date": issue,
                    "due_date": due,
                    "subtotal": _money(subtotal),
                    "vat_amount": vat_amt,
                    "total": total,
                }
            )

        invoices: list[Invoice] = []
        for spec in invoice_specs:
            inv = Invoice(
                invoice_number=spec["number"],
                organization_id=org.id,
                customer_id=customer.id,
                issue_date=spec["issue_date"],
                due_date=spec["due_date"],
                subtotal=spec["subtotal"],
                vat_rate=_money("20.00"),
                vat_amount=spec["vat_amount"],
                total=spec["total"],
                status="SENT",
                payment_status="UNPAID",
                paid_amount=_money("0"),
                notes=f"{SEED_TAG}: demo invoice",
            )
            invoices.append(inv)
        session.add_all(invoices)
        await session.flush()

        providers_cycle = [
            PaymentProvider.BANK_TRANSFER,
            PaymentProvider.MANUAL,
            PaymentProvider.CHEQUE,
            PaymentProvider.BANK_TRANSFER,
            PaymentProvider.OTHER,
        ]

        payment_count = 0
        refund_count = 0
        credit_note_count = 0

        # Fully pay first 10 invoices
        for idx, inv in enumerate(invoices[:10]):
            provider = providers_cycle[idx % len(providers_cycle)]
            pay_date = min(inv.issue_date + timedelta(days=12), today - timedelta(days=1))
            payment = await service.record_payment(
                organization_id=org.id,
                amount=inv.total,
                payment_date=pay_date,
                client_type="CUSTOMER_B2B",
                recorded_by_id=admin.id,
                customer_id=customer.id,
                status=PaymentRecordStatus.DEPOSITED,
                provider=provider,
                notes=f"{SEED_TAG}: full payment {inv.invoice_number}",
                audit_ctx=audit_ctx,
            )
            await service.add_or_revise_allocation(
                payment_id=payment.id,
                invoice_id=inv.id,
                allocated_amount=inv.total,
                actor_id=admin.id,
                notes=f"{SEED_TAG}: full allocation",
                audit_ctx=audit_ctx,
            )
            payment_count += 1

        # Partially pay next 6
        for idx, inv in enumerate(invoices[10:16]):
            provider = providers_cycle[(idx + 2) % len(providers_cycle)]
            partial = (inv.total * Decimal("0.55")).quantize(Decimal("0.01"))
            pay_date = min(inv.issue_date + timedelta(days=8), today - timedelta(days=2))
            payment = await service.record_payment(
                organization_id=org.id,
                amount=partial,
                payment_date=pay_date,
                client_type="CUSTOMER_B2B",
                recorded_by_id=admin.id,
                customer_id=customer.id,
                status=PaymentRecordStatus.DEPOSITED,
                provider=provider,
                notes=f"{SEED_TAG}: partial payment {inv.invoice_number}",
                audit_ctx=audit_ctx,
            )
            await service.add_or_revise_allocation(
                payment_id=payment.id,
                invoice_id=inv.id,
                allocated_amount=partial,
                actor_id=admin.id,
                notes=f"{SEED_TAG}: partial allocation",
                audit_ctx=audit_ctx,
            )
            payment_count += 1

        # Extra unallocated payments (for KPIs / method mix)
        for idx in range(4):
            pay_date = today - timedelta(days=5 + idx * 6)
            await service.record_payment(
                organization_id=org.id,
                amount=_money(250 + idx * 75),
                payment_date=pay_date,
                client_type="CUSTOMER_B2B",
                recorded_by_id=admin.id,
                customer_id=customer.id,
                status=PaymentRecordStatus.DEPOSITED,
                provider=providers_cycle[idx],
                notes=f"{SEED_TAG}: unallocated payment #{idx + 1}",
                audit_ctx=audit_ctx,
            )
            payment_count += 1

        # Pending / not deposited
        await service.record_payment(
            organization_id=org.id,
            amount=_money("199.00"),
            payment_date=today - timedelta(days=1),
            client_type="CUSTOMER_B2B",
            recorded_by_id=admin.id,
            customer_id=customer.id,
            status=PaymentRecordStatus.NOT_DEPOSITED,
            provider=PaymentProvider.BANK_TRANSFER,
            notes=f"{SEED_TAG}: pending deposit",
            audit_ctx=audit_ctx,
        )
        payment_count += 1

        # Refunds on a partially paid invoice (leave headroom for allocation)
        refundable_inv = invoices[20]
        refundable_alloc = (refundable_inv.total * Decimal("0.40")).quantize(Decimal("0.01"))
        refundable_payment = await service.record_payment(
            organization_id=org.id,
            amount=_money("500.00"),
            payment_date=today - timedelta(days=10),
            client_type="CUSTOMER_B2B",
            recorded_by_id=admin.id,
            customer_id=customer.id,
            status=PaymentRecordStatus.DEPOSITED,
            provider=PaymentProvider.BANK_TRANSFER,
            notes=f"{SEED_TAG}: refundable overpayment {refundable_inv.invoice_number}",
            audit_ctx=audit_ctx,
        )
        await service.add_or_revise_allocation(
            payment_id=refundable_payment.id,
            invoice_id=refundable_inv.id,
            allocated_amount=refundable_alloc,
            actor_id=admin.id,
            notes=f"{SEED_TAG}: partial alloc for refund demo",
            audit_ctx=audit_ctx,
        )
        payment_count += 1

        refund_completed = await service.create_refund(
            organization_id=org.id,
            billing_payment_id=refundable_payment.id,
            amount=_money("45.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.BILLING_ERROR,
            reason_description=f"{SEED_TAG}: completed bank refund",
            actor_id=admin.id,
            invoice_id=refundable_inv.id,
            linked_booking_ref=f"{SEED_TAG}-BK-001",
            idempotency_key=f"{SEED_TAG}-refund-complete",
        )
        await service.mark_refund_complete(
            organization_id=org.id,
            refund_id=refund_completed.id,
            actor_id=admin.id,
            note=f"{SEED_TAG}: refund marked complete",
        )
        refund_count += 1

        refund_initiated = await service.create_refund(
            organization_id=org.id,
            billing_payment_id=refundable_payment.id,
            amount=_money("25.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description=f"{SEED_TAG}: initiated refund",
            actor_id=admin.id,
            invoice_id=refundable_inv.id,
            idempotency_key=f"{SEED_TAG}-refund-initiated",
        )
        refund_count += 1
        _ = refund_initiated

        # Credit notes
        cn_applied = await service.create_credit_note(
            organization_id=org.id,
            source_invoice_id=invoices[16].id,
            customer_id=customer.id,
            issue_date_value=today - timedelta(days=14),
            amount=_money("60.00"),
            reason_category="BILLING_ERROR",
            reason=f"{SEED_TAG}: applied credit note",
        )
        await service.apply_credit_note_auto(
            credit_note_id=cn_applied.id,
            invoice_id=invoices[16].id,
            organization_id=org.id,
            actor_id=admin.id,
        )
        credit_note_count += 1

        await service.create_credit_note(
            organization_id=org.id,
            source_invoice_id=invoices[17].id,
            customer_id=customer.id,
            issue_date_value=today - timedelta(days=7),
            amount=_money("35.50"),
            reason_category="CLIENT_REQUEST",
            reason=f"{SEED_TAG}: open credit note A",
        )
        await service.create_credit_note(
            organization_id=org.id,
            source_invoice_id=invoices[18].id,
            customer_id=customer.id,
            issue_date_value=today - timedelta(days=3),
            amount=_money("88.00"),
            reason_category="SERVICE_FAILURE",
            reason=f"{SEED_TAG}: open credit note B",
        )
        credit_note_count += 2

        # Force a few overdue unpaid (due in the past)
        for inv in invoices[19:23]:
            inv.due_date = today - timedelta(days=14 + (invoices.index(inv) % 5))

        await _seed_audit_field_history(session, organization_id=org.id, actor_id=admin.id)
        await session.flush()

        # Account statements — last 3 calendar months
        statement_count = 0
        first_of_month = today.replace(day=1)
        for month_offset in range(1, 4):
            ref_start = first_of_month
            for _ in range(month_offset):
                ref_start = (ref_start - timedelta(days=1)).replace(day=1)
            period_start, period_end = _month_period(ref_start.year, ref_start.month)
            stmt = await stmt_service.create_statement(
                organization_id=org.id,
                period_start=period_start,
                period_end=period_end,
                include_line_item_detail=True,
                include_credit_notes=True,
                include_payment_history=True,
                created_by_user_id=admin.id,
                created_by_user_type=StatementCreatedByType.SYSTEM,
                idempotency_key=f"{SEED_TAG}-stmt-{period_start.isoformat()}",
            )
            snap = dict(stmt.snapshot_json or {})
            snap["seed_tag"] = SEED_TAG
            stmt.snapshot_json = snap
            statement_count += 1

        await session.commit()

        print("=" * 72)
        print("Billing complete demo seed finished.")
        print(f"Organization ID : {org.id}")
        print(f"B2B customer    : {customer.email}")
        print(f"Admin actor     : {admin.email}")
        print(f"Invoices        : {len(invoices)} ({INV_PREFIX}*)")
        print(f"Payments        : {payment_count} tagged with {SEED_TAG}")
        print(f"Refunds         : {refund_count}")
        print(f"Credit notes    : {credit_note_count}+")
        print(f"Statements      : {statement_count} (last 3 months)")
        print("Also run credit overview seed if needed:")
        print("  poetry run python scripts/seed_credit_overview_demo.py")
        print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear complete billing demo for target org.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Insert billing demo data")
    sub.add_parser("clear", help="Remove billing demo data tagged with BILL9491")
    args = parser.parse_args()

    if args.cmd == "seed":
        asyncio.run(seed_demo_data())
    else:
        asyncio.run(_clear_seed_data())


if __name__ == "__main__":
    main()
