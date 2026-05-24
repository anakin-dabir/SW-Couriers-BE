"""users_title_position

Add title (enum) and position_role (free-text) columns to the users table.
These fields support the admin invitation flow — title is collected in Step 1
of the Create New Admin wizard; position_role stores the job title string.

Revision ID: 0048_users_title_position
Revises: 0047_vehicle_deletion_log
Create Date: 2026-04-02
"""
    
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_users_title_position"
down_revision = "0047_vehicle_deletion_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("title", sa.String(10), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("position_role", sa.String(150), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "position_role")
    op.drop_column("users", "title")
