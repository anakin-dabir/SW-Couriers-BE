"""QuickBooks integration persistence models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AppendOnlyModel, BaseModel


class QbConnection(BaseModel):
    """Organization-level QuickBooks OAuth connection and token state."""

    __tablename__ = "qb_connections"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        unique=True,
        index=True,
    )
    realm_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connected_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

class QbLink(BaseModel):
    """Mapping between local entities and QuickBooks entities."""

    __tablename__ = "qb_links"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # customer | invoice
    local_entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    qb_entity_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_SYNCED", server_default="NOT_SYNCED", index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "entity_type", "local_entity_id", name="uq_qb_links_org_entity_local"),
        UniqueConstraint("organization_id", "entity_type", "qb_entity_id", name="uq_qb_links_org_entity_qb"),
    )


class QbSyncLog(AppendOnlyModel):
    """Append-only sync attempt log for observability and support workflows."""

    __tablename__ = "qb_sync_logs"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    local_entity_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    related_qb_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class QbReferenceMapping(BaseModel):
    """Organization-scoped mapping between local accounting keys and QBO refs."""

    __tablename__ = "qb_reference_mappings"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        index=True,
    )
    mapping_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    local_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    qb_ref_id: Mapped[str] = mapped_column(String(100), nullable=False)
    qb_ref_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "mapping_type",
            "local_key",
            name="uq_qb_ref_mappings_org_type_key",
        ),
    )


class QbSyncSettings(BaseModel):
    """Organization-level policy settings for sync behavior and safety."""

    __tablename__ = "qb_sync_settings"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        unique=True,
        index=True,
    )
    strict_mapping_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sync_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    auto_retry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    max_retry_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=45, server_default="45")
    allow_force_reapply_credit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
