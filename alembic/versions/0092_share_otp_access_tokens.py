"""Replace password_hash on org_document_shares with otp_required flag; add share_otps and share_access_tokens tables.

Revision ID: 0092_share_otp_access_tokens
Revises: 0091_doc_access_tokens_hash
Create Date: 2026-04-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092_share_otp_access_tokens"
down_revision: str | None = "0091_doc_access_tokens_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Replace password_hash with otp_required on org_document_shares
    op.drop_column("org_document_shares", "password_hash")
    op.add_column(
        "org_document_shares",
        sa.Column("otp_required", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Short-lived OTPs for unauthenticated share-link recipients
    op.create_table(
        "share_otps",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("share_token", sa.String(64), nullable=False),
        sa.Column("otp_code", sa.String(6), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_otps_recipient_email", "share_otps", ["recipient_email"])
    op.create_index("ix_share_otps_share_token", "share_otps", ["share_token"])

    # 1-hour access grants issued after OTP verification
    op.create_table(
        "share_access_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("share_token", sa.String(64), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_share_access_tokens_recipient_email", "share_access_tokens", ["recipient_email"])
    op.create_index("ix_share_access_tokens_share_token", "share_access_tokens", ["share_token"])
    op.create_index("ix_share_access_tokens_token", "share_access_tokens", ["token"], unique=True)


def downgrade() -> None:
    op.drop_table("share_access_tokens")
    op.drop_table("share_otps")
    op.drop_column("org_document_shares", "otp_required")
    op.add_column(
        "org_document_shares",
        sa.Column("password_hash", sa.String(500), nullable=True),
    )
