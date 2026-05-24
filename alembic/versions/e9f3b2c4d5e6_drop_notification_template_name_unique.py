"""Drop unique constraint on notification_templates.name.

Revision ID: e9f3b2c4d5e6
Revises: e8f2a1b3c4d5
Create Date: 2026-03-17

The name column is a human-readable label, not a lookup key. Template
resolution uses the PK (id) via preference table FKs. The unique
constraint caused collision risk when multiple users/orgs created
templates for the same event+channel context (truncated UUID suffix).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9f3b2c4d5e6"
down_revision: str = "e8f2a1b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "notification_templates_name_key",
        "notification_templates",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "notification_templates_name_key",
        "notification_templates",
        ["name"],
    )
