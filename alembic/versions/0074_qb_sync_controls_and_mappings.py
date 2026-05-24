"""qb_sync_controls_and_mappings.

Revision ID: 0074_qb_sync_controls_and_mappings
Revises: 0073_quickbooks_integration
Create Date: 2026-04-15 09:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0074_qb_sync_controls"
down_revision: Union[str, None] = "0073_quickbooks_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if _has_table("qb_connections") and not _has_column("qb_connections", "last_refreshed_at"):
        op.add_column("qb_connections", sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    if _has_table("qb_connections") and not _has_column("qb_connections", "last_error_at"):
        op.add_column("qb_connections", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))

    if _has_table("qb_sync_logs") and not _has_column("qb_sync_logs", "related_qb_id"):
        op.add_column("qb_sync_logs", sa.Column("related_qb_id", sa.String(length=100), nullable=True))
    if _has_table("qb_sync_logs") and not _has_column("qb_sync_logs", "event_type"):
        op.add_column("qb_sync_logs", sa.Column("event_type", sa.String(length=50), nullable=True))
    if _has_table("qb_sync_logs") and not _has_index("qb_sync_logs", "ix_qb_sync_logs_related_qb_id"):
        op.create_index("ix_qb_sync_logs_related_qb_id", "qb_sync_logs", ["related_qb_id"], unique=False)
    if _has_table("qb_sync_logs") and not _has_index("qb_sync_logs", "ix_qb_sync_logs_event_type"):
        op.create_index("ix_qb_sync_logs_event_type", "qb_sync_logs", ["event_type"], unique=False)

    if not _has_table("qb_reference_mappings"):
        op.create_table(
            "qb_reference_mappings",
            sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("mapping_type", sa.String(length=30), nullable=False),
            sa.Column("local_key", sa.String(length=100), nullable=False),
            sa.Column("qb_ref_id", sa.String(length=100), nullable=False),
            sa.Column("qb_ref_name", sa.String(length=255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "mapping_type", "local_key", name="uq_qb_ref_mappings_org_type_key"),
        )
    if _has_table("qb_reference_mappings") and not _has_index("qb_reference_mappings", "ix_qb_reference_mappings_organization_id"):
        op.create_index("ix_qb_reference_mappings_organization_id", "qb_reference_mappings", ["organization_id"], unique=False)
    if _has_table("qb_reference_mappings") and not _has_index("qb_reference_mappings", "ix_qb_reference_mappings_mapping_type"):
        op.create_index("ix_qb_reference_mappings_mapping_type", "qb_reference_mappings", ["mapping_type"], unique=False)
    if _has_table("qb_reference_mappings") and not _has_index("qb_reference_mappings", "ix_qb_reference_mappings_local_key"):
        op.create_index("ix_qb_reference_mappings_local_key", "qb_reference_mappings", ["local_key"], unique=False)
    if _has_table("qb_reference_mappings") and not _has_index("qb_reference_mappings", "ix_qb_reference_mappings_is_active"):
        op.create_index("ix_qb_reference_mappings_is_active", "qb_reference_mappings", ["is_active"], unique=False)

    if not _has_table("qb_sync_settings"):
        op.create_table(
            "qb_sync_settings",
            sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("strict_mapping_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("sync_attachments", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("auto_retry_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("max_retry_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
            sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False, server_default=sa.text("45")),
            sa.Column("allow_force_reapply_credit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id"),
        )
    if _has_table("qb_sync_settings") and not _has_index("qb_sync_settings", "ix_qb_sync_settings_organization_id"):
        op.create_index("ix_qb_sync_settings_organization_id", "qb_sync_settings", ["organization_id"], unique=True)

    if _has_table("invoices") and not _has_column("invoices", "currency"):
        op.add_column("invoices", sa.Column("currency", sa.String(length=3), nullable=False, server_default="GBP"))
        op.alter_column("invoices", "currency", server_default=None)
    if _has_table("invoices") and not _has_column("invoices", "qb_payload_fingerprint"):
        op.add_column("invoices", sa.Column("qb_payload_fingerprint", sa.String(length=64), nullable=True))
    if _has_table("invoices") and not _has_index("invoices", "ix_invoices_qb_payload_fingerprint"):
        op.create_index("ix_invoices_qb_payload_fingerprint", "invoices", ["qb_payload_fingerprint"], unique=False)

    if _has_table("credit_notes") and not _has_column("credit_notes", "qb_payload_fingerprint"):
        op.add_column("credit_notes", sa.Column("qb_payload_fingerprint", sa.String(length=64), nullable=True))
    if _has_table("credit_notes") and not _has_index("credit_notes", "ix_credit_notes_qb_payload_fingerprint"):
        op.create_index("ix_credit_notes_qb_payload_fingerprint", "credit_notes", ["qb_payload_fingerprint"], unique=False)


def downgrade() -> None:
    if _has_table("qb_connections") and _has_column("qb_connections", "last_error_at"):
        op.drop_column("qb_connections", "last_error_at")
    if _has_table("qb_connections") and _has_column("qb_connections", "last_refreshed_at"):
        op.drop_column("qb_connections", "last_refreshed_at")

    if _has_table("qb_sync_logs") and _has_index("qb_sync_logs", "ix_qb_sync_logs_event_type"):
        op.drop_index("ix_qb_sync_logs_event_type", table_name="qb_sync_logs")
    if _has_table("qb_sync_logs") and _has_column("qb_sync_logs", "event_type"):
        op.drop_column("qb_sync_logs", "event_type")
    if _has_table("qb_sync_logs") and _has_index("qb_sync_logs", "ix_qb_sync_logs_related_qb_id"):
        op.drop_index("ix_qb_sync_logs_related_qb_id", table_name="qb_sync_logs")
    if _has_table("qb_sync_logs") and _has_column("qb_sync_logs", "related_qb_id"):
        op.drop_column("qb_sync_logs", "related_qb_id")

    if _has_table("credit_notes") and _has_index("credit_notes", "ix_credit_notes_qb_payload_fingerprint"):
        op.drop_index("ix_credit_notes_qb_payload_fingerprint", table_name="credit_notes")
    if _has_table("credit_notes") and _has_column("credit_notes", "qb_payload_fingerprint"):
        op.drop_column("credit_notes", "qb_payload_fingerprint")

    if _has_table("invoices") and _has_index("invoices", "ix_invoices_qb_payload_fingerprint"):
        op.drop_index("ix_invoices_qb_payload_fingerprint", table_name="invoices")
    if _has_table("invoices") and _has_column("invoices", "qb_payload_fingerprint"):
        op.drop_column("invoices", "qb_payload_fingerprint")
    if _has_table("invoices") and _has_column("invoices", "currency"):
        op.drop_column("invoices", "currency")

    if _has_table("qb_sync_settings") and _has_index("qb_sync_settings", "ix_qb_sync_settings_organization_id"):
        op.drop_index("ix_qb_sync_settings_organization_id", table_name="qb_sync_settings")
    if _has_table("qb_sync_settings"):
        op.drop_table("qb_sync_settings")

    if _has_table("qb_reference_mappings") and _has_index("qb_reference_mappings", "ix_qb_reference_mappings_is_active"):
        op.drop_index("ix_qb_reference_mappings_is_active", table_name="qb_reference_mappings")
    if _has_table("qb_reference_mappings") and _has_index("qb_reference_mappings", "ix_qb_reference_mappings_local_key"):
        op.drop_index("ix_qb_reference_mappings_local_key", table_name="qb_reference_mappings")
    if _has_table("qb_reference_mappings") and _has_index("qb_reference_mappings", "ix_qb_reference_mappings_mapping_type"):
        op.drop_index("ix_qb_reference_mappings_mapping_type", table_name="qb_reference_mappings")
    if _has_table("qb_reference_mappings") and _has_index("qb_reference_mappings", "ix_qb_reference_mappings_organization_id"):
        op.drop_index("ix_qb_reference_mappings_organization_id", table_name="qb_reference_mappings")
    if _has_table("qb_reference_mappings"):
        op.drop_table("qb_reference_mappings")
