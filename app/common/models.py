"""SQLAlchemy declarative base, mixins, and abstract base models.

All ORM models inherit from one of:
- BaseModel:           UUID + timestamps + version (optimistic locking)
- BaseModelNoVersion:  UUID + timestamps, no version (child entities updated
                       within parent transaction — e.g. InvoiceLineItem)
- AppendOnlyModel:     UUID + created_at only (never updated — audit tables)
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    """Return current time in UTC. Used as default for timestamp columns."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all models. Import this for model definitions and metadata."""

    pass


class TimestampMixin:
    """created_at and updated_at in UTC, set automatically."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
        nullable=False,
    )


class UUIDMixin:
    """Primary key as UUID v4 string."""

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )


class VersionMixin:
    """Optimistic locking via integer version column.

    Every UPDATE must include `WHERE version = :expected_version`.
    If zero rows are affected, the update is rejected with 409 Conflict.
    """

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )


class BaseModel(Base, UUIDMixin, TimestampMixin, VersionMixin):
    """Abstract base for all mutable app models: id (UUID), created_at, updated_at, version.

    Usage:
        class User(BaseModel):
            __tablename__ = "users"
            email: Mapped[str] = mapped_column(unique=True)
    """

    __abstract__ = True


class BaseModelNoVersion(Base, UUIDMixin, TimestampMixin):
    """Abstract base for child entities that don't need independent optimistic locking.

    Use this for entities that are always updated within the parent's transaction
    (e.g. InvoiceLineItem, RouteStop). The parent entity's version column
    protects the entire aggregate.
    """

    __abstract__ = True


class AppendOnlyModel(Base, UUIDMixin):
    """Abstract base for append-only tables (audit_log, shipment_events).

    No version column (never updated). Only created_at (no updated_at).
    """

    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )
