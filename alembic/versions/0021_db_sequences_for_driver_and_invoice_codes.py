"""Move driver/invoice code generation to DB sequences.

Revision ID: 0021_db_sequences_for_codes
Revises: 0020_normalize_driver_identity
Create Date: 2026-03-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_db_sequences_for_codes"
down_revision: str | None = "0020_normalize_driver_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Driver codes: DR-001, DR-002, ... (continues DR-1000+)
    op.execute("CREATE SEQUENCE IF NOT EXISTS driver_code_seq START WITH 1 INCREMENT BY 1")
    op.execute("""
        SELECT setval(
            'driver_code_seq',
            COALESCE(
                (
                    SELECT MAX((regexp_match(driver_code, '^DR-(\\d+)$'))[1]::bigint)
                    FROM drivers
                ),
                1
            ),
            COALESCE(
                (
                    SELECT MAX((regexp_match(driver_code, '^DR-(\\d+)$'))[1]::bigint)
                    FROM drivers
                ),
                0
            ) > 0
        )
        """)
    op.execute("ALTER TABLE drivers ALTER COLUMN driver_code SET DEFAULT 'DR-' || lpad(nextval('driver_code_seq')::text, 3, '0')")

    # Invoice numbers: INV-000001, INV-000002, ... (continues INV-1000000+)
    op.execute("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START WITH 1 INCREMENT BY 1")
    op.execute("""
        SELECT setval(
            'invoice_number_seq',
            COALESCE(
                (
                    SELECT MAX((regexp_match(invoice_number, '^INV-(\\d+)$'))[1]::bigint)
                    FROM invoices
                ),
                1
            ),
            COALESCE(
                (
                    SELECT MAX((regexp_match(invoice_number, '^INV-(\\d+)$'))[1]::bigint)
                    FROM invoices
                ),
                0
            ) > 0
        )
        """)
    op.execute("ALTER TABLE invoices ALTER COLUMN invoice_number SET DEFAULT 'INV-' || lpad(nextval('invoice_number_seq')::text, 6, '0')")


def downgrade() -> None:
    op.execute("ALTER TABLE drivers ALTER COLUMN driver_code DROP DEFAULT")
    op.execute("ALTER TABLE invoices ALTER COLUMN invoice_number DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS driver_code_seq")
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
