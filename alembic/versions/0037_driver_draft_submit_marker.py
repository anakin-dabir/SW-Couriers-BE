"""Add draft submit marker and allow draft drivers without user link.

Revision ID: 0037_draft_submit_marker
Revises: 0036_widen_org_document_type
Create Date: 2026-03-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Keep revision id <= 32 chars (alembic_version.version_num is VARCHAR(32)).
revision = "0037_draft_submit_marker"
down_revision = "0036_widen_org_document_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Driver onboarding: user must change password on first driver login.
    op.add_column(
        "users",
        sa.Column(
            "force_password_change",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("users", "force_password_change", server_default=None)

    op.add_column(
        "driver_drafts",
        sa.Column("is_submitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("idx_driver_drafts_is_submitted", "driver_drafts", ["is_submitted"])

    # Draft JSON payload (identity + form fields) stored until final submit.
    # Keep non-null default '{}'::jsonb so app code can safely read it.
    op.add_column(
        "driver_drafts",
        sa.Column(
            "draft_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute("UPDATE driver_drafts SET draft_data = '{}'::jsonb WHERE draft_data IS NULL")
    # Once backfilled, we can remove the server default (optional).
    op.alter_column("driver_drafts", "draft_data", server_default=None)

    op.alter_column(
        "drivers",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=False),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "drivers",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=False),
        nullable=False,
    )

    op.drop_index("idx_driver_drafts_is_submitted", table_name="driver_drafts")
    op.drop_column("driver_drafts", "is_submitted")

    op.drop_column("driver_drafts", "draft_data")

    op.drop_column("users", "force_password_change")

