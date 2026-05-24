"""org secondary/additional account managers

Revision ID: 0095_org_account_managers
Revises: 0094_billing_remittance
Create Date: 2026-04-28

Adds secondary_account_manager_user_id and additional_account_manager_user_id
FK columns to organizations. pricing_plans JSONB already exists — no DDL needed
for the new per-tier weight_margin_kg / price_per_kg_override fields.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0095_org_account_managers"
down_revision: Union[str, None] = "0094_billing_remittance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "secondary_account_manager_user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "additional_account_manager_user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_organizations_secondary_account_manager_user_id",
        "organizations",
        ["secondary_account_manager_user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_organizations_additional_account_manager_user_id",
        "organizations",
        ["additional_account_manager_user_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_additional_account_manager_user_id", table_name="organizations")
    op.drop_index("ix_organizations_secondary_account_manager_user_id", table_name="organizations")
    op.drop_column("organizations", "additional_account_manager_user_id")
    op.drop_column("organizations", "secondary_account_manager_user_id")
