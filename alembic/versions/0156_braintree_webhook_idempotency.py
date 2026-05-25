from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0156_bt_webhook_idempotency"
down_revision: str | None = "0155_share_access_tokens_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_index(
        "uq_braintree_webhook_dispute_kind",
        "braintree_webhook_events",
        ["dispute_id", "webhook_kind"],
        unique=True,
        postgresql_where=text("dispute_id IS NOT NULL"),
    )

    op.create_index(
        "uq_braintree_webhook_txn_kind",
        "braintree_webhook_events",
        ["braintree_transaction_id", "webhook_kind"],
        unique=True,
        postgresql_where=text("braintree_transaction_id IS NOT NULL"),
    )

def downgrade() -> None:
    op.drop_index("uq_braintree_webhook_dispute_kind", table_name="braintree_webhook_events")
    op.drop_index("uq_braintree_webhook_txn_kind", table_name="braintree_webhook_events")