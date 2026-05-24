"""add braintree webhook and fee profile tables

Revision ID: 0110_bt_webhooks_and_fee
Revises: 0109_routes_navigation_polyline
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0110_bt_webhooks_and_fee"
down_revision: str | None = "0109_routes_navigation_polyline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_cards", sa.Column("country_of_issuance", sa.String(length=2), nullable=True))
    op.drop_table("pricing_rules", if_exists=True)

    op.create_table(
        "braintree_fee_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("card_brand", sa.String(length=30), nullable=False),
        sa.Column("rate_percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("fixed_fee", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("cross_border_percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("scheme_currency_percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("exotic_currency_percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("chargeback_fee", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market", "currency", "card_brand", name="uq_braintree_fee_market_currency_brand"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO braintree_fee_profiles
            (id, market, currency, card_brand, rate_percent, fixed_fee, cross_border_percent, scheme_currency_percent, exotic_currency_percent, chargeback_fee, is_active)
            VALUES
            (gen_random_uuid(), 'UK', 'GBP', 'STANDARD', 1.9000, 0.20, 1.0000, 1.5000, 3.0000, 20.00, true),
            (gen_random_uuid(), 'UK', 'GBP', 'AMEX', 2.4000, 0.20, 1.0000, 1.5000, 3.0000, 20.00, true)
            """
        )
    )

    op.create_table(
        "braintree_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("webhook_kind", sa.String(length=60), nullable=False),
        sa.Column("braintree_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("dispute_id", sa.String(length=100), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_braintree_webhook_events_webhook_kind", "braintree_webhook_events", ["webhook_kind"], unique=False)
    op.create_index("ix_braintree_webhook_events_braintree_transaction_id", "braintree_webhook_events", ["braintree_transaction_id"], unique=False)
    op.create_index("ix_braintree_webhook_events_dispute_id", "braintree_webhook_events", ["dispute_id"], unique=False)

def downgrade() -> None:
    op.drop_index("ix_braintree_webhook_events_dispute_id", table_name="braintree_webhook_events")
    op.drop_index("ix_braintree_webhook_events_braintree_transaction_id", table_name="braintree_webhook_events")
    op.drop_index("ix_braintree_webhook_events_webhook_kind", table_name="braintree_webhook_events")
    op.drop_table("braintree_webhook_events")

    op.drop_table("braintree_fee_profiles")
    op.create_table(
        "pricing_rules",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("rule_type", sa.String(length=30), nullable=False),
        sa.Column("service_tier", sa.String(length=30), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_percentage", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.drop_column("credit_cards", "country_of_issuance", if_exists=True)
