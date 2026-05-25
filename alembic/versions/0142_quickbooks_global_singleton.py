"""Switch QuickBooks integration to global singleton namespace.

Revision ID: 0142_quickbooks_global_singleton
Revises: 0141_staff_time_off
Create Date: 2026-05-19

NOTE: Applied in production — do not change upgrade()/downgrade() SQL. Fresh installs
and downgrade paths must stay aligned with what already ran. The f-string UPDATEs below
use a hardcoded module constant only (not exploitable); do not copy that pattern for
dynamic values — use ``alembic.sql_helpers`` (bindparams) in new migrations instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0142_quickbooks_global_singleton"
down_revision: str | None = "0141_staff_time_off"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QB_GLOBAL_NAMESPACE_ID = "00000000-0000-4000-8000-000000000901"


def upgrade() -> None:
    # Keep only one global connection/settings row and collapse duplicate global keys before namespace rewrite.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY is_active DESC, updated_at DESC, created_at DESC, id DESC) AS rn
            FROM qb_connections
        )
        DELETE FROM qb_connections c
        USING ranked r
        WHERE c.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY updated_at DESC, created_at DESC, id DESC) AS rn
            FROM qb_sync_settings
        )
        DELETE FROM qb_sync_settings s
        USING ranked r
        WHERE s.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY entity_type, local_entity_id
                ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS rn
            FROM qb_links
        )
        DELETE FROM qb_links q
        USING ranked r
        WHERE q.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY entity_type, qb_entity_id
                ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS rn
            FROM qb_links
        )
        DELETE FROM qb_links q
        USING ranked r
        WHERE q.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY mapping_type, local_key
                ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS rn
            FROM qb_reference_mappings
        )
        DELETE FROM qb_reference_mappings m
        USING ranked r
        WHERE m.id = r.id AND r.rn > 1
        """
    )

    # Drop FKs before rewrite: sentinel namespace id is not a real organizations row.
    op.execute("ALTER TABLE qb_connections DROP CONSTRAINT IF EXISTS qb_connections_organization_id_fkey")
    op.execute("ALTER TABLE qb_links DROP CONSTRAINT IF EXISTS qb_links_organization_id_fkey")
    op.execute("ALTER TABLE qb_sync_logs DROP CONSTRAINT IF EXISTS qb_sync_logs_organization_id_fkey")
    op.execute("ALTER TABLE qb_reference_mappings DROP CONSTRAINT IF EXISTS qb_reference_mappings_organization_id_fkey")
    op.execute("ALTER TABLE qb_sync_settings DROP CONSTRAINT IF EXISTS qb_sync_settings_organization_id_fkey")

    # Normalize all QuickBooks rows to the global namespace id.
    op.execute(f"UPDATE qb_connections SET organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid")
    op.execute(f"UPDATE qb_links SET organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid")
    op.execute(f"UPDATE qb_sync_logs SET organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid")
    op.execute(f"UPDATE qb_reference_mappings SET organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid")
    op.execute(f"UPDATE qb_sync_settings SET organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid")

    # Force future writes to use the global namespace.
    op.alter_column("qb_connections", "organization_id", server_default=sa.text(f"'{QB_GLOBAL_NAMESPACE_ID}'::uuid"))
    op.alter_column("qb_links", "organization_id", server_default=sa.text(f"'{QB_GLOBAL_NAMESPACE_ID}'::uuid"))
    op.alter_column("qb_sync_logs", "organization_id", server_default=sa.text(f"'{QB_GLOBAL_NAMESPACE_ID}'::uuid"))
    op.alter_column("qb_reference_mappings", "organization_id", server_default=sa.text(f"'{QB_GLOBAL_NAMESPACE_ID}'::uuid"))
    op.alter_column("qb_sync_settings", "organization_id", server_default=sa.text(f"'{QB_GLOBAL_NAMESPACE_ID}'::uuid"))

    # Existing admins/super-admins must have effective QUICKBOOKS write permission.
    # Defaults already grant WRITE for admin roles; this upgrades explicit overrides that deny/restrict it.
    op.execute(
        """
        UPDATE user_permissions up
        SET level = 2,
            updated_at = now(),
            version = up.version + 1
        FROM users u
        WHERE up.user_id = u.id
          AND u.role IN ('ADMIN', 'SUPER_ADMIN')
          AND up.resource = 'QUICKBOOKS'
          AND up.level < 2
        """
    )


_QB_TABLES = (
    "qb_connections",
    "qb_links",
    "qb_sync_logs",
    "qb_reference_mappings",
    "qb_sync_settings",
)


def downgrade() -> None:
    """Best-effort schema rollback for local/dev.

    Cannot restore rows deleted during upgrade or original per-org organization_id
    values. Reassigns global-namespace rows to the oldest organization so FKs
  can be reattached, then removes server defaults.
    """
    fallback_org = op.get_bind().execute(
        sa.text("SELECT id::text FROM organizations ORDER BY created_at ASC LIMIT 1")
    ).scalar_one_or_none()
    if fallback_org is None:
        raise RuntimeError(
            "Cannot downgrade 0142: no organizations row exists to reattach QuickBooks FKs."
        )

    for table in _QB_TABLES:
        op.alter_column(table, "organization_id", server_default=None)
        op.execute(
            f"UPDATE {table} SET organization_id = '{fallback_org}'::uuid "
            f"WHERE organization_id = '{QB_GLOBAL_NAMESPACE_ID}'::uuid"
        )

    op.create_foreign_key(
        "qb_connections_organization_id_fkey",
        "qb_connections",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "qb_links_organization_id_fkey",
        "qb_links",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "qb_sync_logs_organization_id_fkey",
        "qb_sync_logs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "qb_reference_mappings_organization_id_fkey",
        "qb_reference_mappings",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "qb_sync_settings_organization_id_fkey",
        "qb_sync_settings",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
