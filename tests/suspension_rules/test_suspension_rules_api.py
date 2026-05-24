"""Integration tests for suspension rules APIs."""

from datetime import date
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.organizations.enums import CompanySize, IndustryType
from app.modules.organizations.models import Organization
from app.modules.suspension_rules.enums import RuleScopeType, SuspensionRuleStatus, SuspensionRuleType
from app.modules.suspension_rules.models import SuspensionActivity, SuspensionRuleSet

BASE = "/v1/suspension-rules"


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _customer_b2b_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2B", client_type="CUSTOMER_B2B")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


def _ruleset_payload(name: str, *, scope_type: str = "GLOBAL", scope_org_id: str | None = None, rule_type: str = "CREDIT_LIMIT") -> dict:
    return {
        "name": name,
        "condition_summary": f"{name} summary",
        "scope_type": scope_type,
        "scope_org_id": scope_org_id,
        "rule_type": rule_type,
        "status": "ACTIVE",
        "notes": "test",
        "auto_suspension_enabled": True,
        "pause_new_bookings": True,
        "restrict_portal_login": True,
        "notify_finance_team": True,
        "notify_account_manager": False,
        "conditions": [
            {
                "position": 1,
                "connector": None,
                "condition_type": "INVOICE_OVERDUE_DAYS",
                "threshold_value": 30,
                "unit": "Days",
            },
            {
                "position": 2,
                "connector": "OR",
                "condition_type": "TOTAL_OVERDUE_AMOUNT",
                "threshold_value": 5000,
                "unit": "GBP",
            },
        ],
    }


def _org_payload(tag: str) -> dict:
    return {
        "trading_name": f"Org {tag}",
        "legal_entity_name": f"Org Legal {tag}",
        "industry": IndustryType.LOGISTICS_TRANSPORT,
        "company_size": CompanySize.EMPLOYEES_11_50,
        "date_of_incorporation": date(2021, 1, 1),
        "companies_house_number": f"CH-{tag}",
        "reg_address_line_1": "1 Test Street",
        "reg_city": "London",
        "reg_postcode": "E1 1AA",
    }


@pytest.mark.asyncio
async def test_create_get_and_list_ruleset(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    create = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("v2-global"),
    )
    assert create.status_code == 201
    rule_id = create.json()["data"]["id"]

    get_resp = await client.get(f"{BASE}/rule-sets/{rule_id}", headers=_admin_headers(admin.id))
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["name"] == "v2-global"

    list_resp = await client.get(f"{BASE}/rule-sets", headers=_admin_headers(admin.id))
    assert list_resp.status_code == 200
    assert any(item["id"] == rule_id for item in list_resp.json()["data"]["items"])


@pytest.mark.asyncio
async def test_effective_ruleset_prefers_org_override(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("v2"))
    db_session.add(org)
    await db_session.flush()

    # global credit + global cash
    global_credit = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("global-credit", rule_type="CREDIT_LIMIT"))
    assert global_credit.status_code == 201
    global_credit_id = global_credit.json()["data"]["id"]
    global_cash = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("global-cash", rule_type="CASH"))
    assert global_cash.status_code == 201
    # org credit override
    org_credit = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("org-credit", scope_type="ORG", scope_org_id=org.id, rule_type="CREDIT_LIMIT"),
    )
    assert org_credit.status_code == 201

    effective = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective.status_code == 200
    names = {row["name"] for row in effective.json()["data"]["items"]}
    assert "org-credit" in names
    assert "global-credit" in names
    assert "global-cash" in names
    credit_rows = [r for r in effective.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    assert len(credit_rows) == 2
    default_credit = next(r for r in credit_rows if r["name"] == "global-credit")
    new_credit = next(r for r in credit_rows if r["name"] == "org-credit")
    assert default_credit["is_default_rule"] is True
    assert default_credit["is_effective_for_org"] is True
    assert default_credit["global_rule_set_id"] == global_credit_id
    assert new_credit["is_new_rule"] is True
    assert new_credit["can_restore_default"] is False
    cash_row = next(r for r in effective.json()["data"]["items"] if r["rule_type"] == "CASH")
    assert cash_row["is_override"] is False
    assert cash_row["source_scope_type"] == "GLOBAL"
    assert cash_row["can_restore_default"] is False


@pytest.mark.asyncio
async def test_create_allows_multiple_rulesets_same_scope_org_type(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("multi"))
    db_session.add(org)
    await db_session.flush()

    global_a = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("multi-global-a", rule_type="CREDIT_LIMIT"),
    )
    assert global_a.status_code == 201
    global_b = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("multi-global-b", rule_type="CREDIT_LIMIT"),
    )
    assert global_b.status_code == 201

    org_a = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("multi-org-a", scope_type="ORG", scope_org_id=org.id, rule_type="CREDIT_LIMIT"),
    )
    assert org_a.status_code == 201
    org_b = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("multi-org-b", scope_type="ORG", scope_org_id=org.id, rule_type="CREDIT_LIMIT"),
    )
    assert org_b.status_code == 201

    effective = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective.status_code == 200
    credit_rows = [r for r in effective.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    assert len(credit_rows) == 4
    assert {r["name"] for r in credit_rows} == {"multi-global-a", "multi-global-b", "multi-org-a", "multi-org-b"}


@pytest.mark.asyncio
async def test_create_org_ruleset_with_invalid_org_returns_422(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    payload = _ruleset_payload(
        "org-invalid",
        scope_type="ORG",
        scope_org_id="00000000-0000-0000-0000-000000000000",
        rule_type="CREDIT_LIMIT",
    )
    resp = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=payload)
    assert resp.status_code == 422
    assert "scope_org_id" in resp.text


@pytest.mark.asyncio
async def test_patch_ruleset_updates_name_and_status(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    create = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("patch-me"))
    assert create.status_code == 201
    data = create.json()["data"]
    rule_set_id = data["id"]
    version = data["version"]

    patch = await client.patch(
        f"{BASE}/rule-sets/{rule_set_id}",
        headers=_admin_headers(admin.id),
        json={"name": "patched-name", "status": "INACTIVE", "version": version},
    )
    assert patch.status_code == 200
    patched = patch.json()["data"]
    assert patched["name"] == "patched-name"
    assert patched["status"] == "INACTIVE"


@pytest.mark.asyncio
async def test_patch_ruleset_replaces_conditions(client: AsyncClient, user_factory) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    create = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("patch-conditions", rule_type="CREDIT_LIMIT"),
    )
    assert create.status_code == 201
    data = create.json()["data"]
    rule_set_id = data["id"]
    version = data["version"]
    # Replace INVOICE_OVERDUE_DAYS + TOTAL_OVERDUE_AMOUNT with INVOICE_OVERDUE_DAYS + CREDIT_UTILIZATION
    patch = await client.patch(
        f"{BASE}/rule-sets/{rule_set_id}",
        headers=_admin_headers(admin.id),
        json={
            "name": data["name"],
            "condition_summary": "IF INVOICE_OVERDUE_DAYS 14 Days AND CREDIT_UTILIZATION 80 Percent",
            "status": "ACTIVE",
            "version": version,
            "conditions": [
                {
                    "position": 1,
                    "connector": "NONE",
                    "condition_type": "INVOICE_OVERDUE_DAYS",
                    "threshold_value": 14,
                    "unit": "Days",
                },
                {
                    "position": 2,
                    "connector": "AND",
                    "condition_type": "CREDIT_UTILIZATION",
                    "threshold_value": 80,
                    "unit": "Percent",
                },
            ],
        },
    )
    assert patch.status_code == 200, patch.text
    patched = patch.json()["data"]
    assert len(patched["conditions"]) == 2
    assert patched["conditions"][0]["condition_type"] == "INVOICE_OVERDUE_DAYS"
    assert patched["conditions"][1]["condition_type"] == "CREDIT_UTILIZATION"


@pytest.mark.asyncio
async def test_org_override_upsert_creates_from_global_and_updates(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("upsert"))
    db_session.add(org)
    await db_session.flush()

    global_create = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("global-bank", rule_type="BANK_TRANSFER"),
    )
    assert global_create.status_code == 201

    upsert_1 = await client.put(
        f"{BASE}/orgs/{org.id}/rule-types/BANK_TRANSFER/override",
        headers=_admin_headers(admin.id),
        json={"notes": "org override note", "auto_suspension_enabled": False},
    )
    assert upsert_1.status_code == 200
    data_1 = upsert_1.json()["data"]
    assert data_1["scope_type"] == "ORG"
    assert data_1["scope_org_id"] == org.id
    assert data_1["notes"] == "org override note"

    upsert_2 = await client.put(
        f"{BASE}/orgs/{org.id}/rule-types/BANK_TRANSFER/override",
        headers=_admin_headers(admin.id),
        json={"notes": "org override updated", "version": data_1["version"]},
    )
    assert upsert_2.status_code == 200
    data_2 = upsert_2.json()["data"]
    assert data_2["id"] == data_1["id"]
    assert data_2["notes"] == "org override updated"


@pytest.mark.asyncio
async def test_org_override_upsert_updates_latest_org_rule_when_multiple_exist(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("upsert-multi"))
    db_session.add(org)
    await db_session.flush()

    org_rule_1 = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("org-multi-1", scope_type="ORG", scope_org_id=org.id, rule_type="BANK_TRANSFER"),
    )
    assert org_rule_1.status_code == 201
    org_rule_2 = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("org-multi-2", scope_type="ORG", scope_org_id=org.id, rule_type="BANK_TRANSFER"),
    )
    assert org_rule_2.status_code == 201

    upsert = await client.put(
        f"{BASE}/orgs/{org.id}/rule-types/BANK_TRANSFER/override",
        headers=_admin_headers(admin.id),
        json={"notes": "latest only"},
    )
    assert upsert.status_code == 200
    updated = upsert.json()["data"]
    assert updated["id"] == org_rule_2.json()["data"]["id"]
    assert updated["notes"] == "latest only"


@pytest.mark.asyncio
async def test_customise_global_hides_only_linked_default(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("custom"))
    db_session.add(org)
    await db_session.flush()

    g1 = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("default-a", rule_type="CREDIT_LIMIT"))
    assert g1.status_code == 201
    g1_id = g1.json()["data"]["id"]
    g2 = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("default-b", rule_type="CREDIT_LIMIT"))
    assert g2.status_code == 201

    custom = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{g1_id}/customise",
        headers=_admin_headers(admin.id),
        json={"name": "custom-a", "notes": "edited in b2b settings"},
    )
    assert custom.status_code == 201
    assert custom.json()["data"]["is_customised_rule"] is True
    assert custom.json()["data"]["can_restore_default"] is True

    effective = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective.status_code == 200
    credit_rows = [r for r in effective.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    names = {r["name"] for r in credit_rows}
    assert "default-a" not in names
    assert "default-b" in names
    assert "custom-a" in names


@pytest.mark.asyncio
async def test_org_status_toggle_and_restore_default_flow(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("toggle-restore"))
    db_session.add(org)
    await db_session.flush()

    g = await client.post(f"{BASE}/rule-sets", headers=_admin_headers(admin.id), json=_ruleset_payload("default-toggle", rule_type="BANK_TRANSFER"))
    assert g.status_code == 201
    g_id = g.json()["data"]["id"]

    custom = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{g_id}/customise",
        headers=_admin_headers(admin.id),
        json={"name": "custom-toggle"},
    )
    assert custom.status_code == 201
    custom_data = custom.json()["data"]

    toggled = await client.patch(
        f"{BASE}/orgs/{org.id}/rule-sets/{custom_data['id']}/status",
        headers=_admin_headers(admin.id),
        json={"status": "INACTIVE", "version": custom_data["version"]},
    )
    assert toggled.status_code == 200
    assert toggled.json()["data"]["status"] == "INACTIVE"

    reactivate = await client.patch(
        f"{BASE}/orgs/{org.id}/rule-sets/{custom_data['id']}/status",
        headers=_admin_headers(admin.id),
        json={"status": "ACTIVE"},
    )
    assert reactivate.status_code == 200

    restored = await client.post(
        f"{BASE}/orgs/{org.id}/rule-sets/{custom_data['id']}/restore-default",
        headers=_admin_headers(admin.id),
        json={"version": reactivate.json()["data"]["version"]},
    )
    assert restored.status_code == 200
    restored_data = restored.json()["data"]
    assert restored_data["id"] == g_id
    assert restored_data["is_default_rule"] is True
    assert restored_data["status"] == "ACTIVE"

    effective = await client.get(
        f"{BASE}/effective-rule-sets/{org.id}",
        headers=_admin_headers(admin.id),
        params={"rule_type": "BANK_TRANSFER"},
    )
    assert effective.status_code == 200
    names = {r["name"] for r in effective.json()["data"]["items"]}
    assert "default-toggle" in names
    assert "custom-toggle" not in names


@pytest.mark.asyncio
async def test_activity_returns_client_context(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    account = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True)

    rule_set = SuspensionRuleSet(
        name=f"active-{uuid4().hex[:6]}",
        condition_summary="canonical",
        scope_type=RuleScopeType.GLOBAL,
        scope_org_id=None,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
        auto_suspension_enabled=True,
        pause_new_bookings=True,
        restrict_portal_login=False,
        notify_finance_team=True,
        notify_account_manager=False,
    )
    db_session.add(rule_set)
    await db_session.flush()

    activity = SuspensionActivity(
        rule_set_id=rule_set.id,
        rule_name_snapshot=rule_set.name,
        account_id=account.id,
        conditions_met={"invoice_overdue_days": 42},
        action_taken="WARNING_SENT",
        organization_id=account.organization_id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        notification_status="QUEUED",
    )
    db_session.add(activity)
    await db_session.flush()

    resp = await client.get(f"{BASE}/activity", headers=_admin_headers(admin.id))
    assert resp.status_code == 200
    rows = resp.json()["data"]["items"]
    seeded = next((r for r in rows if r["id"] == activity.id), None)
    assert seeded is not None
    assert seeded["rule_set_id"] == rule_set.id
    assert seeded["rule_id"] == rule_set.id
    assert seeded["rule_type"] == "CREDIT_LIMIT"
    assert seeded["payment_model"] == "CREDIT_ACCOUNT"
    assert seeded["client_email"] == account.email

    by_rule_set = await client.get(
        f"{BASE}/activity",
        headers=_admin_headers(admin.id),
        params={"rule_set_id": rule_set.id},
    )
    assert by_rule_set.status_code == 200
    filtered_rows = by_rule_set.json()["data"]["items"]
    assert any(r["id"] == activity.id for r in filtered_rows)

    by_legacy_alias = await client.get(
        f"{BASE}/activity",
        headers=_admin_headers(admin.id),
        params={"rule_id": rule_set.id},
    )
    assert by_legacy_alias.status_code == 200
    alias_rows = by_legacy_alias.json()["data"]["items"]
    assert any(r["id"] == activity.id for r in alias_rows)


@pytest.mark.asyncio
async def test_org_only_rules_without_global_cannot_restore_default(
    client: AsyncClient, user_factory, db_session
) -> None:  # type: ignore[no-untyped-def]
    """ORG-scoped rules for a type with no global template have nothing to revert to."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("org-only"))
    db_session.add(org)
    await db_session.flush()
    r = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("bank-only-org", scope_type="ORG", scope_org_id=org.id, rule_type="BANK_TRANSFER"),
    )
    assert r.status_code == 201
    effective = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective.status_code == 200
    row = next(it for it in effective.json()["data"]["items"] if it["rule_type"] == "BANK_TRANSFER")
    assert row["is_override"] is True
    assert row["global_rule_set_id"] is None
    assert row["can_restore_default"] is False


@pytest.mark.asyncio
async def test_create_risk_event(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("risk"))
    db_session.add(org)
    await db_session.flush()
    body = {
        "organization_id": org.id,
        "customer_id": None,
        "order_id": None,
        "payment_model": "CARD",
        "event_type": "PAYMENT_FAILED",
        "metadata": {"source": "unit-test"},
    }
    resp = await client.post(f"{BASE}/risk-events", headers=_admin_headers(admin.id), json=body)
    assert resp.status_code == 201
    assert "id" in resp.json()["data"]


@pytest.mark.asyncio
async def test_org_global_suppression_put_list_roundtrip(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("suppress"))
    db_session.add(org)
    await db_session.flush()

    global_credit = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("sup-global-cl", rule_type="CREDIT_LIMIT"),
    )
    assert global_credit.status_code == 201
    gid = global_credit.json()["data"]["id"]

    list_empty = await client.get(
        f"{BASE}/orgs/{org.id}/global-rule-suppressions",
        headers=_admin_headers(admin.id),
    )
    assert list_empty.status_code == 200
    assert list_empty.json()["data"]["global_rule_set_ids"] == []

    put_suppress = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{gid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": True},
    )
    assert put_suppress.status_code == 200
    ids_after = put_suppress.json()["data"]["global_rule_set_ids"]
    assert gid in ids_after

    effective = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective.status_code == 200
    credit_names = [r["name"] for r in effective.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    assert "sup-global-cl" not in credit_names

    put_clear = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{gid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": False},
    )
    assert put_clear.status_code == 200
    assert put_clear.json()["data"]["global_rule_set_ids"] == []

    effective2 = await client.get(f"{BASE}/effective-rule-sets/{org.id}", headers=_admin_headers(admin.id))
    assert effective2.status_code == 200
    credit_names2 = [r["name"] for r in effective2.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    assert "sup-global-cl" in credit_names2


@pytest.mark.asyncio
async def test_org_global_suppression_scoped_per_organization(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org_a = Organization(**_org_payload("sup-a"))
    org_b = Organization(**_org_payload("sup-b"))
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    global_credit = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("iso-global-cl", rule_type="CREDIT_LIMIT"),
    )
    assert global_credit.status_code == 201
    gid = global_credit.json()["data"]["id"]

    await client.put(
        f"{BASE}/orgs/{org_a.id}/global-rule-sets/{gid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": True},
    )

    eff_a = await client.get(f"{BASE}/effective-rule-sets/{org_a.id}", headers=_admin_headers(admin.id))
    eff_b = await client.get(f"{BASE}/effective-rule-sets/{org_b.id}", headers=_admin_headers(admin.id))
    assert eff_a.status_code == eff_b.status_code == 200
    names_a = [r["name"] for r in eff_a.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    names_b = [r["name"] for r in eff_b.json()["data"]["items"] if r["rule_type"] == "CREDIT_LIMIT"]
    assert "iso-global-cl" not in names_a
    assert "iso-global-cl" in names_b


@pytest.mark.asyncio
async def test_org_global_suppression_org_ruleset_id_rejected(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("bad-global"))
    db_session.add(org)
    await db_session.flush()

    org_rule = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("not-global", scope_type="ORG", scope_org_id=org.id, rule_type="CREDIT_LIMIT"),
    )
    assert org_rule.status_code == 201
    oid = org_rule.json()["data"]["id"]

    resp = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{oid}/suppression",
        headers=_admin_headers(admin.id),
        json={"suppressed": True},
    )
    assert resp.status_code == 422
    assert "GLOBAL" in resp.text


@pytest.mark.asyncio
async def test_org_global_suppression_requires_admin(client: AsyncClient, user_factory, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True)
    org = Organization(**_org_payload("need-admin"))
    db_session.add(org)
    await db_session.flush()

    global_credit = await client.post(
        f"{BASE}/rule-sets",
        headers=_admin_headers(admin.id),
        json=_ruleset_payload("adm-global-cl", rule_type="CREDIT_LIMIT"),
    )
    assert global_credit.status_code == 201
    gid = global_credit.json()["data"]["id"]

    forbidden_get = await client.get(
        f"{BASE}/orgs/{org.id}/global-rule-suppressions",
        headers=_customer_b2b_headers(user.id),
    )
    assert forbidden_get.status_code == 403

    forbidden_put = await client.put(
        f"{BASE}/orgs/{org.id}/global-rule-sets/{gid}/suppression",
        headers=_customer_b2b_headers(user.id),
        json={"suppressed": True},
    )
    assert forbidden_put.status_code == 403

