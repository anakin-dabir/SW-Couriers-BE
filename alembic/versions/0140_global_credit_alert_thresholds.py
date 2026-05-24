"""Create global credit alert thresholds table and seed utilisation defaults.

Revision ID: 0140_global_ca_thresholds
Revises: 0139_credit_alert_ui_shape
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0140_global_ca_thresholds"
down_revision: str | None = "0139_credit_alert_ui_shape"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_TYPE = sa.Enum(
    "CREDIT_UTILISATION_MONITORING_WARNING",
    "CREDIT_UTILISATION_MONITORING_CRITICAL",
    "CREDIT_SCORE_DECREASE",
    "CREDIT_RATING_DOWNGRADE",
    "SCHEDULED_CREDIT_REVIEW_REMINDER",
    "REVIEW_OVERDUE",
    "LATE_PAYMENT_BEHAVIOUR",
    "ACCOUNT_ON_HOLD",
    "ACCOUNT_SUSPENDED",
    name="creditalerttype",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "global_credit_alert_thresholds",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("alert_type", _ALERT_TYPE, nullable=False),
        sa.Column("threshold_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_type", name="uq_global_credit_alert_thresholds_alert_type"),
    )

    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO global_credit_alert_thresholds (id, alert_type, threshold_pct)
        VALUES
            (gen_random_uuid(), 'CREDIT_UTILISATION_MONITORING_WARNING', 75.00),
            (gen_random_uuid(), 'CREDIT_UTILISATION_MONITORING_CRITICAL', 95.00)
    """))


def downgrade() -> None:
    op.drop_table("global_credit_alert_thresholds")
