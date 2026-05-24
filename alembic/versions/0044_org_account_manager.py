"""org_account_manager

Add account_manager_user_id FK to organizations table.
Tracks which admin user is assigned as account manager for the organisation.

Revision ID: 0044_org_account_manager
Revises: 0043_org_onboarded_by
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0044_org_account_manager"
down_revision = "0043_org_onboarded_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("account_manager_user_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_index(
        "ix_organizations_account_manager_user_id",
        "organizations",
        ["account_manager_user_id"],
    )
    op.create_foreign_key(
        "fk_organizations_account_manager_user_id",
        "organizations",
        "users",
        ["account_manager_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_organizations_account_manager_user_id", "organizations", type_="foreignkey")
    op.drop_index("ix_organizations_account_manager_user_id", table_name="organizations")
    op.drop_column("organizations", "account_manager_user_id")
