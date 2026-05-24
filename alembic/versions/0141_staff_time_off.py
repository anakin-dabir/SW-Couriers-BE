"""Staff/admin time off for Team Availability (My Leaves).

Revision ID: 0141_staff_time_off
Revises: 0140_global_ca_thresholds
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0141_staff_time_off"
down_revision: str | None = "0140_global_ca_thresholds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_time_off",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_staff_time_off_user_id", "staff_time_off", ["user_id"])
    op.create_index("ix_staff_time_off_start_date", "staff_time_off", ["start_date"])
    op.create_index("ix_staff_time_off_end_date", "staff_time_off", ["end_date"])
    op.create_index(
        "ix_staff_time_off_user_dates",
        "staff_time_off",
        ["user_id", "start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_staff_time_off_user_dates", table_name="staff_time_off")
    op.drop_index("ix_staff_time_off_end_date", table_name="staff_time_off")
    op.drop_index("ix_staff_time_off_start_date", table_name="staff_time_off")
    op.drop_index("ix_staff_time_off_user_id", table_name="staff_time_off")
    op.drop_table("staff_time_off")
