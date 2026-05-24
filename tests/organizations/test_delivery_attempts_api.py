"""Integration API tests — global delivery attempt config CRUD."""

from decimal import Decimal

import pytest
from httpx import AsyncClient

ATTEMPTS = "/v1/delivery-attempts"


class TestDeliveryAttemptsCrud:
    @pytest.mark.asyncio
    async def test_get_seeds_defaults(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.get(ATTEMPTS, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["max_delivery_attempts"] == 3
        assert len(data["delivery_attempt_fees"]) == 3
        assert data["max_return_attempts"] == 3
        assert len(data["return_attempt_fees"]) == 3

    @pytest.mark.asyncio
    async def test_post_conflicts_when_singleton_exists(self, client: AsyncClient, admin_headers: dict) -> None:
        await client.get(ATTEMPTS, headers=admin_headers)
        payload = {
            "max_delivery_attempts": 2,
            "delivery_attempt_fees": [
                {"attempt": 1, "fee": "1.00"},
                {"attempt": 2, "fee": "2.00"},
            ],
            "max_return_attempts": 2,
            "return_attempt_fees": [
                {"attempt": 1, "fee": "1.50"},
                {"attempt": 2, "fee": "2.50"},
            ],
        }
        resp = await client.post(ATTEMPTS, json=payload, headers=admin_headers)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_post_without_max_fields_derives_from_arrays(self, client: AsyncClient, admin_headers: dict) -> None:
        """POST accepts fee arrays only and derives max_* automatically."""
        await client.get(ATTEMPTS, headers=admin_headers)
        await client.delete(ATTEMPTS, headers=admin_headers)
        payload = {
            "delivery_attempt_fees": [
                {"attempt": 1, "fee": "1.00"},
                {"attempt": 2, "fee": "2.00"},
                {"attempt": 3, "fee": "3.00"},
            ],
            "return_attempt_fees": [
                {"attempt": 1, "fee": "1.50"},
                {"attempt": 2, "fee": "2.50"},
            ],
        }
        resp = await client.post(ATTEMPTS, json=payload, headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["max_delivery_attempts"] == 3
        assert data["max_return_attempts"] == 2

    @pytest.mark.asyncio
    async def test_put_without_max_fields_derives_from_arrays(self, client: AsyncClient, admin_headers: dict) -> None:
        """PUT accepts fee arrays only and derives max_* automatically."""
        resp = await client.put(
            ATTEMPTS,
            json={
                "delivery_attempt_fees": [
                    {"attempt": 1, "fee": "0.90"},
                    {"attempt": 2, "fee": "1.90"},
                ],
                "return_attempt_fees": [
                    {"attempt": 1, "fee": "1.10"},
                    {"attempt": 2, "fee": "2.10"},
                    {"attempt": 3, "fee": "3.10"},
                ],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["max_delivery_attempts"] == 2
        assert data["max_return_attempts"] == 3

    @pytest.mark.asyncio
    async def test_patch_compacts_attempts_and_updates_max(self, client: AsyncClient, admin_headers: dict) -> None:
        await client.put(
            ATTEMPTS,
            json={
                "max_delivery_attempts": 3,
                "delivery_attempt_fees": [
                    {"attempt": 1, "fee": "1.00"},
                    {"attempt": 2, "fee": "3.20"},
                    {"attempt": 3, "fee": "5.00"},
                ],
                "max_return_attempts": 3,
                "return_attempt_fees": [
                    {"attempt": 1, "fee": "1.00"},
                    {"attempt": 2, "fee": "3.20"},
                    {"attempt": 3, "fee": "5.00"},
                ],
            },
            headers=admin_headers,
        )
        resp = await client.patch(
            ATTEMPTS,
            json={
                "delivery_attempt_fees": [
                    {"attempt": 1, "fee": "1.00"},
                    {"attempt": 3, "fee": "5.00"},
                ]
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["max_delivery_attempts"] == 2
        assert data["delivery_attempt_fees"] == [
            {"attempt": 1, "fee": "1.00"},
            {"attempt": 2, "fee": "5.00"},
        ]

    @pytest.mark.asyncio
    async def test_delete_removes_singleton_and_get_reseeds_defaults(self, client: AsyncClient, admin_headers: dict) -> None:
        await client.get(ATTEMPTS, headers=admin_headers)
        delete_resp = await client.delete(ATTEMPTS, headers=admin_headers)
        assert delete_resp.status_code == 200

        get_resp = await client.get(ATTEMPTS, headers=admin_headers)
        assert get_resp.status_code == 200
        data = get_resp.json()["data"]
        assert data["max_delivery_attempts"] == 3
        assert Decimal(data["delivery_attempt_fees"][0]["fee"]) == Decimal("0.00")
