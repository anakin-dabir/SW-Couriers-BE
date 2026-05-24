"""Add org_documents table for contract & agreement documents.

Each organisation can have multiple documents (MSA, SLA, Pricing, NDA, DPA).
Files are stored in Cloudflare R2; only the object key is persisted here.
Rows are soft-deleted via is_active=False.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_org_documents"
down_revision: str | None = "0028_violation_proofs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "org_documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),

        # Document metadata
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "document_type",
            sa.String(20),
            nullable=False,
            comment="MSA | SLA | PRICING | NDA | DPA",
        ),
        sa.Column("expiry_date", sa.Date(), nullable=False),

        # Storage
        sa.Column("r2_key", sa.String(500), nullable=False),

        # Uploader
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=False), nullable=True),

        # Soft delete
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),

        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_documents_organization_id", "org_documents", ["organization_id"])
    op.create_index("ix_org_documents_uploaded_by", "org_documents", ["uploaded_by"])
    op.create_index(
        "ix_org_documents_org_active",
        "org_documents",
        ["organization_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_documents_org_active", table_name="org_documents")
    op.drop_index("ix_org_documents_uploaded_by", table_name="org_documents")
    op.drop_index("ix_org_documents_organization_id", table_name="org_documents")
    op.drop_table("org_documents")
