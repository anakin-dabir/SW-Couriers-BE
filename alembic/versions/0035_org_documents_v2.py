"""Expand org_documents table and add org_document_activities.

Changes:
- org_documents: add status, category, issuing_authority, issue_date,
  description (text), confidentiality_level, tags (jsonb),
  uploaded_by_email; make expiry_date nullable.
- New table: org_document_activities (audit log for document actions).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_org_documents_v2"
down_revision: str | None = "0034_nullable_vehicle_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Expand org_documents ──────────────────────────────────────────────────
    op.add_column(
        "org_documents",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="ACTIVE",
            comment="ACTIVE | EXPIRED | EXPIRING_SOON",
        ),
    )
    op.add_column(
        "org_documents",
        sa.Column(
            "category",
            sa.String(20),
            nullable=True,
            comment="CONTRACTS | INTERNAL | CLIENT_UPLOADS",
        ),
    )
    op.add_column(
        "org_documents",
        sa.Column("issuing_authority", sa.String(255), nullable=True),
    )
    op.add_column(
        "org_documents",
        sa.Column("issue_date", sa.Date(), nullable=True),
    )
    # Make expiry_date nullable (was NOT NULL — simple-form still provides it,
    # full Document Operations form may omit it for open-ended documents).
    op.alter_column("org_documents", "expiry_date", existing_type=sa.Date(), nullable=True)
    op.add_column(
        "org_documents",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "org_documents",
        sa.Column(
            "confidentiality_level",
            sa.String(25),
            nullable=True,
            comment="PUBLIC | INTERNAL | CONFIDENTIAL | STRICTLY_CONFIDENTIAL",
        ),
    )
    op.add_column(
        "org_documents",
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "org_documents",
        sa.Column("uploaded_by_email", sa.String(255), nullable=True),
    )

    op.create_index("ix_org_documents_status", "org_documents", ["status"])

    # ── Create org_document_activities ────────────────────────────────────────
    op.create_table(
        "org_document_activities",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "activity_type",
            sa.String(20),
            nullable=False,
            comment="UPLOADED | DOWNLOADED | EXPIRED | DELETED",
        ),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("actor_role", sa.String(50), nullable=True),
        sa.Column("document_name", sa.String(255), nullable=True),
        sa.Column("details", sa.String(500), nullable=True),
        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["org_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_document_activities_organization_id", "org_document_activities", ["organization_id"])
    op.create_index("ix_org_document_activities_document_id", "org_document_activities", ["document_id"])
    op.create_index("ix_org_document_activities_activity_type", "org_document_activities", ["activity_type"])
    op.create_index(
        "ix_org_document_activities_org_created",
        "org_document_activities",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_document_activities_org_created", table_name="org_document_activities")
    op.drop_index("ix_org_document_activities_activity_type", table_name="org_document_activities")
    op.drop_index("ix_org_document_activities_document_id", table_name="org_document_activities")
    op.drop_index("ix_org_document_activities_organization_id", table_name="org_document_activities")
    op.drop_table("org_document_activities")

    op.drop_index("ix_org_documents_status", table_name="org_documents")
    op.drop_column("org_documents", "uploaded_by_email")
    op.drop_column("org_documents", "tags")
    op.drop_column("org_documents", "confidentiality_level")
    op.drop_column("org_documents", "description")
    op.alter_column("org_documents", "expiry_date", existing_type=sa.Date(), nullable=False)
    op.drop_column("org_documents", "issue_date")
    op.drop_column("org_documents", "issuing_authority")
    op.drop_column("org_documents", "category")
    op.drop_column("org_documents", "status")
