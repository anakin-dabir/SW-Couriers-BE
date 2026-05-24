"""Shared helpers for credit note API tests."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.invoices.models import CreditNote, Invoice


async def ensure_credit_note_schema(db_session: AsyncSession) -> None:
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS source_invoice_id uuid"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS reason_category varchar(40) DEFAULT 'OTHER'"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS sent_to_email varchar(255)"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS sent_at timestamptz"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS qb_sync_status varchar(20) DEFAULT 'NOT_SYNCED'"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS qb_last_sync_at timestamptz"))
    await db_session.execute(text("ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS qb_payload_fingerprint varchar(64)"))
    await db_session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS credit_note_pdf_artifacts (
                id uuid PRIMARY KEY,
                credit_note_id uuid NOT NULL,
                template_version varchar(30) NOT NULL,
                signature_hash varchar(64) NOT NULL,
                pdf_version integer NOT NULL,
                status varchar(20) NOT NULL DEFAULT 'GENERATING',
                r2_file_key varchar(512),
                generated_at timestamptz,
                job_id varchar(100),
                error_code varchar(50),
                error_message varchar(500),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    await db_session.flush()


def admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def b2b_headers(user_id: str, organization_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


BILLING = "/v1/billing"
B2B_CN = f"{BILLING}/b2b/credit-notes"
ADMIN_CN = f"{BILLING}/credit-notes"


def make_sent_invoice(
    *,
    organization_id: str,
    customer_id: str,
    invoice_number: str,
    total: Decimal = Decimal("120.00"),
    payment_status: str = "UNPAID",
) -> Invoice:
    return Invoice(
        invoice_number=invoice_number,
        organization_id=organization_id,
        customer_id=customer_id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=total,
        status="SENT",
        payment_status=payment_status,
    )


def make_credit_note(
    *,
    organization_id: str,
    customer_id: str | None,
    credit_note_number: str,
    amount: Decimal = Decimal("50.00"),
    status: str = "ISSUED",
    source_invoice_id: str | None = None,
) -> CreditNote:
    return CreditNote(
        credit_note_number=credit_note_number,
        organization_id=organization_id,
        customer_id=customer_id,
        source_invoice_id=source_invoice_id,
        issue_date=date.today(),
        total_credit_amount=amount,
        currency="GBP",
        status=status,
        reason_category="OTHER",
        reason="Test credit note",
    )


async def seed_credit_note_fixture(
    db_session: AsyncSession,
    *,
    org_id: str,
    customer_id: str,
    cn_number: str = "CN-TEST-001",
    inv_number: str = "INV-CN-TEST-001",
    cn_amount: Decimal = Decimal("50.00"),
    inv_total: Decimal = Decimal("120.00"),
) -> tuple[CreditNote, Invoice]:
    invoice = make_sent_invoice(
        organization_id=org_id,
        customer_id=customer_id,
        invoice_number=inv_number,
        total=inv_total,
    )
    db_session.add(invoice)
    await db_session.flush()
    cn = make_credit_note(
        organization_id=org_id,
        customer_id=customer_id,
        credit_note_number=cn_number,
        amount=cn_amount,
        source_invoice_id=invoice.id,
    )
    db_session.add(cn)
    await db_session.flush()
    return cn, invoice
