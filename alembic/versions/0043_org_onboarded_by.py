"""org_onboarded_by

Add onboarded_by_user_id FK to organizations table.
Tracks which admin user created (onboarded) the organisation.

Revision ID: 0043_org_onboarded_by
Revises: 0042_activity_log_client_context
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0043_org_onboarded_by"
down_revision = "0042_activity_log_client_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("onboarded_by_user_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_index(
        "ix_organizations_onboarded_by_user_id",
        "organizations",
        ["onboarded_by_user_id"],
    )
    op.create_foreign_key(
        "fk_organizations_onboarded_by_user_id",
        "organizations",
        "users",
        ["onboarded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_organizations_onboarded_by_user_id", "organizations", type_="foreignkey")
    op.drop_index("ix_organizations_onboarded_by_user_id", table_name="organizations")
    op.drop_column("organizations", "onboarded_by_user_id")
