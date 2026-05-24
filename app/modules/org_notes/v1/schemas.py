"""Pydantic schemas for org notes and note tags (admin-only)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.common.schemas import BaseResponseSchema, BaseSchema, PaginatedResponse
from app.modules.org_notes.enums import NoteCategory


# ── Tag schemas ───────────────────────────────────────────────────────────────


class OrgNoteTagCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field(default="#6B7280", max_length=30)


class OrgNoteTagUpdate(BaseSchema):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, max_length=30)


class OrgNoteTagResponse(BaseResponseSchema):
    name: str
    color: str
    usage_count: int = 0


# ── Org-level tag assignment schemas ─────────────────────────────────────────


class OrgTagsUpdate(BaseSchema):
    """Body for PUT /organizations/{org_id}/tags — replaces all org tags."""

    tag_ids: list[str] = Field(default_factory=list)


class OrgTagsResponse(BaseSchema):
    """Response for org-level tag reads/writes."""

    tags: list[OrgNoteTagResponse]


# ── Note schemas ──────────────────────────────────────────────────────────────


class AuthorBrief(BaseSchema):
    """Minimal author identity embedded in note responses."""

    id: str
    first_name: str
    last_name: str
    full_name: str


class MentionBrief(BaseSchema):
    """Minimal user info for @mentioned users."""

    id: str
    first_name: str
    last_name: str
    full_name: str


class OrgNoteCreate(BaseSchema):
    category: NoteCategory = NoteCategory.GENERAL
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2000)
    is_pinned: bool = False
    mentioned_user_ids: list[str] = Field(default_factory=list)


class OrgNoteUpdate(BaseSchema):
    category: NoteCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    is_pinned: bool | None = None
    mentioned_user_ids: list[str] | None = None


class OrgNotePinUpdate(BaseSchema):
    is_pinned: bool


class OrgNoteResponse(BaseResponseSchema):
    organization_id: str
    author_id: str | None
    author: AuthorBrief | None
    category: NoteCategory
    title: str
    content: str
    is_pinned: bool
    deleted_at: datetime | None
    mentions: list[MentionBrief]

    @classmethod
    def from_note(cls, note: object) -> "OrgNoteResponse":
        from app.modules.org_notes.models import OrgNote

        n: OrgNote = note  # type: ignore[assignment]

        author_brief: AuthorBrief | None = None
        if n.author is not None:
            u = n.author
            first_name = getattr(u, "first_name", "") or ""
            last_name = getattr(u, "last_name", "") or ""
            author_brief = AuthorBrief(
                id=getattr(u, "id", ""),
                first_name=first_name,
                last_name=last_name,
                full_name=f"{first_name} {last_name}".strip(),
            )

        mentions: list[MentionBrief] = []
        for link in n.mention_links:
            u = link.user
            if u is None:
                continue
            first_name = getattr(u, "first_name", "") or ""
            last_name = getattr(u, "last_name", "") or ""
            mentions.append(
                MentionBrief(
                    id=getattr(u, "id", ""),
                    first_name=first_name,
                    last_name=last_name,
                    full_name=f"{first_name} {last_name}".strip(),
                )
            )

        return cls(
            id=n.id,
            organization_id=n.organization_id,
            author_id=n.author_id,
            author=author_brief,
            category=n.category,
            title=n.title,
            content=n.content,
            is_pinned=n.is_pinned,
            deleted_at=n.deleted_at,
            mentions=mentions,
            created_at=n.created_at,
            updated_at=n.updated_at,
            version=n.version,
        )


class OrgNoteListResponse(PaginatedResponse[OrgNoteResponse]):
    pass
