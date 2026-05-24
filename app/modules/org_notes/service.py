"""Business logic for organisation sticky notes and note tags.

All operations are admin-only. Notes are never surfaced to CUSTOMER_B2B.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.enums import LogEvent
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_notes.enums import NoteCategory
from app.modules.org_notes.models import OrgNote, OrgNoteTag
from app.modules.org_notes.repository import OrgNoteRepository, OrgNoteTagRepository


# ── Tag service ───────────────────────────────────────────────────────────────


class OrgNoteTagService(BaseService):
    """CRUD for reusable org-level tags (VIP, High Volume, etc.) and org assignment."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = OrgNoteTagRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    # ── Global tag CRUD ───────────────────────────────────────────────

    async def list_tags(self) -> list[tuple[OrgNoteTag, int]]:
        """Return all tags with their usage count (# of orgs carrying them)."""
        return await self._repo.list_all_with_usage_count()

    async def get_tag(self, tag_id: str) -> OrgNoteTag:
        return await self._repo.get_by_id_or_404(tag_id)

    async def create_tag(
        self,
        *,
        name: str,
        color: str,
        admin_user_id: str,
        admin_role: str,
    ) -> OrgNoteTag:
        existing = await self._repo.get_by_name(name)
        if existing:
            raise ConflictError(f"Tag with name '{name}' already exists.")

        tag = await self._repo.create({"name": name, "color": color})

        await self._audit.log(
            action="org_note_tag.create",
            entity_type="org_note_tag",
            entity_id=tag.id,
            user_id=admin_user_id,
            user_role=admin_role,
            new_value={"name": tag.name, "color": tag.color},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.TAG_ADDED,
        )
        return tag

    async def update_tag(
        self,
        tag_id: str,
        *,
        name: str | None,
        color: str | None,
        admin_user_id: str,
        admin_role: str,
    ) -> OrgNoteTag:
        tag = await self._repo.get_by_id_or_404(tag_id)

        if name is not None and name != tag.name:
            existing = await self._repo.get_by_name(name)
            if existing:
                raise ConflictError(f"Tag with name '{name}' already exists.")

        old = {"name": tag.name, "color": tag.color}
        data: dict[str, object] = {}
        if name is not None:
            data["name"] = name
        if color is not None:
            data["color"] = color

        if data:
            tag = await self._repo.update_by_id(tag_id, data)

        await self._audit.log(
            action="org_note_tag.update",
            entity_type="org_note_tag",
            entity_id=tag_id,
            user_id=admin_user_id,
            user_role=admin_role,
            old_value=old,
            new_value={"name": tag.name, "color": tag.color},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.TAG_UPDATED,
        )
        return tag

    async def delete_tag(
        self,
        tag_id: str,
        *,
        admin_user_id: str,
        admin_role: str,
    ) -> None:
        tag = await self._repo.get_by_id_or_404(tag_id)
        await self._repo.hard_delete(tag_id)
        await self._audit.log(
            action="org_note_tag.delete",
            entity_type="org_note_tag",
            entity_id=tag_id,
            user_id=admin_user_id,
            user_role=admin_role,
            old_value={"name": tag.name, "color": tag.color},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.TAG_REMOVED,
        )

    # ── Org-level tag assignment ──────────────────────────────────────

    async def get_org_tags(self, org_id: str) -> list[OrgNoteTag]:
        """Return tags currently attached to an organisation."""
        return await self._repo.get_org_tags(org_id)

    async def set_org_tags(
        self,
        org_id: str,
        tag_ids: list[str],
        *,
        admin_user_id: str,
        admin_role: str,
    ) -> list[OrgNoteTag]:
        """Replace all tags on an organisation. Validates that all IDs exist."""
        if tag_ids:
            found = await self._repo.get_by_ids(tag_ids)
            found_ids = {t.id for t in found}
            missing = [tid for tid in tag_ids if tid not in found_ids]
            if missing:
                raise ValidationError(f"Tag IDs not found: {missing}")

        old_tags = await self._repo.get_org_tags(org_id)
        await self._repo.set_org_tags(org_id, tag_ids)

        await self._audit.log(
            action="org.tags.update",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            user_role=admin_role,
            old_value={"tag_ids": [t.id for t in old_tags]},
            new_value={"tag_ids": tag_ids},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.TAG_UPDATED,
        )
        return await self._repo.get_org_tags(org_id)


# ── Note service ──────────────────────────────────────────────────────────────


class OrgNoteService(BaseService):
    """CRUD for sticky notes on an organisation profile."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._note_repo = OrgNoteRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    # ── Read ─────────────────────────────────────────────────────────

    async def list_notes(
        self,
        org_id: str,
        *,
        page: int = 1,
        size: int = 20,
        categories: list[str] | None = None,
        search: str | None = None,
        sort: str = "pinned",
    ) -> tuple[list[OrgNote], int]:
        pinned_first = sort != "newest"
        return await self._note_repo.list_for_org(
            org_id,
            page=page,
            size=size,
            categories=categories or [],
            search=search,
            pinned_first=pinned_first,
        )

    async def get_note(self, org_id: str, note_id: str) -> OrgNote:
        return await self._note_repo.get_active_or_404(note_id, org_id)

    # ── Write ────────────────────────────────────────────────────────

    MAX_PINNED = 3

    async def _assert_pin_limit(
        self, org_id: str, exclude_note_id: str | None = None
    ) -> None:
        """Raise ValidationError if pinning would exceed MAX_PINNED."""
        count = await self._note_repo.count_pinned(org_id, exclude_note_id=exclude_note_id)
        if count >= self.MAX_PINNED:
            raise ValidationError(
                f"Cannot pin more than {self.MAX_PINNED} notes per organisation."
            )

    async def create_note(
        self,
        org_id: str,
        *,
        category: NoteCategory,
        title: str,
        content: str,
        is_pinned: bool,
        mentioned_user_ids: list[str],
        author_id: str,
        author_role: str,
    ) -> OrgNote:
        if is_pinned:
            await self._assert_pin_limit(org_id)

        note = await self._note_repo.create(
            {
                "organization_id": org_id,
                "author_id": author_id,
                "category": category,
                "title": title,
                "content": content,
                "is_pinned": is_pinned,
            }
        )

        if mentioned_user_ids:
            await self._note_repo.set_mentions(note.id, mentioned_user_ids)

        await self._session.refresh(note)

        await self._audit.log(
            action="org_note.create",
            entity_type="org_note",
            entity_id=note.id,
            user_id=author_id,
            user_role=author_role,
            new_value={
                "organization_id": org_id,
                "category": category,
                "title": title,
                "is_pinned": is_pinned,
                "mentioned_user_ids": mentioned_user_ids,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.NOTE_ADDED,
        )
        return note

    async def update_note(
        self,
        org_id: str,
        note_id: str,
        *,
        category: NoteCategory | None,
        title: str | None,
        content: str | None,
        is_pinned: bool | None,
        mentioned_user_ids: list[str] | None,
        admin_user_id: str,
        admin_role: str,
    ) -> OrgNote:
        note = await self._note_repo.get_active_or_404(note_id, org_id)

        # Only check the pin limit when the note is transitioning from unpinned → pinned
        if is_pinned is True and not note.is_pinned:
            await self._assert_pin_limit(org_id, exclude_note_id=note_id)

        old = {
            "category": note.category,
            "title": note.title,
            "content": note.content,
            "is_pinned": note.is_pinned,
        }

        data: dict[str, object] = {}
        if category is not None:
            data["category"] = category
        if title is not None:
            data["title"] = title
        if content is not None:
            data["content"] = content
        if is_pinned is not None:
            data["is_pinned"] = is_pinned

        if data:
            note = await self._note_repo.update_by_id(note_id, data)

        if mentioned_user_ids is not None:
            await self._note_repo.set_mentions(note_id, mentioned_user_ids)
            await self._session.refresh(note)

        await self._audit.log(
            action="org_note.update",
            entity_type="org_note",
            entity_id=note_id,
            user_id=admin_user_id,
            user_role=admin_role,
            old_value=old,
            new_value={
                "category": note.category,
                "title": note.title,
                "content": note.content,
                "is_pinned": note.is_pinned,
                "mentioned_user_ids": mentioned_user_ids,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.NOTE_UPDATED,
        )
        return note

    async def toggle_pin(
        self,
        org_id: str,
        note_id: str,
        *,
        is_pinned: bool,
        admin_user_id: str,
        admin_role: str,
    ) -> OrgNote:
        existing = await self._note_repo.get_active_or_404(note_id, org_id)
        if is_pinned and not existing.is_pinned:
            await self._assert_pin_limit(org_id, exclude_note_id=note_id)
        note = await self._note_repo.update_by_id(note_id, {"is_pinned": is_pinned})
        await self._audit.log(
            action="org_note.pin" if is_pinned else "org_note.unpin",
            entity_type="org_note",
            entity_id=note_id,
            user_id=admin_user_id,
            user_role=admin_role,
            new_value={"is_pinned": is_pinned},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.NOTE_PINNED,
        )
        return note

    async def delete_note(
        self,
        org_id: str,
        note_id: str,
        *,
        admin_user_id: str,
        admin_role: str,
    ) -> None:
        note = await self._note_repo.soft_delete(note_id, org_id)
        await self._audit.log(
            action="org_note.delete",
            entity_type="org_note",
            entity_id=note_id,
            user_id=admin_user_id,
            user_role=admin_role,
            old_value={"title": note.title, "organization_id": org_id},
            ip_address=self._ip,
            user_agent=self._ua,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.NOTE_DELETED,
        )
