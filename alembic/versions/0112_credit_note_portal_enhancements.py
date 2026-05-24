"""credit note portal enhancements

Revision ID: 0112_credit_note_update
Revises: 0111_refunds_management
Create Date: 2026-05-07
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0112_credit_note_update"
down_revision: str | None = "0111_refunds_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_notes", sa.Column("source_invoice_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.add_column("credit_notes", sa.Column("reason_category", sa.String(length=40), nullable=False, server_default="OTHER"))
    op.add_column("credit_notes", sa.Column("sent_to_email", sa.String(length=255), nullable=True))
    op.add_column("credit_notes", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_credit_notes_source_invoice_id", "credit_notes", "invoices", ["source_invoice_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_credit_notes_source_invoice_id", "credit_notes", ["source_invoice_id"])
    op.create_index("ix_credit_notes_reason_category", "credit_notes", ["reason_category"])

    op.create_table(
        "credit_note_pdf_artifacts",
        sa.Column("credit_note_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("template_version", sa.String(length=30), nullable=False),
        sa.Column("signature_hash", sa.String(length=64), nullable=False),
        sa.Column("pdf_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="GENERATING"),
        sa.Column("r2_file_key", sa.String(length=512), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["credit_note_id"], ["credit_notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credit_note_pdf_artifacts_credit_note_id", "credit_note_pdf_artifacts", ["credit_note_id"])
    op.create_index("ix_credit_note_pdf_artifacts_template_version", "credit_note_pdf_artifacts", ["template_version"])
    op.create_index("ix_credit_note_pdf_artifacts_signature_hash", "credit_note_pdf_artifacts", ["signature_hash"])
    op.create_index("ix_credit_note_pdf_artifacts_status", "credit_note_pdf_artifacts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_credit_note_pdf_artifacts_status", table_name="credit_note_pdf_artifacts")
    op.drop_index("ix_credit_note_pdf_artifacts_signature_hash", table_name="credit_note_pdf_artifacts")
    op.drop_index("ix_credit_note_pdf_artifacts_template_version", table_name="credit_note_pdf_artifacts")
    op.drop_index("ix_credit_note_pdf_artifacts_credit_note_id", table_name="credit_note_pdf_artifacts")
    op.drop_table("credit_note_pdf_artifacts")

    op.drop_index("ix_credit_notes_reason_category", table_name="credit_notes")
    op.drop_index("ix_credit_notes_source_invoice_id", table_name="credit_notes")
    op.drop_constraint("fk_credit_notes_source_invoice_id", "credit_notes", type_="foreignkey")
    op.drop_column("credit_notes", "sent_at")
    op.drop_column("credit_notes", "sent_to_email")
    op.drop_column("credit_notes", "reason_category")
    op.drop_column("credit_notes", "source_invoice_id")
