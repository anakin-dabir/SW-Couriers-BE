"""suspension_rules_refactor.

Revision ID: 0077_suspension_rules_refactor
Revises: 0076_backfill_pricing_plans_base
Create Date: 2026-04-17 11:00:00.000000

Tables introduced/updated by this migration:
- suspension_rule_sets: source-of-truth rules per scope (GLOBAL/ORG) and rule_type.
- suspension_rule_conditions: normalized ordered condition rows for each ruleset.
- suspension_evaluation_runs: daily evaluator run telemetry/counters.
- suspension_notification_audit: delivery outcomes for notifications triggered by activity rows.
- payment_risk_events: raw payment-risk signals used to compute card/cash metrics.
- suspension_activity (existing): enriched with v2 evaluation context fields.
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0077_suspension_rules_refactor"
down_revision: Union[str, None] = "0076_backfill_pricing_plans_base"
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
    # Canonical rules configuration table:
    # one row per (scope_type, scope_org_id, rule_type), plus action toggles.
    if not _has_table("suspension_rule_sets"):
        op.create_table(
            "suspension_rule_sets",
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("condition_summary", sa.String(length=255), nullable=True),
            sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="GLOBAL"),
            sa.Column("scope_org_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
            sa.Column("rule_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("auto_suspension_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("pause_new_bookings", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("restrict_portal_login", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("notify_finance_team", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("notify_account_manager", sa.Boolean(), nullable=False, server_default="false"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_susp_rule_sets_name"),
            sa.UniqueConstraint("scope_type", "scope_org_id", "rule_type", name="uq_susp_rule_sets_scope_org_type"),
        )
        op.create_index("ix_suspension_rule_sets_scope_org_id", "suspension_rule_sets", ["scope_org_id"])

    # Normalized condition rows for each rule set:
    # stores position + connector + condition_type + threshold.
    if not _has_table("suspension_rule_conditions"):
        op.create_table(
            "suspension_rule_conditions",
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("rule_set_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("suspension_rule_sets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("connector", sa.String(length=8), nullable=True),
            sa.Column("condition_type", sa.String(length=64), nullable=False),
            sa.Column("threshold_value", sa.Numeric(12, 2), nullable=False),
            sa.Column("unit", sa.String(length=24), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("rule_set_id", "condition_type", name="uq_susp_rule_cond_unique_type_per_rule"),
            sa.UniqueConstraint("rule_set_id", "position", name="uq_susp_rule_cond_unique_position_per_rule"),
        )
        op.create_index("ix_suspension_rule_conditions_rule_set_id", "suspension_rule_conditions", ["rule_set_id"])

    # Runtime evaluator run log:
    # one row per scheduled evaluation execution with aggregate counters.
    if not _has_table("suspension_evaluation_runs"):
        op.create_table(
            "suspension_evaluation_runs",
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("run_date", sa.String(length=10), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
            sa.Column("evaluated_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("warned_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("suspended_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_suspension_evaluation_runs_run_date", "suspension_evaluation_runs", ["run_date"])

    # Notification delivery audit:
    # records queued/sent/failed external notifications for each activity row.
    if not _has_table("suspension_notification_audit"):
        op.create_table(
            "suspension_notification_audit",
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("activity_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("suspension_activity.id", ondelete="CASCADE"), nullable=False),
            sa.Column("channel", sa.String(length=16), nullable=False, server_default="EMAIL"),
            sa.Column("recipient", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="QUEUED"),
            sa.Column("external_id", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.String(length=500), nullable=True),
            sa.Column("rule_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_suspension_notification_audit_activity_id", "suspension_notification_audit", ["activity_id"])
    elif _has_column("suspension_notification_audit", "metadata") and not _has_column("suspension_notification_audit", "rule_metadata"):
        op.alter_column("suspension_notification_audit", "metadata", new_column_name="rule_metadata")

    # Payment risk event ledger:
    # persisted risk signals used for metrics (e.g. PAYMENT_FAILED/CHARGEBACK).
    if not _has_table("payment_risk_events"):
        op.create_table(
            "payment_risk_events",
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("customer_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True),
            sa.Column("payment_model", sa.String(length=32), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("occurred_on", sa.Date(), nullable=False),
            sa.Column("rule_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_payment_risk_events_organization_id", "payment_risk_events", ["organization_id"])
        op.create_index("ix_payment_risk_events_event_type", "payment_risk_events", ["event_type"])
        op.create_index("ix_payment_risk_events_occurred_on", "payment_risk_events", ["occurred_on"])
    elif _has_column("payment_risk_events", "metadata") and not _has_column("payment_risk_events", "rule_metadata"):
        op.alter_column("payment_risk_events", "metadata", new_column_name="rule_metadata")

    # Enrich existing suspension_activity table with v2 evaluator context:
    # org/rule typing, run linkage, expression evaluation details, notification status.
    if _has_table("suspension_activity"):
        columns = [
            ("organization_id", postgresql.UUID(as_uuid=False), {"nullable": True}),
            ("rule_type", sa.String(length=32), {"nullable": True}),
            ("payment_model", sa.String(length=32), {"nullable": True}),
            ("run_id", postgresql.UUID(as_uuid=False), {"nullable": True}),
            ("evaluated_expression", sa.Text(), {"nullable": True}),
            ("group_results", postgresql.JSONB(astext_type=sa.Text()), {"nullable": True}),
            ("final_result", sa.Boolean(), {"nullable": True}),
            ("notification_status", sa.String(length=32), {"nullable": True}),
        ]
        for name, typ, kwargs in columns:
            if not _has_column("suspension_activity", name):
                op.add_column("suspension_activity", sa.Column(name, typ, **kwargs))

        if not _has_index("suspension_activity", "ix_suspension_activity_organization_id"):
            op.create_index("ix_suspension_activity_organization_id", "suspension_activity", ["organization_id"])
        if not _has_index("suspension_activity", "ix_suspension_activity_rule_type"):
            op.create_index("ix_suspension_activity_rule_type", "suspension_activity", ["rule_type"])
        if not _has_index("suspension_activity", "ix_suspension_activity_payment_model"):
            op.create_index("ix_suspension_activity_payment_model", "suspension_activity", ["payment_model"])
        if not _has_index("suspension_activity", "ix_suspension_activity_run_id"):
            op.create_index("ix_suspension_activity_run_id", "suspension_activity", ["run_id"])

        fk_names = [fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("suspension_activity")]
        if "fk_suspension_activity_run_id" not in fk_names:
            op.create_foreign_key(
                "fk_suspension_activity_run_id",
                "suspension_activity",
                "suspension_evaluation_runs",
                ["run_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "fk_suspension_activity_org_id" not in fk_names:
            op.create_foreign_key(
                "fk_suspension_activity_org_id",
                "suspension_activity",
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete="SET NULL",
            )

    # Backfill legacy configurations into canonical tables:
    # - suspension_rule (legacy global) -> suspension_rule_sets + suspension_rule_conditions
    # - org_suspension_configs (legacy org JSON rules) -> suspension_rule_sets + suspension_rule_conditions
    bind = op.get_bind()
    if _has_table("suspension_rule"):
        legacy_rows = bind.execute(
            sa.text(
                """
                SELECT id, name, condition_summary, status, notes,
                       primary_trigger, overdue_days_threshold, overdue_amount_threshold,
                       credit_utilisation_threshold, additional_conditions,
                       notify_finance_team, created_at, updated_at, version
                FROM suspension_rule
                """
            )
        ).mappings().all()
        for row in legacy_rows:
            exists = bind.execute(
                sa.text("SELECT 1 FROM suspension_rule_sets WHERE id = :id"),
                {"id": row["id"]},
            ).first()
            if exists:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO suspension_rule_sets (
                        id, created_at, updated_at, version, name, condition_summary,
                        scope_type, scope_org_id, rule_type, status, notes,
                        auto_suspension_enabled, pause_new_bookings, restrict_portal_login,
                        notify_finance_team, notify_account_manager
                    ) VALUES (
                        :id, :created_at, :updated_at, :version, :name, :condition_summary,
                        'GLOBAL', NULL, 'CREDIT_LIMIT', :status, :notes,
                        true, false, true, :notify_finance_team, false
                    )
                    """
                ),
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "version": row["version"],
                    "name": row["name"],
                    "condition_summary": row["condition_summary"],
                    "status": row["status"],
                    "notes": row["notes"],
                    "notify_finance_team": row["notify_finance_team"],
                },
            )

            cond_rows: list[tuple[str, float, str | None]] = []
            trigger = row["primary_trigger"]
            if trigger == "OVERDUE_DAYS":
                cond_rows.append(("INVOICE_OVERDUE_DAYS", float(row["overdue_days_threshold"] or 0), None))
            elif trigger == "OVERDUE_AMOUNT":
                cond_rows.append(("TOTAL_OVERDUE_AMOUNT", float(row["overdue_amount_threshold"] or 0), None))
            elif trigger == "OVERDUE_DAYS_AND_AMOUNT":
                cond_rows.append(("INVOICE_OVERDUE_DAYS", float(row["overdue_days_threshold"] or 0), None))
                cond_rows.append(("TOTAL_OVERDUE_AMOUNT", float(row["overdue_amount_threshold"] or 0), "AND"))
            elif trigger == "CREDIT_UTILISATION_PERCENT":
                cond_rows.append(("CREDIT_UTILIZATION", float(row["credit_utilisation_threshold"] or 0), None))
            elif trigger == "CREDIT_NOT_CLEARED_AFTER_CLEARING_DATE":
                cond_rows.append(("CREDIT_NOT_CLEARED_AFTER_DUE_DATE", float(row["overdue_days_threshold"] or 0), None))

            for idx, (ctype, threshold, connector) in enumerate(cond_rows, start=1):
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO suspension_rule_conditions (
                            id, created_at, updated_at, rule_set_id, position,
                            connector, condition_type, threshold_value, unit
                        ) VALUES (
                            :id, now(), now(), :rule_set_id, :position,
                            :connector, :condition_type, :threshold_value, NULL
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "rule_set_id": row["id"],
                        "position": idx,
                        "connector": connector or "NONE",
                        "condition_type": ctype,
                        "threshold_value": threshold,
                    },
                )

    if _has_table("org_suspension_configs"):
        org_rows = bind.execute(
            sa.text(
                """
                SELECT id, organization_id, trigger_conditions,
                       auto_suspension_enabled, pause_new_bookings,
                       restrict_portal_login, notify_finance_team, notify_account_manager,
                       created_at, updated_at, version
                FROM org_suspension_configs
                """
            )
        ).mappings().all()
        for row in org_rows:
            if not row["organization_id"]:
                continue
            rule_set_id = row["id"]
            exists = bind.execute(sa.text("SELECT 1 FROM suspension_rule_sets WHERE id=:id"), {"id": rule_set_id}).first()
            if not exists:
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO suspension_rule_sets (
                            id, created_at, updated_at, version, name, condition_summary,
                            scope_type, scope_org_id, rule_type, status, notes,
                            auto_suspension_enabled, pause_new_bookings, restrict_portal_login,
                            notify_finance_team, notify_account_manager
                        ) VALUES (
                            :id, :created_at, :updated_at, :version,
                            :name, NULL, 'ORG', :scope_org_id, 'CREDIT_LIMIT',
                            'ACTIVE', NULL, :auto_suspension_enabled,
                            :pause_new_bookings, :restrict_portal_login,
                            :notify_finance_team, :notify_account_manager
                        )
                        """
                    ),
                    {
                        "id": rule_set_id,
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "version": row["version"],
                        "name": f"OrgOverride-{row['organization_id']}",
                        "scope_org_id": row["organization_id"],
                        "auto_suspension_enabled": row["auto_suspension_enabled"],
                        "pause_new_bookings": row["pause_new_bookings"],
                        "restrict_portal_login": row["restrict_portal_login"],
                        "notify_finance_team": row["notify_finance_team"],
                        "notify_account_manager": row["notify_account_manager"],
                    },
                )
            trigger_conditions = row["trigger_conditions"] or []
            for item in trigger_conditions:
                condition_type = str(item.get("condition_type") or "INVOICE_OVERDUE_DAYS")
                cond_map = {
                    "INVOICE_OVERDUE_DAYS": "INVOICE_OVERDUE_DAYS",
                    "TOTAL_OVERDUE_AMOUNT": "TOTAL_OVERDUE_AMOUNT",
                    "CREDIT_UTILIZATION": "CREDIT_UTILIZATION",
                    "CREDIT_NOT_CLEARED_AFTER_DUE_DATE": "CREDIT_NOT_CLEARED_AFTER_DUE_DATE",
                }
                mapped_type = cond_map.get(condition_type, "INVOICE_OVERDUE_DAYS")
                pos = int(item.get("position") or 1)
                conn = item.get("logic_operator") or "NONE"
                threshold = float(item.get("condition_value") or 0)
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO suspension_rule_conditions (
                            id, created_at, updated_at, rule_set_id, position,
                            connector, condition_type, threshold_value, unit
                        ) VALUES (
                            :id, now(), now(), :rule_set_id, :position,
                            :connector, :condition_type, :threshold_value, NULL
                        )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "rule_set_id": rule_set_id,
                        "position": pos,
                        "connector": conn,
                        "condition_type": mapped_type,
                        "threshold_value": threshold,
                    },
                )


def downgrade() -> None:
    # Revert v2 enrichment from suspension_activity first, then drop v2 tables.
    if _has_table("suspension_activity"):
        for idx in (
            "ix_suspension_activity_run_id",
            "ix_suspension_activity_payment_model",
            "ix_suspension_activity_rule_type",
            "ix_suspension_activity_organization_id",
        ):
            if _has_index("suspension_activity", idx):
                op.drop_index(idx, table_name="suspension_activity")

        fk_names = [fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("suspension_activity")]
        if "fk_suspension_activity_org_id" in fk_names:
            op.drop_constraint("fk_suspension_activity_org_id", "suspension_activity", type_="foreignkey")
        if "fk_suspension_activity_run_id" in fk_names:
            op.drop_constraint("fk_suspension_activity_run_id", "suspension_activity", type_="foreignkey")

        for column in (
            "notification_status",
            "final_result",
            "group_results",
            "evaluated_expression",
            "run_id",
            "payment_model",
            "rule_type",
            "organization_id",
        ):
            if _has_column("suspension_activity", column):
                op.drop_column("suspension_activity", column)

    if _has_table("payment_risk_events"):
        op.drop_table("payment_risk_events")
    if _has_table("suspension_notification_audit"):
        op.drop_table("suspension_notification_audit")
    if _has_table("suspension_evaluation_runs"):
        op.drop_table("suspension_evaluation_runs")
    if _has_table("suspension_rule_conditions"):
        op.drop_table("suspension_rule_conditions")
    if _has_table("suspension_rule_sets"):
        op.drop_table("suspension_rule_sets")
