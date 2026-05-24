"""Credit note reversal invoice link; invoice billing contact email."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0149_billing_enhancements"
down_revision: str | None = "0148_orders_contact_user_id"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "credit_notes",
        sa.Column(
            "reversal_invoice_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_credit_notes_reversal_invoice_id",
        "credit_notes",
        ["reversal_invoice_id"],
        unique=True,
    )
    op.add_column(
        "invoices",
        sa.Column("billing_contact_email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "billing_contact_email")
    op.drop_index("ix_credit_notes_reversal_invoice_id", table_name="credit_notes")
    op.drop_column("credit_notes", "reversal_invoice_id")
