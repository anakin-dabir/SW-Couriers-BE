"""Integration API tests — OrgPaymentConfig and OrgPaymentMethod endpoints.

New schema (migration 0072):
- OrgPaymentConfig  — shared config: VAT, delivery/return attempts, weight margin/surcharge
- OrgPaymentMethod  — per-model rows (CARD | BANK_TRANSFER | CREDIT_ACCOUNT | CASH)
  UNIQUE on (organization_id, payment_model)

Covers:
- POST   /v1/organizations                    create org with inline payment_config
- GET    /v1/organizations/{id}/payment-config
- PATCH  /v1/organizations/{id}/payment-config
- DELETE /v1/organizations/{id}/payment-config
- POST   /v1/organizations/{id}/payment-config/methods
- PATCH  /v1/organizations/{id}/payment-config/methods/{payment_model}
- DELETE /v1/organizations/{id}/payment-config/methods/{payment_model}

All tests use per-test transaction rollback (no persistent state).
"""

import json
import uuid
from base64 import urlsafe_b64decode
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import OrgPaymentConfig

ORGS = "/v1/organizations"


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _decode_user_id_from_auth(headers: dict) -> str:
    token = str(headers.get("Authorization", "")).removeprefix("Bearer ").strip()
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
    return str(payload["sub"])


def _org_form_data(account_manager_user_id: str, email: str | None = None) -> dict:
    contacts = [
        {
            "email": email or f"owner-{uuid.uuid4().hex[:8]}@paytest.com",
            "first_name": "Pay",
            "last_name": "Owner",
            "contact_number": "+447700900001",
            "contact_role": "ACCOUNT_OWNER",
        }
    ]
    return {
        "trading_name": "Payment Test Ltd",
        "legal_entity_name": "Payment Test Limited",
        "companies_house_number": f"PT{uuid.uuid4().hex[:6].upper()}",
        "vat_number": f"GB{uuid.uuid4().int % 10**9:09d}",
        "date_of_incorporation": "2018-01-01",
        "industry": "LOGISTICS_TRANSPORT",
        "company_size": "11-50 employees",
        "reg_address_line_1": "1 Payment Street",
        "reg_city": "London",
        "reg_postcode": "EC1A 1BB",
        "account_manager_user_id": account_manager_user_id,
        "contacts": json.dumps(contacts),
    }


def _card_method() -> dict:
    return {
        "payment_model": "CARD",
        "billing_schedule": "IMMEDIATE",
        "is_default": True,
    }


def _bank_transfer_method(is_default: bool = False) -> dict:
    return {
        "payment_model": "BANK_TRANSFER",
        "billing_schedule": "FIXED_MONTHLY_DATE",
        "billing_day_of_month": 15,
        "bank_account_name": "SW Couriers Ltd",
        "bank_account_number": "12345678",
        "bank_sort_code": "20-00-00",
        "is_default": is_default,
    }


def _credit_account_method(is_default: bool = False) -> dict:
    return {
        "payment_model": "CREDIT_ACCOUNT",
        "billing_schedule": "DAYS_AFTER_ORDER",
        "billing_days_after_order": 30,
        "credit_limit": "5000.00",
        "credit_utilization_warning_pct": 80,
        "is_default": is_default,
    }


def _cash_method(is_default: bool = False) -> dict:
    return {
        "payment_model": "CASH",
        "billing_schedule": "FIXED_MONTHLY_DATE",
        "billing_day_of_month": 1,
        "is_default": is_default,
    }


def _shared_config(payment_methods: list[dict] | None = None) -> dict:
    """Build a full OrgPaymentConfigCreate payload."""
    return {
        "vat_number": "GB123456789",
        "vat_rate": "STANDARD_20",
        "vat_treatment": "UK",
        "max_delivery_attempts": 3,
        "delivery_attempt_fees": [
            {"attempt": 1, "fee": "0.00"},
            {"attempt": 2, "fee": "1.50"},
            {"attempt": 3, "fee": "2.50"},
        ],
        "max_return_attempts": 2,
        "return_attempt_fees": [
            {"attempt": 1, "fee": "5.00"},
            {"attempt": 2, "fee": "8.00"},
        ],
        "weight_margin_kg": 0.5,
        "weight_surcharge_per_kg": "1.50",
        "payment_methods": payment_methods or [_card_method()],
    }


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


async def _create_org_with_config(
    client: AsyncClient,
    admin_headers: dict,
    config: dict | None = None,
    email: str | None = None,
) -> tuple[str, dict | None]:
    """Helper: create an org with an optional payment_config. Returns (org_id, payment_config_data)."""
    data = _org_form_data(_decode_user_id_from_auth(admin_headers), email)
    if config is not None:
        data["payment_config"] = json.dumps(config)
    with _mock_enqueue(), _mock_create_invite():
        resp = await client.post(ORGS, data=data, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    resp_data = resp.json()["data"]
    return resp_data["organization"]["id"], resp_data["payment_config"]


# ═══════════════════════════════════════════════════════════════════════════════
#  CREATE (inline during org creation)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateOrgWithPaymentConfig:
    """POST /v1/organizations — payment_config block is optional and created atomically."""

    @pytest.mark.asyncio
    async def test_create_without_payment_config_returns_null(self, client: AsyncClient, admin_headers: dict) -> None:
        """When payment_config is omitted the field is null in the response."""
        _, pc = await _create_org_with_config(client, admin_headers, config=None)
        assert pc is None

    @pytest.mark.asyncio
    async def test_create_with_single_card_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """Single CARD method is accepted; response includes shared config + method list."""
        org_id, pc = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        assert pc is not None
        assert pc["organization_id"] == org_id
        assert pc["vat_rate"] == "STANDARD_20"
        assert pc["max_delivery_attempts"] == 3
        assert len(pc["delivery_attempt_fees"]) == 3
        assert pc["max_return_attempts"] == 2
        assert len(pc["return_attempt_fees"]) == 2
        assert float(pc["weight_margin_kg"]) == 0.5
        assert Decimal(pc["weight_surcharge_per_kg"]) == Decimal("1.50")
        assert len(pc["payment_methods"]) == 1
        assert pc["payment_methods"][0]["payment_model"] == "CARD"
        assert pc["payment_methods"][0]["billing_schedule"] == "IMMEDIATE"
        assert pc["payment_methods"][0]["is_default"] is True

    @pytest.mark.asyncio
    async def test_create_with_attempt_arrays_only_derives_max(self, client: AsyncClient, admin_headers: dict) -> None:
        """Create path derives max_* from fee arrays when omitted by FE."""
        config = _shared_config([_card_method()])
        config.pop("max_delivery_attempts", None)
        config.pop("max_return_attempts", None)
        _, pc = await _create_org_with_config(client, admin_headers, config)
        assert pc is not None
        assert pc["max_delivery_attempts"] == len(pc["delivery_attempt_fees"])
        assert pc["max_return_attempts"] == len(pc["return_attempt_fees"])

    @pytest.mark.asyncio
    async def test_create_with_multiple_payment_methods(self, client: AsyncClient, admin_headers: dict) -> None:
        """Multiple payment methods are all persisted and returned."""
        config = _shared_config([_card_method(), _bank_transfer_method(), _cash_method()])
        org_id, pc = await _create_org_with_config(client, admin_headers, config)

        assert pc is not None
        assert len(pc["payment_methods"]) == 3
        models = {m["payment_model"] for m in pc["payment_methods"]}
        assert models == {"CARD", "BANK_TRANSFER", "CASH"}

    @pytest.mark.asyncio
    async def test_create_bank_transfer_includes_bank_details(self, client: AsyncClient, admin_headers: dict) -> None:
        """BANK_TRANSFER method stores bank_account_name/number/sort_code."""
        config = _shared_config([_bank_transfer_method(is_default=True)])
        _, pc = await _create_org_with_config(client, admin_headers, config)

        method = pc["payment_methods"][0]
        assert method["payment_model"] == "BANK_TRANSFER"
        assert method["bank_account_name"] == "SW Couriers Ltd"
        assert method["bank_account_number"] == "12345678"
        assert method["bank_sort_code"] == "20-00-00"

    @pytest.mark.asyncio
    async def test_create_credit_account_includes_credit_fields(self, client: AsyncClient, admin_headers: dict) -> None:
        """CREDIT_ACCOUNT method stores credit_limit and utilization warning."""
        config = _shared_config([_credit_account_method(is_default=True)])
        _, pc = await _create_org_with_config(client, admin_headers, config)

        method = pc["payment_methods"][0]
        assert method["payment_model"] == "CREDIT_ACCOUNT"
        assert Decimal(method["credit_limit"]) == Decimal("5000.00")
        assert method["credit_utilization_warning_pct"] == 80

    @pytest.mark.asyncio
    async def test_create_cash_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """CASH is a valid payment model with FIXED_MONTHLY_DATE schedule."""
        config = _shared_config([_cash_method(is_default=True)])
        _, pc = await _create_org_with_config(client, admin_headers, config)

        method = pc["payment_methods"][0]
        assert method["payment_model"] == "CASH"
        assert method["billing_day_of_month"] == 1

    @pytest.mark.asyncio
    async def test_create_first_method_becomes_default_when_none_marked(self, client: AsyncClient, admin_headers: dict) -> None:
        """When no method has is_default=True, the first one is promoted."""
        methods = [
            {**_card_method(), "is_default": False},
            {**_bank_transfer_method(), "is_default": False},
        ]
        config = _shared_config(methods)
        _, pc = await _create_org_with_config(client, admin_headers, config)

        defaults = [m for m in pc["payment_methods"] if m["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["payment_model"] == "CARD"

    # ── Validation errors ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_card_with_non_immediate_schedule_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """CARD + FIXED_MONTHLY_DATE → 422."""
        bad = {**_card_method(), "billing_schedule": "FIXED_MONTHLY_DATE", "billing_day_of_month": 10}
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_bank_transfer_with_immediate_schedule_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """BANK_TRANSFER + IMMEDIATE → 422."""
        bad = {**_bank_transfer_method(), "billing_schedule": "IMMEDIATE"}
        bad.pop("billing_day_of_month", None)
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_bank_transfer_missing_bank_details_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """BANK_TRANSFER without bank_account_name → 422."""
        bad = {**_bank_transfer_method()}
        bad.pop("bank_account_name")
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_credit_account_missing_credit_limit_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """CREDIT_ACCOUNT without credit_limit → 422."""
        bad = {**_credit_account_method()}
        bad.pop("credit_limit")
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_payment_models_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Two CARD methods → 422 (unique constraint on payment_model per org)."""
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([_card_method(), _card_method()]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_multiple_defaults_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Two methods both marked is_default=True → 422."""
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(
            _shared_config([
                {**_card_method(), "is_default": True},
                {**_bank_transfer_method(), "is_default": True},
            ])
        )
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_mismatched_delivery_attempt_count_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """delivery_attempt_fees length != max_delivery_attempts → 422."""
        bad_config = {
            **_shared_config(),
            "max_delivery_attempts": 3,
            "delivery_attempt_fees": [
                {"attempt": 1, "fee": "0.00"},
                {"attempt": 2, "fee": "1.00"},
                # only 2 entries for 3 attempts
            ],
        }
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(bad_config)
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_mismatched_return_attempt_count_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """return_attempt_fees length != max_return_attempts → 422."""
        bad_config = {
            **_shared_config(),
            "max_return_attempts": 3,
            "return_attempt_fees": [
                {"attempt": 1, "fee": "5.00"},
                # missing 2 of 3
            ],
        }
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(bad_config)
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_fixed_monthly_missing_day_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """FIXED_MONTHLY_DATE without billing_day_of_month → 422."""
        bad = {**_bank_transfer_method(), "billing_day_of_month": None}
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_days_after_order_missing_days_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """DAYS_AFTER_ORDER without billing_days_after_order → 422."""
        bad = {**_credit_account_method(), "billing_days_after_order": None}
        data = _org_form_data(_decode_user_id_from_auth(admin_headers))
        data["payment_config"] = json.dumps(_shared_config([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
#  GET shared config
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetPaymentConfig:
    """GET /v1/organizations/{id}/payment-config"""

    @pytest.mark.asyncio
    async def test_admin_can_get_payment_config(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin retrieves config including payment_methods list."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.get(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)

        assert resp.status_code == 200
        pc = resp.json()["data"]
        assert pc["organization_id"] == org_id
        assert "payment_methods" in pc
        assert len(pc["payment_methods"]) == 1
        assert "id" in pc
        assert "created_at" in pc
        assert "version" in pc

    @pytest.mark.asyncio
    async def test_super_admin_can_get_payment_config_without_org_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        user_factory,
        org_factory,
    ) -> None:
        """SUPER_ADMIN bypasses org_contact membership for payment-config read."""
        org = await org_factory()
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        token, _ = create_access_token(
            user_id=super_admin.id,
            role=super_admin.role,
            client_type="ADMIN",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "ADMIN",
        }
        resp = await client.get(f"{ORGS}/{org.id}/payment-config", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["organization_id"] == org.id

    @pytest.mark.asyncio
    async def test_get_includes_all_new_schema_fields(self, client: AsyncClient, admin_headers: dict) -> None:
        """Response includes all fields from the new schema."""
        org_id, _ = await _create_org_with_config(
            client, admin_headers,
            _shared_config([_bank_transfer_method(is_default=True)])
        )
        resp = await client.get(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)
        assert resp.status_code == 200
        pc = resp.json()["data"]
        for field in (
            "id", "organization_id",
            "vat_number", "vat_rate", "vat_treatment",
            "max_delivery_attempts", "delivery_attempt_fees",
            "max_return_attempts", "return_attempt_fees",
            "weight_margin_kg", "weight_surcharge_per_kg",
            "payment_methods",
            "created_at", "updated_at", "version",
        ):
            assert field in pc, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_get_payment_config_missing_row_falls_back_to_global_defaults(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ) -> None:
        """Org with no config row resolves and persists defaults from global config."""
        org = await org_factory()
        resp = await client.get(f"{ORGS}/{org.id}/payment-config", headers=admin_headers)
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["organization_id"] == org.id
        assert payload["max_delivery_attempts"] == len(payload["delivery_attempt_fees"])
        assert payload["max_return_attempts"] == len(payload["return_attempt_fees"])
        assert payload["payment_methods"] == []

    @pytest.mark.asyncio
    async def test_get_payment_config_missing_row_uses_current_global_values(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ) -> None:
        """Fallback row is seeded from current global attempt config, not hardcoded defaults."""
        global_resp = await client.put(
            "/v1/delivery-attempts",
            json={
                "delivery_attempt_fees": [
                    {"attempt": 1, "fee": "2.00"},
                    {"attempt": 2, "fee": "4.00"},
                ],
                "return_attempt_fees": [
                    {"attempt": 1, "fee": "6.00"},
                ],
            },
            headers=admin_headers,
        )
        assert global_resp.status_code == 200

        org = await org_factory()
        resp = await client.get(f"{ORGS}/{org.id}/payment-config", headers=admin_headers)
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["max_delivery_attempts"] == 2
        assert payload["delivery_attempt_fees"] == [
            {"attempt": 1, "fee": "2.00"},
            {"attempt": 2, "fee": "4.00"},
        ]
        assert payload["max_return_attempts"] == 1
        assert payload["return_attempt_fees"] == [{"attempt": 1, "fee": "6.00"}]

    @pytest.mark.asyncio
    async def test_get_payment_config_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org ID → 404."""
        resp = await client.get(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/payment-config",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, client: AsyncClient, admin_headers: dict) -> None:
        """No auth → 401."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.get(
            f"{ORGS}/{org_id}/payment-config",
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_non_admin_non_member_returns_403(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """B2C user (not an org member) → 403."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.get(f"{ORGS}/{org_id}/payment-config", headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
#  PATCH shared config (VAT, attempts, weight)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdatePaymentConfig:
    """PATCH /v1/organizations/{id}/payment-config — shared fields only; reason mandatory."""

    @pytest.mark.asyncio
    async def test_update_vat_rate(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can update vat_rate."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED", "reason": "Customer is zero-rated VAT"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["vat_rate"] == "ZERO_RATED"

    @pytest.mark.asyncio
    async def test_update_vat_treatment(self, client: AsyncClient, admin_headers: dict) -> None:
        """vat_treatment can be changed to OVERSEAS."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_treatment": "OVERSEAS", "reason": "Client operates overseas"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["vat_treatment"] == "OVERSEAS"

    @pytest.mark.asyncio
    async def test_update_return_attempt_fees(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can update return_attempt_fees (replaces return_to_sender_fee)."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={
                "max_return_attempts": 2,
                "return_attempt_fees": [
                    {"attempt": 1, "fee": "7.50"},
                    {"attempt": 2, "fee": "12.00"},
                ],
                "reason": "Fee schedule update",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        fees = resp.json()["data"]["return_attempt_fees"]
        assert len(fees) == 2
        assert Decimal(fees[0]["fee"]) == Decimal("7.50")

    @pytest.mark.asyncio
    async def test_update_weight_margin(self, client: AsyncClient, admin_headers: dict) -> None:
        """weight_margin_kg and weight_surcharge_per_kg are updateable."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"weight_margin_kg": 1.0, "weight_surcharge_per_kg": "2.50", "reason": "New weight policy"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        pc = resp.json()["data"]
        assert float(pc["weight_margin_kg"]) == 1.0
        assert Decimal(pc["weight_surcharge_per_kg"]) == Decimal("2.50")

    @pytest.mark.asyncio
    async def test_update_delivery_attempt_fees(self, client: AsyncClient, admin_headers: dict) -> None:
        """delivery_attempt_fees can be replaced (must match max_delivery_attempts)."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        new_fees = [
            {"attempt": 1, "fee": "0.00"},
            {"attempt": 2, "fee": "2.00"},
            {"attempt": 3, "fee": "4.00"},
        ]
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"delivery_attempt_fees": new_fees, "reason": "Updated fee schedule"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]["delivery_attempt_fees"]) == 3

    @pytest.mark.asyncio
    async def test_update_response_includes_payment_methods(self, client: AsyncClient, admin_headers: dict) -> None:
        """PATCH response always includes the payment_methods list."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED", "reason": "Response completeness check"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        pc = resp.json()["data"]
        assert "payment_methods" in pc
        assert len(pc["payment_methods"]) == 1

    @pytest.mark.asyncio
    async def test_update_requires_reason(self, client: AsyncClient, admin_headers: dict) -> None:
        """Missing reason → 422."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_reason_too_short_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Reason shorter than 3 chars → 422."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED", "reason": "ab"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_fees_array_only_derives_new_max(self, client: AsyncClient, admin_headers: dict) -> None:
        """When only fees are sent, max_delivery_attempts is derived from array length."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={
                "delivery_attempt_fees": [{"attempt": 1, "fee": "0.00"}],
                "reason": "Derive from provided fee list",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["max_delivery_attempts"] == 1
        assert payload["delivery_attempt_fees"] == [{"attempt": 1, "fee": "0.00"}]

    @pytest.mark.asyncio
    async def test_update_auto_creates_from_global_defaults_when_missing(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        """PATCH on org with no config auto-creates shared config and applies update."""
        org_id, _ = await _create_org_with_config(client, admin_headers)
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED", "reason": "Should 404"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["vat_rate"] == "ZERO_RATED"
        assert payload["max_delivery_attempts"] >= 1
        assert len(payload["delivery_attempt_fees"]) == payload["max_delivery_attempts"]

    @pytest.mark.asyncio
    async def test_update_compacts_attempt_numbers_when_gap_is_sent(self, client: AsyncClient, admin_headers: dict) -> None:
        """Sparse attempt list is compacted and max_delivery_attempts is updated."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={
                "delivery_attempt_fees": [
                    {"attempt": 1, "fee": "0.00"},
                    {"attempt": 3, "fee": "4.00"},
                ],
                "reason": "Remove middle attempt and compact",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["max_delivery_attempts"] == 2
        assert payload["delivery_attempt_fees"] == [
            {"attempt": 1, "fee": "0.00"},
            {"attempt": 2, "fee": "4.00"},
        ]

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """Non-admin → 403."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "ZERO_RATED", "reason": "Forbidden"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_partial_update_does_not_overwrite_other_fields(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unset fields are not cleared (partial update)."""
        org_id, original = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config",
            json={"vat_rate": "REDUCED_5", "reason": "Partial update test"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        pc = resp.json()["data"]
        assert pc["vat_rate"] == "REDUCED_5"
        assert pc["max_delivery_attempts"] == original["max_delivery_attempts"]
        assert pc["vat_treatment"] == original["vat_treatment"]


# ═══════════════════════════════════════════════════════════════════════════════
#  DELETE shared config
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeletePaymentConfig:
    """DELETE /v1/organizations/{id}/payment-config — hard-delete, admin only."""

    @pytest.mark.asyncio
    async def test_admin_can_delete_payment_config(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin deletes config; subsequent GET recreates fallback config from global/default."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())

        resp = await client.delete(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        get_resp = await client.get(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)
        assert get_resp.status_code == 200
        payload = get_resp.json()["data"]
        assert payload["max_delivery_attempts"] == len(payload["delivery_attempt_fees"])

    @pytest.mark.asyncio
    async def test_delete_is_hard_delete(self, client: AsyncClient, admin_headers: dict, db_session: AsyncSession) -> None:
        """Row is physically removed from DB."""
        from sqlalchemy import select

        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        await client.delete(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)

        result = await db_session.execute(
            select(OrgPaymentConfig).where(OrgPaymentConfig.organization_id == org_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_delete_not_found_returns_404(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Deleting config on org with no config → 404."""
        org = await org_factory()
        resp = await client.delete(f"{ORGS}/{org.id}/payment-config", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """Non-admin → 403."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.delete(f"{ORGS}/{org_id}/payment-config", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, client: AsyncClient, admin_headers: dict) -> None:
        """No auth → 401."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config",
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  POST payment method (add)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAddPaymentMethod:
    """POST /v1/organizations/{id}/payment-config/methods"""

    @pytest.mark.asyncio
    async def test_add_second_payment_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can add a second payment method; both are returned."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json=_bank_transfer_method(),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        pc = resp.json()["data"]
        assert len(pc["payment_methods"]) == 2
        models = {m["payment_model"] for m in pc["payment_methods"]}
        assert models == {"CARD", "BANK_TRANSFER"}

    @pytest.mark.asyncio
    async def test_add_cash_payment_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """CASH model can be added as a payment method."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json=_cash_method(),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        models = {m["payment_model"] for m in resp.json()["data"]["payment_methods"]}
        assert "CASH" in models

    @pytest.mark.asyncio
    async def test_add_duplicate_payment_model_returns_409(self, client: AsyncClient, admin_headers: dict) -> None:
        """Adding a model that already exists → 409."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json=_card_method(),
            headers=admin_headers,
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_add_method_without_shared_config_returns_404(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Cannot add method to org without a shared config row → 404."""
        org = await org_factory()
        resp = await client.post(
            f"{ORGS}/{org.id}/payment-config/methods",
            json=_card_method(),
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_add_new_default_clears_old_default(self, client: AsyncClient, admin_headers: dict) -> None:
        """Adding method with is_default=True clears the previous default."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json={**_bank_transfer_method(), "is_default": True},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        methods = resp.json()["data"]["payment_methods"]
        defaults = [m for m in methods if m["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["payment_model"] == "BANK_TRANSFER"

    @pytest.mark.asyncio
    async def test_add_method_invalid_schedule_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """BANK_TRANSFER + IMMEDIATE → 422."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))
        bad = {**_bank_transfer_method(), "billing_schedule": "IMMEDIATE"}
        bad.pop("billing_day_of_month", None)
        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json=bad,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_method_requires_admin(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """Non-admin → 403."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.post(
            f"{ORGS}/{org_id}/payment-config/methods",
            json=_bank_transfer_method(),
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
#  PATCH payment method (update)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdatePaymentMethod:
    """PATCH /v1/organizations/{id}/payment-config/methods/{payment_model}"""

    @pytest.mark.asyncio
    async def test_update_billing_day(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can change billing_day_of_month on a BANK_TRANSFER method."""
        org_id, _ = await _create_org_with_config(
            client, admin_headers, _shared_config([_bank_transfer_method(is_default=True)])
        )

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config/methods/BANK_TRANSFER",
            json={"billing_day_of_month": 28},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        methods = resp.json()["data"]["payment_methods"]
        bt = next(m for m in methods if m["payment_model"] == "BANK_TRANSFER")
        assert bt["billing_day_of_month"] == 28

    @pytest.mark.asyncio
    async def test_update_credit_limit(self, client: AsyncClient, admin_headers: dict) -> None:
        """credit_limit can be increased on a CREDIT_ACCOUNT method."""
        org_id, _ = await _create_org_with_config(
            client, admin_headers, _shared_config([_credit_account_method(is_default=True)])
        )

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config/methods/CREDIT_ACCOUNT",
            json={"credit_limit": "10000.00"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        methods = resp.json()["data"]["payment_methods"]
        ca = next(m for m in methods if m["payment_model"] == "CREDIT_ACCOUNT")
        assert Decimal(ca["credit_limit"]) == Decimal("10000.00")

    @pytest.mark.asyncio
    async def test_update_set_as_default(self, client: AsyncClient, admin_headers: dict) -> None:
        """Setting is_default=True on a method clears the existing default."""
        config = _shared_config([_card_method(), _bank_transfer_method()])
        org_id, _ = await _create_org_with_config(client, admin_headers, config)

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config/methods/BANK_TRANSFER",
            json={"is_default": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        methods = resp.json()["data"]["payment_methods"]
        defaults = [m for m in methods if m["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["payment_model"] == "BANK_TRANSFER"

    @pytest.mark.asyncio
    async def test_update_unknown_model_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Updating a model not configured for the org → 404."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config/methods/BANK_TRANSFER",
            json={"billing_day_of_month": 10},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_method_requires_admin(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """Non-admin → 403."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config())
        resp = await client.patch(
            f"{ORGS}/{org_id}/payment-config/methods/CARD",
            json={"billing_day_of_month": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
#  DELETE payment method
# ═══════════════════════════════════════════════════════════════════════════════


class TestRemovePaymentMethod:
    """DELETE /v1/organizations/{id}/payment-config/methods/{payment_model}"""

    @pytest.mark.asyncio
    async def test_remove_non_default_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can remove a non-default method; one method remains."""
        config = _shared_config([_card_method(), _bank_transfer_method()])
        org_id, _ = await _create_org_with_config(client, admin_headers, config)

        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config/methods/BANK_TRANSFER",
            headers=admin_headers,
        )
        assert resp.status_code == 200

        get_resp = await client.get(f"{ORGS}/{org_id}/payment-config", headers=admin_headers)
        methods = get_resp.json()["data"]["payment_methods"]
        assert len(methods) == 1
        assert methods[0]["payment_model"] == "CARD"

    @pytest.mark.asyncio
    async def test_cannot_remove_last_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """Removing the only method → 422."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config/methods/CARD",
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cannot_remove_default_method(self, client: AsyncClient, admin_headers: dict) -> None:
        """Removing the default method → 422 (reassign default first)."""
        config = _shared_config([_card_method(), _bank_transfer_method()])
        org_id, _ = await _create_org_with_config(client, admin_headers, config)

        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config/methods/CARD",
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_remove_unknown_model_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Model not configured for this org → 404."""
        org_id, _ = await _create_org_with_config(client, admin_headers, _shared_config([_card_method()]))

        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config/methods/CASH",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_method_requires_admin(self, client: AsyncClient, admin_headers: dict, auth_headers: dict) -> None:
        """Non-admin → 403."""
        config = _shared_config([_card_method(), _bank_transfer_method()])
        org_id, _ = await _create_org_with_config(client, admin_headers, config)

        resp = await client.delete(
            f"{ORGS}/{org_id}/payment-config/methods/BANK_TRANSFER",
            headers=auth_headers,
        )
        assert resp.status_code == 403
