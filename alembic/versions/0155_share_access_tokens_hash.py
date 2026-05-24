"""Store SHA-256 fingerprint for share access tokens; drop plaintext token column.

Revision ID: 0155_share_access_tokens_hash
Revises: 0154_client_inactivity
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0155_share_access_tokens_hash"
down_revision: str | None = "0154_client_inactivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.add_column(
        "share_access_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE share_access_tokens SET token_hash = encode(digest(token, 'sha256'), 'hex') "
            "WHERE token_hash IS NULL"
        )
    )
    op.alter_column("share_access_tokens", "token_hash", nullable=False)
    op.drop_index("ix_share_access_tokens_token", table_name="share_access_tokens")
    op.drop_constraint("share_access_tokens_token_key", "share_access_tokens", type_="unique")
    op.drop_column("share_access_tokens", "token")
    op.create_index(
        "ix_share_access_tokens_token_hash",
        "share_access_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM share_access_tokens"))
    op.drop_index("ix_share_access_tokens_token_hash", table_name="share_access_tokens")
    op.drop_column("share_access_tokens", "token_hash")
    op.add_column(
        "share_access_tokens",
        sa.Column("token", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_share_access_tokens_token", "share_access_tokens", ["token"], unique=True)
