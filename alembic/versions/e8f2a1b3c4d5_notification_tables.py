"""Create notification module tables.

Revision ID: e8f2a1b3c4d5
Revises: 0010_access_jti
Create Date: 2026-03-17

Builds on initial migration (da8153402035): only notification-related delta.
  - Drops legacy notification_log; creates notification_audit_log in its place.
  - Alters notification_templates.is_active default (table already exists).
Creates:
  - system_notification_defaults, user_notification_preferences
  - notifications (in-app inbox), notification_audit_log (replaces notification_log)
  - recipient_notification_preferences, user_device_tokens
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "e8f2a1b3c4d5"
down_revision: str = "e363b2b7b2e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_notification_defaults",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("notification_type", sa.String(20), nullable=False),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("template_id", UUID(as_uuid=False), sa.ForeignKey("notification_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint(
            "notification_type",
            "event",
            "channel",
            name="uq_sys_notif_default_type_event_channel",
        ),
    )

    op.create_table(
        "user_notification_preferences",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("template_id", UUID(as_uuid=False), sa.ForeignKey("notification_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "event",
            "channel",
            name="uq_user_notif_pref_user_event_channel",
        ),
    )

    # NOTE: `notification_templates` already exists in the initial migration
    # (`da8153402035_create_all_tables.py`). Here we only apply the delta needed
    # by the notifications module (defaults).
    op.alter_column(
        "notification_templates",
        "is_active",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    )

    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("recipient_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("organization_id", UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("event", sa.String(50), nullable=False, index=True),
        sa.Column("notification_type", sa.String(20), nullable=False, index=True),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context_json", JSONB, nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )

    # Replace legacy notification_log (from initial migration) with notification_audit_log.
    op.drop_index(op.f("ix_notification_log_recipient_id"), table_name="notification_log")
    op.drop_index(op.f("ix_notification_log_channel"), table_name="notification_log")
    op.drop_table("notification_log")

    op.create_table(
        "notification_audit_log",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("notification_id", UUID(as_uuid=False), sa.ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("recipient_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("organization_id", UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("event", sa.String(50), nullable=False, index=True),
        sa.Column("notification_type", sa.String(20), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=True),
        sa.Column("recipient_phone", sa.String(50), nullable=True),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("context_json", JSONB, nullable=True),
        sa.Column("email_status", sa.String(20), nullable=True),
        sa.Column("email_error", sa.Text(), nullable=True),
        sa.Column("email_external_id", sa.String(100), nullable=True),
        sa.Column("sms_status", sa.String(20), nullable=True),
        sa.Column("sms_error", sa.Text(), nullable=True),
        sa.Column("sms_external_id", sa.String(100), nullable=True),
        sa.Column("push_status", sa.String(20), nullable=True),
        sa.Column("push_error", sa.Text(), nullable=True),
        sa.Column("push_external_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "recipient_notification_preferences",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("template_id", UUID(as_uuid=False), sa.ForeignKey("notification_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "event",
            "channel",
            name="uq_recipient_notif_pref_org_event_channel",
        ),
    )

    op.create_table(
        "user_device_tokens",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("device_token", sa.String(500), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_unique_constraint("uq_user_device_tokens_token", "user_device_tokens", ["device_token"])


def downgrade() -> None:
    op.drop_table("user_device_tokens")
    op.drop_table("recipient_notification_preferences")
    op.drop_table("notification_audit_log")

    # Restore legacy notification_log as in initial migration.
    op.create_table(
        "notification_log",
        sa.Column("recipient_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("recipient_address", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_log_channel"), "notification_log", ["channel"], unique=False)
    op.create_index(op.f("ix_notification_log_recipient_id"), "notification_log", ["recipient_id"], unique=False)

    op.drop_table("notifications")
    op.alter_column(
        "notification_templates",
        "is_active",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=None,
    )
    op.drop_table("user_notification_preferences")
    op.drop_table("system_notification_defaults")
