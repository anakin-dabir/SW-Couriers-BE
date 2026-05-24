"""driver_self_onboarding_consents

Revision ID: 0070_driver_self_consents
Revises: 0069_master_label_stop_track
Create Date: 2026-04-09 21:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0070_driver_self_consents"
down_revision: Union[str, None] = "0069_master_label_stop_track"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_terms_and_conditions",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_terms_and_conditions_is_active", "driver_terms_and_conditions", ["is_active"], unique=False)
    op.create_table(
        "driver_terms_clauses",
        sa.Column("terms_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("clause_order", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["terms_id"], ["driver_terms_and_conditions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("terms_id", "clause_order", name="uq_driver_terms_clauses_terms_order"),
    )
    op.create_index("ix_driver_terms_clauses_terms_id", "driver_terms_clauses", ["terms_id"], unique=False)

    op.add_column("drivers", sa.Column("terms_and_conditions_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.create_index("ix_drivers_terms_and_conditions_id", "drivers", ["terms_and_conditions_id"], unique=False)
    op.create_foreign_key(
        "fk_drivers_terms_and_conditions_id",
        "drivers",
        "driver_terms_and_conditions",
        ["terms_and_conditions_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("drivers", sa.Column("terms_accepted_content_hash", sa.String(length=64), nullable=True))
    op.add_column("drivers", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("drivers", sa.Column("location_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("drivers", sa.Column("map_preference", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("drivers", "map_preference")
    op.drop_column("drivers", "location_consent_at")
    op.drop_column("drivers", "terms_accepted_at")
    op.drop_column("drivers", "terms_accepted_content_hash")
    op.drop_constraint("fk_drivers_terms_and_conditions_id", "drivers", type_="foreignkey")
    op.drop_index("ix_drivers_terms_and_conditions_id", table_name="drivers")
    op.drop_column("drivers", "terms_and_conditions_id")
    op.drop_index("ix_driver_terms_clauses_terms_id", table_name="driver_terms_clauses")
    op.drop_table("driver_terms_clauses")
    op.drop_index("ix_driver_terms_and_conditions_is_active", table_name="driver_terms_and_conditions")
    op.drop_table("driver_terms_and_conditions")
