"""rename payment_methods to credit_cards

Revision ID: 0099_credit_cards
Revises: 0098_orders_pricing
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0099_credit_cards"
down_revision: str | None = "0098_orders_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("payment_methods", "credit_cards")
    op.execute("ALTER TABLE credit_cards RENAME CONSTRAINT payment_methods_pkey TO credit_cards_pkey")
    op.execute("ALTER TABLE credit_cards RENAME CONSTRAINT ck_payment_methods_owner TO ck_credit_cards_owner")
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT payment_methods_user_id_fkey TO credit_cards_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT payment_methods_organization_id_fkey TO credit_cards_organization_id_fkey"
    )
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT payment_methods_created_by_id_fkey TO credit_cards_created_by_id_fkey"
    )
    op.execute("ALTER INDEX ix_payment_methods_user_id RENAME TO ix_credit_cards_user_id")
    op.execute("ALTER INDEX ix_payment_methods_organization_id RENAME TO ix_credit_cards_organization_id")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_credit_cards_organization_id RENAME TO ix_payment_methods_organization_id")
    op.execute("ALTER INDEX ix_credit_cards_user_id RENAME TO ix_payment_methods_user_id")
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT credit_cards_created_by_id_fkey TO payment_methods_created_by_id_fkey"
    )
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT credit_cards_organization_id_fkey TO payment_methods_organization_id_fkey"
    )
    op.execute(
        "ALTER TABLE credit_cards RENAME CONSTRAINT credit_cards_user_id_fkey TO payment_methods_user_id_fkey"
    )
    op.execute("ALTER TABLE credit_cards RENAME CONSTRAINT ck_credit_cards_owner TO ck_payment_methods_owner")
    op.execute("ALTER TABLE credit_cards RENAME CONSTRAINT credit_cards_pkey TO payment_methods_pkey")
    op.rename_table("credit_cards", "payment_methods")
