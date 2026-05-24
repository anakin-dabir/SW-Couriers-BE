"""Seed/clear frontend demo data for organization billing + audit field history.

Uses the org's existing ``CUSTOMER_B2B`` portal user (does not create a synthetic customer).

**Warning:** ``seed`` clears and recreates only rows tagged ``FEBILL01`` / ``INV-FEBD-*``.
If billing data is already present for that B2B account, **do not re-run** — use
``scripts/seed_fe_order_scenarios.py`` for orders/drafts instead.

Usage:
  poetry run python scripts/seed_frontend_org_billing_audit_demo.py seed
  poetry run python scripts/seed_frontend_org_billing_audit_demo.py clear
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.common.enums import UserRole, UserStatus
from app.common.types import AuditContext
from app.core.database import get_async_session
from app.core.security import hash_password
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.models import AuditLog
from app.modules.billing.enums import PaymentProvider, PaymentRecordStatus, RefundMethod, RefundReasonCategory, RefundType
from app.modules.billing.models import BillingPayment, Refund
from app.modules.billing.service import BillingService
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.user.models import User
from scripts.fe_demo_lib import (
    BILLING_DEMO_ORG_ID,
    BILLING_DEMO_PICKUP_LABEL,
    resolve_org_b2b_customer,
)

SEED_TAG = "FEBILL01"
TARGET_ORG_ID = BILLING_DEMO_ORG_ID
ADMIN_EMAIL = "demo.billing.admin@gmail.com"
INV_PREFIX = "INV-FEBD-"
PICKUP_LABEL = BILLING_DEMO_PICKUP_LABEL


def _money(v: str) -> Decimal:
    return Decimal(v)


async def _clear_seed_data() -> None:
    async with get_async_session() as session:
        org = await session.get(Organization, TARGET_ORG_ID)
        if org is None:
            raise SystemExit(f"Target organization {TARGET_ORG_ID} not found.")

        org_id = org.id

        await session.execute(
            delete(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action.like("seed.frontend_demo.%"),
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
            await session.execute(delete(InvoiceCreditApplication).where(InvoiceCreditApplication.invoice_id.in_(invoice_ids)))
        if credit_note_ids:
            await session.execute(delete(InvoiceCreditApplication).where(InvoiceCreditApplication.credit_note_id.in_(credit_note_ids)))

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
        await session.execute(
            delete(PickupAddress).where(
                PickupAddress.organization_id == org_id,
                PickupAddress.label == PICKUP_LABEL,
            )
        )
        await session.execute(delete(User).where(User.email == ADMIN_EMAIL))
        await session.commit()
        print(f"Cleared seeded billing/audit demo data in org {org_id}.")


async def _seed_audit_field_history(session, *, organization_id: str, actor_id: str) -> None:
    now = datetime.now(UTC)

    credit_limits = [2500, 5000, 7000, 9000, 12000, 15000]
    for idx in range(1, len(credit_limits)):
        old_val = credit_limits[idx - 1]
        new_val = credit_limits[idx]
        created_at = now - timedelta(days=idx * 28)
        log = AuditLog(
            action="seed.frontend_demo.credit_limit_updated",
            category=AuditCategory.CREDIT.value,
            event_type=AuditEventType.CREDIT_LIMIT_UPDATED.value,
            severity="NOTICE",
            entity_type="organization_billing_profile",
            entity_id=organization_id,
            user_id=actor_id,
            user_role=UserRole.ADMIN.value,
            old_value={"credit_limit": f"{old_val}.00"},
            new_value={"credit_limit": f"{new_val}.00"},
            reason=f"Seeded credit limit adjustment #{idx}",
            organization_id=organization_id,
            created_at=created_at,
        )
        session.add(log)

    status_changes = [
        ("UNDER_REVIEW", "APPROVED"),
        ("APPROVED", "ACTIVE"),
        ("ACTIVE", "ACTIVE"),
    ]
    for idx, (old_status, new_status) in enumerate(status_changes, start=1):
        created_at = now - timedelta(days=idx * 7)
        log = AuditLog(
            action="seed.frontend_demo.billing_status_changed",
            category=AuditCategory.BILLING.value,
            event_type=AuditEventType.BILLING_CONFIG_CHANGED.value,
            severity="INFO",
            entity_type="organization_billing_profile",
            entity_id=organization_id,
            user_id=actor_id,
            user_role=UserRole.ADMIN.value,
            old_value={"billing_status": old_status},
            new_value={"billing_status": new_status},
            reason=f"Seeded billing status transition #{idx}",
            organization_id=organization_id,
            created_at=created_at,
        )
        session.add(log)


async def seed_demo_data() -> None:
    await _clear_seed_data()

    async with get_async_session() as session:
        today = date.today()
        org = await session.get(Organization, TARGET_ORG_ID)
        if org is None:
            raise SystemExit(f"Target organization {TARGET_ORG_ID} not found.")

        admin = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if admin is None:
            admin = User(email=ADMIN_EMAIL)
            session.add(admin)
        admin.first_name = "Demo"
        admin.last_name = "BillingAdmin"
        admin.phone = "07700900901"
        admin.password_hash = hash_password("Admin123!Demo")
        admin.role = UserRole.ADMIN
        admin.status = UserStatus.ACTIVE
        admin.email_verified = True
        admin.organization_id = None

        customer = await resolve_org_b2b_customer(session, org.id)

        pickup = await session.scalar(
            select(PickupAddress).where(
                PickupAddress.organization_id == org.id,
                PickupAddress.label == PICKUP_LABEL,
            )
        )
        if pickup is None:
            pickup = PickupAddress(
                organization_id=org.id,
                label=PICKUP_LABEL,
                line_1="77 Frontend Quay",
                city="London",
                postcode="SE1 7AA",
                country="United Kingdom",
                latitude=51.5037,
                longitude=-0.0828,
                is_default=False,
                created_by_user_id=customer.id,
            )
            session.add(pickup)
            await session.flush()

        invoices = [
            Invoice(
                invoice_number=f"{INV_PREFIX}1001",
                organization_id=org.id,
                customer_id=customer.id,
                issue_date=today - timedelta(days=45),
                due_date=today - timedelta(days=15),
                subtotal=_money("200.00"),
                vat_rate=_money("20.00"),
                vat_amount=_money("40.00"),
                total=_money("240.00"),
                status="SENT",
                payment_status="UNPAID",
                paid_amount=_money("0.00"),
            ),
            Invoice(
                invoice_number=f"{INV_PREFIX}1002",
                organization_id=org.id,
                customer_id=customer.id,
                issue_date=today - timedelta(days=20),
                due_date=today + timedelta(days=10),
                subtotal=_money("150.00"),
                vat_rate=_money("20.00"),
                vat_amount=_money("30.00"),
                total=_money("180.00"),
                status="SENT",
                payment_status="UNPAID",
                paid_amount=_money("0.00"),
            ),
            Invoice(
                invoice_number=f"{INV_PREFIX}1003",
                organization_id=org.id,
                customer_id=customer.id,
                issue_date=today - timedelta(days=12),
                due_date=today + timedelta(days=18),
                subtotal=_money("250.00"),
                vat_rate=_money("20.00"),
                vat_amount=_money("50.00"),
                total=_money("300.00"),
                status="SENT",
                payment_status="UNPAID",
                paid_amount=_money("0.00"),
            ),
            Invoice(
                invoice_number=f"{INV_PREFIX}1004",
                organization_id=org.id,
                customer_id=customer.id,
                issue_date=today - timedelta(days=10),
                due_date=today + timedelta(days=20),
                subtotal=_money("166.67"),
                vat_rate=_money("20.00"),
                vat_amount=_money("33.33"),
                total=_money("200.00"),
                status="SENT",
                payment_status="UNPAID",
                paid_amount=_money("0.00"),
            ),
        ]
        session.add_all(invoices)
        await session.flush()

        service = BillingService(session)
        audit_ctx = AuditContext(
            user_id=admin.id,
            user_role=UserRole.ADMIN.value,
            ip_address="127.0.0.1",
            user_agent="seed_frontend_org_billing_audit_demo",
        )

        payment_full = await service.record_payment(
            organization_id=org.id,
            amount=_money("180.00"),
            payment_date=today - timedelta(days=7),
            client_type="CUSTOMER_B2B",
            recorded_by_id=admin.id,
            customer_id=customer.id,
            status=PaymentRecordStatus.DEPOSITED,
            provider=PaymentProvider.BANK_TRANSFER,
            notes=f"{SEED_TAG}: full payment for invoice {invoices[1].invoice_number}",
            audit_ctx=audit_ctx,
        )
        await service.add_or_revise_allocation(
            payment_id=payment_full.id,
            invoice_id=invoices[1].id,
            allocated_amount=_money("180.00"),
            actor_id=admin.id,
            notes=f"{SEED_TAG}: allocation full",
            audit_ctx=audit_ctx,
        )

        payment_partial = await service.record_payment(
            organization_id=org.id,
            amount=_money("150.00"),
            payment_date=today - timedelta(days=4),
            client_type="CUSTOMER_B2B",
            recorded_by_id=admin.id,
            customer_id=customer.id,
            status=PaymentRecordStatus.DEPOSITED,
            provider=PaymentProvider.MANUAL,
            notes=f"{SEED_TAG}: partial payment for invoice {invoices[2].invoice_number}",
            audit_ctx=audit_ctx,
        )
        await service.add_or_revise_allocation(
            payment_id=payment_partial.id,
            invoice_id=invoices[2].id,
            allocated_amount=_money("120.00"),
            actor_id=admin.id,
            notes=f"{SEED_TAG}: allocation partial",
            audit_ctx=audit_ctx,
        )

        payment_refundable = await service.record_payment(
            organization_id=org.id,
            amount=_money("110.00"),
            payment_date=today - timedelta(days=2),
            client_type="CUSTOMER_B2B",
            recorded_by_id=admin.id,
            customer_id=customer.id,
            status=PaymentRecordStatus.DEPOSITED,
            provider=PaymentProvider.BANK_TRANSFER,
            notes=f"{SEED_TAG}: refundable payment for invoice {invoices[3].invoice_number}",
            audit_ctx=audit_ctx,
        )
        await service.add_or_revise_allocation(
            payment_id=payment_refundable.id,
            invoice_id=invoices[3].id,
            allocated_amount=_money("110.00"),
            actor_id=admin.id,
            notes=f"{SEED_TAG}: allocation refundable",
            audit_ctx=audit_ctx,
        )

        refund = await service.create_refund(
            organization_id=org.id,
            billing_payment_id=payment_refundable.id,
            amount=_money("30.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.BILLING_ERROR,
            reason_description=f"{SEED_TAG}: partial correction refund",
            actor_id=admin.id,
            invoice_id=invoices[3].id,
            linked_booking_ref=f"{SEED_TAG}-BK-1004",
            idempotency_key=f"{SEED_TAG}-refund-1004",
            metadata_json={"seed_tag": SEED_TAG},
        )
        await service.mark_refund_complete(
            organization_id=org.id,
            refund_id=refund.id,
            actor_id=admin.id,
            note=f"{SEED_TAG}: bank transfer refund completed",
        )

        credit_note_applied = await service.create_credit_note(
            organization_id=org.id,
            source_invoice_id=invoices[3].id,
            customer_id=customer.id,
            issue_date_value=today - timedelta(days=1),
            amount=_money("80.00"),
            reason_category="BILLING_ERROR",
            reason=f"{SEED_TAG}: applied credit note",
        )
        await service.apply_credit_note_auto(
            credit_note_id=credit_note_applied.id,
            invoice_id=invoices[3].id,
            organization_id=org.id,
            actor_id=customer.id,
        )

        _open_credit_note = await service.create_credit_note(
            organization_id=org.id,
            source_invoice_id=invoices[0].id,
            customer_id=customer.id,
            issue_date_value=today,
            amount=_money("45.00"),
            reason_category="CLIENT_REQUEST",
            reason=f"{SEED_TAG}: open credit note for testing",
        )

        await _seed_audit_field_history(session, organization_id=org.id, actor_id=admin.id)
        await session.commit()

        print("=" * 72)
        print("Frontend organization billing/audit demo seed complete.")
        print(f"Organization ID : {org.id} (existing target org)")
        print(f"Admin email     : {ADMIN_EMAIL}")
        print(f"B2B customer    : {customer.email}  (role={customer.role.value})")
        print("Invoices        : " + ", ".join(i.invoice_number for i in invoices))
        print(f"Payments        : {payment_full.payment_number}, {payment_partial.payment_number}, {payment_refundable.payment_number}")
        print(
            "Field history API samples:\n"
            f"  /api/v1/organizations/{org.id}/audit-logs/field-history/credit_limit\n"
            f"  /api/v1/organizations/{org.id}/audit-logs/field-history/billing_status"
        )
        print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear frontend organization billing/audit demo data.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Insert seeded frontend billing/audit org data")
    sub.add_parser("clear", help="Delete seeded frontend billing/audit org data")
    args = parser.parse_args()

    if args.cmd == "seed":
        asyncio.run(seed_demo_data())
    else:
        asyncio.run(_clear_seed_data())


if __name__ == "__main__":
    main()
