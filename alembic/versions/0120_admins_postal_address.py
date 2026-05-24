"""add postal address columns to admins

Adds address_line_1, address_line_2, city, state, postcode, and country to ``admins``.
Existing rows are backfilled so NOT NULL constraints apply; ``country`` keeps a
server default of United Kingdom for new inserts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0120_admins_postal_address"
down_revision: str | None = "0119_admins_table_admin_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admins", sa.Column("address_line_1", sa.String(length=255), nullable=True))
    op.add_column("admins", sa.Column("address_line_2", sa.String(length=255), nullable=True))
    op.add_column("admins", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("admins", sa.Column("state", sa.String(length=100), nullable=True))
    op.add_column("admins", sa.Column("postcode", sa.String(length=20), nullable=True))
    op.add_column(
        "admins",
        sa.Column(
            "country",
            sa.String(length=100),
            nullable=True,
            server_default=sa.text("'United Kingdom'"),
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE admins
            SET
              address_line_1 = COALESCE(NULLIF(btrim(address_line_1), ''), 'Legacy'),
              city = COALESCE(NULLIF(btrim(city), ''), 'Legacy'),
              state = COALESCE(NULLIF(btrim(state), ''), 'Legacy'),
              postcode = COALESCE(NULLIF(btrim(postcode), ''), 'XX0 0XX'),
              country = COALESCE(NULLIF(btrim(country), ''), 'United Kingdom')
            """
        )
    )

    op.alter_column("admins", "address_line_1", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("admins", "city", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("admins", "state", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("admins", "postcode", existing_type=sa.String(length=20), nullable=False)
    op.alter_column(
        "admins",
        "country",
        existing_type=sa.String(length=100),
        nullable=False,
        server_default=sa.text("'United Kingdom'"),
    )


def downgrade() -> None:
    op.drop_column("admins", "country")
    op.drop_column("admins", "postcode")
    op.drop_column("admins", "state")
    op.drop_column("admins", "city")
    op.drop_column("admins", "address_line_2")
    op.drop_column("admins", "address_line_1")
