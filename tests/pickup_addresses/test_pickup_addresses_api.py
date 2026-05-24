from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.organizations.models import Organization
from app.modules.user.models import User

PICKUP = "/v1/pickup-addresses"


def _b2b_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=user.organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


def _b2c_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role="CUSTOMER_B2C",
        client_type="CUSTOMER_B2C",
        organization_id=None,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2C"}


def _minimal_create_payload() -> list[dict]:
    return [
        {
            "line_1": "1 Warehouse Way",
            "city": "Birmingham",
            "state": "West Midlands",
            "postcode": "B1 1AA",
            "country": "United Kingdom",
        }
    ]


@pytest.mark.asyncio
async def test_b2b_pickup_address_crud(
    client: AsyncClient,
    user_factory,
    org_factory,
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _b2b_headers(user)

    create_resp = await client.post(PICKUP, headers=headers, json=_minimal_create_payload())
    assert create_resp.status_code == 201
    data = create_resp.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["id"]
    assert row["organization_id"] == org.id
    assert row["user_id"] is None
    assert row["line_1"] == "1 Warehouse Way"
    address_id = row["id"]

    listed = await client.get(PICKUP, headers=headers)
    assert listed.status_code == 200
    items = listed.json()["data"]
    assert len(items) >= 1
    assert any(x["id"] == address_id for x in items)

    one = await client.get(f"{PICKUP}/{address_id}", headers=headers)
    assert one.status_code == 200
    assert one.json()["data"]["id"] == address_id

    patch = await client.patch(
        f"{PICKUP}/{address_id}",
        headers=headers,
        json={"label": "Main depot"},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["label"] == "Main depot"

    deleted = await client.delete(f"{PICKUP}/{address_id}", headers=headers)
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_b2c_pickup_address_create_and_list(client: AsyncClient, user_factory) -> None:
    user: User = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
    headers = _b2c_headers(user)

    create_resp = await client.post(PICKUP, headers=headers, json=_minimal_create_payload())
    assert create_resp.status_code == 201
    data = create_resp.json()["data"]
    assert data[0]["user_id"] == user.id
    assert data[0]["organization_id"] is None

    listed = await client.get(PICKUP, headers=headers)
    assert listed.status_code == 200
    assert any(x["id"] == data[0]["id"] for x in listed.json()["data"])


@pytest.mark.asyncio
async def test_geocode_returns_422_when_google_not_configured(
    client: AsyncClient,
    user_factory,
    org_factory,
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _b2b_headers(user)
    resp = await client.post(f"{PICKUP}/geocode", headers=headers, json={"query": "London SW1A 1AA"})
    assert resp.status_code == 422
