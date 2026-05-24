"""org_credit_suspension

Creates two tables for per-organisation credit & suspension configuration:
  - org_credit_configs   — one-to-one: approved limit, clearance period, utilisation warning
  - org_suspension_configs — one-to-one: trigger conditions (JSONB) + action toggles

Revision ID: 0018_org_credit_suspension
Revises: 0017_drop_pricing_tiers, 9f1c2a3b4d5e
Create Date: 2026-03-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018_org_credit_suspension"
down_revision: tuple[str, ...] = ("0017_drop_pricing_tiers", "9f1c2a3b4d5e")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ── org_credit_configs ────────────────────────────────────────────────────
    op.create_table(
        "org_credit_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("approved_credit_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("credit_clearance_period_days", sa.Integer(), nullable=True),
        sa.Column("credit_utilization_warning_pct", sa.Integer(), nullable=True),
        sa.Column("allow_bookings_beyond_limit", sa.Boolean(), nullable=False, server_default="false"),
        # ── Standard audit columns ────────────────────────────────────────────
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_org_credit_configs_organization_id", "org_credit_configs", ["organization_id"])

    # ── org_suspension_configs ─────────────────────────────────────────────────
    op.create_table(
        "org_suspension_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Ordered list: [{"position": 1, "logic_operator": null,
        #                  "condition_type": "INVOICE_OVERDUE_DAYS", "condition_value": "40.00"}, ...]
        sa.Column("trigger_conditions", JSONB(), nullable=True),
        sa.Column("auto_suspension_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pause_new_bookings", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("restrict_portal_login", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notify_finance_team", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notify_account_manager", sa.Boolean(), nullable=False, server_default="false"),
        # ── Standard audit columns ────────────────────────────────────────────
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_org_suspension_configs_organization_id", "org_suspension_configs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_org_suspension_configs_organization_id", table_name="org_suspension_configs")
    op.drop_table("org_suspension_configs")

    op.drop_index("ix_org_credit_configs_organization_id", table_name="org_credit_configs")
    op.drop_table("org_credit_configs")
