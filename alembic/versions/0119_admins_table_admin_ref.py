"""create admins table and move admin_ref off users

Adds ``admins`` (1:1 with ``users`` for ADMIN / SUPER_ADMIN) with ``admin_ref``
generated via existing ``admin_ref_seq`` (prefix ``ADM-``, same as migration 0057).
Migrates values from ``users.admin_ref``, then drops that column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0119_admins_table_admin_ref"
down_revision: str | None = "0118_dropdown_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADMIN_REF_PREFIX = "ADM"


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "admin_ref",
            sa.String(15),
            nullable=False,
            server_default=sa.text(
                f"'{_ADMIN_REF_PREFIX}-' || lpad(nextval('admin_ref_seq')::text, 4, '0')"
            ),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_admins_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("admin_ref"),
    )

    pfx = _ADMIN_REF_PREFIX
    op.execute(
        sa.text(
            f"""
            INSERT INTO admins (user_id, admin_ref, created_at, updated_at, version)
            SELECT u.id,
              CASE
                WHEN u.admin_ref IS NOT NULL AND btrim(u.admin_ref) <> ''
                THEN u.admin_ref
                ELSE '{pfx}-' || lpad(nextval('admin_ref_seq')::text, 4, '0')
              END,
              u.created_at, u.updated_at, 1
            FROM users u
            WHERE u.role IN ('ADMIN', 'SUPER_ADMIN')
            """
        )
    )

    op.execute(
        sa.text(
            """
            SELECT setval(
              'admin_ref_seq',
              GREATEST(
                COALESCE(
                  (SELECT MAX((substring(admin_ref from '[0-9]+$'))::integer)
                   FROM admins
                   WHERE admin_ref ~ :ref_pat),
                  0
                ),
                COALESCE((SELECT last_value FROM admin_ref_seq), 0)
              ),
              true
            )
            """
        ).bindparams(ref_pat=rf"^{pfx}-[0-9]+$")
    )

    op.drop_index("ix_users_admin_ref", table_name="users", if_exists=True)
    op.drop_column("users", "admin_ref")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "admin_ref",
            sa.String(15),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE users u
        SET admin_ref = a.admin_ref
        FROM admins a
        WHERE a.user_id = u.id
        """
    )
    op.drop_table("admins")
    op.create_index("ix_users_admin_ref", "users", ["admin_ref"], unique=False)
