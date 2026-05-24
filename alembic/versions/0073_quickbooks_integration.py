"""quickbooks_integration

Revision ID: 0073_quickbooks_integration
Revises: 0072_payment_methods_tiered_disc
Create Date: 2026-04-13 12:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0073_quickbooks_integration"
down_revision: Union[str, None] = "0072_payment_methods_tiered_disc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "qb_sync_status",
            sa.String(length=20),
            nullable=False,
            server_default="NOT_SYNCED",
        ),
    )
    op.add_column("invoices", sa.Column("qb_last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_invoices_qb_sync_status", "invoices", ["qb_sync_status"], unique=False)

    op.create_table(
        "qb_connections",
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("realm_id", sa.String(length=64), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("connected_by_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["connected_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index("ix_qb_connections_connected_by_id", "qb_connections", ["connected_by_id"], unique=False)
    op.create_index("ix_qb_connections_is_active", "qb_connections", ["is_active"], unique=False)
    op.create_index("ix_qb_connections_organization_id", "qb_connections", ["organization_id"], unique=True)
    op.create_index("ix_qb_connections_realm_id", "qb_connections", ["realm_id"], unique=False)

    op.create_table(
        "qb_links",
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("local_entity_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("qb_entity_id", sa.String(length=100), nullable=False),
        sa.Column("sync_token", sa.String(length=50), nullable=True),
        sa.Column("sync_status", sa.String(length=20), nullable=False, server_default="NOT_SYNCED"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "local_entity_id",
            name="uq_qb_links_org_entity_local",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "qb_entity_id",
            name="uq_qb_links_org_entity_qb",
        ),
    )
    op.create_index("ix_qb_links_entity_type", "qb_links", ["entity_type"], unique=False)
    op.create_index("ix_qb_links_local_entity_id", "qb_links", ["local_entity_id"], unique=False)
    op.create_index("ix_qb_links_organization_id", "qb_links", ["organization_id"], unique=False)
    op.create_index("ix_qb_links_qb_entity_id", "qb_links", ["qb_entity_id"], unique=False)
    op.create_index("ix_qb_links_sync_status", "qb_links", ["sync_status"], unique=False)

    op.create_table(
        "qb_sync_logs",
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("local_entity_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qb_sync_logs_entity_type", "qb_sync_logs", ["entity_type"], unique=False)
    op.create_index("ix_qb_sync_logs_job_id", "qb_sync_logs", ["job_id"], unique=False)
    op.create_index("ix_qb_sync_logs_local_entity_id", "qb_sync_logs", ["local_entity_id"], unique=False)
    op.create_index("ix_qb_sync_logs_organization_id", "qb_sync_logs", ["organization_id"], unique=False)
    op.create_index("ix_qb_sync_logs_status", "qb_sync_logs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_qb_sync_logs_status", table_name="qb_sync_logs")
    op.drop_index("ix_qb_sync_logs_organization_id", table_name="qb_sync_logs")
    op.drop_index("ix_qb_sync_logs_local_entity_id", table_name="qb_sync_logs")
    op.drop_index("ix_qb_sync_logs_job_id", table_name="qb_sync_logs")
    op.drop_index("ix_qb_sync_logs_entity_type", table_name="qb_sync_logs")
    op.drop_table("qb_sync_logs")

    op.drop_index("ix_qb_links_sync_status", table_name="qb_links")
    op.drop_index("ix_qb_links_qb_entity_id", table_name="qb_links")
    op.drop_index("ix_qb_links_organization_id", table_name="qb_links")
    op.drop_index("ix_qb_links_local_entity_id", table_name="qb_links")
    op.drop_index("ix_qb_links_entity_type", table_name="qb_links")
    op.drop_table("qb_links")

    op.drop_index("ix_qb_connections_realm_id", table_name="qb_connections")
    op.drop_index("ix_qb_connections_organization_id", table_name="qb_connections")
    op.drop_index("ix_qb_connections_is_active", table_name="qb_connections")
    op.drop_index("ix_qb_connections_connected_by_id", table_name="qb_connections")
    op.drop_table("qb_connections")

    op.drop_index("ix_invoices_qb_sync_status", table_name="invoices")
    op.drop_column("invoices", "qb_last_sync_at")
    op.drop_column("invoices", "qb_sync_status")
