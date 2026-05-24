"""Align credit alert configs with one UI card per alert type.

Revision ID: 0139_credit_alert_ui_shape
Revises: 0138_account_statements
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0139_credit_alert_ui_shape"
down_revision: str | None = "0138_account_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_TYPE_OLD = sa.Enum(
    "CREDIT_UTILISATION_MONITORING",
    "CREDIT_LIMIT_BREACH",
    "CREDIT_SCORE_DROP",
    "CREDIT_RATING_DOWNGRADE",
    "SCHEDULED_CREDIT_REVIEW_REMINDER",
    "REVIEW_OVERDUE",
    "LATE_PAYMENT_BEHAVIOUR",
    "CREDIT_FACILITY_EXPIRY_REMINDER",
    "CREDIT_FACILITY_EXPIRED",
    "ACCOUNT_ON_HOLD",
    "ACCOUNT_SUSPENDED",
    name="creditalerttype",
    native_enum=False,
)

_ALERT_TYPE_NEW = sa.Enum(
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

_COOLDOWN_OLD = sa.Enum(
    "ONE_HOUR",
    "SEVEN_HOURS",
    "FOURTEEN_HOURS",
    "TWENTY_FOUR_HOURS",
    name="creditalertcooldownhours",
    native_enum=False,
)

_COOLDOWN_NEW = sa.Enum(
    "FIVE_MINUTES",
    "FIFTEEN_MINUTES",
    "THIRTY_MINUTES",
    "FORTY_FIVE_MINUTES",
    "ONE_HOUR",
    "SEVEN_HOURS",
    "FOURTEEN_HOURS",
    "TWENTY_FOUR_HOURS",
    "ONE_DAY",
    "TWO_DAYS",
    "THREE_DAYS",
    "FOUR_DAYS",
    "FIVE_DAYS",
    "SIX_DAYS",
    "SEVEN_DAYS",
    name="creditalertcooldownperiod",
    native_enum=False,
)


def upgrade() -> None:
    op.alter_column("org_credit_alert_configs", "alert_type", existing_type=_ALERT_TYPE_OLD, type_=_ALERT_TYPE_NEW, existing_nullable=False)
    op.alter_column("org_credit_alerts", "alert_type", existing_type=_ALERT_TYPE_OLD, type_=_ALERT_TYPE_NEW, existing_nullable=False)
    op.alter_column("org_credit_alert_configs", "cooldown_period", existing_type=_COOLDOWN_OLD, type_=_COOLDOWN_NEW, existing_nullable=False)

    op.add_column("org_credit_alert_configs", sa.Column("threshold_pct", sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("score_drop_points", sa.Integer(), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("reminder_days", sa.Integer(), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("late_payment_count", sa.Integer(), nullable=True))

    op.execute("""
        UPDATE org_credit_alert_configs
        SET alert_type = 'CREDIT_UTILISATION_MONITORING_WARNING',
            threshold_pct = warning_threshold_pct
        WHERE alert_type = 'CREDIT_UTILISATION_MONITORING'
    """)
    op.execute("""
        INSERT INTO org_credit_alert_configs (
            id, organization_id, alert_type, enabled, threshold_pct,
            cooldown_period, delivery_channel, auto_acknowledge, created_at, updated_at
        )
        SELECT md5(random()::text || clock_timestamp()::text)::uuid, organization_id, 'CREDIT_UTILISATION_MONITORING_CRITICAL', enabled,
               critical_threshold_pct, 'FIVE_MINUTES', delivery_channel, auto_acknowledge,
               created_at, updated_at
        FROM org_credit_alert_configs
        WHERE alert_type = 'CREDIT_UTILISATION_MONITORING_WARNING'
          AND critical_threshold_pct IS NOT NULL
    """)
    op.execute("""
        UPDATE org_credit_alert_configs
        SET alert_type = 'CREDIT_SCORE_DECREASE',
            score_drop_points = threshold_value_int
        WHERE alert_type = 'CREDIT_SCORE_DROP'
    """)
    op.execute("UPDATE org_credit_alert_configs SET reminder_days = threshold_days WHERE alert_type = 'SCHEDULED_CREDIT_REVIEW_REMINDER'")
    op.execute("UPDATE org_credit_alert_configs SET late_payment_count = threshold_value_int WHERE alert_type = 'LATE_PAYMENT_BEHAVIOUR'")
    op.execute("DELETE FROM org_credit_alert_configs WHERE alert_type IN ('CREDIT_LIMIT_BREACH', 'CREDIT_FACILITY_EXPIRY_REMINDER', 'CREDIT_FACILITY_EXPIRED')")

    op.execute("""
        UPDATE org_credit_alerts
        SET alert_type = CASE
            WHEN severity = 'CRITICAL' THEN 'CREDIT_UTILISATION_MONITORING_CRITICAL'
            ELSE 'CREDIT_UTILISATION_MONITORING_WARNING'
        END
        WHERE alert_type = 'CREDIT_UTILISATION_MONITORING'
    """)
    op.execute("UPDATE org_credit_alerts SET alert_type = 'CREDIT_SCORE_DECREASE' WHERE alert_type = 'CREDIT_SCORE_DROP'")
    op.execute("DELETE FROM org_credit_alerts WHERE alert_type IN ('CREDIT_LIMIT_BREACH', 'CREDIT_FACILITY_EXPIRY_REMINDER', 'CREDIT_FACILITY_EXPIRED')")

    op.drop_column("org_credit_alert_configs", "warning_threshold_pct")
    op.drop_column("org_credit_alert_configs", "critical_threshold_pct")
    op.drop_column("org_credit_alert_configs", "threshold_value_int")
    op.drop_column("org_credit_alert_configs", "threshold_days")


def downgrade() -> None:
    op.add_column("org_credit_alert_configs", sa.Column("threshold_days", sa.Integer(), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("threshold_value_int", sa.Integer(), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("critical_threshold_pct", sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column("org_credit_alert_configs", sa.Column("warning_threshold_pct", sa.Numeric(precision=5, scale=2), nullable=True))

    op.execute("""
        UPDATE org_credit_alert_configs
        SET alert_type = 'CREDIT_UTILISATION_MONITORING',
            warning_threshold_pct = threshold_pct
        WHERE alert_type = 'CREDIT_UTILISATION_MONITORING_WARNING'
    """)
    op.execute("""
        UPDATE org_credit_alert_configs AS warning
        SET critical_threshold_pct = critical.threshold_pct
        FROM org_credit_alert_configs AS critical
        WHERE warning.organization_id = critical.organization_id
          AND warning.alert_type = 'CREDIT_UTILISATION_MONITORING'
          AND critical.alert_type = 'CREDIT_UTILISATION_MONITORING_CRITICAL'
    """)
    op.execute("DELETE FROM org_credit_alert_configs WHERE alert_type = 'CREDIT_UTILISATION_MONITORING_CRITICAL'")
    op.execute("UPDATE org_credit_alert_configs SET alert_type = 'CREDIT_SCORE_DROP', threshold_value_int = score_drop_points WHERE alert_type = 'CREDIT_SCORE_DECREASE'")
    op.execute("UPDATE org_credit_alert_configs SET threshold_days = reminder_days WHERE alert_type = 'SCHEDULED_CREDIT_REVIEW_REMINDER'")
    op.execute("UPDATE org_credit_alert_configs SET threshold_value_int = late_payment_count WHERE alert_type = 'LATE_PAYMENT_BEHAVIOUR'")

    op.execute(
        "UPDATE org_credit_alerts SET alert_type = 'CREDIT_UTILISATION_MONITORING' WHERE alert_type IN ('CREDIT_UTILISATION_MONITORING_WARNING', 'CREDIT_UTILISATION_MONITORING_CRITICAL')"
    )
    op.execute("UPDATE org_credit_alerts SET alert_type = 'CREDIT_SCORE_DROP' WHERE alert_type = 'CREDIT_SCORE_DECREASE'")

    op.drop_column("org_credit_alert_configs", "late_payment_count")
    op.drop_column("org_credit_alert_configs", "reminder_days")
    op.drop_column("org_credit_alert_configs", "score_drop_points")
    op.drop_column("org_credit_alert_configs", "threshold_pct")

    op.alter_column("org_credit_alert_configs", "alert_type", existing_type=_ALERT_TYPE_NEW, type_=_ALERT_TYPE_OLD, existing_nullable=False)
    op.alter_column("org_credit_alerts", "alert_type", existing_type=_ALERT_TYPE_NEW, type_=_ALERT_TYPE_OLD, existing_nullable=False)
    op.alter_column("org_credit_alert_configs", "cooldown_period", existing_type=_COOLDOWN_NEW, type_=_COOLDOWN_OLD, existing_nullable=False)
