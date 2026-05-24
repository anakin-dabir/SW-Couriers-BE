"""Admin extension profile: business reference keyed to ``users`` (ADMIN / SUPER_ADMIN)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.enums.sequence import SequentialPrefix
from app.common.models import Base, BaseModel

if TYPE_CHECKING:
    from app.modules.user.models import User

admin_ref_seq = Sequence("admin_ref_seq", metadata=Base.metadata)


class Admin(BaseModel):
    __tablename__ = "admins"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_admins_user_id_users", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    admin_ref: Mapped[str] = mapped_column(
        String(15),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(
            f"'{SequentialPrefix.ADMIN}-' || lpad(nextval('{admin_ref_seq.name}')::text, 4, '0')"
        ),
    )

    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        server_default=text("'United Kingdom'"),
    )

    user = relationship("User", lazy="raise", foreign_keys=[user_id])
