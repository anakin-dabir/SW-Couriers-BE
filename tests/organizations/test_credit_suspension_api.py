"""Integration API tests — OrgCreditSuspension endpoints.

Covers:
- POST   /v1/organizations              create org with inline credit_config / suspension_config
- GET    /v1/organizations/{id}/credit-suspension
- PUT    /v1/organizations/{id}/credit-suspension/credit
- PUT    /v1/organizations/{id}/credit-suspension/suspension

All tests use per-test transaction rollback (no persistent state).
Arq background jobs are mocked so no Redis/worker is needed.
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

ORGS = "/v1/organizations"


# ── Mocks ──────────────────────────────────────────────────────────────────────


def _mock_enqueue():
    return patch("app.modules.organizations.service.enqueue", new_callable=AsyncMock, return_value=None)


def _mock_create_invite():
    from app.modules.auth.service import CreateInviteResult

    fake_invite = MagicMock()
    fake_invite.id = "invite-id-fake"
    fake_user = MagicMock()
    return patch(
        "app.modules.organizations.service.AuthService.create_invite",
        new_callable=AsyncMock,
        return_value=CreateInviteResult(False, fake_invite, "raw-token-abc123", fake_user, "invite-id-fake"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _org_form_data(
    credit_config: dict | None = None,
    suspension_config: dict | None = None,
) -> dict:
    """Build flat multipart form data for org creation (multipart/form-data endpoint).

    ``credit_config`` and ``suspension_config`` are serialised to JSON strings
    as required by the form endpoint.
    """
    contacts = [
        {
            "email": f"owner-{uuid.uuid4().hex[:8]}@credittest.com",
            "first_name": "Credit",
            "last_name": "Owner",
            "contact_number": "+447700900001",
            "contact_role": "ACCOUNT_OWNER",
        }
    ]
    data = {
        "trading_name": "Credit Test Ltd",
        "legal_entity_name": "Credit Test Limited",
        "companies_house_number": f"CT{uuid.uuid4().hex[:6].upper()}",
        "vat_number": f"GB{uuid.uuid4().int % 10**9:09d}",
        "date_of_incorporation": "2018-01-01",
        "industry": "LOGISTICS_TRANSPORT",
        "company_size": "11-50 employees",
        "reg_address_line_1": "1 Credit Street",
        "reg_city": "London",
        "reg_postcode": "EC1A 1BB",
        "contacts": json.dumps(contacts),
    }
    if credit_config is not None:
        data["credit_config"] = json.dumps(credit_config)
    if suspension_config is not None:
        data["suspension_config"] = json.dumps(suspension_config)
    return data


def _credit_config() -> dict:
    return {
        "approved_credit_limit": "5000.00",
        "credit_clearance_period_days": 30,
        "credit_utilization_warning_pct": 80,
        "allow_bookings_beyond_limit": False,
    }


def _suspension_config_with_triggers() -> dict:
    return {
        "trigger_conditions": [
            {
                "position": 1,
                "logic_operator": None,
                "condition_type": "INVOICE_OVERDUE_DAYS",
                "condition_value": "40.00",
            },
            {
                "position": 2,
                "logic_operator": "OR",
                "condition_type": "CREDIT_UTILIZATION",
                "condition_value": "90.00",
            },
        ],
        "auto_suspension_enabled": True,
        "pause_new_bookings": True,
        "restrict_portal_login": False,
        "notify_finance_team": True,
        "notify_account_manager": False,
    }


def _suspension_config_empty() -> dict:
    return {
        "trigger_conditions": [],
        "auto_suspension_enabled": False,
        "pause_new_bookings": False,
        "restrict_portal_login": False,
        "notify_finance_team": False,
        "notify_account_manager": False,
    }


# ── Create org with inline credit/suspension config ────────────────────────────


class TestCreateOrgWithCreditSuspensionConfig:
    @pytest.mark.asyncio
    async def test_create_without_configs_returns_null_configs(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["credit_config"] is None
        assert data["suspension_config"] is None

    @pytest.mark.asyncio
    async def test_create_with_credit_config_only(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(credit_config=_credit_config()), headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        cc = data["credit_config"]
        assert cc is not None
        assert Decimal(cc["approved_credit_limit"]) == Decimal("5000.00")
        assert cc["credit_clearance_period_days"] == 30
        assert cc["credit_utilization_warning_pct"] == 80
        assert cc["allow_bookings_beyond_limit"] is False
        assert data["suspension_config"] is None

    @pytest.mark.asyncio
    async def test_create_with_suspension_config_only(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(suspension_config=_suspension_config_with_triggers()), headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["credit_config"] is None
        sc = data["suspension_config"]
        assert sc is not None
        assert sc["auto_suspension_enabled"] is True
        assert sc["pause_new_bookings"] is True
        assert sc["notify_finance_team"] is True
        assert len(sc["trigger_conditions"]) == 2

    @pytest.mark.asyncio
    async def test_create_with_both_configs(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                ORGS,
                data=_org_form_data(credit_config=_credit_config(), suspension_config=_suspension_config_with_triggers()),
                headers=admin_headers,
            )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["credit_config"] is not None
        assert data["suspension_config"] is not None

    @pytest.mark.asyncio
    async def test_create_trigger_position_1_with_logic_operator_returns_422(self, client: AsyncClient, admin_headers: dict):
        bad_triggers = [
            {
                "position": 1,
                "logic_operator": "AND",  # must be null for position=1
                "condition_type": "INVOICE_OVERDUE_DAYS",
                "condition_value": "40.00",
            }
        ]
        bad_sc = {**_suspension_config_empty(), "trigger_conditions": bad_triggers}
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(suspension_config=bad_sc), headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_trigger_non_sequential_positions_returns_422(self, client: AsyncClient, admin_headers: dict):
        bad_triggers = [
            {"position": 1, "logic_operator": None, "condition_type": "INVOICE_OVERDUE_DAYS", "condition_value": "40.00"},
            {"position": 3, "logic_operator": "AND", "condition_type": "CREDIT_UTILIZATION", "condition_value": "90.00"},
        ]
        bad_sc = {**_suspension_config_empty(), "trigger_conditions": bad_triggers}
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(suspension_config=bad_sc), headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_trigger_missing_logic_operator_for_position_2_returns_422(self, client: AsyncClient, admin_headers: dict):
        bad_triggers = [
            {"position": 1, "logic_operator": None, "condition_type": "INVOICE_OVERDUE_DAYS", "condition_value": "40.00"},
            {"position": 2, "logic_operator": None, "condition_type": "CREDIT_UTILIZATION", "condition_value": "90.00"},
        ]
        bad_sc = {**_suspension_config_empty(), "trigger_conditions": bad_triggers}
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(suspension_config=bad_sc), headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_credit_limit_must_be_positive(self, client: AsyncClient, admin_headers: dict):
        bad_cc = {**_credit_config(), "approved_credit_limit": "0.00"}
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(credit_config=bad_cc), headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_utilization_pct_exceeds_100_returns_422(self, client: AsyncClient, admin_headers: dict):
        bad_cc = {**_credit_config(), "credit_utilization_warning_pct": 101}
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(credit_config=bad_cc), headers=admin_headers)
        assert resp.status_code == 422


# ── GET credit-suspension ──────────────────────────────────────────────────────


class TestGetCreditSuspensionConfig:
    @pytest.mark.asyncio
    async def test_get_returns_null_when_no_configs(self, client: AsyncClient, admin_headers: dict, sample_org):
        resp = await client.get(f"{ORGS}/{sample_org.id}/credit-suspension", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["credit_config"] is None
        assert data["suspension_config"] is None

    @pytest.mark.asyncio
    async def test_get_returns_existing_configs(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue(), _mock_create_invite():
            create_resp = await client.post(
                ORGS,
                data=_org_form_data(credit_config=_credit_config(), suspension_config=_suspension_config_with_triggers()),
                headers=admin_headers,
            )
        org_id = create_resp.json()["data"]["organization"]["id"]

        resp = await client.get(f"{ORGS}/{org_id}/credit-suspension", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["credit_config"] is not None
        assert data["suspension_config"] is not None
        assert len(data["suspension_config"]["trigger_conditions"]) == 2

    @pytest.mark.asyncio
    async def test_get_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        resp = await client.get(f"{ORGS}/{uuid.uuid4()}/credit-suspension", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(f"{ORGS}/{sample_org.id}/credit-suspension")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_requires_admin(self, client: AsyncClient, sample_org, auth_headers: dict):
        resp = await client.get(f"{ORGS}/{sample_org.id}/credit-suspension", headers=auth_headers)
        assert resp.status_code == 403


# ── PUT credit-suspension/credit ───────────────────────────────────────────────


class TestUpsertCreditConfig:
    @pytest.mark.asyncio
    async def test_creates_credit_config_when_none_exists(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_credit_config(), "reason": "Initial credit limit setup"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert Decimal(data["approved_credit_limit"]) == Decimal("5000.00")
        assert data["credit_clearance_period_days"] == 30
        assert data["organization_id"] == sample_org.id

    @pytest.mark.asyncio
    async def test_updates_existing_credit_config(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_credit_config(), "reason": "Initial setup"}
        await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        # Now update
        updated = {**_credit_config(), "approved_credit_limit": "10000.00", "reason": "Increased limit"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=updated,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["data"]["approved_credit_limit"]) == Decimal("10000.00")

    @pytest.mark.asyncio
    async def test_update_requires_reason(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = _credit_config()  # no reason
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_reason_too_short_returns_422(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_credit_config(), "reason": "Ab"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        body = {**_credit_config(), "reason": "Test reason for unknown org"}
        resp = await client.put(
            f"{ORGS}/{uuid.uuid4()}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, sample_org, auth_headers: dict):
        body = {**_credit_config(), "reason": "Unauthorized attempt"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_can_clear_credit_limit(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Setting approved_credit_limit to null is allowed."""
        body = {**_credit_config(), "approved_credit_limit": None, "reason": "Removing credit limit"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/credit",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["approved_credit_limit"] is None


# ── PUT credit-suspension/suspension ───────────────────────────────────────────


class TestUpsertSuspensionConfig:
    @pytest.mark.asyncio
    async def test_creates_suspension_config_with_triggers(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_suspension_config_with_triggers(), "reason": "Setting suspension rules"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["auto_suspension_enabled"] is True
        assert data["pause_new_bookings"] is True
        assert len(data["trigger_conditions"]) == 2
        assert data["organization_id"] == sample_org.id

    @pytest.mark.asyncio
    async def test_creates_suspension_config_without_triggers(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_suspension_config_empty(), "reason": "Enabling actions without triggers"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["trigger_conditions"] == []

    @pytest.mark.asyncio
    async def test_updates_existing_suspension_config(self, client: AsyncClient, admin_headers: dict, sample_org):
        # Create first
        body = {**_suspension_config_with_triggers(), "reason": "Initial setup"}
        await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        # Now clear triggers and toggle some booleans
        update = {**_suspension_config_empty(), "notify_account_manager": True, "reason": "Clear triggers"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=update,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["trigger_conditions"] == []
        assert data["notify_account_manager"] is True

    @pytest.mark.asyncio
    async def test_trigger_order_preserved(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {**_suspension_config_with_triggers(), "reason": "Checking trigger order"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        triggers = resp.json()["data"]["trigger_conditions"]
        positions = [t["position"] for t in triggers]
        assert positions == sorted(positions)

    @pytest.mark.asyncio
    async def test_update_requires_reason(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = _suspension_config_empty()  # no reason
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        body = {**_suspension_config_empty(), "reason": "Test reason for unknown org"}
        resp = await client.put(
            f"{ORGS}/{uuid.uuid4()}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, sample_org, auth_headers: dict):
        body = {**_suspension_config_empty(), "reason": "Unauthorized attempt"}
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_condition_type_returns_422(self, client: AsyncClient, admin_headers: dict, sample_org):
        body = {
            **_suspension_config_empty(),
            "trigger_conditions": [
                {
                    "position": 1,
                    "logic_operator": None,
                    "condition_type": "INVALID_CONDITION",
                    "condition_value": "10.00",
                }
            ],
            "reason": "Bad condition type",
        }
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_trigger_decimal_precision_preserved(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Decimal values in JSONB should round-trip without precision loss."""
        body = {
            **_suspension_config_empty(),
            "trigger_conditions": [
                {
                    "position": 1,
                    "logic_operator": None,
                    "condition_type": "TOTAL_OVERDUE_AMOUNT",
                    "condition_value": "1234.56",
                }
            ],
            "reason": "Checking decimal precision",
        }
        resp = await client.put(
            f"{ORGS}/{sample_org.id}/credit-suspension/suspension",
            json=body,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        trigger = resp.json()["data"]["trigger_conditions"][0]
        assert Decimal(trigger["condition_value"]) == Decimal("1234.56")
