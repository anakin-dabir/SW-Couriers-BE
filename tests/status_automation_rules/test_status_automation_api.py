"""Integration tests for status automation rules API."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token

BASE = "/v1/status-automation-rules"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _create_payload(name: str) -> dict:
    return {
        "name": name,
        "scope_type": "GLOBAL",
        "scope_org_id": None,
        "status": "ACTIVE",
        "priority": 100,
        "notes": None,
        "trigger": {"entity_type": "PACKAGE", "status": "DAMAGED"},
        "conditions": [],
        "actions": [{"new_status": "DISPOSED"}],
    }


@pytest.mark.asyncio
async def test_create_rule_set_returns_hydrated_trigger_graph(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_create_payload(f"status-auto-{uuid.uuid4().hex[:8]}"),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["data"]["trigger"]["entity_type"] == "PACKAGE"
    assert body["data"]["trigger"]["status"] == "DAMAGED"
    assert body["data"]["actions"][0]["new_status"] == "DISPOSED"


@pytest.mark.asyncio
async def test_patch_rule_set_replaces_graph_without_unique_conflict(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    create_resp = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_create_payload(f"status-auto-patch-{uuid.uuid4().hex[:8]}"),
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    rule_set_id = created["id"]

    patch_resp = await client.patch(
        f"{BASE}/rule-sets/{rule_set_id}",
        headers=_admin_headers(admin.id),
        json={
            "name": f"status-auto-patch-updated-{uuid.uuid4().hex[:8]}",
            "status": "ACTIVE",
            "priority": 100,
            "notes": None,
            "trigger": {"entity_type": "PACKAGE", "status": "DAMAGED"},
            "conditions": [],
            "actions": [{"new_status": "RETURN_INITIATED"}],
            "version": created["version"],
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()["data"]
    assert patched["actions"][0]["new_status"] == "RETURN_INITIATED"
