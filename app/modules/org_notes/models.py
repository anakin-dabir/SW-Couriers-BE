"""ORM models for internal organisation sticky notes and tags.

Notes are admin-only and never exposed to the B2B portal.

Tags (OrgNoteTag) are org-level labels — attached to an *organisation*, not
to individual notes.  The junction is OrgTagOrgLink (tag ↔ organisation).

Notes have a single ``category`` field (enum) for the note type and an
``OrgNoteMention`` junction for @mentioned users (in-app only).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import Base, BaseModel
from app.modules.org_notes.enums import NoteCategory


class OrgNoteTag(BaseModel):
    """Reusable org-level labels created and managed by admins.

    Examples: VIP, High Volume, Priority Client, Corporate Account.
    These attach to *organisations*, not to individual notes.
    """

    __tablename__ = "org_note_tags"

    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(30), nullable=False, default="#6B7280")

    # Organisations that carry this tag (via junction)
    org_links: Mapped[list[OrgTagOrgLink]] = relationship(
        "OrgTagOrgLink",
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class OrgTagOrgLink(Base):
    """Junction — attaches a tag to an organisation (many-to-many).

    Replaces the old note-level org_note_tag_links table.
    """

    __tablename__ = "org_tag_org_links"

    tag_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_note_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    tag: Mapped[OrgNoteTag] = relationship("OrgNoteTag", back_populates="org_links")


class OrgNote(BaseModel):
    """Internal sticky note on a client (organisation) profile.

    Admin-only — never returned to CUSTOMER_B2B endpoints.
    Soft-deleted via ``deleted_at``; hard rows are never purged.
    """

    __tablename__ = "org_notes"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[NoteCategory] = mapped_column(
        sa.Enum(NoteCategory, name="notecategory", create_type=False),
        nullable=False,
        default=NoteCategory.GENERAL,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    author: Mapped[object] = relationship(
        "User",
        foreign_keys=[author_id],
        lazy="joined",
    )
    mention_links: Mapped[list[OrgNoteMention]] = relationship(
        "OrgNoteMention",
        back_populates="note",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OrgNoteMention(Base):
    """Junction — records which users are @mentioned in a note (in-app only).

    No timestamps; the note's own timestamps cover the history.
    """

    __tablename__ = "org_note_mentions"

    note_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_notes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    note: Mapped[OrgNote] = relationship("OrgNote", back_populates="mention_links")
    user: Mapped[object] = relationship("User", foreign_keys=[user_id], lazy="joined")
