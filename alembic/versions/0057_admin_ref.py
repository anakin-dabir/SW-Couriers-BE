"""Add admin_ref column to users table for display purposes.

Adds:
- admin_ref_seq sequence for generating ADM-XXXX references
- admin_ref column on users (nullable, unique)
- Server default uses sequence for new inserts
- Backfill existing admins with ADM-XXXX values
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_admin_ref"
down_revision: str | None = "0056_org_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create sequence for admin_ref
    op.execute("CREATE SEQUENCE admin_ref_seq START 1 INCREMENT 1")

    # Add admin_ref column with server default
    op.add_column(
        "users",
        sa.Column(
            "admin_ref",
            sa.String(15),
            nullable=True,
            server_default=sa.text("'ADM-' || lpad(nextval('admin_ref_seq')::text, 4, '0')"),
            unique=True,
        ),
    )

    # Create index for efficient lookups
    op.create_index("ix_users_admin_ref", "users", ["admin_ref"])

    # Backfill existing admin users (ordered by created_at)
    op.execute(
        """
        UPDATE users
        SET admin_ref = 'ADM-' || lpad(nextval('admin_ref_seq')::text, 4, '0')
        WHERE id IN (
            SELECT id FROM users
            WHERE role IN ('ADMIN', 'SUPER_ADMIN') AND admin_ref IS NULL
            ORDER BY created_at ASC
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_admin_ref")
    op.drop_column("users", "admin_ref")
    op.execute("DROP SEQUENCE admin_ref_seq")
