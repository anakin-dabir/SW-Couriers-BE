"""Add document sharing support.

Changes:
- Creates `doc_ref_seq` sequence for DOC-{YEAR}-NNNNN references
- Adds `reference` column to `org_documents`
- Creates `org_document_shares` table
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_org_document_shares"
down_revision: str | None = "0039_vehicle_draft_published_by"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Sequence for OrgDocument references (DOC-{YEAR}-NNNNN) ───────────────
    op.execute("CREATE SEQUENCE IF NOT EXISTS doc_ref_seq START 1 INCREMENT 1")

    # ── Add reference column to org_documents ─────────────────────────────────
    op.add_column(
        "org_documents",
        sa.Column("reference", sa.String(25), nullable=True),
    )
    op.create_unique_constraint("uq_org_documents_reference", "org_documents", ["reference"])
    op.create_index("ix_org_documents_reference", "org_documents", ["reference"])

    # ── Create org_document_shares table ──────────────────────────────────────
    op.create_table(
        "org_document_shares",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),

        # Unique share token (32-byte hex secret)
        sa.Column("share_token", sa.String(64), nullable=False),

        # Recipients (JSONB array of email strings)
        sa.Column("recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=False),

        # Actor snapshot
        sa.Column("shared_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("shared_by_name", sa.String(255), nullable=True),

        # Document snapshot (denormalised for history display after soft-delete)
        sa.Column("document_title", sa.String(255), nullable=True),
        sa.Column("document_reference", sa.String(25), nullable=True),

        # Share settings
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("password_hash", sa.String(500), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),

        # Status & tracking
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),

        # Revocation
        sa.Column("revoked_at", sa.Date(), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("revoke_reason", sa.String(500), nullable=True),

        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["org_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shared_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_token", name="uq_org_document_shares_token"),
    )
    op.create_index("ix_org_document_shares_organization_id", "org_document_shares", ["organization_id"])
    op.create_index("ix_org_document_shares_document_id", "org_document_shares", ["document_id"])
    op.create_index("ix_org_document_shares_share_token", "org_document_shares", ["share_token"])
    op.create_index("ix_org_document_shares_status", "org_document_shares", ["status"])
    op.create_index("ix_org_document_shares_shared_by", "org_document_shares", ["shared_by"])


def downgrade() -> None:
    op.drop_index("ix_org_document_shares_shared_by", table_name="org_document_shares")
    op.drop_index("ix_org_document_shares_status", table_name="org_document_shares")
    op.drop_index("ix_org_document_shares_share_token", table_name="org_document_shares")
    op.drop_index("ix_org_document_shares_document_id", table_name="org_document_shares")
    op.drop_index("ix_org_document_shares_organization_id", table_name="org_document_shares")
    op.drop_table("org_document_shares")

    op.drop_index("ix_org_documents_reference", table_name="org_documents")
    op.drop_constraint("uq_org_documents_reference", "org_documents", type_="unique")
    op.drop_column("org_documents", "reference")

    op.execute("DROP SEQUENCE IF EXISTS doc_ref_seq")
