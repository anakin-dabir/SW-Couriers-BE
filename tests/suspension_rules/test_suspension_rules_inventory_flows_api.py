"""Integration tests: effective vs applicable inventory APIs and common org rule flows."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.organizations.models import Organization
from app.modules.suspension_rules.service import SuspensionRulesService

from tests.suspension_rules.test_suspension_rules_api import BASE, _admin_headers, _org_payload, _ruleset_payload


@pytest.mark.asyncio
async def test_flow_global_customise_restore_effective_vs_applicable(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    """GLOBAL → customise (hides DEFAULT) → restore-default → DEFAULT visible in effective again."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("inv-flow-a"))
    db_session.add(org)
    await db_session.flush()

    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("flow-a-global", rule_type="CREDIT_LIMIT"),
    )
    assert g.status_code == 201
    g_id = g.json()["data"]["id"]

    eff0 = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    app0 = await client.get(f"{BASE}/orgs/{org.id}/applicable-rule-sets", headers=_admin_headers(admin.id))
    assert eff0.status_code == app0.status_code == 200
    assert any(r["name"] == "flow-a-global" and r["is_default_rule"] for r in eff0.json()["data"]["items"])
    row_g0 = next(r for r in app0.json()["data"]["items"] if r["id"] == g_id)
    assert row_g0["is_effective_for_org"] is True

    custom = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{g_id}/customise",
        headers=_admin_headers(admin.id),
        json={"name": "flow-a-custom"},
    )
    assert custom.status_code == 201
    cid = custom.json()["data"]["id"]

    eff1 = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    app1 = await client.get(f"{BASE}/orgs/{org.id}/applicable-rule-sets", headers=_admin_headers(admin.id))
    names_eff = {r["name"] for r in eff1.json()["data"]["items"]}
    assert "flow-a-global" not in names_eff
    assert "flow-a-custom" in names_eff

    app1_items = app1.json()["data"]["items"]
    assert not any(r["id"] == g_id for r in app1_items), "DEFAULT shell omitted when ACTIVE customised exists"
    row_c1 = next(r for r in app1_items if r["id"] == cid)
    assert row_c1["is_effective_for_org"] is True

    ver = custom.json()["data"]["version"]
    restored = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{cid}/restore-default",
        headers=_admin_headers(admin.id),
        json={"version": ver},
    )
    assert restored.status_code == 200

    eff2 = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    app2 = await client.get(f"{BASE}/orgs/{org.id}/applicable-rule-sets", headers=_admin_headers(admin.id))
    names_eff2 = {r["name"] for r in eff2.json()["data"]["items"]}
    assert "flow-a-global" in names_eff2
    assert "flow-a-custom" not in names_eff2

    app2_items = app2.json()["data"]["items"]
    row_g2 = next(r for r in app2_items if r["id"] == g_id)
    assert row_g2["is_effective_for_org"] is True
    assert not any(r["id"] == cid for r in app2_items), "custom row deleted on restore-default"


@pytest.mark.asyncio
async def test_flow_global_new_then_inactive_global(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    """GLOBAL + NEW org rule; inactivate GLOBAL — NEW stays effective; applicable lists inactive GLOBAL with flag false."""
    suffix = uuid4().hex[:10]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload(f"inv-flow-b-{suffix}"))
    db_session.add(org)
    await db_session.flush()

    g_name = f"flow-b-global-{suffix}"
    n_name = f"flow-b-new-{suffix}"

    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(g_name, rule_type="CREDIT_LIMIT"),
    )
    assert g.status_code == 201
    g_id = g.json()["data"]["id"]
    gv = g.json()["data"]["version"]

    new_org = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(n_name, scope_type="ORG", scope_org_id=org.id, rule_type="CREDIT_LIMIT"),
    )
    assert new_org.status_code == 201
    nid = new_org.json()["data"]["id"]

    eff = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    app = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert eff.status_code == app.status_code == 200
    credit_eff = [r for r in eff.json()["data"]["items"] if r["id"] in (g_id, nid)]
    assert {r["name"] for r in credit_eff} == {g_name, n_name}
    row_g_app = next(r for r in app.json()["data"]["items"] if r["id"] == g_id)
    row_n_app = next(r for r in app.json()["data"]["items"] if r["id"] == nid)
    assert row_g_app["is_effective_for_org"] is row_n_app["is_effective_for_org"] is True

    patch_g = await client.patch(
        f"{BASE}/rule-sets/{g_id}",
        headers=_admin_headers(admin.id),
        json={"status": "INACTIVE", "version": gv},
    )
    assert patch_g.status_code == 200

    eff2 = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    app2 = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    credit_eff2 = [r for r in eff2.json()["data"]["items"] if r["id"] == nid]
    assert len(credit_eff2) == 1 and credit_eff2[0]["name"] == n_name
    row_g2 = next(r for r in app2.json()["data"]["items"] if r["id"] == g_id)
    row_n2 = next(r for r in app2.json()["data"]["items"] if r["id"] == nid)
    assert row_g2["status"] == "INACTIVE"
    assert row_g2["is_effective_for_org"] is False
    assert row_n2["is_effective_for_org"] is True


@pytest.mark.asyncio
async def test_applicable_inventory_matches_service(db_session) -> None:  # type: ignore[no-untyped-def]
    """Smoke: ORM inventory rows align with effective id membership."""
    org = Organization(**_org_payload("inv-unit"))
    db_session.add(org)
    await db_session.flush()
    service = SuspensionRulesService(db_session, request=None)
    rows = await service.get_org_applicable_rule_sets_with_source_for_org(org.id)
    effective = await service.get_effective_rule_sets_with_source_for_org(org.id)
    eff_ids = {str(r["rule_set"].id) for r in effective}
    for row in rows:
        assert (str(row["rule_set"].id) in eff_ids) == row["is_effective_for_org"]


@pytest.mark.asyncio
async def test_edge_inactive_customised_unhides_global_in_effective(
    client: AsyncClient, user_factory, db_session
) -> None:  # type: ignore[no-untyped-def]
    """Plan caveat §1.4: INACTIVE customised row drops out of overlay → GLOBAL DEFAULT applies again."""
    suffix = uuid4().hex[:10]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload(f"edge-inactive-{suffix}"))
    db_session.add(org)
    await db_session.flush()

    g_name = f"edge-g-{suffix}"
    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(g_name, rule_type="CREDIT_LIMIT"),
    )
    assert g.status_code == 201
    gid = g.json()["data"]["id"]

    cust = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{gid}/customise",
        headers=_admin_headers(admin.id),
        json={"name": f"edge-c-{suffix}"},
    )
    assert cust.status_code == 201
    cid = cust.json()["data"]["id"]
    cv = cust.json()["data"]["version"]

    eff_on = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert g_name not in {r["name"] for r in eff_on.json()["data"]["items"]}

    inactive_c = await client.patch(
        f"{BASE}/orgs/{org.id}/rule-sets/{cid}/status",
        headers=_admin_headers(admin.id),
        json={"status": "INACTIVE", "version": cv},
    )
    assert inactive_c.status_code == 200

    eff_off = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    names = {r["name"] for r in eff_off.json()["data"]["items"]}
    assert g_name in names
    assert f"edge-c-{suffix}" not in names


@pytest.mark.asyncio
async def test_edge_activate_customised_after_toggle_hides_global_again(
    client: AsyncClient, user_factory, db_session
) -> None:  # type: ignore[no-untyped-def]
    suffix = uuid4().hex[:10]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload(f"edge-toggle-{suffix}"))
    db_session.add(org)
    await db_session.flush()

    g_name = f"tgl-g-{suffix}"
    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(g_name, rule_type="CREDIT_LIMIT"),
    )
    assert g.status_code == 201
    gid = g.json()["data"]["id"]

    cust = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{gid}/customise",
        headers=_admin_headers(admin.id),
        json={"name": f"tgl-c-{suffix}"},
    )
    assert cust.status_code == 201
    cid = cust.json()["data"]["id"]
    cv = cust.json()["data"]["version"]

    patch_off = await client.patch(
        f"{BASE}/orgs/{org.id}/rule-sets/{cid}/status",
        headers=_admin_headers(admin.id),
        json={"status": "INACTIVE", "version": cv},
    )
    assert patch_off.status_code == 200
    nv = patch_off.json()["data"]["version"]

    eff_mid = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert g_name in {r["name"] for r in eff_mid.json()["data"]["items"]}

    patch_on = await client.patch(
        f"{BASE}/orgs/{org.id}/rule-sets/{cid}/status",
        headers=_admin_headers(admin.id),
        json={"status": "ACTIVE", "version": nv},
    )
    assert patch_on.status_code == 200

    eff_final = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    names = {r["name"] for r in eff_final.json()["data"]["items"]}
    assert g_name not in names
    assert f"tgl-c-{suffix}" in names


@pytest.mark.asyncio
async def test_edge_junction_suppression_effective_vs_applicable(
    client: AsyncClient, user_factory, db_session
) -> None:  # type: ignore[no-untyped-def]
    suffix = uuid4().hex[:10]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload(f"edge-junc-{suffix}"))
    db_session.add(org)
    await db_session.flush()

    g_name = f"junc-g-{suffix}"
    g = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(g_name, rule_type="CREDIT_LIMIT"),
    )
    assert g.status_code == 201
    gid = g.json()["data"]["id"]

    eff0 = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert gid in {r["id"] for r in eff0.json()["data"]["items"]}

    put_sup = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{gid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": True},
    )
    assert put_sup.status_code == 200

    eff1 = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    app1 = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert gid not in {r["id"] for r in eff1.json()["data"]["items"]}
    row_g = next(r for r in app1.json()["data"]["items"] if r["id"] == gid)
    assert row_g["is_effective_for_org"] is False

    put_clr = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{gid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": False},
    )
    assert put_clr.status_code == 200

    eff2 = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    assert gid in {r["id"] for r in eff2.json()["data"]["items"]}


@pytest.mark.asyncio
async def test_edge_two_globals_customise_one_other_global_stays_visible(
    client: AsyncClient, user_factory, db_session
) -> None:  # type: ignore[no-untyped-def]
    suffix = uuid4().hex[:10]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload(f"edge-two-{suffix}"))
    db_session.add(org)
    await db_session.flush()

    g1 = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(f"two-a-{suffix}", rule_type="CREDIT_LIMIT"),
    )
    g2 = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload(f"two-b-{suffix}", rule_type="CREDIT_LIMIT"),
    )
    assert g1.status_code == g2.status_code == 201
    g1_id = g1.json()["data"]["id"]

    custom = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{g1_id}/customise",
        headers=_admin_headers(admin.id),
        json={"name": f"two-custom-{suffix}"},
    )
    assert custom.status_code == 201

    eff = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    names = {r["name"] for r in eff.json()["data"]["items"]}
    assert f"two-b-{suffix}" in names
    assert f"two-a-{suffix}" not in names
    assert f"two-custom-{suffix}" in names

    app = await client.get(
        f"{BASE}/orgs/{org.id}/applicable-rule-sets",
        headers=_admin_headers(admin.id),
        params={"rule_type": "CREDIT_LIMIT"},
    )
    app_ids = {r["id"] for r in app.json()["data"]["items"]}
    assert g1_id not in app_ids
    assert g2.json()["data"]["id"] in app_ids
    assert custom.json()["data"]["id"] in app_ids


@pytest.mark.asyncio
async def test_daily_evaluation_uses_same_rules_as_get_effective_rule_sets_for_org(db_session) -> None:  # type: ignore[no-untyped-def]
    """Scheduled job path (:meth:`_effective_rule_sets_for_org`) matches public effective accessor."""
    org = Organization(**_org_payload("parity-eval"))
    db_session.add(org)
    await db_session.flush()
    service = SuspensionRulesService(db_session, request=None)
    plain = await service._effective_rule_sets_for_org(org.id)
    public = await service.get_effective_rule_sets_for_org(org.id)
    assert [r.id for r in plain] == [r.id for r in public]
