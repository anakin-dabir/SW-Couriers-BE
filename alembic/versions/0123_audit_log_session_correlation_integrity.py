"""add session, correlation, and integrity hash columns to audit_log

Adds:
- session_id (FK to sessions.session_id, SET NULL on delete)
- correlation_id (uuid, indexed) to group audits produced in the same request
- integrity_hash + prev_hash (sha-256 hex) to enable forward-only tamper-evident chain

Existing rows keep NULL for all new columns (forward-only adoption).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0123_audit_log_session_corr_int"
down_revision: str | None = "0122_vehicle_svc_mileage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("session_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("correlation_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("integrity_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
    )

    op.create_index(
        "ix_audit_log_session_id",
        "audit_log",
        ["session_id"],
    )
    op.create_index(
        "ix_audit_log_correlation_id",
        "audit_log",
        ["correlation_id"],
    )

    op.create_foreign_key(
        "fk_audit_log_session_id_sessions",
        "audit_log",
        "sessions",
        ["session_id"],
        ["session_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_log_session_id_sessions", "audit_log", type_="foreignkey")
    op.drop_index("ix_audit_log_correlation_id", table_name="audit_log")
    op.drop_index("ix_audit_log_session_id", table_name="audit_log")
    op.drop_column("audit_log", "prev_hash")
    op.drop_column("audit_log", "integrity_hash")
    op.drop_column("audit_log", "correlation_id")
    op.drop_column("audit_log", "session_id")
