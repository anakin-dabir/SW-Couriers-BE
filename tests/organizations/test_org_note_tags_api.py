"""Integration API tests — OrgNoteTag endpoints.

Covers:
- GET    /v1/org-note-tags              list all tags (with usage_count)
- POST   /v1/org-note-tags              create tag
- PATCH  /v1/org-note-tags/{id}         update tag
- DELETE /v1/org-note-tags/{id}         delete tag
- GET    /v1/organizations/{id}/tags    get tags on an org
- PUT    /v1/organizations/{id}/tags    set tags on an org

All tests use per-test transaction rollback (no persistent state).
"""

import uuid

import pytest
from httpx import AsyncClient

TAGS = "/v1/org-note-tags"
ORGS = "/v1/organizations"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _unique_name(prefix: str = "Tag") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _tag_payload(**overrides) -> dict:
    return {"name": _unique_name(), "color": "#FF0000", **overrides}


async def _create_tag(client: AsyncClient, headers: dict, name: str | None = None) -> dict:
    payload = _tag_payload(name=name or _unique_name())
    resp = await client.post(TAGS, json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()["data"]


def _org_tags_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/tags"


# ═══════════════════════════════════════════════════
#  LIST TAGS
# ═══════════════════════════════════════════════════


class TestListTags:
    @pytest.mark.asyncio
    async def test_list_returns_success_with_list(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.get(TAGS, headers=admin_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    @pytest.mark.asyncio
    async def test_list_returns_created_tags(
        self, client: AsyncClient, admin_headers: dict
    ):
        name_a = _unique_name("VIP")
        name_b = _unique_name("HighVol")
        await client.post(TAGS, json=_tag_payload(name=name_a), headers=admin_headers)
        await client.post(TAGS, json=_tag_payload(name=name_b), headers=admin_headers)

        resp = await client.get(TAGS, headers=admin_headers)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["data"]]
        assert name_a in names
        assert name_b in names

    @pytest.mark.asyncio
    async def test_list_includes_usage_count(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(TAGS, json=_tag_payload(name=_unique_name()), headers=admin_headers)
        tag_id = resp.json()["data"]["id"]

        resp = await client.get(TAGS, headers=admin_headers)
        tag = next(t for t in resp.json()["data"] if t["id"] == tag_id)
        assert "usage_count" in tag
        assert tag["usage_count"] == 0

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get(TAGS)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_requires_admin(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get(TAGS, headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  CREATE TAG
# ═══════════════════════════════════════════════════


class TestCreateTag:
    @pytest.mark.asyncio
    async def test_create_success(self, client: AsyncClient, admin_headers: dict):
        name = _unique_name("Meeting")
        resp = await client.post(TAGS, json=_tag_payload(name=name), headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == name
        assert data["color"] == "#FF0000"
        assert data["usage_count"] == 0
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_create_uses_default_color_when_omitted(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(TAGS, json={"name": _unique_name("NoColor")}, headers=admin_headers)
        assert resp.status_code == 201
        assert resp.json()["data"]["color"] == "#6B7280"

    @pytest.mark.asyncio
    async def test_create_duplicate_name_returns_409(
        self, client: AsyncClient, admin_headers: dict
    ):
        name = _unique_name("Dup")
        await client.post(TAGS, json=_tag_payload(name=name), headers=admin_headers)
        resp = await client.post(TAGS, json=_tag_payload(name=name), headers=admin_headers)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_empty_name_returns_422(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(TAGS, json={"name": ""}, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_name_too_long_returns_422(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(TAGS, json={"name": "x" * 51}, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, client: AsyncClient):
        resp = await client.post(TAGS, json=_tag_payload())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_requires_admin(self, client: AsyncClient, auth_headers: dict):
        resp = await client.post(TAGS, json=_tag_payload(), headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  UPDATE TAG
# ═══════════════════════════════════════════════════


class TestUpdateTag:
    @pytest.mark.asyncio
    async def test_update_name(self, client: AsyncClient, admin_headers: dict):
        tag = await _create_tag(client, admin_headers)
        new_name = _unique_name("New")
        resp = await client.patch(f"{TAGS}/{tag['id']}", json={"name": new_name}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == new_name

    @pytest.mark.asyncio
    async def test_update_color(self, client: AsyncClient, admin_headers: dict):
        tag = await _create_tag(client, admin_headers)
        resp = await client.patch(f"{TAGS}/{tag['id']}", json={"color": "#00FF00"}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["color"] == "#00FF00"

    @pytest.mark.asyncio
    async def test_update_preserves_usage_count(
        self, client: AsyncClient, admin_headers: dict
    ):
        tag = await _create_tag(client, admin_headers)
        resp = await client.patch(
            f"{TAGS}/{tag['id']}", json={"color": "#123456"}, headers=admin_headers
        )
        assert resp.status_code == 200
        assert "usage_count" in resp.json()["data"]

    @pytest.mark.asyncio
    async def test_update_name_conflict_returns_409(
        self, client: AsyncClient, admin_headers: dict
    ):
        name_a = _unique_name("Existing")
        name_b = _unique_name("Other")
        await client.post(TAGS, json=_tag_payload(name=name_a), headers=admin_headers)
        tag2 = await _create_tag(client, admin_headers, name=name_b)

        resp = await client.patch(f"{TAGS}/{tag2['id']}", json={"name": name_a}, headers=admin_headers)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_update_same_name_is_allowed(
        self, client: AsyncClient, admin_headers: dict
    ):
        tag = await _create_tag(client, admin_headers)
        resp = await client.patch(f"{TAGS}/{tag['id']}", json={"name": tag["name"]}, headers=admin_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_unknown_tag_returns_404(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.patch(f"{TAGS}/{uuid.uuid4()}", json={"name": "X"}, headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, auth_headers: dict):
        resp = await client.patch(f"{TAGS}/{uuid.uuid4()}", json={"name": "X"}, headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  DELETE TAG
# ═══════════════════════════════════════════════════


class TestDeleteTag:
    @pytest.mark.asyncio
    async def test_delete_success(self, client: AsyncClient, admin_headers: dict):
        tag = await _create_tag(client, admin_headers)
        resp = await client.delete(f"{TAGS}/{tag['id']}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Tag deleted."

    @pytest.mark.asyncio
    async def test_deleted_tag_no_longer_in_list(
        self, client: AsyncClient, admin_headers: dict
    ):
        tag = await _create_tag(client, admin_headers)
        await client.delete(f"{TAGS}/{tag['id']}", headers=admin_headers)

        resp = await client.get(TAGS, headers=admin_headers)
        ids = [t["id"] for t in resp.json()["data"]]
        assert tag["id"] not in ids

    @pytest.mark.asyncio
    async def test_delete_unknown_tag_returns_404(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.delete(f"{TAGS}/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, client: AsyncClient):
        resp = await client.delete(f"{TAGS}/{uuid.uuid4()}")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, client: AsyncClient, auth_headers: dict):
        resp = await client.delete(f"{TAGS}/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  ORG-LEVEL TAG ASSIGNMENT
# ═══════════════════════════════════════════════════


class TestOrgTags:
    @pytest.mark.asyncio
    async def test_get_org_tags_empty(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.get(_org_tags_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["tags"] == []

    @pytest.mark.asyncio
    async def test_set_org_tags(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        tag = await _create_tag(client, admin_headers)
        resp = await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [tag["id"]]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        tag_ids = [t["id"] for t in resp.json()["data"]["tags"]]
        assert tag["id"] in tag_ids

    @pytest.mark.asyncio
    async def test_set_org_tags_updates_usage_count(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        tag = await _create_tag(client, admin_headers)

        # Assign to org
        await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [tag["id"]]},
            headers=admin_headers,
        )

        # Usage count should now be 1
        resp = await client.get(TAGS, headers=admin_headers)
        found = next(t for t in resp.json()["data"] if t["id"] == tag["id"])
        assert found["usage_count"] == 1

    @pytest.mark.asyncio
    async def test_set_org_tags_replaces_existing(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        tag_a = await _create_tag(client, admin_headers)
        tag_b = await _create_tag(client, admin_headers)

        # Set tag_a first
        await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [tag_a["id"]]},
            headers=admin_headers,
        )

        # Replace with tag_b only
        resp = await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [tag_b["id"]]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        tag_ids = [t["id"] for t in resp.json()["data"]["tags"]]
        assert tag_b["id"] in tag_ids
        assert tag_a["id"] not in tag_ids

    @pytest.mark.asyncio
    async def test_set_org_tags_empty_clears_all(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        tag = await _create_tag(client, admin_headers)
        await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [tag["id"]]},
            headers=admin_headers,
        )

        resp = await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": []},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["tags"] == []

    @pytest.mark.asyncio
    async def test_set_org_tags_invalid_tag_id_returns_422(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        resp = await client.put(
            _org_tags_url(sample_org.id),
            json={"tag_ids": [str(uuid.uuid4())]},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_org_tags_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_org_tags_url(sample_org.id))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_org_tags_requires_admin(
        self, client: AsyncClient, sample_org, auth_headers: dict
    ):
        resp = await client.get(_org_tags_url(sample_org.id), headers=auth_headers)
        assert resp.status_code == 403
