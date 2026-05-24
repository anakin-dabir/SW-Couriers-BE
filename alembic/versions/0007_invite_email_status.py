"""Add invite email delivery tracking (for Arq worker + DLQ awareness).

Revision ID: 0007_invite_email_status
Revises: 0006_invites_flow_b
Create Date: 2026-02-23

- email_status: pending | sent | failed (worker updates; failed = gave up or DLQ)
- email_sent_at: set when worker successfully sends
- email_last_error: last error message when status=failed (Arq handles retries; we track final failure)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_invite_email_status"
down_revision: str = "0006_invites_flow_b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invites",
        sa.Column("email_status", sa.String(length=20), nullable=False, server_default="pending"),
    )
    op.add_column(
        "invites",
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "invites",
        sa.Column("email_last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_invites_email_status", "invites", ["email_status"])


def downgrade() -> None:
    op.drop_index("ix_invites_email_status", "invites")
    op.drop_column("invites", "email_last_error")
    op.drop_column("invites", "email_sent_at")
    op.drop_column("invites", "email_status")
