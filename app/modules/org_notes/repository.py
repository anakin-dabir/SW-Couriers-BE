"""Repositories for org notes and note tags.

Custom queries are needed for:
- Soft-delete filtering (deleted_at IS NULL)
- Pinned-first ordering with text search
- Usage-count aggregation on tags (# of orgs that carry each tag)
- Org-level tag link management (org_tag_org_links)
- Mention link management (org_note_mentions)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.modules.org_notes.models import OrgNote, OrgNoteMention, OrgNoteTag, OrgTagOrgLink


class OrgNoteTagRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgNoteTag)

    async def get_by_name(self, name: str) -> OrgNoteTag | None:
        stmt = select(OrgNoteTag).where(OrgNoteTag.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[OrgNoteTag]:
        stmt = select(OrgNoteTag).order_by(OrgNoteTag.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_with_usage_count(self) -> list[tuple[OrgNoteTag, int]]:
        """Return all tags with the number of organisations carrying each tag."""
        usage_sub = (
            select(
                OrgTagOrgLink.tag_id,
                func.count(OrgTagOrgLink.organization_id).label("usage_count"),
            )
            .group_by(OrgTagOrgLink.tag_id)
            .subquery()
        )
        stmt = (
            select(OrgNoteTag, func.coalesce(usage_sub.c.usage_count, 0).label("usage_count"))
            .outerjoin(usage_sub, usage_sub.c.tag_id == OrgNoteTag.id)
            .order_by(OrgNoteTag.name.asc())
        )
        result = await self.session.execute(stmt)
        return [(row.OrgNoteTag, row.usage_count) for row in result.all()]

    async def get_by_ids(self, ids: list[str]) -> list[OrgNoteTag]:
        if not ids:
            return []
        stmt = select(OrgNoteTag).where(OrgNoteTag.id.in_(ids))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ── Org-level tag link management ────────────────────────────────

    async def get_org_tags(self, organization_id: str) -> list[OrgNoteTag]:
        """Return all tags currently attached to an organisation."""
        stmt = (
            select(OrgNoteTag)
            .join(OrgTagOrgLink, OrgTagOrgLink.tag_id == OrgNoteTag.id)
            .where(OrgTagOrgLink.organization_id == organization_id)
            .order_by(OrgNoteTag.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_org_tags(self, organization_id: str, tag_ids: list[str]) -> None:
        """Replace all tags on an organisation atomically."""
        await self.session.execute(
            delete(OrgTagOrgLink).where(OrgTagOrgLink.organization_id == organization_id)
        )
        if tag_ids:
            await self.session.execute(
                insert(OrgTagOrgLink),
                [{"tag_id": tid, "organization_id": organization_id} for tid in tag_ids],
            )
        await self.session.flush()


class OrgNoteRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgNote)

    # ── Read ─────────────────────────────────────────────────────────

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        categories: list[str] | None = None,
        search: str | None = None,
        pinned_first: bool = True,
    ) -> tuple[list[OrgNote], int]:
        """List active (non-deleted) notes for an organisation.

        Ordered pinned-first then newest. Supports optional multi-category filter
        and full-text search across title + content.
        """
        base = select(OrgNote).where(
            OrgNote.organization_id == organization_id,
            OrgNote.deleted_at.is_(None),
        )
        count_base = (
            select(func.count())
            .select_from(OrgNote)
            .where(
                OrgNote.organization_id == organization_id,
                OrgNote.deleted_at.is_(None),
            )
        )

        if categories:
            base = base.where(OrgNote.category.in_(categories))
            count_base = count_base.where(OrgNote.category.in_(categories))

        if search:
            pattern = f"%{search}%"
            base = base.where(
                OrgNote.title.ilike(pattern) | OrgNote.content.ilike(pattern)
            )
            count_base = count_base.where(
                OrgNote.title.ilike(pattern) | OrgNote.content.ilike(pattern)
            )

        total_result = await self.session.execute(count_base)
        total = total_result.scalar_one()

        if pinned_first:
            base = base.order_by(OrgNote.is_pinned.desc(), OrgNote.created_at.desc())
        else:
            base = base.order_by(OrgNote.created_at.desc())

        offset = (page - 1) * size
        base = base.offset(offset).limit(size)

        result = await self.session.execute(base)
        return list(result.scalars().all()), total

    async def count_pinned(self, organization_id: str, exclude_note_id: str | None = None) -> int:
        """Return the number of currently pinned (non-deleted) notes for an organisation."""
        stmt = (
            select(func.count())
            .select_from(OrgNote)
            .where(
                OrgNote.organization_id == organization_id,
                OrgNote.is_pinned.is_(True),
                OrgNote.deleted_at.is_(None),
            )
        )
        if exclude_note_id is not None:
            stmt = stmt.where(OrgNote.id != exclude_note_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_active(self, note_id: str, organization_id: str) -> OrgNote | None:
        """Fetch a non-deleted note scoped to the given organisation."""
        stmt = select(OrgNote).where(
            OrgNote.id == note_id,
            OrgNote.organization_id == organization_id,
            OrgNote.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_or_404(self, note_id: str, organization_id: str) -> OrgNote:
        note = await self.get_active(note_id, organization_id)
        if note is None:
            raise NotFoundError(resource="org_notes", id=note_id)
        return note

    # ── Soft delete ──────────────────────────────────────────────────

    async def soft_delete(  # type: ignore[override]
        self,
        note_id: str,
        organization_id: str,
    ) -> OrgNote:
        note = await self.get_active_or_404(note_id, organization_id)
        note.deleted_at = datetime.now(UTC)
        note.updated_at = datetime.now(UTC)
        note.version += 1
        await self.session.flush()
        await self.session.refresh(note)
        return note

    # ── Mention management ───────────────────────────────────────────

    async def set_mentions(self, note_id: str, user_ids: list[str]) -> None:
        """Replace all @mentions on a note atomically."""
        await self.session.execute(
            delete(OrgNoteMention).where(OrgNoteMention.note_id == note_id)
        )
        if user_ids:
            await self.session.execute(
                insert(OrgNoteMention),
                [{"note_id": note_id, "user_id": uid} for uid in user_ids],
            )
        await self.session.flush()

    async def get_mentioned_users(self, note_id: str) -> list[object]:
        """Return User objects for all mentions on a note."""
        stmt = select(OrgNoteMention).where(OrgNoteMention.note_id == note_id)
        result = await self.session.execute(stmt)
        mentions = list(result.scalars().all())
        return [m.user for m in mentions if m.user is not None]
