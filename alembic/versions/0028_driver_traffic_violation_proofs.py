"""violation_proofs

Create a separate table for traffic violation proof files.

DB is empty in this environment, so we remove the legacy single proof_key column.

Revision ID: 0028_violation_proofs
Revises: 0027_driver_country_state
Create Date: 2026-03-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# IMPORTANT: alembic_version.version_num is VARCHAR(32) in this project.
# Keep revision ids <= 32 chars to avoid truncation errors.
revision = "0028_violation_proofs"
down_revision = "0027_driver_country_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "driver_traffic_violation_proofs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "violation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("driver_traffic_violations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_driver_traffic_violation_proofs_violation_id",
        "driver_traffic_violation_proofs",
        ["violation_id"],
    )
    op.drop_column("driver_traffic_violations", "proof_key")


def downgrade() -> None:
    op.add_column("driver_traffic_violations", sa.Column("proof_key", sa.String(length=255), nullable=True))
    op.drop_index("ix_driver_traffic_violation_proofs_violation_id", table_name="driver_traffic_violation_proofs")
    op.drop_table("driver_traffic_violation_proofs")

