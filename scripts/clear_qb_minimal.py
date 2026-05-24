"""Remove QuickBooks sandbox data seeded by scripts.seed_qb_minimal.

Usage:

    poetry run python -m scripts.clear_qb_minimal
    poetry run python -m scripts.clear_qb_minimal --org-id <ORG_ID>

Default behavior (no --org-id):
- Finds all organizations matching the seed marker:
  trading_name='QB Sandbox Org' AND legal_entity_name='QB Sandbox Org Ltd'
- Removes related seeded invoices/credit notes and QuickBooks integration rows
- Removes seeded test users with emails:
  admin.qb.*@example.com, customer.qb.*@example.com
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, or_, select

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.integrations.quickbooks.models import QbConnection, QbLink, QbReferenceMapping, QbSyncLog, QbSyncSettings
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.organizations.models import Organization
from app.modules.user.models import User

SEED_TRADING_NAME = "QB Sandbox Org"
SEED_LEGAL_NAME = "QB Sandbox Org Ltd"
SEED_INVOICE_PREFIX = "INV-QB-%"
SEED_CREDIT_NOTE_PREFIX = "CN-QB-%"
SEED_ADMIN_EMAIL_PREFIX = "admin.qb.%@example.com"
SEED_CUSTOMER_EMAIL_PREFIX = "customer.qb.%@example.com"


async def _resolve_org_ids(org_id: str | None) -> list[str]:
    async with get_async_session() as session:
        if org_id:
            return [org_id]
        rows = (
            await session.execute(
                select(Organization.id).where(
                    Organization.trading_name == SEED_TRADING_NAME,
                    Organization.legal_entity_name == SEED_LEGAL_NAME,
                )
            )
        ).scalars().all()
        return list(rows)


async def _clear(org_ids: list[str]) -> None:
    async with get_async_session() as session:
        if not org_ids:
            print("No QB seed organizations found. Nothing to clear.")
            return

        invoice_ids = (
            await session.execute(
                select(Invoice.id).where(
                    Invoice.organization_id.in_(org_ids),
                    Invoice.invoice_number.like(SEED_INVOICE_PREFIX),
                )
            )
        ).scalars().all()

        credit_note_ids = (
            await session.execute(
                select(CreditNote.id).where(
                    CreditNote.organization_id.in_(org_ids),
                    CreditNote.credit_note_number.like(SEED_CREDIT_NOTE_PREFIX),
                )
            )
        ).scalars().all()

        deleted_credit_apps = 0
        if invoice_ids or credit_note_ids:
            deleted_credit_apps = (
                await session.execute(
                    delete(InvoiceCreditApplication).where(
                        or_(
                            InvoiceCreditApplication.invoice_id.in_(invoice_ids) if invoice_ids else False,
                            InvoiceCreditApplication.credit_note_id.in_(credit_note_ids) if credit_note_ids else False,
                        )
                    )
                )
            ).rowcount or 0

        deleted_qb_links = (await session.execute(delete(QbLink).where(QbLink.organization_id.in_(org_ids)))).rowcount or 0
        deleted_qb_logs = (await session.execute(delete(QbSyncLog).where(QbSyncLog.organization_id.in_(org_ids)))).rowcount or 0
        deleted_qb_mappings = (await session.execute(delete(QbReferenceMapping).where(QbReferenceMapping.organization_id.in_(org_ids)))).rowcount or 0
        deleted_qb_settings = (await session.execute(delete(QbSyncSettings).where(QbSyncSettings.organization_id.in_(org_ids)))).rowcount or 0
        deleted_qb_connections = (await session.execute(delete(QbConnection).where(QbConnection.organization_id.in_(org_ids)))).rowcount or 0

        deleted_invoices = (
            await session.execute(
                delete(Invoice).where(
                    Invoice.organization_id.in_(org_ids),
                    Invoice.invoice_number.like(SEED_INVOICE_PREFIX),
                )
            )
        ).rowcount or 0

        deleted_credit_notes = (
            await session.execute(
                delete(CreditNote).where(
                    CreditNote.organization_id.in_(org_ids),
                    CreditNote.credit_note_number.like(SEED_CREDIT_NOTE_PREFIX),
                )
            )
        ).rowcount or 0

        deleted_orgs = (await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))).rowcount or 0

        deleted_seed_users = (
            await session.execute(
                delete(User).where(
                    or_(
                        User.email.like(SEED_ADMIN_EMAIL_PREFIX),
                        User.email.like(SEED_CUSTOMER_EMAIL_PREFIX),
                    )
                )
            )
        ).rowcount or 0

        await session.commit()

        print("QuickBooks seed cleanup complete:")
        print(f"- organizations: {deleted_orgs}")
        print(f"- invoices: {deleted_invoices}")
        print(f"- credit_notes: {deleted_credit_notes}")
        print(f"- invoice_credit_applications: {deleted_credit_apps}")
        print(f"- qb_connections: {deleted_qb_connections}")
        print(f"- qb_links: {deleted_qb_links}")
        print(f"- qb_sync_logs: {deleted_qb_logs}")
        print(f"- qb_reference_mappings: {deleted_qb_mappings}")
        print(f"- qb_sync_settings: {deleted_qb_settings}")
        print(f"- seeded_users: {deleted_seed_users}")


async def main(org_id: str | None) -> None:
    org_ids = await _resolve_org_ids(org_id)
    await _clear(org_ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear QuickBooks minimal seed data")
    parser.add_argument("--org-id", dest="org_id", type=str, default=None, help="Specific organization id to clear")
    args = parser.parse_args()
    asyncio.run(main(args.org_id))
