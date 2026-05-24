"""Add FK suspension_activity.rule_id -> suspension_rule.id.

The table suspension_activity was created in 6698f60b0823 without this constraint.
The ORM model has ForeignKey("suspension_rule.id"); this migration adds it to the DB.

Revision ID: a1b2c3d4e5f7
Revises: 72bf17bcac4c
Create Date: 2026-03-19

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "72bf17bcac4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_suspension_activity_rule_id",
        "suspension_activity",
        "suspension_rule",
        ["rule_id"],
        ["id"],
    )


def downgrade() -> None:
    # Remove FK; table and column remain
    op.drop_constraint(
        "fk_suspension_activity_rule_id",
        "suspension_activity",
        type_="foreignkey",
    )
