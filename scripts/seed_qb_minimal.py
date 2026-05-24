import argparse
import asyncio
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import app.models  # noqa: F401
from app.common.exceptions import NotFoundError
from app.common.enums.user import UserRole, UserStatus
from app.core.database import get_async_session
from app.core.security import hash_password
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.user.models import User


async def main(existing_org_id: str | None = None) -> None:
    run_suffix = uuid4().hex[:8].lower()
    org_id = existing_org_id or str(uuid4())
    admin_id = str(uuid4()) if existing_org_id is None else None
    customer_id = str(uuid4())
    paid_invoice_id = str(uuid4())
    credit_invoice_id = str(uuid4())
    credit_note_id = str(uuid4())
    application_id = str(uuid4())
    admin_email = f"admin.qb.{run_suffix}@example.com"
    customer_email = f"customer.qb.{run_suffix}@example.com"

    async with get_async_session() as session:
        if existing_org_id is None:
            org = Organization(
                id=org_id,
                trading_name="QB Sandbox Org",
                legal_entity_name="QB Sandbox Org Ltd",
                industry=IndustryType.OTHER,
                company_size=CompanySize.EMPLOYEES_1_10,
                date_of_incorporation=date(2020, 1, 1),
                companies_house_number="12345678",
                reg_address_line_1="1 Test Street",
                reg_city="London",
                reg_postcode="SW1A 1AA",
                reg_country="United Kingdom",
                status=OrganizationStatus.ACTIVE,
            )
            admin = User(
                id=admin_id,
                email=admin_email,
                first_name="QB",
                last_name="Admin",
                password_hash=hash_password("Admin123!"),
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
                email_verified=True,
            )
            session.add_all([org, admin])
        else:
            org = await session.get(Organization, org_id)
            if org is None:
                raise NotFoundError(resource="organization", id=org_id)

        customer = User(
            id=customer_id,
            email=customer_email,
            first_name="QB",
            last_name="Customer",
            password_hash=hash_password("Customer123!"),
            role=UserRole.CUSTOMER_B2B,
            status=UserStatus.ACTIVE,
            email_verified=True,
            organization_id=org_id,
        )

        session.add(customer)
        # Ensure parent rows exist before inserting FK-dependent billing rows.
        await session.flush()

        today = date.today()
        paid_invoice = Invoice(
            id=paid_invoice_id,
            invoice_number=f"INV-QB-PAID-{uuid4().hex[:6].upper()}",
            organization_id=org_id,
            customer_id=customer_id,
            issue_date=today,
            due_date=today + timedelta(days=14),
            subtotal=Decimal("50.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("10.00"),
            total=Decimal("60.00"),
            status="SENT",
            payment_status="PAID",
            paid_amount=Decimal("60.00"),
            qb_sync_status="NOT_SYNCED",
        )

        credit_invoice = Invoice(
            id=credit_invoice_id,
            invoice_number=f"INV-QB-CREDIT-{uuid4().hex[:6].upper()}",
            organization_id=org_id,
            customer_id=customer_id,
            issue_date=today,
            due_date=today + timedelta(days=14),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
            payment_status="UNPAID",
            paid_amount=Decimal("0.00"),
            qb_sync_status="NOT_SYNCED",
        )

        credit_note = CreditNote(
            id=credit_note_id,
            credit_note_number=f"CN-QB-{uuid4().hex[:8].upper()}",
            organization_id=org_id,
            customer_id=customer_id,
            issue_date=today,
            total_credit_amount=Decimal("20.00"),
            currency="GBP",
            status="ISSUED",
            reason="Sandbox credit test",
            qb_sync_status="NOT_SYNCED",
        )

        credit_application = InvoiceCreditApplication(
            id=application_id,
            invoice_id=credit_invoice_id,
            credit_note_id=credit_note_id,
            applied_amount=Decimal("20.00"),
            applied_at=today,
            applied_by=admin_id,
        )

        session.add_all([paid_invoice, credit_invoice, credit_note, credit_application])
        await session.commit()

    print("Seeded successfully:")
    print(f"ORG_ID={org_id}")
    if existing_org_id is None:
        print(f"ADMIN_EMAIL={admin_email}")
        print("ADMIN_PASSWORD=Admin123!")
    print(f"CUSTOMER_ID={customer_id}")
    print(f"PAID_INVOICE_ID={paid_invoice_id}")
    print(f"CREDIT_INVOICE_ID={credit_invoice_id}")
    print(f"CREDIT_NOTE_ID={credit_note_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed minimal QuickBooks sandbox data")
    parser.add_argument(
        "--org-id",
        dest="org_id",
        type=str,
        default=None,
        help="Existing organization ID to seed into (recommended for already-connected QuickBooks org)",
    )
    args = parser.parse_args()
    asyncio.run(main(existing_org_id=args.org_id))
