"""FastAPI routes for org sticky notes and note tags.

All endpoints are admin-only — never exposed to CUSTOMER_B2B.

Note routes are mounted under /organizations (prefix shared with the org
router) so the final paths become:
    /v1/organizations/{org_id}/notes/...
    /v1/organizations/{org_id}/tags

Tag management routes are mounted separately under /org-note-tags.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.deps import Allowed, CurrentUserDep
from app.common.enums.user import UserRole
from app.common.schemas import MessageResponse, SuccessResponse
from app.modules.org_notes.enums import NoteCategory
from app.modules.org_notes.service import OrgNoteService, OrgNoteTagService
from app.modules.org_notes.v1.schemas import (
    OrgNoteCreate,
    OrgNoteListResponse,
    OrgNotePinUpdate,
    OrgNoteResponse,
    OrgNoteTagCreate,
    OrgNoteTagResponse,
    OrgNoteTagUpdate,
    OrgNoteUpdate,
    OrgTagsResponse,
    OrgTagsUpdate,
)

# ── Dependency aliases ────────────────────────────────────────────────────────

AdminDep = Annotated[CurrentUserDep, Allowed(UserRole.ADMIN)]
NoteServiceDep = Annotated[OrgNoteService, Depends(OrgNoteService.dep)]
TagServiceDep = Annotated[OrgNoteTagService, Depends(OrgNoteTagService.dep)]

# ── Routers ───────────────────────────────────────────────────────────────────

notes_router = APIRouter()   # mounted at /organizations
tags_router = APIRouter()    # mounted at /org-note-tags


# ── Global tag management endpoints ──────────────────────────────────────────


@tags_router.get(
    "",
    response_model=SuccessResponse[list[OrgNoteTagResponse]],
    summary="List all note tags with usage count",
)
async def list_tags(
    admin: AdminDep,
    svc: TagServiceDep,
) -> SuccessResponse[list[OrgNoteTagResponse]]:
    tag_counts = await svc.list_tags()
    return SuccessResponse(
        data=[
            OrgNoteTagResponse(
                id=tag.id,
                name=tag.name,
                color=tag.color,
                usage_count=count,
                created_at=tag.created_at,
                updated_at=tag.updated_at,
                version=tag.version,
            )
            for tag, count in tag_counts
        ]
    )


@tags_router.post(
    "",
    response_model=SuccessResponse[OrgNoteTagResponse],
    status_code=201,
    summary="Create a note tag",
)
async def create_tag(
    body: OrgNoteTagCreate,
    admin: AdminDep,
    svc: TagServiceDep,
) -> SuccessResponse[OrgNoteTagResponse]:
    tag = await svc.create_tag(
        name=body.name,
        color=body.color,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    return SuccessResponse(
        data=OrgNoteTagResponse(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            usage_count=0,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
            version=tag.version,
        )
    )


@tags_router.patch(
    "/{tag_id}",
    response_model=SuccessResponse[OrgNoteTagResponse],
    summary="Update a note tag",
)
async def update_tag(
    tag_id: str,
    body: OrgNoteTagUpdate,
    admin: AdminDep,
    svc: TagServiceDep,
) -> SuccessResponse[OrgNoteTagResponse]:
    tag = await svc.update_tag(
        tag_id,
        name=body.name,
        color=body.color,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    # Re-fetch usage count for updated tag
    tag_counts = await svc.list_tags()
    count = next((c for t, c in tag_counts if t.id == tag_id), 0)
    return SuccessResponse(
        data=OrgNoteTagResponse(
            id=tag.id,
            name=tag.name,
            color=tag.color,
            usage_count=count,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
            version=tag.version,
        )
    )


@tags_router.delete(
    "/{tag_id}",
    response_model=MessageResponse,
    summary="Delete a note tag",
)
async def delete_tag(
    tag_id: str,
    admin: AdminDep,
    svc: TagServiceDep,
) -> MessageResponse:
    await svc.delete_tag(
        tag_id,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    return MessageResponse(message="Tag deleted.")


# ── Org-level tag assignment endpoints ───────────────────────────────────────


@notes_router.get(
    "/{org_id}/tags",
    response_model=SuccessResponse[OrgTagsResponse],
    summary="Get tags attached to an organisation",
)
async def get_org_tags(
    org_id: str,
    admin: AdminDep,
    svc: TagServiceDep,
) -> SuccessResponse[OrgTagsResponse]:
    tags = await svc.get_org_tags(org_id)
    tag_counts = await svc.list_tags()
    count_map = {t.id: c for t, c in tag_counts}
    return SuccessResponse(
        data=OrgTagsResponse(
            tags=[
                OrgNoteTagResponse(
                    id=t.id,
                    name=t.name,
                    color=t.color,
                    usage_count=count_map.get(t.id, 0),
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                    version=t.version,
                )
                for t in tags
            ]
        )
    )


@notes_router.put(
    "/{org_id}/tags",
    response_model=SuccessResponse[OrgTagsResponse],
    summary="Replace all tags on an organisation",
)
async def set_org_tags(
    org_id: str,
    body: OrgTagsUpdate,
    admin: AdminDep,
    svc: TagServiceDep,
) -> SuccessResponse[OrgTagsResponse]:
    tags = await svc.set_org_tags(
        org_id,
        body.tag_ids,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    tag_counts = await svc.list_tags()
    count_map = {t.id: c for t, c in tag_counts}
    return SuccessResponse(
        data=OrgTagsResponse(
            tags=[
                OrgNoteTagResponse(
                    id=t.id,
                    name=t.name,
                    color=t.color,
                    usage_count=count_map.get(t.id, 0),
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                    version=t.version,
                )
                for t in tags
            ]
        )
    )


# ── Note endpoints ────────────────────────────────────────────────────────────


@notes_router.get(
    "/{org_id}/notes",
    response_model=SuccessResponse[OrgNoteListResponse],
    summary="List notes for an organisation",
)
async def list_notes(
    request: Request,
    org_id: str,
    admin: AdminDep,
    svc: NoteServiceDep,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    category: list[NoteCategory] | None = Query(default=None, description="Filter by one or more categories"),
    search: str | None = Query(default=None, description="Search title and content"),
    sort: str = Query(default="pinned", description="'pinned' or 'newest'"),
) -> SuccessResponse[OrgNoteListResponse]:
    notes, total = await svc.list_notes(
        org_id,
        page=page,
        size=size,
        categories=category or [],
        search=search,
        sort=sort,
    )
    items = [OrgNoteResponse.from_note(n) for n in notes]
    return SuccessResponse(
        data=OrgNoteListResponse.create(
            items=items, total=total, page=page, size=size, request=request
        )
    )


@notes_router.post(
    "/{org_id}/notes",
    response_model=SuccessResponse[OrgNoteResponse],
    status_code=201,
    summary="Create a note on an organisation",
)
async def create_note(
    org_id: str,
    body: OrgNoteCreate,
    admin: AdminDep,
    svc: NoteServiceDep,
) -> SuccessResponse[OrgNoteResponse]:
    note = await svc.create_note(
        org_id,
        category=body.category,
        title=body.title,
        content=body.content,
        is_pinned=body.is_pinned,
        mentioned_user_ids=body.mentioned_user_ids,
        author_id=admin.id,
        author_role=admin.role,
    )
    return SuccessResponse(data=OrgNoteResponse.from_note(note))


@notes_router.get(
    "/{org_id}/notes/{note_id}",
    response_model=SuccessResponse[OrgNoteResponse],
    summary="Get a single note",
)
async def get_note(
    org_id: str,
    note_id: str,
    admin: AdminDep,
    svc: NoteServiceDep,
) -> SuccessResponse[OrgNoteResponse]:
    note = await svc.get_note(org_id, note_id)
    return SuccessResponse(data=OrgNoteResponse.from_note(note))


@notes_router.patch(
    "/{org_id}/notes/{note_id}",
    response_model=SuccessResponse[OrgNoteResponse],
    summary="Edit a note (category, title, content, mentions, pin state)",
)
async def update_note(
    org_id: str,
    note_id: str,
    body: OrgNoteUpdate,
    admin: AdminDep,
    svc: NoteServiceDep,
) -> SuccessResponse[OrgNoteResponse]:
    note = await svc.update_note(
        org_id,
        note_id,
        category=body.category,
        title=body.title,
        content=body.content,
        is_pinned=body.is_pinned,
        mentioned_user_ids=body.mentioned_user_ids,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    return SuccessResponse(data=OrgNoteResponse.from_note(note))


@notes_router.patch(
    "/{org_id}/notes/{note_id}/pin",
    response_model=SuccessResponse[OrgNoteResponse],
    summary="Pin or unpin a note",
)
async def toggle_pin(
    org_id: str,
    note_id: str,
    body: OrgNotePinUpdate,
    admin: AdminDep,
    svc: NoteServiceDep,
) -> SuccessResponse[OrgNoteResponse]:
    note = await svc.toggle_pin(
        org_id,
        note_id,
        is_pinned=body.is_pinned,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    return SuccessResponse(data=OrgNoteResponse.from_note(note))


@notes_router.delete(
    "/{org_id}/notes/{note_id}",
    response_model=MessageResponse,
    summary="Soft-delete a note",
)
async def delete_note(
    org_id: str,
    note_id: str,
    admin: AdminDep,
    svc: NoteServiceDep,
) -> MessageResponse:
    await svc.delete_note(
        org_id,
        note_id,
        admin_user_id=admin.id,
        admin_role=admin.role,
    )
    return MessageResponse(message="Note deleted.")
