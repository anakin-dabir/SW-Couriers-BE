"""Add document access OTP and token tables.

Changes:
- Creates `doc_otps` table for OTP codes (rate-limited, 10-min expiry)
- Creates `doc_access_tokens` table for 1-hour document access grants
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_doc_access"
down_revision: str | None = "0040_org_document_shares"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── doc_otps ──────────────────────────────────────────────────────────────
    op.create_table(
        "doc_otps",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("otp_code", sa.String(6), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doc_otps_user_id", "doc_otps", ["user_id"])
    op.create_index("ix_doc_otps_expires_at", "doc_otps", ["expires_at"])

    # ── doc_access_tokens ─────────────────────────────────────────────────────
    op.create_table(
        "doc_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_doc_access_tokens_token"),
    )
    op.create_index("ix_doc_access_tokens_user_id", "doc_access_tokens", ["user_id"])
    op.create_index("ix_doc_access_tokens_token", "doc_access_tokens", ["token"])
    op.create_index("ix_doc_access_tokens_expires_at", "doc_access_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_doc_access_tokens_expires_at", table_name="doc_access_tokens")
    op.drop_index("ix_doc_access_tokens_token", table_name="doc_access_tokens")
    op.drop_index("ix_doc_access_tokens_user_id", table_name="doc_access_tokens")
    op.drop_table("doc_access_tokens")

    op.drop_index("ix_doc_otps_expires_at", table_name="doc_otps")
    op.drop_index("ix_doc_otps_user_id", table_name="doc_otps")
    op.drop_table("doc_otps")
