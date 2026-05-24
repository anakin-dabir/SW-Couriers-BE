"""Integration tests: status automation restore-default deletes customised org rules."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.status_automation_rules.test_status_automation_api import BASE, _admin_headers, _create_payload


@pytest.mark.asyncio
async def test_flow_global_customise_restore_deletes_custom_from_lists(
    client: AsyncClient,
    user_factory,
    org_factory,
) -> None:  # type: ignore[no-untyped-def]
    """GLOBAL → customise (hides DEFAULT) → restore-default → only DEFAULT on GET lists."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await org_factory()

    global_name = f"sa-global-{uuid.uuid4().hex[:8]}"
    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_create_payload(global_name),
    )
    assert g.status_code == 201, g.text
    g_id = g.json()["data"]["id"]

    eff0 = await client.get(
        f"{BASE}/orgs/{org.id}/effective-rule-sets",
        headers=_admin_headers(admin.id),
    )
    app0 = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
    )
    assert eff0.status_code == app0.status_code == 200
    assert any(r["id"] == g_id and r["rule_kind"] == "DEFAULT" for r in eff0.json()["data"]["items"])
    row_g0 = next(r for r in app0.json()["data"]["items"] if r["id"] == g_id)
    assert row_g0["is_effective_for_org"] is True

    custom_name = f"sa-custom-{uuid.uuid4().hex[:8]}"
    custom = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{g_id}/customise",
        headers=_admin_headers(admin.id),
        json={"name": custom_name},
    )
    assert custom.status_code == 200, custom.text
    cid = custom.json()["data"]["id"]
    assert custom.json()["data"]["rule_kind"] == "CUSTOMISED"

    eff1 = await client.get(
        f"{BASE}/orgs/{org.id}/effective-rule-sets",
        headers=_admin_headers(admin.id),
    )
    app1 = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
    )
    names_eff = {r["name"] for r in eff1.json()["data"]["items"]}
    assert global_name not in names_eff
    assert custom_name in names_eff

    app1_items = app1.json()["data"]["items"]
    assert not any(r["id"] == g_id for r in app1_items), "DEFAULT omitted while ACTIVE customised exists"
    row_c1 = next(r for r in app1_items if r["id"] == cid)
    assert row_c1["is_effective_for_org"] is True

    restored = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{cid}/restore-default",
        headers=_admin_headers(admin.id),
        json={"version": custom.json()["data"]["version"]},
    )
    assert restored.status_code == 200, restored.text
    restored_data = restored.json()["data"]
    assert restored_data["id"] == g_id
    assert restored_data["rule_kind"] == "DEFAULT"
    assert restored_data["status"] == "ACTIVE"
    assert restored_data["can_restore_default"] is False

    eff2 = await client.get(
        f"{BASE}/orgs/{org.id}/effective-rule-sets",
        headers=_admin_headers(admin.id),
    )
    app2 = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
    )
    names_eff2 = {r["name"] for r in eff2.json()["data"]["items"]}
    assert global_name in names_eff2
    assert custom_name not in names_eff2

    app2_items = app2.json()["data"]["items"]
    row_g2 = next(r for r in app2_items if r["id"] == g_id)
    assert row_g2["is_effective_for_org"] is True
    assert not any(r["id"] == cid for r in app2_items), "custom row deleted on restore-default"


@pytest.mark.asyncio
async def test_restore_default_rejects_org_only_new_rule(
    client: AsyncClient,
    user_factory,
    org_factory,
) -> None:  # type: ignore[no-untyped-def]
    """NEW org rules (no global parent) cannot use restore-default."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await org_factory()

    new_rule = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json={
            **_create_payload(f"sa-new-{uuid.uuid4().hex[:8]}"),
            "scope_type": "ORG",
            "scope_org_id": org.id,
        },
    )
    assert new_rule.status_code == 201, new_rule.text
    new_id = new_rule.json()["data"]["id"]

    restored = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{new_id}/restore-default",
        headers=_admin_headers(admin.id),
        json={"version": new_rule.json()["data"]["version"]},
    )
    assert restored.status_code == 422
