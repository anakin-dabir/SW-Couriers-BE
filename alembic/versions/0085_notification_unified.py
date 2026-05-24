"""Notification streams (admin / B2B customer / recipient), B2B_CUSTOMER rename, wide preferences.

Revision ID: 0085_notification_unified
Revises: 0084_billing_payments_found
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0085_notification_unified"
down_revision: str | None = "0084_billing_payments_found"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_notification_preferences",
        sa.Column("notification_type", sa.String(30), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE user_notification_preferences SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type IS NULL"
        )
    )
    op.alter_column(
        "user_notification_preferences",
        "notification_type",
        nullable=False,
        server_default=sa.text("'B2B_INTERNAL'"),
    )

    op.drop_constraint("uq_user_notif_pref_user_event_channel", "user_notification_preferences", type_="unique")
    op.create_unique_constraint(
        "uq_user_notif_pref_user_type_event_channel",
        "user_notification_preferences",
        ["user_id", "notification_type", "event", "channel"],
    )

    op.execute(
        sa.text("UPDATE system_notification_defaults SET notification_type = 'B2B_INTERNAL' WHERE notification_type = 'INTERNAL'")
    )
    op.execute(sa.text("UPDATE notifications SET notification_type = 'B2B_INTERNAL' WHERE notification_type = 'INTERNAL'"))
    op.execute(sa.text("UPDATE notification_audit_log SET notification_type = 'B2B_INTERNAL' WHERE notification_type = 'INTERNAL'"))

    op.add_column(
        "recipient_notification_preferences",
        sa.Column("notification_type", sa.String(30), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE recipient_notification_preferences SET notification_type = 'RECIPIENT' "
            "WHERE notification_type IS NULL"
        )
    )
    op.alter_column(
        "recipient_notification_preferences",
        "notification_type",
        nullable=False,
        server_default=sa.text("'RECIPIENT'"),
    )

    op.drop_constraint("uq_recipient_notif_pref_org_event_channel", "recipient_notification_preferences", type_="unique")
    op.create_unique_constraint(
        "uq_org_notif_pref_org_type_event_channel",
        "recipient_notification_preferences",
        ["organization_id", "notification_type", "event", "channel"],
    )

    op.execute(
        sa.text(
            "UPDATE user_notification_preferences SET notification_type = 'B2B_CUSTOMER' "
            "WHERE notification_type = 'B2B_INTERNAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE system_notification_defaults SET notification_type = 'B2B_CUSTOMER' "
            "WHERE notification_type = 'B2B_INTERNAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE notifications SET notification_type = 'B2B_CUSTOMER' "
            "WHERE notification_type = 'B2B_INTERNAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE notification_audit_log SET notification_type = 'B2B_CUSTOMER' "
            "WHERE notification_type = 'B2B_INTERNAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE recipient_notification_preferences SET notification_type = 'B2B_CUSTOMER' "
            "WHERE notification_type = 'B2B_INTERNAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE notification_templates SET name = replace(name, '_B2B_INTERNAL_', '_B2B_CUSTOMER_') "
            "WHERE name LIKE '%B2B_INTERNAL%'"
        )
    )
    op.alter_column(
        "user_notification_preferences",
        "notification_type",
        server_default=sa.text("'B2B_CUSTOMER'"),
    )

    op.drop_constraint("uq_sys_notif_default_type_event_channel", "system_notification_defaults", type_="unique")
    op.add_column("system_notification_defaults", sa.Column("email_enabled", sa.Boolean(), nullable=True))
    op.add_column("system_notification_defaults", sa.Column("sms_enabled", sa.Boolean(), nullable=True))
    op.add_column("system_notification_defaults", sa.Column("email_template_id", UUID(as_uuid=False), nullable=True))
    op.add_column("system_notification_defaults", sa.Column("sms_template_id", UUID(as_uuid=False), nullable=True))
    op.execute(
        sa.text(
            "UPDATE system_notification_defaults SET email_enabled = enabled, email_template_id = template_id "
            "WHERE channel = 'EMAIL'"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE system_notification_defaults AS d
            SET sms_enabled = s.enabled, sms_template_id = s.template_id
            FROM system_notification_defaults AS s
            WHERE s.notification_type = d.notification_type
              AND s.event = d.event
              AND s.channel = 'SMS'
              AND d.channel = 'EMAIL'
            """
        )
    )
    op.execute(sa.text("DELETE FROM system_notification_defaults WHERE channel = 'SMS'"))
    op.execute(
        sa.text(
            "UPDATE system_notification_defaults SET email_enabled = COALESCE(email_enabled, true), "
            "sms_enabled = COALESCE(sms_enabled, true)"
        )
    )
    op.alter_column("system_notification_defaults", "email_enabled", nullable=False, server_default=sa.sql.true())
    op.alter_column("system_notification_defaults", "sms_enabled", nullable=False, server_default=sa.sql.true())
    op.create_foreign_key(
        "fk_sys_notif_default_email_tpl",
        "system_notification_defaults",
        "notification_templates",
        ["email_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sys_notif_default_sms_tpl",
        "system_notification_defaults",
        "notification_templates",
        ["sms_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("system_notification_defaults", "channel")
    op.drop_column("system_notification_defaults", "enabled")
    op.drop_column("system_notification_defaults", "template_id")
    op.create_unique_constraint(
        "uq_sys_notif_default_type_event",
        "system_notification_defaults",
        ["notification_type", "event"],
    )

    op.drop_constraint("uq_user_notif_pref_user_type_event_channel", "user_notification_preferences", type_="unique")
    op.add_column("user_notification_preferences", sa.Column("email_enabled", sa.Boolean(), nullable=True))
    op.add_column("user_notification_preferences", sa.Column("sms_enabled", sa.Boolean(), nullable=True))
    op.add_column("user_notification_preferences", sa.Column("email_template_id", UUID(as_uuid=False), nullable=True))
    op.add_column("user_notification_preferences", sa.Column("sms_template_id", UUID(as_uuid=False), nullable=True))
    op.execute(
        sa.text(
            "UPDATE user_notification_preferences SET email_enabled = enabled, email_template_id = template_id "
            "WHERE channel = 'EMAIL'"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE user_notification_preferences AS d
            SET sms_enabled = s.enabled, sms_template_id = s.template_id
            FROM user_notification_preferences AS s
            WHERE s.user_id = d.user_id
              AND s.notification_type = d.notification_type
              AND s.event = d.event
              AND s.channel = 'SMS'
              AND d.channel = 'EMAIL'
            """
        )
    )
    op.execute(sa.text("DELETE FROM user_notification_preferences WHERE channel = 'SMS'"))
    op.create_foreign_key(
        "fk_user_notif_pref_email_tpl",
        "user_notification_preferences",
        "notification_templates",
        ["email_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_user_notif_pref_sms_tpl",
        "user_notification_preferences",
        "notification_templates",
        ["sms_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("user_notification_preferences", "channel")
    op.drop_column("user_notification_preferences", "enabled")
    op.drop_column("user_notification_preferences", "template_id")
    op.create_unique_constraint(
        "uq_user_notif_pref_user_type_event",
        "user_notification_preferences",
        ["user_id", "notification_type", "event"],
    )

    op.drop_constraint("uq_org_notif_pref_org_type_event_channel", "recipient_notification_preferences", type_="unique")
    op.add_column("recipient_notification_preferences", sa.Column("email_enabled", sa.Boolean(), nullable=True))
    op.add_column("recipient_notification_preferences", sa.Column("sms_enabled", sa.Boolean(), nullable=True))
    op.add_column("recipient_notification_preferences", sa.Column("email_template_id", UUID(as_uuid=False), nullable=True))
    op.add_column("recipient_notification_preferences", sa.Column("sms_template_id", UUID(as_uuid=False), nullable=True))
    op.execute(
        sa.text(
            "UPDATE recipient_notification_preferences SET email_enabled = enabled, email_template_id = template_id "
            "WHERE channel = 'EMAIL'"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recipient_notification_preferences AS d
            SET sms_enabled = s.enabled, sms_template_id = s.template_id
            FROM recipient_notification_preferences AS s
            WHERE s.organization_id = d.organization_id
              AND s.notification_type = d.notification_type
              AND s.event = d.event
              AND s.channel = 'SMS'
              AND d.channel = 'EMAIL'
            """
        )
    )
    op.execute(sa.text("DELETE FROM recipient_notification_preferences WHERE channel = 'SMS'"))
    op.create_foreign_key(
        "fk_org_notif_pref_email_tpl",
        "recipient_notification_preferences",
        "notification_templates",
        ["email_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_org_notif_pref_sms_tpl",
        "recipient_notification_preferences",
        "notification_templates",
        ["sms_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("recipient_notification_preferences", "channel")
    op.drop_column("recipient_notification_preferences", "enabled")
    op.drop_column("recipient_notification_preferences", "template_id")
    op.create_unique_constraint(
        "uq_org_notif_pref_org_type_event",
        "recipient_notification_preferences",
        ["organization_id", "notification_type", "event"],
    )
    op.rename_table("recipient_notification_preferences", "org_notification_preferences")


def downgrade() -> None:
    op.drop_constraint("uq_org_notif_pref_org_type_event", "org_notification_preferences", type_="unique")
    op.drop_constraint("fk_org_notif_pref_sms_tpl", "org_notification_preferences", type_="foreignkey")
    op.drop_constraint("fk_org_notif_pref_email_tpl", "org_notification_preferences", type_="foreignkey")
    op.drop_column("org_notification_preferences", "sms_template_id")
    op.drop_column("org_notification_preferences", "email_template_id")
    op.drop_column("org_notification_preferences", "sms_enabled")
    op.drop_column("org_notification_preferences", "email_enabled")
    op.add_column(
        "org_notification_preferences",
        sa.Column("channel", sa.String(20), nullable=False, server_default=sa.text("'EMAIL'")),
    )
    op.add_column(
        "org_notification_preferences",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
    )
    op.add_column(
        "org_notification_preferences",
        sa.Column(
            "template_id",
            UUID(as_uuid=False),
            sa.ForeignKey("notification_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_org_notif_pref_org_type_event_channel",
        "org_notification_preferences",
        ["organization_id", "notification_type", "event", "channel"],
    )
    op.rename_table("org_notification_preferences", "recipient_notification_preferences")

    op.drop_constraint("uq_user_notif_pref_user_type_event", "user_notification_preferences", type_="unique")
    op.drop_constraint("fk_user_notif_pref_sms_tpl", "user_notification_preferences", type_="foreignkey")
    op.drop_constraint("fk_user_notif_pref_email_tpl", "user_notification_preferences", type_="foreignkey")
    op.drop_column("user_notification_preferences", "sms_template_id")
    op.drop_column("user_notification_preferences", "email_template_id")
    op.drop_column("user_notification_preferences", "sms_enabled")
    op.drop_column("user_notification_preferences", "email_enabled")
    op.add_column(
        "user_notification_preferences",
        sa.Column("channel", sa.String(20), nullable=False, server_default=sa.text("'EMAIL'")),
    )
    op.add_column(
        "user_notification_preferences",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
    )
    op.add_column(
        "user_notification_preferences",
        sa.Column(
            "template_id",
            UUID(as_uuid=False),
            sa.ForeignKey("notification_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_user_notif_pref_user_type_event_channel",
        "user_notification_preferences",
        ["user_id", "notification_type", "event", "channel"],
    )

    op.drop_constraint("uq_sys_notif_default_type_event", "system_notification_defaults", type_="unique")
    op.drop_constraint("fk_sys_notif_default_sms_tpl", "system_notification_defaults", type_="foreignkey")
    op.drop_constraint("fk_sys_notif_default_email_tpl", "system_notification_defaults", type_="foreignkey")
    op.drop_column("system_notification_defaults", "sms_template_id")
    op.drop_column("system_notification_defaults", "email_template_id")
    op.drop_column("system_notification_defaults", "sms_enabled")
    op.drop_column("system_notification_defaults", "email_enabled")
    op.add_column(
        "system_notification_defaults",
        sa.Column("channel", sa.String(20), nullable=False, server_default=sa.text("'EMAIL'")),
    )
    op.add_column(
        "system_notification_defaults",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
    )
    op.add_column(
        "system_notification_defaults",
        sa.Column(
            "template_id",
            UUID(as_uuid=False),
            sa.ForeignKey("notification_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_sys_notif_default_type_event_channel",
        "system_notification_defaults",
        ["notification_type", "event", "channel"],
    )

    op.alter_column(
        "user_notification_preferences",
        "notification_type",
        server_default=sa.text("'B2B_INTERNAL'"),
    )
    op.execute(
        sa.text(
            "UPDATE notification_templates SET name = replace(name, '_B2B_CUSTOMER_', '_B2B_INTERNAL_') "
            "WHERE name LIKE '%B2B_CUSTOMER%'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE recipient_notification_preferences SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type = 'B2B_CUSTOMER'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE notification_audit_log SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type = 'B2B_CUSTOMER'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE notifications SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type = 'B2B_CUSTOMER'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE system_notification_defaults SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type = 'B2B_CUSTOMER'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE user_notification_preferences SET notification_type = 'B2B_INTERNAL' "
            "WHERE notification_type = 'B2B_CUSTOMER'"
        )
    )

    op.drop_constraint("uq_org_notif_pref_org_type_event_channel", "recipient_notification_preferences", type_="unique")
    op.create_unique_constraint(
        "uq_recipient_notif_pref_org_event_channel",
        "recipient_notification_preferences",
        ["organization_id", "event", "channel"],
    )
    op.drop_column("recipient_notification_preferences", "notification_type")

    op.execute(
        sa.text("UPDATE system_notification_defaults SET notification_type = 'INTERNAL' WHERE notification_type = 'B2B_INTERNAL'")
    )
    op.execute(sa.text("UPDATE notifications SET notification_type = 'INTERNAL' WHERE notification_type = 'B2B_INTERNAL'"))
    op.execute(sa.text("UPDATE notification_audit_log SET notification_type = 'INTERNAL' WHERE notification_type = 'B2B_INTERNAL'"))

    op.drop_constraint("uq_user_notif_pref_user_type_event_channel", "user_notification_preferences", type_="unique")
    op.create_unique_constraint(
        "uq_user_notif_pref_user_event_channel",
        "user_notification_preferences",
        ["user_id", "event", "channel"],
    )
    op.drop_column("user_notification_preferences", "notification_type")
