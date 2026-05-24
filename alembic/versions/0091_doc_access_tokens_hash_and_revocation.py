"""Store SHA-256 fingerprint for doc access tokens; add revocation timestamp.

Revision ID: 0091_doc_access_tokens_hash
Revises: 0090_multi_susp_rules_scope_type
Create Date: 2026-04-23

Replaces plaintext `token` with `token_hash` (hex digest of UTF-8 raw token).
Adds `revoked_at` for invalidation without deleting rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0091_doc_access_tokens_hash"
down_revision: str | None = "0090_multi_susp_rules_scope_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.add_column(
        "doc_access_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "doc_access_tokens",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE doc_access_tokens SET token_hash = encode(digest(token, 'sha256'), 'hex') "
            "WHERE token_hash IS NULL"
        )
    )
    op.alter_column("doc_access_tokens", "token_hash", nullable=False)
    op.drop_index("ix_doc_access_tokens_token", table_name="doc_access_tokens")

    op.drop_column("doc_access_tokens", "token")
    op.create_index("ix_doc_access_tokens_token_hash", "doc_access_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM doc_access_tokens"))
    op.drop_index("ix_doc_access_tokens_token_hash", table_name="doc_access_tokens")
    op.drop_column("doc_access_tokens", "revoked_at")
    op.drop_column("doc_access_tokens", "token_hash")
    op.add_column(
        "doc_access_tokens",
        sa.Column("token", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_doc_access_tokens_token", "doc_access_tokens", ["token"], unique=False)
    op.create_unique_constraint("uq_doc_access_tokens_token", "doc_access_tokens", ["token"])
