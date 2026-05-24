"""Update invoice table and add tables for invoice PDF generation and credit notes.

Invoice-only migration (no org/suspension changes). Creates:
- credit_notes: credit memos (CN-NNNNNN); applied to invoices via invoice_credit_applications.
- invoice_credit_applications: links credit note to invoice; applied_amount reduces outstanding.
- invoice_events: append-only activity log (CREATED, FINALIZED, VOIDED, etc.).
- invoice_pdf_artifacts: PDF generation state and R2 key; one per (invoice, template_version, signature).

Alters invoices:
- Adds payment_status (NOT NULL, server_default='UNPAID') for existing rows.
- Widens status from VARCHAR(20) to String(30) (DRAFT | SENT).
- Creates index on payment_status.

Downgrade: drops new tables and column; reversible but destroys data in new tables.

Revision ID: 72bf17bcac4c
Revises: 9f1c2a3b4d5e
Create Date: 2026-03-19 12:06:13.178139

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72bf17bcac4c"
down_revision: str | None = "9f1c2a3b4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New tables (order: referenced first; credit_notes before invoice_credit_applications)
    op.create_table(
        "credit_notes",
        sa.Column("credit_note_number", sa.String(length=30), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("customer_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("total_credit_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_credit_notes_credit_note_number"), "credit_notes", ["credit_note_number"], unique=True)
    op.create_index(op.f("ix_credit_notes_customer_id"), "credit_notes", ["customer_id"], unique=False)
    op.create_index(op.f("ix_credit_notes_organization_id"), "credit_notes", ["organization_id"], unique=False)
    op.create_index(op.f("ix_credit_notes_status"), "credit_notes", ["status"], unique=False)
    op.create_table(
        "invoice_credit_applications",
        sa.Column("invoice_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("credit_note_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("applied_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("applied_at", sa.Date(), nullable=False),
        sa.Column("applied_by", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["applied_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["credit_note_id"], ["credit_notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_credit_applications_credit_note_id"), "invoice_credit_applications", ["credit_note_id"], unique=False)
    op.create_index(op.f("ix_invoice_credit_applications_invoice_id"), "invoice_credit_applications", ["invoice_id"], unique=False)
    op.create_table(
        "invoice_events",
        sa.Column("invoice_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_role", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_events_event_type"), "invoice_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_invoice_events_invoice_id"), "invoice_events", ["invoice_id"], unique=False)
    op.create_table(
        "invoice_pdf_artifacts",
        sa.Column("invoice_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("template_version", sa.String(length=30), nullable=False),
        sa.Column("signature_hash", sa.String(length=64), nullable=False),
        sa.Column("pdf_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("r2_file_key", sa.String(length=512), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_pdf_artifacts_invoice_id"), "invoice_pdf_artifacts", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_invoice_pdf_artifacts_signature_hash"), "invoice_pdf_artifacts", ["signature_hash"], unique=False)
    op.create_index(op.f("ix_invoice_pdf_artifacts_status"), "invoice_pdf_artifacts", ["status"], unique=False)
    op.create_index(op.f("ix_invoice_pdf_artifacts_template_version"), "invoice_pdf_artifacts", ["template_version"], unique=False)
    # ── Invoices: payment_status (required for existing rows) and status length ──
    op.add_column(
        "invoices",
        sa.Column(
            "payment_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'UNPAID'"),
        ),
    )
    op.alter_column("invoices", "status", existing_type=sa.VARCHAR(length=20), type_=sa.String(length=30), existing_nullable=False)
    op.create_index(op.f("ix_invoices_payment_status"), "invoices", ["payment_status"], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # Reverse order: invoices changes first, then drop child tables (pdf_artifacts, events, credit_applications, credit_notes)
    op.drop_index(op.f("ix_invoices_payment_status"), table_name="invoices")
    op.alter_column("invoices", "status", existing_type=sa.String(length=30), type_=sa.VARCHAR(length=20), existing_nullable=False)
    op.drop_column("invoices", "payment_status")
    op.drop_index(op.f("ix_invoice_pdf_artifacts_template_version"), table_name="invoice_pdf_artifacts")
    op.drop_index(op.f("ix_invoice_pdf_artifacts_status"), table_name="invoice_pdf_artifacts")
    op.drop_index(op.f("ix_invoice_pdf_artifacts_signature_hash"), table_name="invoice_pdf_artifacts")
    op.drop_index(op.f("ix_invoice_pdf_artifacts_invoice_id"), table_name="invoice_pdf_artifacts")
    op.drop_table("invoice_pdf_artifacts")
    op.drop_index(op.f("ix_invoice_events_invoice_id"), table_name="invoice_events")
    op.drop_index(op.f("ix_invoice_events_event_type"), table_name="invoice_events")
    op.drop_table("invoice_events")
    op.drop_index(op.f("ix_invoice_credit_applications_invoice_id"), table_name="invoice_credit_applications")
    op.drop_index(op.f("ix_invoice_credit_applications_credit_note_id"), table_name="invoice_credit_applications")
    op.drop_table("invoice_credit_applications")
    op.drop_index(op.f("ix_credit_notes_status"), table_name="credit_notes")
    op.drop_index(op.f("ix_credit_notes_organization_id"), table_name="credit_notes")
    op.drop_index(op.f("ix_credit_notes_customer_id"), table_name="credit_notes")
    op.drop_index(op.f("ix_credit_notes_credit_note_number"), table_name="credit_notes")
    op.drop_table("credit_notes")
    # ### end Alembic commands ###
