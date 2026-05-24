"""service_tier_scope_and_pricing_breakdown

Revision ID: 0087_service_tier_scope
Revises: 0086_terms_accept_audit
Create Date: 2026-04-25

Adds GLOBAL/ORG scoping (mirroring suspension rules), error margin (kg), and
price breakdown columns (base price, price per 1kg, price per package).
Replaces the single unique on (tier_name, available_for) with partial unique
indexes for global vs org scope.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0087_service_tier_scope"
down_revision: Union[str, None] = "0086_terms_accept_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(i["name"] == index_name for i in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("service_tier", "scope_type"):
        op.add_column(
            "service_tier",
            sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="GLOBAL"),
        )
    if not _has_column("service_tier", "scope_org_id"):
        op.add_column(
            "service_tier",
            sa.Column("scope_org_id", postgresql.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            "fk_service_tier_scope_org_id",
            "service_tier",
            "organizations",
            ["scope_org_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_service_tier_scope_org_id", "service_tier", ["scope_org_id"])

    if not _has_column("service_tier", "error_margin_kg"):
        op.add_column(
            "service_tier",
            sa.Column("error_margin_kg", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column("service_tier", "price_per_kg"):
        op.add_column(
            "service_tier",
            sa.Column("price_per_kg", sa.Numeric(10, 2), nullable=False, server_default="0"),
        )
    if not _has_column("service_tier", "base_price"):
        op.add_column(
            "service_tier",
            sa.Column("base_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        )

    # Drop legacy uniqueness; replaced by partial indexes below.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uqs = [c["name"] for c in inspector.get_unique_constraints("service_tier")]
    if "uq_service_tier_name_available_for" in uqs:
        op.drop_constraint("uq_service_tier_name_available_for", "service_tier", type_="unique")

    if not _has_index("service_tier", "uq_service_tier_global_name_audience"):
        op.create_index(
            "uq_service_tier_global_name_audience",
            "service_tier",
            ["tier_name", "available_for"],
            unique=True,
            postgresql_where=sa.text("scope_type = 'GLOBAL'"),
        )
    if not _has_index("service_tier", "uq_service_tier_org_name_audience"):
        op.create_index(
            "uq_service_tier_org_name_audience",
            "service_tier",
            ["scope_org_id", "tier_name", "available_for"],
            unique=True,
            postgresql_where=sa.text("scope_type = 'ORG'"),
        )

    op.alter_column("service_tier", "scope_type", server_default=None)


def downgrade() -> None:
    if _has_index("service_tier", "uq_service_tier_org_name_audience"):
        op.drop_index("uq_service_tier_org_name_audience", table_name="service_tier")
    if _has_index("service_tier", "uq_service_tier_global_name_audience"):
        op.drop_index("uq_service_tier_global_name_audience", table_name="service_tier")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uqs = [c["name"] for c in inspector.get_unique_constraints("service_tier")]
    if "uq_service_tier_name_available_for" not in uqs:
        op.create_unique_constraint(
            "uq_service_tier_name_available_for",
            "service_tier",
            ["tier_name", "available_for"],
        )

    if _has_column("service_tier", "base_price"):
        op.drop_column("service_tier", "base_price")
    if _has_column("service_tier", "price_per_kg"):
        op.drop_column("service_tier", "price_per_kg")
    if _has_column("service_tier", "error_margin_kg"):
        op.drop_column("service_tier", "error_margin_kg")

    if _has_index("service_tier", "ix_service_tier_scope_org_id"):
        op.drop_index("ix_service_tier_scope_org_id", table_name="service_tier")
    if _has_column("service_tier", "scope_org_id"):
        op.drop_constraint("fk_service_tier_scope_org_id", "service_tier", type_="foreignkey")
        op.drop_column("service_tier", "scope_org_id")
    if _has_column("service_tier", "scope_type"):
        op.drop_column("service_tier", "scope_type")
