"""Update payment_methods for dual ownership and billing details.

Changes from original payment_methods table in da8153402035:
- user_id becomes nullable (supports org-owned cards)
- add organization_id FK + index
- add created_by_id FK
- add braintree_customer_id
- add billing address fields
- add owner check constraint (exactly one of organization_id or user_id)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_payment_billing_address"
down_revision: str | None = "0054_org_logo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "payment_methods",
        "user_id",
        existing_type=sa.UUID(as_uuid=False),
        nullable=True,
    )

    op.add_column("payment_methods", sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=True))
    op.create_foreign_key(
        "payment_methods_organization_id_fkey",
        "payment_methods",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_payment_methods_organization_id", "payment_methods", ["organization_id"])

    op.add_column("payment_methods", sa.Column("created_by_id", sa.UUID(as_uuid=False), nullable=True))
    op.create_foreign_key(
        "payment_methods_created_by_id_fkey",
        "payment_methods",
        "users",
        ["created_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("payment_methods", sa.Column("braintree_customer_id", sa.String(255), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_building", sa.String(255), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_line1", sa.String(255), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_line2", sa.String(255), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_city", sa.String(100), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_county", sa.String(100), nullable=True))
    op.add_column("payment_methods", sa.Column("billing_postcode", sa.String(20), nullable=True))

    op.create_check_constraint(
        "ck_payment_methods_owner",
        "payment_methods",
        "(organization_id IS NOT NULL AND user_id IS NULL) OR "
        "(organization_id IS NULL AND user_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payment_methods_owner", "payment_methods", type_="check")

    op.drop_column("payment_methods", "billing_postcode")
    op.drop_column("payment_methods", "billing_county")
    op.drop_column("payment_methods", "billing_city")
    op.drop_column("payment_methods", "billing_line2")
    op.drop_column("payment_methods", "billing_line1")
    op.drop_column("payment_methods", "billing_building")
    op.drop_column("payment_methods", "braintree_customer_id")

    op.drop_constraint("payment_methods_created_by_id_fkey", "payment_methods", type_="foreignkey")
    op.drop_column("payment_methods", "created_by_id")

    op.drop_index("ix_payment_methods_organization_id", table_name="payment_methods")
    op.drop_constraint("payment_methods_organization_id_fkey", "payment_methods", type_="foreignkey")
    op.drop_column("payment_methods", "organization_id")

    op.alter_column(
        "payment_methods",
        "user_id",
        existing_type=sa.UUID(as_uuid=False),
        nullable=False,
    )
