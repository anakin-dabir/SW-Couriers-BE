"""UserPermission ORM model — per-user overrides to role default permissions.

Only stores rows that DIFFER from the role's default permission matrix.
If a user has no row for a given resource, the default for their role applies.

Audit trail for permission changes lives in the audit_log table (append-only).
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel


class UserPermission(BaseModel):
    """Per-user permission override for a specific resource."""

    __tablename__ = "user_permissions"
    __table_args__ = (UniqueConstraint("user_id", "resource", name="uq_user_permissions_user_resource"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    level: Mapped[int] = mapped_column(
        nullable=False,
    )
    granted_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user = relationship("User", foreign_keys=[user_id], lazy="raise")
    granted_by_user = relationship("User", foreign_keys=[granted_by], lazy="raise")

    def __repr__(self) -> str:
        return f"<UserPermission user={self.user_id} resource={self.resource} level={self.level}>"
