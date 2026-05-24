"""API tests for payment card and Braintree client-token routes (/v1/payment-methods/)."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.organizations.models import Organization
from app.modules.user.models import User
from tests.payments.conftest import (
    admin_headers,
    b2b_headers,
    b2c_headers,
    make_braintree_credit_card_result,
    make_braintree_customer_result,
    make_braintree_duplicate_card_result,
    make_braintree_failed_result,
    make_braintree_nonce_find_three_ds_fail,
    make_braintree_nonce_find_three_ds_ok,
    make_braintree_pm_result,
    make_braintree_tx_result,
    make_braintree_vault_nonce_create_result,
    make_create_card_payload,
    make_dev_raw_card_payload,
)

BASE = "/v1/payment-methods"
CARDS = f"{BASE}/cards"
BRAINTREE_CLIENT_TOKEN = f"{CARDS}/braintree-client-token"
PREPARE_PAYMENT = f"{CARDS}/prepare-payment"


def _mock_gateway():
    """Patch get_braintree_gateway to return a MagicMock."""
    gw = MagicMock()
    gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_ok()
    return patch("app.modules.payments.service.get_braintree_gateway", return_value=gw), gw


# ── Auth ──────────────────────────────────────────────────────


class TestPaymentAuth:
    """Unauthenticated access must be rejected."""

    @pytest.mark.asyncio
    async def test_client_token_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(BRAINTREE_CLIENT_TOKEN)
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_list_cards_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(CARDS)
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_create_card_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(CARDS, json=make_create_card_payload())
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_prepare_payment_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(PREPARE_PAYMENT, json={"card_id": str(uuid4())})
        assert resp.status_code in (401, 422)


# ── Client Token ──────────────────────────────────────────────


class TestClientToken:
    """GET /v1/payment-methods/cards/braintree-client-token"""

    @pytest.mark.asyncio
    async def test_b2b_gets_client_token(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.client_token.generate.return_value = "sandbox_fake_token_abc"

        with mock_patch:
            resp = await client.get(BRAINTREE_CLIENT_TOKEN, headers=b2b_headers(b2b_user))

        assert resp.status_code == 200
        assert resp.json()["data"]["client_token"] == "sandbox_fake_token_abc"

    @pytest.mark.asyncio
    async def test_b2c_gets_client_token(self, client: AsyncClient, b2c_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.client_token.generate.return_value = "sandbox_b2c_token"

        with mock_patch:
            resp = await client.get(BRAINTREE_CLIENT_TOKEN, headers=b2c_headers(b2c_user))

        assert resp.status_code == 200
        assert resp.json()["data"]["client_token"] == "sandbox_b2c_token"

    @pytest.mark.asyncio
    async def test_b2b_client_token_scopes_duplicate_after_vault_customer_exists(
        self, client: AsyncClient, b2b_user: User
    ) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result("bt-cust-scoped")
        gw.payment_method.create.return_value = make_braintree_pm_result()
        gw.client_token.generate.return_value = "token_after_card"

        with mock_patch:
            await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))
            resp = await client.get(BRAINTREE_CLIENT_TOKEN, headers=b2b_headers(b2b_user))

        assert resp.status_code == 200
        gw.client_token.generate.assert_called_with(
            {
                "customer_id": "bt-cust-scoped",
            }
        )


# ── Create Payment Method (Nonce) ─────────────────────────────


class TestCreatePaymentMethod:
    """POST /v1/payment-methods/cards"""

    @pytest.mark.asyncio
    async def test_b2b_creates_card_201(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["card_type"] == "VISA"
        assert data["last_four"] == "4242"
        assert data["expiry_month"] == 12
        assert data["expiry_year"] == 2029
        assert data["cardholder_name"] == "Test User"
        assert data["is_default"] is True
        assert data["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_b2c_creates_card_201(self, client: AsyncClient, b2c_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result("bt-b2c-cust")
        gw.payment_method.create.return_value = make_braintree_pm_result(token="bt-b2c-tok")

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2c_headers(b2c_user))

        assert resp.status_code == 201
        assert resp.json()["data"]["card_type"] == "VISA"

    @pytest.mark.asyncio
    async def test_first_card_auto_default(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            resp = await client.post(
                CARDS,
                json=make_create_card_payload(set_as_default=False),
                headers=b2b_headers(b2b_user),
            )

        assert resp.status_code == 201
        assert resp.json()["data"]["is_default"] is True  # First card is always default

    @pytest.mark.asyncio
    async def test_card_verification_failed_422(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_failed_result("Do Not Honor")

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dup_code", ("81763", "81724"))
    async def test_duplicate_card_409(self, client: AsyncClient, b2b_user: User, dup_code: str) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_duplicate_card_result(dup_code)

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CONFLICT"
        assert "already saved" in body["message"]

    @pytest.mark.asyncio
    async def test_create_rejects_nonce_without_three_d_secure_422(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(missing_info=True)
        gw.customer.create.return_value = make_braintree_customer_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "BANK_AUTHENTICATION_REQUIRED"
        assert body["error"]["details"][0]["type"] == "bank_authentication_required"
        assert "bank" in body["message"].lower()
        gw.payment_method.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_allows_three_d_secure_ineligible_nonce(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(
            shifted=False, possible=False
        )
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 201
        gw.payment_method.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_allows_lookup_error_status_even_if_liability_flags_wrong(
        self, client: AsyncClient, b2b_user: User
    ) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(
            shifted=False,
            possible=True,
            status="lookup_error",
        )
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 201
        gw.payment_method.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_rejects_authenticate_frictionless_failed_422(
        self, client: AsyncClient, b2b_user: User
    ) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(
            shifted=False,
            possible=False,
            status="authenticate_frictionless_failed",
        )
        gw.customer.create.return_value = make_braintree_customer_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "BANK_VERIFICATION_FAILED"
        gw.payment_method.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_rejects_three_d_secure_challenge_incomplete_422(
        self, client: AsyncClient, b2b_user: User
    ) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(
            shifted=False, possible=True
        )
        gw.customer.create.return_value = make_braintree_customer_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "BANK_VERIFICATION_FAILED"
        assert body["error"]["details"][0]["type"] == "bank_verification_failed"
        gw.payment_method.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_rejects_three_d_secure_authenticate_failed_status_422(
        self, client: AsyncClient, b2b_user: User
    ) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.find.return_value = make_braintree_nonce_find_three_ds_fail(
            shifted=False,
            possible=False,
            status="authenticate_failed",
        )
        gw.customer.create.return_value = make_braintree_customer_result()

        with mock_patch:
            resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "BANK_VERIFICATION_FAILED"
        assert body["error"]["details"][0]["type"] == "bank_verification_failed"
        gw.payment_method.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_nonce_422(self, client: AsyncClient, b2b_user: User) -> None:
        resp = await client.post(CARDS, json={"cardholder_name": "Test"}, headers=b2b_headers(b2b_user))
        assert resp.status_code == 422


# ── List Payment Methods ──────────────────────────────────────


class TestListPaymentMethods:
    """GET /v1/payment-methods/cards"""

    @pytest.mark.asyncio
    async def test_empty_list(self, client: AsyncClient, b2b_user: User) -> None:
        resp = await client.get(CARDS, headers=b2b_headers(b2b_user))
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_lists_created_cards(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        resp = await client.get(CARDS, headers=b2b_headers(b2b_user))
        assert resp.status_code == 200
        cards = resp.json()["data"]
        assert len(cards) == 1
        assert cards[0]["last_four"] == "4242"

    @pytest.mark.asyncio
    async def test_b2b_does_not_see_other_org_cards(
        self, client: AsyncClient, b2b_user: User, user_factory, org_factory
    ) -> None:
        """Cards from org A should not be visible to org B."""
        other_org = await org_factory()
        other_user = await user_factory(
            role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=other_org.id
        )

        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        # Create card for other org
        with mock_patch:
            await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(other_user))

        # b2b_user's org should have no cards
        resp = await client.get(CARDS, headers=b2b_headers(b2b_user))
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ── Get Payment Method ────────────────────────────────────────


class TestGetPaymentMethod:
    """GET /v1/payment-methods/cards/{card_id}"""

    @pytest.mark.asyncio
    async def test_get_card_200(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            create_resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))
        card_id = create_resp.json()["data"]["id"]

        resp = await client.get(f"{CARDS}/{card_id}", headers=b2b_headers(b2b_user))
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == card_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_card_404(self, client: AsyncClient, b2b_user: User) -> None:
        resp = await client.get(f"{CARDS}/{uuid4()}", headers=b2b_headers(b2b_user))
        assert resp.status_code == 404


# ── Set Default ───────────────────────────────────────────────


class TestSetDefault:
    """PATCH /v1/payment-methods/cards/{card_id}/default"""

    @pytest.mark.asyncio
    async def test_set_default_200(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result(token="tok-1", last_4="1111")

        with mock_patch:
            r1 = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        gw.payment_method.create.return_value = make_braintree_pm_result(token="tok-2", last_4="2222")
        with mock_patch:
            r2 = await client.post(
                CARDS, json=make_create_card_payload(set_as_default=False), headers=b2b_headers(b2b_user)
            )

        card2_id = r2.json()["data"]["id"]

        resp = await client.patch(f"{CARDS}/{card2_id}/default", headers=b2b_headers(b2b_user))
        assert resp.status_code == 200
        assert resp.json()["data"]["is_default"] is True

        # Verify first card is no longer default
        card1_id = r1.json()["data"]["id"]
        check = await client.get(f"{CARDS}/{card1_id}", headers=b2b_headers(b2b_user))
        assert check.json()["data"]["is_default"] is False

    @pytest.mark.asyncio
    async def test_set_default_nonexistent_404(self, client: AsyncClient, b2b_user: User) -> None:
        resp = await client.patch(f"{CARDS}/{uuid4()}/default", headers=b2b_headers(b2b_user))
        assert resp.status_code == 404


# ── Prepare payment (vault nonce) ─────────────────────────────


class TestPrepareCheckoutNonce:
    """POST /v1/payment-methods/cards/prepare-payment"""

    @pytest.mark.asyncio
    async def test_prepare_returns_nonce_and_bin(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()
        gw.payment_method_nonce.create.return_value = make_braintree_vault_nonce_create_result()

        with mock_patch:
            create_resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))
        card_id = create_resp.json()["data"]["id"]

        with mock_patch:
            resp = await client.post(
                PREPARE_PAYMENT,
                json={"card_id": card_id},
                headers=b2b_headers(b2b_user),
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["nonce"] == "vault-forwarded-nonce-abc"
        assert data["bin"] == "411111"
        gw.payment_method_nonce.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_prepare_unknown_card_404(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.payment_method_nonce.create.return_value = make_braintree_vault_nonce_create_result()

        with mock_patch:
            resp = await client.post(
                PREPARE_PAYMENT,
                json={"card_id": str(uuid4())},
                headers=b2b_headers(b2b_user),
            )

        assert resp.status_code == 404


class TestDeletePaymentMethod:
    """DELETE /v1/payment-methods/cards/{card_id}"""

    @pytest.mark.asyncio
    async def test_delete_card_200(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result()

        with mock_patch:
            create_resp = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))
        card_id = create_resp.json()["data"]["id"]

        with mock_patch:
            resp = await client.delete(f"{CARDS}/{card_id}", headers=b2b_headers(b2b_user))

        assert resp.status_code == 200

        # Verify card is gone
        check = await client.get(f"{CARDS}/{card_id}", headers=b2b_headers(b2b_user))
        assert check.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_default_promotes_next(self, client: AsyncClient, b2b_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.payment_method.create.return_value = make_braintree_pm_result(token="tok-1", last_4="1111")

        with mock_patch:
            r1 = await client.post(CARDS, json=make_create_card_payload(), headers=b2b_headers(b2b_user))

        gw.payment_method.create.return_value = make_braintree_pm_result(token="tok-2", last_4="2222")
        with mock_patch:
            r2 = await client.post(
                CARDS, json=make_create_card_payload(set_as_default=False), headers=b2b_headers(b2b_user)
            )

        card1_id = r1.json()["data"]["id"]
        card2_id = r2.json()["data"]["id"]

        # Delete the default card (card1)
        with mock_patch:
            await client.delete(f"{CARDS}/{card1_id}", headers=b2b_headers(b2b_user))

        # Card2 should now be default
        check = await client.get(f"{CARDS}/{card2_id}", headers=b2b_headers(b2b_user))
        assert check.json()["data"]["is_default"] is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent_404(self, client: AsyncClient, b2b_user: User) -> None:
        resp = await client.delete(f"{CARDS}/{uuid4()}", headers=b2b_headers(b2b_user))
        assert resp.status_code == 404


# ── Dev: Raw Card ─────────────────────────────────────────────


class TestDevRawCard:
    """POST /v1/payment-methods/cards/dev/raw-card"""

    @pytest.mark.asyncio
    async def test_dev_raw_card_creates_201(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.find.side_effect = Exception("Not found")
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.credit_card.create.return_value = make_braintree_credit_card_result()

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/raw-card",
                json=make_dev_raw_card_payload(),
                headers=admin_headers(admin_user),
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["card_type"] == "VISA"
        assert data["last_four"] == "1111"
        assert data["is_default"] is True

    @pytest.mark.asyncio
    async def test_dev_raw_card_verification_failed_422(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.find.side_effect = Exception("Not found")
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.credit_card.create.return_value = make_braintree_failed_result("CVV mismatch")

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/raw-card",
                json=make_dev_raw_card_payload(),
                headers=admin_headers(admin_user),
            )

        assert resp.status_code == 422


# ── Dev: Charge Saved Card ────────────────────────────────────


class TestDevChargeSavedCard:
    """POST /v1/payment-methods/cards/dev/charge-saved"""

    @pytest.mark.asyncio
    async def test_charge_success(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        # First create a card
        gw.customer.find.side_effect = Exception("Not found")
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.credit_card.create.return_value = make_braintree_credit_card_result()

        with mock_patch:
            create_resp = await client.post(
                f"{CARDS}/dev/raw-card",
                json=make_dev_raw_card_payload(),
                headers=admin_headers(admin_user),
            )
        card_id = create_resp.json()["data"]["id"]

        # Now charge it
        gw.transaction.sale.return_value = make_braintree_tx_result(success=True)

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/charge-saved",
                json={
                    "credit_card_id": card_id,
                    "amount": "15.50",
                    "nonce": "dev-verified-nonce-from-verifycard",
                },
                headers=admin_headers(admin_user),
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is True
        assert data["payment_status"] == "paid"
        assert data["braintree_transaction_id"] == "bt-tx-001"
        sale_call = gw.transaction.sale.call_args[0][0]
        assert sale_call["payment_method_nonce"] == "dev-verified-nonce-from-verifycard"
        assert "payment_method_token" not in sale_call

    @pytest.mark.asyncio
    async def test_charge_requires_nonce(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.find.side_effect = Exception("Not found")
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.credit_card.create.return_value = make_braintree_credit_card_result()

        with mock_patch:
            create_resp = await client.post(
                f"{CARDS}/dev/raw-card",
                json=make_dev_raw_card_payload(),
                headers=admin_headers(admin_user),
            )
        card_id = create_resp.json()["data"]["id"]

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/charge-saved",
                json={"credit_card_id": card_id, "amount": "10.00"},
                headers=admin_headers(admin_user),
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_charge_failed(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()
        gw.customer.find.side_effect = Exception("Not found")
        gw.customer.create.return_value = make_braintree_customer_result()
        gw.credit_card.create.return_value = make_braintree_credit_card_result()

        with mock_patch:
            create_resp = await client.post(
                f"{CARDS}/dev/raw-card",
                json=make_dev_raw_card_payload(),
                headers=admin_headers(admin_user),
            )
        card_id = create_resp.json()["data"]["id"]

        gw.transaction.sale.return_value = make_braintree_tx_result(success=False)

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/charge-saved",
                json={
                    "credit_card_id": card_id,
                    "amount": "2000.00",
                    "nonce": "dev-verified-nonce-from-verifycard",
                },
                headers=admin_headers(admin_user),
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is False
        assert data["payment_status"] == "failed"

    @pytest.mark.asyncio
    async def test_charge_nonexistent_card_404(self, client: AsyncClient, admin_user: User) -> None:
        mock_patch, gw = _mock_gateway()

        with mock_patch:
            resp = await client.post(
                f"{CARDS}/dev/charge-saved",
                json={"credit_card_id": str(uuid4()), "amount": "10.00", "nonce": "n"},
                headers=admin_headers(admin_user),
            )

        assert resp.status_code == 404
