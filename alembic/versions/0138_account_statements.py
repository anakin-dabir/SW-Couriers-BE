"""Account statements, schedules, and delivery events.

Revision ID: 0138_account_statements
Revises: 0137_seed_dropdown_configs
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0138_account_statements"
down_revision: str | None = "0137_seed_dropdown_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SEQUENCE IF NOT EXISTS account_statement_number_seq START 1"))

    op.create_table(
        "account_statements",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "statement_number",
            sa.String(30),
            nullable=False,
            server_default=sa.text("'ST-' || lpad(nextval('account_statement_number_seq')::text, 6, '0')"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("closing_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_invoice_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_paid", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_unpaid", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_overdue", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("aging_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("include_line_item_detail", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_credit_notes", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("include_payment_history", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("pdf_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("pdf_r2_key", sa.String(512), nullable=True),
        sa.Column("pdf_template_version", sa.String(30), nullable=False, server_default="v1"),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(100), nullable=True),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_type", sa.String(20), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("ix_account_statements_statement_number", "account_statements", ["statement_number"], unique=True)
    op.create_index("ix_account_statements_org_created", "account_statements", ["organization_id", "created_at"])
    op.create_index("ix_account_statements_org_period", "account_statements", ["organization_id", "period_start", "period_end"])
    op.create_index("ix_account_statements_org_signature", "account_statements", ["organization_id", "content_signature"])

    op.create_table(
        "account_statement_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("frequency", sa.String(30), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/London"),
        sa.Column("include_line_item_detail", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_credit_notes", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("include_payment_history", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_cron", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("ix_account_statement_schedules_org_status", "account_statement_schedules", ["organization_id", "status"])
    op.create_index("ix_account_statement_schedules_next_run", "account_statement_schedules", ["next_run_at"])

    op.create_table(
        "account_statement_delivery_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("statement_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("account_statements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_account_statement_delivery_statement", "account_statement_delivery_events", ["statement_id"])


def downgrade() -> None:
    op.drop_table("account_statement_delivery_events")
    op.drop_table("account_statement_schedules")
    op.drop_table("account_statements")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS account_statement_number_seq"))
