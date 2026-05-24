"""Integration API tests — OrgNote endpoints.

Covers:
- GET    /v1/organizations/{org_id}/notes                list notes (pagination, search, category filter, sort)
- POST   /v1/organizations/{org_id}/notes                create note
- GET    /v1/organizations/{org_id}/notes/{note_id}      get single note
- PATCH  /v1/organizations/{org_id}/notes/{note_id}      update note
- PATCH  /v1/organizations/{org_id}/notes/{note_id}/pin  toggle pin
- DELETE /v1/organizations/{org_id}/notes/{note_id}      soft delete

All tests use per-test transaction rollback (no persistent state).
"""

import uuid

import pytest
from httpx import AsyncClient

ORGS = "/v1/organizations"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _notes_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/notes"


def _note_url(org_id: str, note_id: str) -> str:
    return f"{ORGS}/{org_id}/notes/{note_id}"


def _note_payload(**overrides) -> dict:
    return {
        "title": "Test Note",
        "content": "This is a test note content.",
        "is_pinned": False,
        "category": "GENERAL",
        "mentioned_user_ids": [],
        **overrides,
    }


async def _create_note(
    client: AsyncClient, headers: dict, org_id: str, **overrides
) -> dict:
    resp = await client.post(_notes_url(org_id), json=_note_payload(**overrides), headers=headers)
    assert resp.status_code == 201
    return resp.json()["data"]


# ═══════════════════════════════════════════════════
#  LIST NOTES
# ═══════════════════════════════════════════════════


class TestListNotes:
    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_paginated(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.get(_notes_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_returns_created_notes(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        await _create_note(client, admin_headers, sample_org.id, title="Note A")
        await _create_note(client, admin_headers, sample_org.id, title="Note B")

        resp = await client.get(_notes_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        titles = [n["title"] for n in data["items"]]
        assert "Note A" in titles
        assert "Note B" in titles

    @pytest.mark.asyncio
    async def test_list_only_shows_notes_for_that_org(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ):
        org1 = await org_factory()
        org2 = await org_factory()
        await _create_note(client, admin_headers, org1.id, title="Org1 Note")
        await _create_note(client, admin_headers, org2.id, title="Org2 Note")

        resp = await client.get(_notes_url(org1.id), headers=admin_headers)
        titles = [n["title"] for n in resp.json()["data"]["items"]]
        assert "Org1 Note" in titles
        assert "Org2 Note" not in titles

    @pytest.mark.asyncio
    async def test_list_pagination(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        for i in range(5):
            await _create_note(client, admin_headers, sample_org.id, title=f"Note {i}")

        resp = await client.get(
            _notes_url(sample_org.id), params={"page": 1, "size": 2}, headers=admin_headers
        )
        data = resp.json()["data"]
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["pages"] == 3

    @pytest.mark.asyncio
    async def test_list_pinned_sort_shows_pinned_first(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        await _create_note(client, admin_headers, sample_org.id, title="Unpinned", is_pinned=False)
        await _create_note(client, admin_headers, sample_org.id, title="Pinned", is_pinned=True)

        resp = await client.get(
            _notes_url(sample_org.id), params={"sort": "pinned"}, headers=admin_headers
        )
        items = resp.json()["data"]["items"]
        assert items[0]["is_pinned"] is True
        assert items[0]["title"] == "Pinned"

    @pytest.mark.asyncio
    async def test_list_filter_by_category(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        await _create_note(client, admin_headers, sample_org.id, title="Escalated", category="ESCALATION")
        await _create_note(client, admin_headers, sample_org.id, title="General Note", category="GENERAL")

        resp = await client.get(
            _notes_url(sample_org.id), params={"category": "ESCALATION"}, headers=admin_headers
        )
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Escalated"
        assert data["items"][0]["category"] == "ESCALATION"

    @pytest.mark.asyncio
    async def test_list_search_by_title(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        await _create_note(client, admin_headers, sample_org.id, title="Meeting summary")
        await _create_note(client, admin_headers, sample_org.id, title="Invoice discussion")

        resp = await client.get(
            _notes_url(sample_org.id), params={"search": "meeting"}, headers=admin_headers
        )
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Meeting summary"

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_notes_url(sample_org.id))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.get(_notes_url(sample_org.id), headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  CREATE NOTE
# ═══════════════════════════════════════════════════


class TestCreateNote:
    @pytest.mark.asyncio
    async def test_create_basic_note(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(title="My Note", content="Some content"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["title"] == "My Note"
        assert data["content"] == "Some content"
        assert data["is_pinned"] is False
        assert data["category"] == "GENERAL"
        assert data["mentions"] == []
        assert data["organization_id"] == sample_org.id
        assert data["author"] is not None

    @pytest.mark.asyncio
    async def test_create_with_category(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(category="ESCALATION"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["category"] == "ESCALATION"

    @pytest.mark.asyncio
    async def test_create_all_valid_categories(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        categories = ["GENERAL", "MEETING_NOTES", "PHONE_CALL", "ESCALATION", "COMPLIANCE", "COMMERCIAL"]
        for cat in categories:
            resp = await client.post(
                _notes_url(sample_org.id),
                json=_note_payload(category=cat),
                headers=admin_headers,
            )
            assert resp.status_code == 201, f"Failed for category {cat}"
            assert resp.json()["data"]["category"] == cat

    @pytest.mark.asyncio
    async def test_create_invalid_category_returns_422(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(category="INVALID_CATEGORY"),
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_pinned_note(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(is_pinned=True),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["is_pinned"] is True

    @pytest.mark.asyncio
    async def test_create_note_with_mentions(
        self, client: AsyncClient, admin_headers: dict, sample_org, user_factory
    ):
        mentioned_user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(mentioned_user_ids=[mentioned_user.id]),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        mention_ids = [m["id"] for m in resp.json()["data"]["mentions"]]
        assert mentioned_user.id in mention_ids

    @pytest.mark.asyncio
    async def test_create_note_missing_title_returns_422(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json={"content": "No title"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_note_empty_content_returns_422(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.post(
            _notes_url(sample_org.id),
            json=_note_payload(content=""),
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_note_unknown_org_returns_error(
        self, client: AsyncClient, admin_headers: dict
    ):
        """Unknown org_id violates the FK constraint — returns 422 (integrity error)."""
        resp = await client.post(
            _notes_url(str(uuid.uuid4())),
            json=_note_payload(),
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.post(_notes_url(sample_org.id), json=_note_payload())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.post(
            _notes_url(sample_org.id), json=_note_payload(), headers=auth_headers
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  GET SINGLE NOTE
# ═══════════════════════════════════════════════════


class TestGetNote:
    @pytest.mark.asyncio
    async def test_get_existing_note(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, title="Get Me")
        resp = await client.get(_note_url(sample_org.id, note["id"]), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Get Me"

    @pytest.mark.asyncio
    async def test_get_note_has_expected_fields(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, category="COMMERCIAL")
        resp = await client.get(_note_url(sample_org.id, note["id"]), headers=admin_headers)
        data = resp.json()["data"]
        assert "category" in data
        assert "mentions" in data
        assert data["category"] == "COMMERCIAL"
        assert isinstance(data["mentions"], list)

    @pytest.mark.asyncio
    async def test_get_unknown_note_returns_404(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.get(_note_url(sample_org.id, str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_note_wrong_org_returns_404(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ):
        org1 = await org_factory()
        org2 = await org_factory()
        note = await _create_note(client, admin_headers, org1.id)
        resp = await client.get(_note_url(org2.id, note["id"]), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_note_url(sample_org.id, str(uuid.uuid4())))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.get(_note_url(sample_org.id, str(uuid.uuid4())), headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  UPDATE NOTE
# ═══════════════════════════════════════════════════


class TestUpdateNote:
    @pytest.mark.asyncio
    async def test_update_title(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, title="Original")
        resp = await client.patch(
            _note_url(sample_org.id, note["id"]),
            json={"title": "Updated"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Updated"

    @pytest.mark.asyncio
    async def test_update_content(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id)
        resp = await client.patch(
            _note_url(sample_org.id, note["id"]),
            json={"content": "New content here"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["content"] == "New content here"

    @pytest.mark.asyncio
    async def test_update_category(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, category="GENERAL")
        resp = await client.patch(
            _note_url(sample_org.id, note["id"]),
            json={"category": "PHONE_CALL"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["category"] == "PHONE_CALL"

    @pytest.mark.asyncio
    async def test_update_mentions(
        self, client: AsyncClient, admin_headers: dict, sample_org, user_factory
    ):
        user1 = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        note = await _create_note(client, admin_headers, sample_org.id)

        resp = await client.patch(
            _note_url(sample_org.id, note["id"]),
            json={"mentioned_user_ids": [user1.id]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        mention_ids = [m["id"] for m in resp.json()["data"]["mentions"]]
        assert user1.id in mention_ids

    @pytest.mark.asyncio
    async def test_update_clear_mentions(
        self, client: AsyncClient, admin_headers: dict, sample_org, user_factory
    ):
        user1 = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        note = await _create_note(
            client, admin_headers, sample_org.id, mentioned_user_ids=[user1.id]
        )

        resp = await client.patch(
            _note_url(sample_org.id, note["id"]),
            json={"mentioned_user_ids": []},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["mentions"] == []

    @pytest.mark.asyncio
    async def test_update_unknown_note_returns_404(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.patch(
            _note_url(sample_org.id, str(uuid.uuid4())),
            json={"title": "X"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.patch(
            _note_url(sample_org.id, str(uuid.uuid4())),
            json={"title": "X"},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  TOGGLE PIN
# ═══════════════════════════════════════════════════


class TestTogglePin:
    @pytest.mark.asyncio
    async def test_pin_note(self, client: AsyncClient, admin_headers: dict, sample_org):
        note = await _create_note(client, admin_headers, sample_org.id, is_pinned=False)
        resp = await client.patch(
            f"{_note_url(sample_org.id, note['id'])}/pin",
            json={"is_pinned": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_pinned"] is True

    @pytest.mark.asyncio
    async def test_unpin_note(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, is_pinned=True)
        resp = await client.patch(
            f"{_note_url(sample_org.id, note['id'])}/pin",
            json={"is_pinned": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_pinned"] is False

    @pytest.mark.asyncio
    async def test_pin_unknown_note_returns_404(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.patch(
            f"{_note_url(sample_org.id, str(uuid.uuid4()))}/pin",
            json={"is_pinned": True},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_pin_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.patch(
            f"{_note_url(sample_org.id, str(uuid.uuid4()))}/pin",
            json={"is_pinned": True},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  DELETE NOTE
# ═══════════════════════════════════════════════════


class TestDeleteNote:
    @pytest.mark.asyncio
    async def test_delete_note(self, client: AsyncClient, admin_headers: dict, sample_org):
        note = await _create_note(client, admin_headers, sample_org.id, title="Delete Me")
        resp = await client.delete(_note_url(sample_org.id, note["id"]), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Note deleted."

    @pytest.mark.asyncio
    async def test_deleted_note_not_returned_in_list(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id, title="Gone")
        await client.delete(_note_url(sample_org.id, note["id"]), headers=admin_headers)

        resp = await client.get(_notes_url(sample_org.id), headers=admin_headers)
        ids = [n["id"] for n in resp.json()["data"]["items"]]
        assert note["id"] not in ids

    @pytest.mark.asyncio
    async def test_deleted_note_get_returns_404(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        note = await _create_note(client, admin_headers, sample_org.id)
        await client.delete(_note_url(sample_org.id, note["id"]), headers=admin_headers)

        resp = await client.get(_note_url(sample_org.id, note["id"]), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_unknown_note_returns_404(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.delete(
            _note_url(sample_org.id, str(uuid.uuid4())), headers=admin_headers
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.delete(_note_url(sample_org.id, str(uuid.uuid4())))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.delete(
            _note_url(sample_org.id, str(uuid.uuid4())), headers=auth_headers
        )
        assert resp.status_code == 403
