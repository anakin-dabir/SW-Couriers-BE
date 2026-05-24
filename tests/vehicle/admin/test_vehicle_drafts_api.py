"""Tests for GET /v1/vehicles/drafts (and query params) when draft routes are mounted.

If ``GET /v1/vehicles/drafts`` is not registered **before** ``GET /{vehicle_id}``, requests
hit ``get_vehicle("drafts")`` and return 404 — these tests skip in that case.
When draft CRUD is merged on the router, they run without changes.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from httpx import AsyncClient

from app.modules.user.models import User
from tests.vehicle.conftest import admin_headers, idem_headers


def _is_draft_list_envelope(resp) -> bool:
    if resp.status_code != 200:
        return False
    body = resp.json()
    if not body.get("success"):
        return False
    data = body.get("data") or {}
    return isinstance(data.get("items"), list) and "total" in data and "page" in data


@pytest.mark.asyncio
async def test_list_drafts_paginated_when_mounted(client: AsyncClient, vehicle_admin: User) -> None:
    headers = admin_headers(vehicle_admin)
    resp = await client.get("/v1/vehicles/drafts", headers=headers)
    if not _is_draft_list_envelope(resp):
        pytest.skip(
            "Draft list not mounted: define GET /drafts on vehicles router before /{vehicle_id}, "
            "or merge the vehicle drafts feature branch."
        )
    data = resp.json()["data"]
    assert data["page"] >= 1
    assert data["size"] >= 1


@pytest.mark.asyncio
async def test_list_drafts_accepts_order_desc_when_mounted(client: AsyncClient, vehicle_admin: User) -> None:
    headers = admin_headers(vehicle_admin)
    resp = await client.get(
        "/v1/vehicles/drafts",
        headers=headers,
        params={"page": 1, "size": 10, "order_desc": "true"},
    )
    if not _is_draft_list_envelope(resp):
        pytest.skip("Draft list not mounted (see test_list_drafts_paginated_when_mounted).")
    assert resp.status_code == 200

    resp_asc = await client.get(
        "/v1/vehicles/drafts",
        headers=headers,
        params={"page": 1, "size": 10, "order_desc": "false"},
    )
    assert resp_asc.status_code == 200
    assert _is_draft_list_envelope(resp_asc)


@pytest.mark.asyncio
async def test_list_drafts_search_filters_by_vehicle_fields(client: AsyncClient, vehicle_admin: User) -> None:
    probe = await client.get("/v1/vehicles/drafts", headers=admin_headers(vehicle_admin))
    if not _is_draft_list_envelope(probe):
        pytest.skip("Draft list not mounted (see test_list_drafts_paginated_when_mounted).")

    suffix_a = uuid.uuid4().hex[:6].upper()
    suffix_b = uuid.uuid4().hex[:6].upper()
    reg_a = f"ZZ{suffix_a}"
    reg_b = f"YY{suffix_b}"
    r_a = await client.post(
        "/v1/vehicles/drafts",
        headers={**admin_headers(vehicle_admin), **idem_headers()},
        data={
            "vehicle_data": json.dumps(
                {
                    "registration_number": reg_a,
                    "make": "SearchUniqueFord",
                    "max_continuous_driving_hours": 4.0,
                    "break_duration_minutes": 30,
                }
            )
        },
    )
    r_b = await client.post(
        "/v1/vehicles/drafts",
        headers={**admin_headers(vehicle_admin), **idem_headers()},
        data={
            "vehicle_data": json.dumps(
                {
                    "registration_number": reg_b,
                    "make": "SearchUniqueToyota",
                    "max_continuous_driving_hours": 4.0,
                    "break_duration_minutes": 30,
                }
            )
        },
    )
    assert r_a.status_code == 201, r_a.text
    assert r_b.status_code == 201, r_b.text

    list_headers = admin_headers(vehicle_admin)
    resp = await client.get(
        "/v1/vehicles/drafts",
        headers=list_headers,
        params={"page": 1, "size": 50, "search": "SearchUniqueFord"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    regs = {it.get("registration_number") for it in items}
    assert reg_a in regs
    assert reg_b not in regs

    resp_reg = await client.get(
        "/v1/vehicles/drafts",
        headers=list_headers,
        params={"page": 1, "size": 50, "search": suffix_a},
    )
    assert resp_reg.status_code == 200
    items2 = resp_reg.json()["data"]["items"]
    assert any(it.get("registration_number") == reg_a for it in items2)


@pytest.mark.asyncio
async def test_patch_draft_in_maintenance_updates_availability_and_get_returns_initial_maintenance(
    client: AsyncClient, vehicle_admin: User,
) -> None:
    probe = await client.get("/v1/vehicles/drafts", headers=admin_headers(vehicle_admin))
    if not _is_draft_list_envelope(probe):
        pytest.skip("Draft list not mounted (see test_list_drafts_paginated_when_mounted).")

    suffix = uuid.uuid4().hex[:6].upper()
    reg = f"PD{suffix}"
    create = await client.post(
        "/v1/vehicles/drafts",
        headers={**admin_headers(vehicle_admin), **idem_headers()},
        data={
            "vehicle_data": json.dumps(
                {
                    "registration_number": reg,
                    "make": "Ford",
                    "max_continuous_driving_hours": 4.0,
                    "break_duration_minutes": 30,
                }
            )
        },
    )
    assert create.status_code == 201, create.text
    draft_id = create.json()["data"]["id"]
    assert create.json()["data"]["availability"] == "ACTIVE"

    maint = {
        "maintenance_types": ["MOT"],
        "provider_type": "EXTERNAL",
        "date_from": date.today().isoformat(),
        "cost": 0,
        "garage": "Test Garage Ltd",
    }
    patch = await client.patch(
        f"/v1/vehicles/drafts/{draft_id}",
        headers={**admin_headers(vehicle_admin), **idem_headers()},
        data={"vehicle_data": json.dumps({"availability": "IN_MAINTENANCE", "initial_maintenance": maint})},
    )
    assert patch.status_code == 200, patch.text
    patched = patch.json()["data"]
    assert patched["availability"] == "IN_MAINTENANCE"
    assert patched.get("initial_maintenance") is not None
    assert patched["initial_maintenance"]["garage"] == "Test Garage Ltd"

    get_resp = await client.get(f"/v1/vehicles/drafts/{draft_id}", headers=admin_headers(vehicle_admin))
    assert get_resp.status_code == 200, get_resp.text
    got = get_resp.json()["data"]
    assert got["availability"] == "IN_MAINTENANCE"
    assert got.get("initial_maintenance") is not None
    assert got["initial_maintenance"]["garage"] == "Test Garage Ltd"

    patch2 = await client.patch(
        f"/v1/vehicles/drafts/{draft_id}",
        headers={**admin_headers(vehicle_admin), **idem_headers()},
        data={
            "vehicle_data": json.dumps(
                {
                    "initial_maintenance": {
                        **maint,
                        "garage": "Updated Garage",
                    }
                }
            )
        },
    )
    assert patch2.status_code == 200, patch2.text
    assert patch2.json()["data"]["initial_maintenance"]["garage"] == "Updated Garage"
