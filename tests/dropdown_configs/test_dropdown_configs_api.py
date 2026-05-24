"""API tests for vehicle dropdown configuration (v1)."""

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.user.models import User

PREFIX = "/v1/dropdown-configs"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _rows_label_color(rows: list[dict]) -> list[dict]:
    return [{"label": r["label"], "color_hex": r.get("color_hex")} for r in rows]


@pytest.mark.asyncio
class TestDropdownConfigsApi:
    async def test_list_keys(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{PREFIX}/keys", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        keys = {row["key"] for row in data}
        assert keys == {
            "FUEL_TYPE",
            "DEFECT_CATEGORY",
            "MAINTENANCE_TYPE",
            "SERVICE_TYPE",
            "VEHICLE_AVAILABILITY",
        }
        fuel = next(r for r in data if r["key"] == "FUEL_TYPE")
        assert fuel["values_count"] == 4

    async def test_list_values(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{PREFIX}/keys/FUEL_TYPE/values",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        codes = {row["code"] for row in resp.json()["data"]}
        assert codes == {"DIESEL", "PETROL", "ELECTRIC", "HYBRID"}
        ordered = [row["code"] for row in resp.json()["data"]]
        assert ordered == sorted(ordered)
        first = resp.json()["data"][0]
        assert first["dropdown_key"] == "FUEL_TYPE"
        assert "is_system" not in first

    async def test_list_all_values_grouped(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{PREFIX}/values", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [row["key"] for row in data] == sorted(
            [
                "FUEL_TYPE",
                "DEFECT_CATEGORY",
                "MAINTENANCE_TYPE",
                "SERVICE_TYPE",
                "VEHICLE_AVAILABILITY",
            ]
        )
        fuel = next(row for row in data if row["key"] == "FUEL_TYPE")
        assert fuel["display_name"] == "Fuel Type"
        assert {row["code"] for row in fuel["values"]} == {"DIESEL", "PETROL", "ELECTRIC", "HYBRID"}

    async def test_replace_values_add_reorder_remove(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        list_resp = await client.get(f"{PREFIX}/keys/FUEL_TYPE/values", headers=headers)
        assert list_resp.status_code == 200
        base = list_resp.json()["data"]

        with_new = _rows_label_color(base)
        with_new.append({"label": "Biodiesel", "color_hex": "#00AAFF"})

        patch_resp = await client.patch(
            f"{PREFIX}/keys/FUEL_TYPE/values",
            headers=headers,
            json={"values": with_new},
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.json()["data"]
        codes_after_add = {row["code"] for row in patched}
        assert codes_after_add == {"DIESEL", "PETROL", "ELECTRIC", "HYBRID", "BIODIESEL"}

        reordered_payload = list(reversed(patched))
        reorder_resp = await client.patch(
            f"{PREFIX}/keys/FUEL_TYPE/values",
            headers=headers,
            json={"values": _rows_label_color(reordered_payload)},
        )
        assert reorder_resp.status_code == 200
        assert {row["code"] for row in reorder_resp.json()["data"]} == codes_after_add
        reordered_codes = [row["code"] for row in reorder_resp.json()["data"]]
        assert reordered_codes == sorted(reordered_codes)

        without_biodiesel = _rows_label_color(
            [r for r in reorder_resp.json()["data"] if r["label"] != "Biodiesel"]
        )
        trim_resp = await client.patch(
            f"{PREFIX}/keys/FUEL_TYPE/values",
            headers=headers,
            json={"values": without_biodiesel},
        )
        assert trim_resp.status_code == 200
        assert "BIODIESEL" not in {row["code"] for row in trim_resp.json()["data"]}

    async def test_replace_removes_seeded_value(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        list_resp = await client.get(f"{PREFIX}/keys/FUEL_TYPE/values", headers=headers)
        rows = [r for r in list_resp.json()["data"] if r["label"] != "Diesel"]
        patch_resp = await client.patch(
            f"{PREFIX}/keys/FUEL_TYPE/values",
            headers=headers,
            json={"values": _rows_label_color(rows)},
        )
        assert patch_resp.status_code == 200
        assert "DIESEL" not in {row["code"] for row in patch_resp.json()["data"]}
