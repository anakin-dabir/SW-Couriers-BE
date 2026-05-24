"""add missing quickbooks sync columns to credit_notes

Revision ID: 0127_credit_notes_qb_sync
Revises: 0126_create_crews_rca
Create Date: 2026-05-14 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0127_credit_notes_qb_sync"
down_revision: str | None = "0126_create_crews_rca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    if _has_table("credit_notes") and not _has_column("credit_notes", "qb_sync_status"):
        op.add_column(
            "credit_notes",
            sa.Column("qb_sync_status", sa.String(length=20), nullable=False, server_default="NOT_SYNCED"),
        )

    if _has_table("credit_notes") and not _has_column("credit_notes", "qb_last_sync_at"):
        op.add_column(
            "credit_notes",
            sa.Column("qb_last_sync_at", sa.DateTime(timezone=True), nullable=True),
        )

    if _has_table("credit_notes") and not _has_index("credit_notes", "ix_credit_notes_qb_sync_status"):
        op.create_index("ix_credit_notes_qb_sync_status", "credit_notes", ["qb_sync_status"], unique=False)


def downgrade() -> None:
    if _has_table("credit_notes") and _has_index("credit_notes", "ix_credit_notes_qb_sync_status"):
        op.drop_index("ix_credit_notes_qb_sync_status", table_name="credit_notes")

    if _has_table("credit_notes") and _has_column("credit_notes", "qb_last_sync_at"):
        op.drop_column("credit_notes", "qb_last_sync_at")

    if _has_table("credit_notes") and _has_column("credit_notes", "qb_sync_status"):
        op.drop_column("credit_notes", "qb_sync_status")
