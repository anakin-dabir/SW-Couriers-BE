"""API tests for driver self-service profile endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver, DriverTermsAcceptanceRecord, DriverTermsAndConditions
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus, ServiceTier
from app.modules.orders.models import DeliveryStop, Order, Package, StopNote
from app.modules.organizations.models import Organization
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

DRIVERS = "/v1/drivers"
DRIVER_PROFILE = "/v1/driver-profile/me"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


def _driver_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="DRIVER", client_type="DRIVER")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "DRIVER",
    }


async def _create_driver_and_headers(client: AsyncClient, user_factory) -> tuple[dict[str, str], dict]:
    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    admin_headers = _admin_headers(admin.id)
    email = f"self-driver-{uuid.uuid4().hex[:8]}@example.com"
    with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=admin_headers,
            data={
                "email": email,
                "first_name": "Self",
                "last_name": "Driver",
                "phone": "07123456789",
                "state": "England",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "10 Self Street",
                "city": "London",
                "postcode": "SW1A 1AA",
                "max_stops": "20",
                "okay_with_layover": True,
                "layover_cost_per_night": "85",
                "max_layover_nights": 5,
                # Driving licence is required on create (exactly one file + one metadata object).
                "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
            },
            files=[
                ("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf")),
            ],
        )
    assert resp.status_code == 201
    payload = resp.json()["data"]["driver"]
    return _driver_headers(payload["user_id"]), payload


class TestDriverSelfProfileApi:
    @pytest.mark.asyncio
    async def test_admin_terms_management_endpoints(self, client: AsyncClient, user_factory, db_session) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        created = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=headers,
            json={
                "title": "SW Couriers Driver Terms and Conditions",
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "By accessing and using this application.",
                    }
                ],
                "is_active": True,
            },
        )
        assert created.status_code == 201
        terms_id = created.json()["data"]["id"]
        assert created.json()["data"]["title"] == "SW Couriers Driver Terms and Conditions"
        assert created.json()["data"]["is_active"] is True
        assert len(created.json()["data"]["clauses"]) == 1

        listed = await client.get(f"{DRIVERS}/terms-and-conditions/config", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["data"]["items"]) >= 1

        updated = await client.patch(
            f"{DRIVERS}/terms-and-conditions/config/{terms_id}",
            headers=headers,
            json={"title": "Updated Terms", "is_active": True},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["title"] == "Updated Terms"

        logs = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action.in_(["driver.terms.create", "driver.terms.update"]))
            )
        ).scalars().all()
        actions = {log.action for log in logs}
        assert "driver.terms.create" in actions
        assert "driver.terms.update" in actions

    @pytest.mark.asyncio
    async def test_get_my_profile(self, client: AsyncClient, user_factory) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        resp = await client.get(DRIVER_PROFILE, headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == created["id"]
        assert data["user_id"] == created["user_id"]
        assert data["email"] == created["user"]["email"]
        assert data["requires_password_change"] is True

    @pytest.mark.asyncio
    async def test_onboarding_consents_and_map_preference_flow(self, client: AsyncClient, user_factory, db_session) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        terms_create = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "SW Couriers Driver Terms and Conditions",
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "By accessing and using this application.",
                    }
                ],
                "is_active": True,
            },
        )
        assert terms_create.status_code == 201
        terms_id = terms_create.json()["data"]["id"]

        initial = await client.get(f"{DRIVER_PROFILE}/onboarding-status", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["data"]["terms_accepted"] is False
        assert initial.json()["data"]["requires_terms_reacceptance"] is False
        assert initial.json()["data"]["location_consent_given"] is False

        current_terms = await client.get(f"{DRIVER_PROFILE}/terms-and-conditions/current", headers=headers)
        assert current_terms.status_code == 200
        assert current_terms.json()["data"]["id"] is not None
        assert len(current_terms.json()["data"]["clauses"]) == 1

        invalid_consent = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": False,
                "allow_location_access": True,
            },
        )
        assert invalid_consent.status_code == 422

        consent_headers = {
            **headers,
            "User-Agent": "SWCouriersDriverTests/1.0",
            "X-Client-Type": "DRIVER",
        }
        consent = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=consent_headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
                "device_platform": "android",
                "device_model": "PixelTest",
                "app_version": "2.1.0",
            },
        )
        assert consent.status_code == 200
        consent_data = consent.json()["data"]
        assert consent_data["terms_accepted"] is True
        assert consent_data["requires_terms_reacceptance"] is False
        assert consent_data["location_consent_given"] is True
        assert consent_data["terms_accepted_at"] is not None
        assert consent_data["location_consent_at"] is not None

        acc_rows = (
            await db_session.execute(
                select(DriverTermsAcceptanceRecord).where(DriverTermsAcceptanceRecord.driver_id == _created["id"])
            )
        ).scalars().all()
        assert len(acc_rows) == 1
        assert acc_rows[0].terms_id == terms_id
        assert acc_rows[0].client_type == "DRIVER"
        assert acc_rows[0].user_agent == "SWCouriersDriverTests/1.0"
        assert acc_rows[0].device_info == {"platform": "android", "model": "PixelTest", "app_version": "2.1.0"}

        map_pref = await client.patch(
            f"{DRIVER_PROFILE}/map-preference",
            headers=headers,
            json={"map_preference": "WAZE"},
        )
        assert map_pref.status_code == 200
        assert map_pref.json()["data"]["map_preference"] == "WAZE"

        final_state = await client.get(f"{DRIVER_PROFILE}/onboarding-status", headers=headers)
        assert final_state.status_code == 200
        assert final_state.json()["data"]["map_preference"] == "WAZE"
        assert final_state.json()["data"]["requires_terms_reacceptance"] is False

        updated_terms = await client.patch(
            f"{DRIVERS}/terms-and-conditions/config/{terms_id}",
            headers=admin_headers,
            json={
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "By accessing and using this application - updated.",
                    }
                ]
            },
        )
        assert updated_terms.status_code == 200
        after_terms_change = await client.get(f"{DRIVER_PROFILE}/onboarding-status", headers=headers)
        assert after_terms_change.status_code == 200
        assert after_terms_change.json()["data"]["requires_terms_reacceptance"] is True

        logs = (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action.in_(["driver.self.onboarding_consents.accept", "driver.self.map_preference.set"])
                )
            )
        ).scalars().all()
        actions = {log.action for log in logs}
        assert "driver.self.onboarding_consents.accept" in actions
        assert "driver.self.map_preference.set" in actions

    @pytest.mark.asyncio
    async def test_self_terms_current_and_consents_fail_without_active_terms(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        # DB may contain seeded active terms from migrations; deactivate so this scenario is deterministic.
        await db_session.execute(update(DriverTermsAndConditions).values(is_active=False))
        await db_session.flush()

        headers, _created = await _create_driver_and_headers(client, user_factory)

        current_terms = await client.get(f"{DRIVER_PROFILE}/terms-and-conditions/current", headers=headers)
        assert current_terms.status_code == 404

        consent = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
            },
        )
        assert consent.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_terms_create_rejects_duplicate_clause_order(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=headers,
            json={
                "title": "Invalid Terms",
                "clauses": [
                    {"clause_order": 1, "heading": "One", "body": "A"},
                    {"clause_order": 1, "heading": "Duplicate", "body": "B"},
                ],
                "is_active": True,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_terms_update_requires_at_least_one_field(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        created = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=headers,
            json={
                "title": "Base Terms",
                "clauses": [{"clause_order": 1, "heading": "H1", "body": "B1"}],
                "is_active": True,
            },
        )
        assert created.status_code == 201
        terms_id = created.json()["data"]["id"]

        updated = await client.patch(
            f"{DRIVERS}/terms-and-conditions/config/{terms_id}",
            headers=headers,
            json={},
        )
        assert updated.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_terms_create_active_deactivates_previous_active(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        first = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=headers,
            json={
                "title": "Terms A",
                "clauses": [{"clause_order": 1, "heading": "H1", "body": "B1"}],
                "is_active": True,
            },
        )
        assert first.status_code == 201
        first_id = first.json()["data"]["id"]

        second = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=headers,
            json={
                "title": "Terms B",
                "clauses": [{"clause_order": 1, "heading": "H2", "body": "B2"}],
                "is_active": True,
            },
        )
        assert second.status_code == 201
        second_id = second.json()["data"]["id"]

        listed = await client.get(f"{DRIVERS}/terms-and-conditions/config", headers=headers)
        assert listed.status_code == 200
        items = listed.json()["data"]["items"]
        active_items = [row for row in items if row["is_active"] is True]
        assert len(active_items) == 1
        assert active_items[0]["id"] == second_id
        assert all(row["id"] != first_id or row["is_active"] is False for row in items)

    @pytest.mark.asyncio
    async def test_terms_admin_endpoints_require_authentication(self, client: AsyncClient) -> None:
        list_resp = await client.get(f"{DRIVERS}/terms-and-conditions/config")
        assert list_resp.status_code == 401

        create_resp = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            json={
                "title": "Blocked",
                "clauses": [{"clause_order": 1, "heading": "H1", "body": "B1"}],
                "is_active": True,
            },
        )
        assert create_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_onboarding_consents_is_repeatable_and_keeps_reacceptance_false(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created_driver = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        created_terms = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "Stable Terms",
                "clauses": [
                    {"clause_order": 1, "heading": "Acceptance", "body": "You accept."},
                    {"clause_order": 2, "heading": "Usage", "body": "Use lawfully."},
                ],
                "is_active": True,
            },
        )
        assert created_terms.status_code == 201

        first = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={"accept_terms_and_conditions": True, "allow_location_access": True},
        )
        assert first.status_code == 200
        first_terms_ts = first.json()["data"]["terms_accepted_at"]
        first_loc_ts = first.json()["data"]["location_consent_at"]
        assert first_terms_ts is not None
        assert first_loc_ts is not None

        second = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={"accept_terms_and_conditions": True, "allow_location_access": True},
        )
        assert second.status_code == 200
        second_data = second.json()["data"]
        assert second_data["terms_accepted"] is True
        assert second_data["location_consent_given"] is True
        assert second_data["requires_terms_reacceptance"] is False
        assert second_data["terms_accepted_at"] is not None
        assert second_data["location_consent_at"] is not None

        driver = await db_session.get(Driver, created_driver["id"])
        assert driver is not None
        assert driver.terms_and_conditions_id is not None
        assert driver.terms_accepted_content_hash is not None

    @pytest.mark.asyncio
    async def test_reordering_clause_payload_does_not_trigger_reacceptance(self, client: AsyncClient, user_factory) -> None:
        headers, _created_driver = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        terms_created = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "Canonical Terms",
                "clauses": [
                    {"clause_order": 1, "heading": "One", "body": "A"},
                    {"clause_order": 2, "heading": "Two", "body": "B"},
                ],
                "is_active": True,
            },
        )
        assert terms_created.status_code == 201
        terms_id = terms_created.json()["data"]["id"]

        consent = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={"accept_terms_and_conditions": True, "allow_location_access": True},
        )
        assert consent.status_code == 200

        reorder_only = await client.patch(
            f"{DRIVERS}/terms-and-conditions/config/{terms_id}",
            headers=admin_headers,
            json={
                "clauses": [
                    {"clause_order": 2, "heading": "Two", "body": "B"},
                    {"clause_order": 1, "heading": "One", "body": "A"},
                ]
            },
        )
        assert reorder_only.status_code == 200

        status_after = await client.get(f"{DRIVER_PROFILE}/onboarding-status", headers=headers)
        assert status_after.status_code == 200
        assert status_after.json()["data"]["requires_terms_reacceptance"] is False

    @pytest.mark.asyncio
    async def test_device_installation_id_per_install_terms_and_legacy_path(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        terms_resp = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "Device Terms",
                "clauses": [{"clause_order": 1, "heading": "H", "body": "B"}],
                "is_active": True,
            },
        )
        assert terms_resp.status_code == 201

        device_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        device_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

        legacy = await client.get(f"{DRIVER_PROFILE}/onboarding-status", headers=headers)
        assert legacy.status_code == 200
        d0 = legacy.json()["data"]
        assert d0["requires_terms_reacceptance"] is False

        before = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": device_a},
        )
        assert before.status_code == 200
        d1 = before.json()["data"]
        assert d1["terms_accepted"] is False
        assert d1["requires_terms_reacceptance"] is False

        consent_a = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
                "device_installation_id": device_a,
            },
        )
        assert consent_a.status_code == 200
        ca = consent_a.json()["data"]
        assert ca["requires_terms_reacceptance"] is False

        ok_a = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": device_a},
        )
        assert ok_a.json()["data"]["requires_terms_reacceptance"] is False

        need_b = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": device_b},
        )
        assert need_b.status_code == 200
        nb = need_b.json()["data"]
        assert nb["requires_terms_reacceptance"] is True

        consent_b = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers={
                **headers,
                "X-Device-Installation-Id": device_b,
            },
            json={"accept_terms_and_conditions": True, "allow_location_access": True},
        )
        assert consent_b.status_code == 200
        cb = consent_b.json()["data"]
        assert cb["requires_terms_reacceptance"] is False

        rows = (
            await db_session.execute(
                select(DriverTermsAcceptanceRecord).where(DriverTermsAcceptanceRecord.driver_id == created["id"])
            )
        ).scalars().all()
        assert len(rows) == 2
        by_install = {r.device_installation_id for r in rows}
        assert by_install == {device_a, device_b}

        header_only = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers={**headers, "X-Device-Installation-Id": device_a},
        )
        assert header_only.json()["data"]["requires_terms_reacceptance"] is False

    @pytest.mark.asyncio
    async def test_onboarding_status_rejects_short_device_installation_id(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        bad = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": "short"},
        )
        assert bad.status_code == 422

    @pytest.mark.asyncio
    async def test_onboarding_consents_reject_short_device_installation_id(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "T",
                "clauses": [{"clause_order": 1, "heading": "H", "body": "B"}],
                "is_active": True,
            },
        )
        bad = await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
                "device_installation_id": "tiny",
            },
        )
        assert bad.status_code == 422

    @pytest.mark.asyncio
    async def test_terms_content_change_resets_device_terms_until_reaccept(
        self, client: AsyncClient, user_factory
    ) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        created_terms = await client.post(
            f"{DRIVERS}/terms-and-conditions/config",
            headers=admin_headers,
            json={
                "title": "V1",
                "clauses": [{"clause_order": 1, "heading": "H", "body": "One"}],
                "is_active": True,
            },
        )
        assert created_terms.status_code == 201
        terms_id = created_terms.json()["data"]["id"]
        device_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

        await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
                "device_installation_id": device_id,
            },
        )

        await client.patch(
            f"{DRIVERS}/terms-and-conditions/config/{terms_id}",
            headers=admin_headers,
            json={"clauses": [{"clause_order": 1, "heading": "H", "body": "Two"}]},
        )

        st = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": device_id},
        )
        data = st.json()["data"]
        assert data["requires_terms_reacceptance"] is True

        await client.post(
            f"{DRIVER_PROFILE}/onboarding-consents",
            headers=headers,
            json={
                "accept_terms_and_conditions": True,
                "allow_location_access": True,
                "device_installation_id": device_id,
            },
        )
        st2 = await client.get(
            f"{DRIVER_PROFILE}/onboarding-status",
            headers=headers,
            params={"device_installation_id": device_id},
        )
        d2 = st2.json()["data"]
        assert d2["requires_terms_reacceptance"] is False

    @pytest.mark.asyncio
    async def test_map_preference_rejects_invalid_enum(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        resp = await client.patch(
            f"{DRIVER_PROFILE}/map-preference",
            headers=headers,
            json={"map_preference": "INVALID_MAP_APP"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_my_profile(self, client: AsyncClient, user_factory) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "07999999999",
                "expected_version": created["version"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["first_name"] == "Updated"
        assert data["last_name"] == "Name"
        assert data["phone"] == "07999999999"
        assert data["version"] == created["version"] + 1

    @pytest.mark.asyncio
    async def test_patch_my_profile_validation(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)

        empty_resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={},
        )
        assert empty_resp.status_code == 422

        invalid_phone_resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={"phone": "invalid_phone"},
        )
        assert invalid_phone_resp.status_code == 422

        blank_name_resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={"first_name": "   "},
        )
        assert blank_name_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_my_profile_rejects_email_in_body(self, client: AsyncClient, user_factory) -> None:
        """Email is not part of the self-service schema; clients must not send it."""
        headers, _created = await _create_driver_and_headers(client, user_factory)
        only_email = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={"email": "hacker@example.com"},
        )
        assert only_email.status_code == 422
        mixed = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={"first_name": "Ok", "email": "hacker@example.com"},
        )
        assert mixed.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_my_profile_persists_to_user(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={
                "first_name": "Persisted",
                "last_name": "Driver",
                "phone": "07000000001",
                "expected_version": created["version"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["email"] == created["user"]["email"]

        user = await db_session.get(User, created["user_id"])
        assert user is not None
        assert user.first_name == "Persisted"
        assert user.email == created["user"]["email"]
        assert user.phone == "07000000001"

    @pytest.mark.asyncio
    async def test_photo_upload_and_delete(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        with patch("app.modules.drivers.service.get_images_client") as get_client_mock:
            client_mock = get_client_mock.return_value
            client_mock.upload_image = AsyncMock()
            client_mock.upload_image.return_value.id = "img_123"
            client_mock.upload_image.return_value.filename = "profile.png"
            client_mock.upload_image.return_value.variants = []
            client_mock.generate_signed_url.return_value = "https://example.com/driver-photo"
            upload_resp = await client.post(
                f"{DRIVER_PROFILE}/photo",
                headers=headers,
                files={"photo": ("profile.png", b"image-bytes", "image/png")},
            )
        assert upload_resp.status_code == 200
        upload_data = upload_resp.json()["data"]
        assert upload_data["profile_photo_url"] is not None

        with patch("app.modules.drivers.service.get_images_client") as get_client_mock:
            client_mock = get_client_mock.return_value
            client_mock.delete_image = AsyncMock(return_value=None)
            delete_resp = await client.delete(f"{DRIVER_PROFILE}/photo", headers=headers)
        assert delete_resp.status_code == 200
        assert delete_resp.json()["data"] == {}

    @pytest.mark.asyncio
    async def test_photo_upload_rejects_too_large(self, client: AsyncClient, user_factory) -> None:
        """POST /driver-profile/me/photo enforces 5MB max for JPEG/PNG."""
        headers, _created = await _create_driver_and_headers(client, user_factory)

        oversized = b"0" * (5 * 1024 * 1024 + 1)
        files = {"photo": ("too-big.png", oversized, "image/png")}

        with patch("app.modules.drivers.service.get_images_client") as get_client_mock:
            get_client_mock.return_value.upload_image = AsyncMock()
            resp = await client.post(f"{DRIVER_PROFILE}/photo", headers=headers, files=files)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_photo_upload_rejects_wrong_mime_type(self, client: AsyncClient, user_factory) -> None:
        """Only JPEG/PNG are accepted for driver profile photo upload."""
        headers, _created = await _create_driver_and_headers(client, user_factory)
        files = {"photo": ("bad.pdf", b"%PDF-1.4 bad", "application/pdf")}

        with patch("app.modules.drivers.service.get_images_client") as get_client_mock:
            get_client_mock.return_value.upload_image = AsyncMock()
            resp = await client.post(f"{DRIVER_PROFILE}/photo", headers=headers, files=files)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_my_profile_photo_idempotent(self, client: AsyncClient, user_factory, db_session) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        first = await client.delete(f"{DRIVER_PROFILE}/photo", headers=headers)
        second = await client.delete(f"{DRIVER_PROFILE}/photo", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200

        driver = await db_session.get(Driver, created["id"])
        assert driver is not None
        assert driver.profile_photo_key is None

    @pytest.mark.asyncio
    async def test_non_driver_cannot_access_self_profile(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.get(DRIVER_PROFILE, headers=headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_driver_cannot_mutate_self_profile(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.patch(
            DRIVER_PROFILE,
            headers=headers,
            json={"first_name": "Blocked"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_client_type_rejected(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        mismatched_headers = dict(headers)
        mismatched_headers["X-Client-Type"] = "ADMIN"
        resp = await client.get(DRIVER_PROFILE, headers=mismatched_headers)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_driver_without_profile_gets_404(self, client: AsyncClient, driver_user: User) -> None:
        headers = _driver_headers(driver_user.id)
        resp = await client.get(DRIVER_PROFILE, headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_mobile_self_routes_and_actions(self, client: AsyncClient, user_factory, db_session) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]

        suffix = uuid.uuid4().hex[:8].upper()
        org = Organization(
            reference=f"T{suffix}"[:20],
            trading_name=f"Self Org {suffix}",
            legal_entity_name=f"Self Org {suffix} Limited",
            companies_house_number=f"CH{suffix[:8]}",
            vat_number=f"GB{suffix[:9]}",
            date_of_incorporation=date(2020, 1, 1),
            industry="OTHER",
            company_size="1-10 employees",
            reg_address_line_1="1 Test Street",
            reg_city="London",
            reg_postcode="EC1A 1BB",
            status="ACTIVE",
        )
        db_session.add(org)
        await db_session.flush()
        depot = Depot(
            name="Self Mobile Depot",
            code=f"DP-{suffix}",
            address_line_1="1 Mobile Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"SELF-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        service_date = datetime.now(UTC).date()
        plan = RoutePlan(service_date=service_date, depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        prev_plan = RoutePlan(service_date=service_date - timedelta(days=1), depot_id=depot.id, status="READY")
        db_session.add(prev_plan)
        await db_session.flush()

        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-{suffix}",
            route_type="DELIVERY",
            total_stops=2,
            total_distance_km=16.0,
            estimated_drive_time_min=45.0,
            actual_drive_time_min=10.0,
            status="ASSIGNED",
        )
        db_session.add(route)
        await db_session.flush()
        prev_route = Route(
            plan_id=prev_plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-PREV-{suffix}",
            route_type="DELIVERY",
            total_stops=10,
            total_distance_km=8.0,
            estimated_drive_time_min=30.0,
            actual_drive_time_min=8.0,
            status="ASSIGNED",
        )
        db_session.add(prev_route)
        await db_session.flush()
        order = Order(
            order_id=f"SWC-ORD-{suffix}",
            master_label_id=f"ML-{suffix}",
            organization_id=org.id,
            customer_id=created["user_id"],
            subtotal=0,
            vat_amount=0,
            total_amount=0,
            status=OrderStatus.DELIVERY_IN_PROGRESS,
        )
        db_session.add(order)
        await db_session.flush()
        dstop = DeliveryStop(
            order_id=order.id,
            tracking_id=f"TRK-{suffix}",
            recipient_first_name="North",
            recipient_last_name="Hub",
            recipient_phone="07111111111",
            recipient_email=f"north-hub-{suffix.lower()}@example.com",
            line_1="North Hub Street",
            city="London",
            postcode="SW1A 2AA",
            service_tier=ServiceTier.STANDARD,
            signature_required=False,
            safe_place_allowed=True,
            status=DeliveryStopStatus.OUT_FOR_DELIVERY,
        )
        db_session.add(dstop)
        await db_session.flush()
        stop_eta = datetime(2026, 4, 24, 10, 0, 0, tzinfo=UTC)
        stop = RouteStop(
            route_id=route.id,
            delivery_stop_id=dstop.id,
            sequence=1,
            status="PENDING",
            estimated_arrival=stop_eta,
        )
        db_session.add(stop)
        stop_tail = RouteStop(
            route_id=route.id,
            delivery_stop_id=None,
            sequence=2,
            status="PENDING",
        )
        db_session.add(stop_tail)
        db_session.add_all(
            [
                Package(
                    order_id=order.id,
                    delivery_stop_id=dstop.id,
                    package_id=f"PKG-{suffix}1",
                    status=PackageStatus.OUT_FOR_DELIVERY,
                ),
                Package(
                    order_id=order.id,
                    delivery_stop_id=dstop.id,
                    package_id=f"PKG-{suffix}2",
                    status=PackageStatus.OUT_FOR_DELIVERY,
                ),
            ]
        )
        db_session.add(
            RouteEvent(
                route_id=route.id,
                driver_id=driver_id,
                event_type="SPEEDING",
                occurred_at=datetime.now(UTC) - timedelta(minutes=5),
                lat=51.5,
                lng=-0.12,
                event_metadata={"speed_mph": 40, "limit_mph": 30},
            )
        )
        db_session.add(
            RouteEvent(
                route_id=route.id,
                driver_id=driver_id,
                event_type="SPEEDING",
                occurred_at=datetime.now(UTC) - timedelta(minutes=3),
                lat=51.52,
                lng=-0.13,
                event_metadata={"speed_mph": 74, "limit_mph": 40},
            )
        )
        db_session.add(
            RouteEvent(
                route_id=route.id,
                driver_id=driver_id,
                event_type="HARSH_BRAKING",
                occurred_at=datetime.now(UTC) - timedelta(minutes=2),
                lat=51.53,
                lng=-0.14,
                event_metadata={"start_speed_mph": 45, "end_speed_mph": 10, "severity": "HIGH"},
            )
        )
        db_session.add(
            StopNote(
                delivery_stop_id=dstop.id,
                note_type="IMPORTANT",
                message="Leave at side gate if no answer.",
                is_blocking=True,
                sort_order=1,
            )
        )
        await db_session.commit()

        home = await client.get(f"{DRIVER_PROFILE}/home/summary?period=today", headers=headers)
        assert home.status_code == 200
        home_data = home.json()["data"]
        assert home_data["addresses_attended"] == 0
        assert "addresses_change_pct" in home_data
        assert home_data["average_speed_mph"] is not None
        assert "average_speed_change_pct" in home_data

        last_month = await client.get(f"{DRIVER_PROFILE}/home/summary?period=last_month", headers=headers)
        assert last_month.status_code == 200
        last_month_data = last_month.json()["data"]
        assert last_month_data["addresses_attended"] == 0
        assert last_month_data["average_speed_mph"] is None

        routes = await client.get(f"{DRIVER_PROFILE}/routes", headers=headers)
        assert routes.status_code == 200
        assert routes.json()["data"]["table"]["total"] >= 1

        summary = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["data"]["route_id"] == route.id
        assert summary.json()["data"]["progress"]["completed_stops"] == 0
        assert summary.json()["data"]["progress"]["total_stops"] == 2
        assert summary.json()["data"]["progress"]["percent"] == 0

        current = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert current.status_code == 200
        cur = current.json()["data"]["current_route"]
        assert cur is not None
        assert cur["route_id"] == route.id
        assert cur["route_code"] == route.route_code
        assert cur["status"] == "ASSIGNED"
        assert cur["service_date"] == service_date.isoformat()
        assert cur["progress"] == {"completed_stops": 0, "total_stops": 2, "percent": 0}
        assert cur["todays_deliveries_count"] == 2
        assert cur["todays_deliveries_change_pct"] == -80.0
        assert cur["estimated_drive_time_change_pct"] == 50.0
        assert cur["next_stop"] is not None
        assert cur["next_stop"]["stop_id"] == stop.id
        assert cur["next_stop"]["tracking_id"] == dstop.tracking_id
        assert cur["next_stop"]["stop_flow_type"] == "DELIVERY"
        assert cur["next_stop"]["location_name"] == "North Hub Street"
        assert cur["next_stop"]["scheduled_at"] is not None

        telem = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/telematics", headers=headers)
        assert telem.status_code == 200

        above_70 = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/reports/above-70-mph", headers=headers)
        assert above_70.status_code == 200
        assert above_70.json()["data"]["table"]["total"] == 1

        sharp = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/reports/sharp-brakes", headers=headers)
        assert sharp.status_code == 200
        assert sharp.json()["data"]["table"]["total"] == 1

        avg_speed = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/average-speed", headers=headers)
        assert avg_speed.status_code == 200
        assert avg_speed.json()["data"]["route_id"] == route.id
        assert avg_speed.json()["data"]["average_speed_mph"] is not None

        active_map = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/active-driving-map", headers=headers)
        assert active_map.status_code == 200
        am = active_map.json()["data"]
        assert "location" in am
        assert "vehicle" in am
        assert "navigation" in am
        assert "data" in am
        assert len(am["data"]) >= 1
        first = am["data"][0]
        assert first["stop_id"] == stop.id
        assert first["sequence"] == 1
        assert first["stop_flow_type"] == "DELIVERY"
        assert first["tracking_id"] == dstop.tracking_id
        assert am["navigation"]["encoded_polyline"] is None
        assert am["vehicle"]["latitude"] is None

        from app.modules.planning.route_navigation import compute_route_navigation_fingerprint

        correct_fp = compute_route_navigation_fingerprint(
            sequences_and_route_stop_ids=[(1, stop.id), (2, stop_tail.id)],
        )
        route.navigation_encoded_polyline = "xPoly"
        route.navigation_fingerprint = "f" * 64
        await db_session.commit()
        stale_resp = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/active-driving-map", headers=headers)
        assert stale_resp.status_code == 200
        st = stale_resp.json()["data"]["navigation"]
        assert st["encoded_polyline"] is None
        assert st["meta"] is not None
        assert st["meta"].get("polyline_stale") is True

        route.navigation_fingerprint = correct_fp
        await db_session.commit()
        good_resp = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/active-driving-map", headers=headers)
        assert good_resp.json()["data"]["navigation"]["encoded_polyline"] == "xPoly"

        stops = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/stops", headers=headers)
        assert stops.status_code == 200
        stop_items = stops.json()["data"]["items"]
        assert len(stop_items) >= 1
        assert stop_items[0]["packages_count"] >= 2
        assert stop_items[0]["stop_flow_type"] == "DELIVERY"
        assert stop_items[0]["tracking_id"] == dstop.tracking_id
        assert stop_items[0]["tracking_summary"] == f"#{dstop.tracking_id}"
        assert stop_items[0]["recipient_phone"] == dstop.recipient_phone

        stop_packages = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/stops/{stop.id}/packages", headers=headers)
        assert stop_packages.status_code == 200
        package_items = stop_packages.json()["data"]["items"]
        assert len(package_items) == 2
        assert stop_packages.json()["data"]["tracking_id"] == dstop.tracking_id

        important_note = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/stops/{stop.id}/important-note", headers=headers)
        assert important_note.status_code == 200
        assert len(important_note.json()["data"]["items"]) == 1

        delivery_detail = await client.get(
            f"{DRIVER_PROFILE}/routes/{route.id}/stops/{stop.id}/delivery-detail",
            headers=headers,
        )
        assert delivery_detail.status_code == 200
        detail_data = delivery_detail.json()["data"]
        assert detail_data["trackingId"] == dstop.tracking_id
        assert "packages_summary" in detail_data
        assert "package_breakdown" in detail_data

        start = await client.post(f"{DRIVER_PROFILE}/routes/{route.id}/start", headers=headers)
        assert start.status_code == 200
        assert start.json()["data"]["status"] == "ACTIVE"

        arrive = await client.post(
            f"{DRIVER_PROFILE}/stops/{stop.id}/arrive",
            headers=headers,
            json={"notes": "Reached destination"},
        )
        assert arrive.status_code == 200
        assert arrive.json()["data"]["status"] == "ARRIVED"

        complete = await client.post(f"{DRIVER_PROFILE}/stops/{stop.id}/complete", headers=headers, json={})
        assert complete.status_code == 200
        assert complete.json()["data"]["status"] == "COMPLETED"

        summary_mid = await client.get(f"{DRIVER_PROFILE}/routes/{route.id}/summary", headers=headers)
        assert summary_mid.json()["data"]["progress"] == {"completed_stops": 1, "total_stops": 2, "percent": 50}
        current_mid = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert current_mid.json()["data"]["current_route"]["progress"]["percent"] == 50
        assert current_mid.json()["data"]["current_route"]["next_stop"]["stop_id"] == stop_tail.id

        home_after_complete = await client.get(f"{DRIVER_PROFILE}/home/summary?period=today", headers=headers)
        assert home_after_complete.status_code == 200
        assert home_after_complete.json()["data"]["addresses_attended"] >= 1

        telemetry = await client.post(
            f"{DRIVER_PROFILE}/telemetry/batch",
            headers=headers,
            json={
                "items": [
                    {
                        "route_id": route.id,
                        "lat": 51.501,
                        "lng": -0.121,
                        "speed_mph": 33.5,
                        "source": "gps",
                    }
                ]
            },
        )
        assert telemetry.status_code == 200
        assert telemetry.json()["data"]["accepted"] == 1

    @pytest.mark.asyncio
    async def test_mobile_self_routes_forbid_cross_driver_access(self, client: AsyncClient, user_factory, db_session) -> None:
        headers_a, created_a = await _create_driver_and_headers(client, user_factory)
        headers_b, created_b = await _create_driver_and_headers(client, user_factory)
        driver_b_id = created_b["id"]

        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Cross Driver Depot",
            code=f"DP-CROSS-{suffix}",
            address_line_1="1 Isolation Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"CROSS-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()

        route_b = Route(
            plan_id=plan.id,
            driver_id=driver_b_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-CROSS-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add(route_b)
        await db_session.flush()
        stop_b = RouteStop(route_id=route_b.id, sequence=1, status="PENDING")
        db_session.add(stop_b)
        await db_session.commit()

        # Driver A cannot read driver B route summary.
        summary = await client.get(f"{DRIVER_PROFILE}/routes/{route_b.id}/summary", headers=headers_a)
        assert summary.status_code == 404

        # Driver A cannot mutate driver B route.
        start = await client.post(f"{DRIVER_PROFILE}/routes/{route_b.id}/start", headers=headers_a)
        assert start.status_code == 404

        # Driver A cannot read driver B route stops.
        stops = await client.get(f"{DRIVER_PROFILE}/routes/{route_b.id}/stops", headers=headers_a)
        assert stops.status_code == 404

        current_a = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers_a)
        assert current_a.status_code == 200
        assert current_a.json()["data"]["current_route"] is None

        current_b = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers_b)
        assert current_b.status_code == 200
        assert current_b.json()["data"]["current_route"]["route_id"] == route_b.id

        # Driver A cannot mutate driver B stop.
        arrive = await client.post(f"{DRIVER_PROFILE}/stops/{stop_b.id}/arrive", headers=headers_a, json={})
        assert arrive.status_code == 404

    @pytest.mark.asyncio
    async def test_mobile_self_home_summary_invalid_period_rejected(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        resp = await client.get(f"{DRIVER_PROFILE}/home/summary?period=quarter_to_date", headers=headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_mobile_self_stop_packages_requires_matching_route_stop(self, client: AsyncClient, user_factory, db_session) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()

        depot = Depot(
            name="Mismatch Depot",
            code=f"DP-MIS-{suffix}",
            address_line_1="1 Mismatch Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"MIS-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route_a = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-MIS-A-{suffix}",
            route_type="DELIVERY",
            status="ASSIGNED",
        )
        route_b = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-MIS-B-{suffix}",
            route_type="DELIVERY",
            status="ASSIGNED",
        )
        db_session.add_all([route_a, route_b])
        await db_session.flush()
        stop_b = RouteStop(route_id=route_b.id, sequence=1, status="PENDING")
        db_session.add(stop_b)
        await db_session.commit()

        # stop_b does not belong to route_a, should be hidden as not found.
        resp = await client.get(f"{DRIVER_PROFILE}/routes/{route_a.id}/stops/{stop_b.id}/packages", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_telemetry_batch_derives_speeding_and_harsh_braking_events(self, client: AsyncClient, user_factory, db_session) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()

        depot = Depot(
            name="Telemetry Depot",
            code=f"DP-TLM-{suffix}",
            address_line_1="1 Telematics Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"TLM-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-TLM-{suffix}",
            route_type="DELIVERY",
            total_stops=0,
            status="ACTIVE",
        )
        db_session.add(route)
        await db_session.commit()

        telemetry = await client.post(
            f"{DRIVER_PROFILE}/telemetry/batch",
            headers=headers,
            json={
                "items": [
                    {
                        "route_id": route.id,
                        "occurred_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                        "lat": 51.501,
                        "lng": -0.121,
                        "speed_mph": 80.0,
                        "source": "gps",
                    },
                    {
                        "route_id": route.id,
                        "occurred_at": datetime.now(UTC).isoformat(),
                        "lat": 51.502,
                        "lng": -0.122,
                        "speed_mph": 50.0,
                        "source": "gps",
                    },
                ]
            },
        )
        assert telemetry.status_code == 200
        assert telemetry.json()["data"]["accepted"] == 2

        speeding = await client.get(
            f"{DRIVER_PROFILE}/routes/{route.id}/reports/above-70-mph",
            headers=headers,
        )
        assert speeding.status_code == 200
        assert speeding.json()["data"]["table"]["total"] >= 1

        harsh = await client.get(
            f"{DRIVER_PROFILE}/routes/{route.id}/reports/sharp-brakes",
            headers=headers,
        )
        assert harsh.status_code == 200
        assert harsh.json()["data"]["table"]["total"] >= 1


class TestDriverTodayAndAssignedRoutesApi:
    # Calendar-offset behaviour for depot-local “today” is covered in ``tests/unit/test_driver_calendar_timezone.py``.

    @pytest.mark.asyncio
    async def test_today_route_null_without_open_route(self, client: AsyncClient, user_factory) -> None:
        headers, _created = await _create_driver_and_headers(client, user_factory)
        resp = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["current_route"] is None

    @pytest.mark.asyncio
    async def test_today_route_null_when_assigned_plan_not_today(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Yesterday Plan Depot",
            code=f"DP-YDAY-{suffix}",
            address_line_1="1 Yesterday Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"YDAY-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        past = datetime.now(UTC).date() - timedelta(days=4)
        plan = RoutePlan(service_date=past, depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-YDAY-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add(route)
        await db_session.flush()
        db_session.add(RouteStop(route_id=route.id, sequence=1, status="PENDING"))
        await db_session.commit()

        today_resp = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert today_resp.status_code == 200
        assert today_resp.json()["data"]["current_route"] is None

        assigned_resp = await client.get(f"{DRIVER_PROFILE}/routes/assigned", headers=headers)
        assert assigned_resp.status_code == 200
        assert assigned_resp.json()["data"]["table"]["total"] == 1
        assert assigned_resp.json()["data"]["table"]["items"][0]["route_id"] == route.id

    @pytest.mark.asyncio
    async def test_list_assigned_excludes_active(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Mix Status Depot",
            code=f"DP-MIX-{suffix}",
            address_line_1="1 Mix Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"MIX-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        today = datetime.now(UTC).date()
        plan_today = RoutePlan(service_date=today, depot_id=depot.id, status="READY")
        plan_y = RoutePlan(service_date=today - timedelta(days=2), depot_id=depot.id, status="READY")
        db_session.add_all([plan_today, plan_y])
        await db_session.flush()
        route_active = Route(
            plan_id=plan_today.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-ACT-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ACTIVE",
        )
        route_assigned = Route(
            plan_id=plan_y.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-ASG-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add_all([route_active, route_assigned])
        await db_session.flush()
        db_session.add_all(
            [
                RouteStop(route_id=route_active.id, sequence=1, status="PENDING"),
                RouteStop(route_id=route_assigned.id, sequence=1, status="PENDING"),
            ]
        )
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/assigned", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["table"]["total"] == 1
        assert resp.json()["data"]["table"]["items"][0]["route_id"] == route_assigned.id

    @pytest.mark.asyncio
    async def test_routes_board_upcoming_and_past_tabs(self, client: AsyncClient, user_factory, db_session) -> None:
        """GET /me/routes/board — All Routes screen: upcoming vs completed."""
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Board Depot",
            code=f"DP-BRD-{suffix}",
            address_line_1="1 Board Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"BRD-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        today = datetime.now(UTC).date()
        plan_today = RoutePlan(service_date=today, depot_id=depot.id, status="READY")
        plan_y = RoutePlan(service_date=today - timedelta(days=2), depot_id=depot.id, status="READY")
        db_session.add_all([plan_today, plan_y])
        await db_session.flush()
        route_active = Route(
            plan_id=plan_today.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-BRD-A-{suffix}",
            route_type="DELIVERY",
            total_stops=3,
            estimated_drive_time_min=120.0,
            actual_drive_time_min=None,
            total_distance_km=50.0,
            status="ACTIVE",
        )
        route_assigned = Route(
            plan_id=plan_y.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-BRD-B-{suffix}",
            route_type="DELIVERY",
            total_stops=2,
            status="ASSIGNED",
        )
        route_done = Route(
            plan_id=plan_y.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-BRD-DONE-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            actual_drive_time_min=60.0,
            total_distance_km=40.0,
            status="COMPLETED",
        )
        db_session.add_all([route_active, route_assigned, route_done])
        await db_session.flush()
        await db_session.commit()

        up = await client.get(f"{DRIVER_PROFILE}/routes/board", headers=headers, params={"tab": "upcoming"})
        assert up.status_code == 200
        up_ids = {row["route_id"] for row in up.json()["data"]["table"]["items"]}
        assert route_active.id in up_ids
        assert route_assigned.id in up_ids
        assert route_done.id not in up_ids
        active_row = next(r for r in up.json()["data"]["table"]["items"] if r["route_id"] == route_active.id)
        assert active_row["status"] == "ACTIVE"
        assert active_row["is_service_date_today"] is True

        past = await client.get(f"{DRIVER_PROFILE}/routes/board", headers=headers, params={"tab": "past"})
        assert past.status_code == 200
        assert past.json()["data"]["table"]["total"] >= 1
        past_ids = {row["route_id"] for row in past.json()["data"]["table"]["items"]}
        assert route_done.id in past_ids

        search = await client.get(
            f"{DRIVER_PROFILE}/routes/board",
            headers=headers,
            params={"tab": "upcoming", "search": f"BRD-A-{suffix}"},
        )
        assert search.status_code == 200
        assert len(search.json()["data"]["table"]["items"]) == 1

    @pytest.mark.asyncio
    async def test_routes_board_vehicle_reg_type_and_sort(self, client: AsyncClient, user_factory, db_session) -> None:
        """Board API: search by vehicle reg, type filter, sort by service_date."""
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Filter Depot",
            code=f"DP-FLT-{suffix}",
            address_line_1="1 Filter Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        v_del = Vehicle(registration_number=f"REG-DEL-{suffix}", depot_id=depot.id)
        v_pic = Vehicle(registration_number=f"REG-PIC-{suffix}", depot_id=depot.id)
        db_session.add_all([v_del, v_pic])
        await db_session.flush()
        today = datetime.now(UTC).date()
        p_early = RoutePlan(service_date=today - timedelta(days=1), depot_id=depot.id, status="READY")
        p_late = RoutePlan(service_date=today + timedelta(days=1), depot_id=depot.id, status="READY")
        db_session.add_all([p_early, p_late])
        await db_session.flush()
        r_del = Route(
            plan_id=p_late.id,
            driver_id=driver_id,
            vehicle_id=v_del.id,
            route_code=f"RT-FLT-D-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        r_pic = Route(
            plan_id=p_early.id,
            driver_id=driver_id,
            vehicle_id=v_pic.id,
            route_code=f"RT-FLT-P-{suffix}",
            route_type="PICKUP",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add_all([r_del, r_pic])
        await db_session.flush()
        await db_session.commit()

        only_del = await client.get(
            f"{DRIVER_PROFILE}/routes/board",
            headers=headers,
            params=[("tab", "upcoming"), ("type", "DELIVERY")],
        )
        assert only_del.status_code == 200
        ids = {row["route_id"] for row in only_del.json()["data"]["table"]["items"]}
        assert r_del.id in ids
        assert r_pic.id not in ids

        by_reg = await client.get(
            f"{DRIVER_PROFILE}/routes/board",
            headers=headers,
            params={"tab": "upcoming", "search": f"REG-PIC-{suffix}"},
        )
        assert by_reg.status_code == 200
        assert len(by_reg.json()["data"]["table"]["items"]) == 1
        assert by_reg.json()["data"]["table"]["items"][0]["route_id"] == r_pic.id

        newest = await client.get(
            f"{DRIVER_PROFILE}/routes/board",
            headers=headers,
            params={"tab": "upcoming", "sort": "newest_first", "size": 50},
        )
        assert newest.status_code == 200
        items = newest.json()["data"]["table"]["items"]
        idx_del = next(i for i, row in enumerate(items) if row["route_id"] == r_del.id)
        idx_pic = next(i for i, row in enumerate(items) if row["route_id"] == r_pic.id)
        assert idx_del < idx_pic

        oldest = await client.get(
            f"{DRIVER_PROFILE}/routes/board",
            headers=headers,
            params={"tab": "upcoming", "sort": "oldest_first", "size": 50},
        )
        assert oldest.status_code == 200
        items_o = oldest.json()["data"]["table"]["items"]
        idx_del_o = next(i for i, row in enumerate(items_o) if row["route_id"] == r_del.id)
        idx_pic_o = next(i for i, row in enumerate(items_o) if row["route_id"] == r_pic.id)
        assert idx_pic_o < idx_del_o

    @pytest.mark.asyncio
    async def test_routes_board_tolerates_legacy_published_plan_status(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Legacy Plan Depot",
            code=f"DP-LEG-{suffix}",
            address_line_1="1 Legacy Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"LEG-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()

        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="PUBLISHED")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-LEG-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add(route)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/board", headers=headers, params={"tab": "upcoming"})
        assert resp.status_code == 200
        ids = {row["route_id"] for row in resp.json()["data"]["table"]["items"]}
        assert route.id in ids

    @pytest.mark.asyncio
    async def test_today_route_service_date_query_override(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Fixed Date Depot",
            code=f"DP-FIX-{suffix}",
            address_line_1="1 Fixed Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"FIX-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        fixed = date(2030, 6, 15)
        plan = RoutePlan(service_date=fixed, depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-FIX-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add(route)
        await db_session.flush()
        db_session.add(RouteStop(route_id=route.id, sequence=1, status="PENDING"))
        await db_session.commit()

        resp = await client.get(
            f"{DRIVER_PROFILE}/routes/today?service_date=2030-06-15",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["current_route"]["route_id"] == route.id

    @pytest.mark.asyncio
    async def test_today_route_prefers_active_over_assigned_same_service_date(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot_a = Depot(
            name="Today Depot A",
            code=f"DP-CRTA-{suffix}",
            address_line_1="1 Current Street A",
            city="London",
            postcode="SW1A 1AA",
        )
        depot_b = Depot(
            name="Today Depot B",
            code=f"DP-CRTB-{suffix}",
            address_line_1="1 Current Street B",
            city="London",
            postcode="SW1A 1AB",
        )
        db_session.add_all([depot_a, depot_b])
        await db_session.flush()
        vehicle_a = Vehicle(registration_number=f"CRT-A-{suffix}", depot_id=depot_a.id)
        vehicle_b = Vehicle(registration_number=f"CRT-B-{suffix}", depot_id=depot_b.id)
        db_session.add_all([vehicle_a, vehicle_b])
        await db_session.flush()
        today = datetime.now(UTC).date()
        plan_a = RoutePlan(service_date=today, depot_id=depot_a.id, status="READY")
        plan_b = RoutePlan(service_date=today, depot_id=depot_b.id, status="READY")
        db_session.add_all([plan_a, plan_b])
        await db_session.flush()
        route_active = Route(
            plan_id=plan_a.id,
            driver_id=driver_id,
            vehicle_id=vehicle_a.id,
            route_code=f"RT-ACTIVE-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ACTIVE",
        )
        route_assigned = Route(
            plan_id=plan_b.id,
            driver_id=driver_id,
            vehicle_id=vehicle_b.id,
            route_code=f"RT-ASSIGNED-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add_all([route_active, route_assigned])
        await db_session.flush()
        db_session.add_all(
            [
                RouteStop(route_id=route_active.id, sequence=1, status="PENDING"),
                RouteStop(route_id=route_assigned.id, sequence=1, status="PENDING"),
            ]
        )
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["current_route"]["route_id"] == route_active.id

    @pytest.mark.asyncio
    async def test_list_assigned_routes_orders_by_service_date_asc(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Tiebreak Depot",
            code=f"DP-TIE-{suffix}",
            address_line_1="1 Tiebreak Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"TIE-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        today = datetime.now(UTC).date()
        plan_older = RoutePlan(service_date=today - timedelta(days=5), depot_id=depot.id, status="READY")
        plan_newer = RoutePlan(service_date=today - timedelta(days=1), depot_id=depot.id, status="READY")
        db_session.add_all([plan_older, plan_newer])
        await db_session.flush()
        route_old = Route(
            plan_id=plan_older.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-OLD-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        route_new = Route(
            plan_id=plan_newer.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-NEW-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="ASSIGNED",
        )
        db_session.add_all([route_old, route_new])
        await db_session.flush()
        db_session.add_all(
            [
                RouteStop(route_id=route_old.id, sequence=1, status="PENDING"),
                RouteStop(route_id=route_new.id, sequence=1, status="PENDING"),
            ]
        )
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/assigned?page=1&size=20", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["table"]["items"]
        assert len(items) == 2
        assert items[0]["route_id"] == route_old.id
        assert items[1]["route_id"] == route_new.id
        assert items[0]["status"] == "ASSIGNED"

    @pytest.mark.asyncio
    async def test_today_route_next_stop_null_when_all_stops_terminal(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Terminal Stops Depot",
            code=f"DP-TRM-{suffix}",
            address_line_1="1 Terminal Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"TRM-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-TRM-{suffix}",
            route_type="DELIVERY",
            total_stops=2,
            status="ASSIGNED",
        )
        db_session.add(route)
        await db_session.flush()
        db_session.add_all(
            [
                RouteStop(route_id=route.id, sequence=1, status="COMPLETED"),
                RouteStop(route_id=route.id, sequence=2, status="FAILED"),
            ]
        )
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert resp.status_code == 200
        cur = resp.json()["data"]["current_route"]
        assert cur["route_id"] == route.id
        assert cur["progress"] == {"completed_stops": 1, "total_stops": 2, "percent": 50}
        assert cur["next_stop"] is None

    @pytest.mark.asyncio
    async def test_today_route_null_when_only_completed_route(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Done Depot",
            code=f"DP-DONE-{suffix}",
            address_line_1="1 Done Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"DONE-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=datetime.now(UTC).date(), depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-DONE-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="COMPLETED",
        )
        db_session.add(route)
        await db_session.flush()
        db_session.add(RouteStop(route_id=route.id, sequence=1, status="COMPLETED"))
        await db_session.commit()

        resp = await client.get(f"{DRIVER_PROFILE}/routes/today", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["current_route"] is None

    @pytest.mark.asyncio
    async def test_average_speed_report_endpoint(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        headers, created = await _create_driver_and_headers(client, user_factory)
        driver_id = created["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Avg Speed Report Depot",
            code=f"DP-ASR-{suffix}",
            address_line_1="1 Report Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"ASR-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        service_day = datetime.now(UTC).date()
        plan = RoutePlan(service_date=service_day, depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        route = Route(
            plan_id=plan.id,
            driver_id=driver_id,
            vehicle_id=vehicle.id,
            route_code=f"RT-ASR-{suffix}",
            route_type="DELIVERY",
            total_stops=2,
            total_distance_km=40.0,
            actual_drive_time_min=60.0,
            status="COMPLETED",
        )
        db_session.add(route)
        await db_session.flush()
        db_session.add_all(
            [
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="LOCATION_PING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=2),
                    event_metadata={"speed_mph": 38.0},
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="LOCATION_PING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=1),
                    event_metadata={"speed_mph": 47.0},
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=1),
                    event_metadata={"speed_over_mph": 6.0},
                ),
            ]
        )
        await db_session.commit()

        resp = await client.get(
            f"{DRIVER_PROFILE}/reports/average-speed",
            headers=headers,
            params={
                "start_date": (service_day - timedelta(days=6)).isoformat(),
                "end_date": service_day.isoformat(),
                "page": 1,
                "size": 20,
            },
        )
        assert resp.status_code == 200

        period_resp = await client.get(
            f"{DRIVER_PROFILE}/reports/average-speed",
            headers=headers,
            params={"period": "last_month", "page": 1, "size": 20},
        )
        assert period_resp.status_code == 200, period_resp.text
        data = resp.json()["data"]["table"]
        assert data["total"] >= 1
        row = next(item for item in data["items"] if item["route_id"] == route.id)
        assert row["route_code"] == route.route_code
        assert row["average_speed_mph"] == 24.9
        assert row["speed_range_min_mph"] == 38.0
        assert row["speed_range_max_mph"] == 47.0
        assert row["severity"] == "MILD"

    @pytest.mark.asyncio
    async def test_safety_reports_above_70_and_sharp_brakes_date_range(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        """GET /me/reports/above-70-mph and /me/reports/sharp-brakes — window + isolation + pagination."""
        headers_a, created_a = await _create_driver_and_headers(client, user_factory)
        driver_a = created_a["id"]
        headers_b, created_b = await _create_driver_and_headers(client, user_factory)
        driver_b = created_b["id"]
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Safety Report Depot",
            code=f"DP-SAF-{suffix}",
            address_line_1="1 Safety Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"SAF-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        service_day = datetime.now(UTC).date()
        plan_in = RoutePlan(service_date=service_day, depot_id=depot.id, status="READY")
        plan_out = RoutePlan(service_date=service_day - timedelta(days=50), depot_id=depot.id, status="READY")
        db_session.add_all([plan_in, plan_out])
        await db_session.flush()
        route_in = Route(
            plan_id=plan_in.id,
            driver_id=driver_a,
            vehicle_id=vehicle.id,
            route_code=f"RT-SAF-IN-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="COMPLETED",
        )
        route_out = Route(
            plan_id=plan_out.id,
            driver_id=driver_a,
            vehicle_id=vehicle.id,
            route_code=f"RT-SAF-OUT-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="COMPLETED",
        )
        route_b = Route(
            plan_id=plan_in.id,
            driver_id=driver_b,
            vehicle_id=vehicle.id,
            route_code=f"RT-SAF-B-{suffix}",
            route_type="DELIVERY",
            total_stops=1,
            status="COMPLETED",
        )
        db_session.add_all([route_in, route_out, route_b])
        await db_session.flush()
        t0 = datetime.now(UTC)
        db_session.add_all(
            [
                RouteEvent(
                    route_id=route_in.id,
                    driver_id=driver_a,
                    event_type="SPEEDING",
                    occurred_at=t0 - timedelta(minutes=5),
                    event_metadata={"speed_mph": 81.0, "limit_mph": 70.0, "speed_over_mph": 11.0},
                ),
                RouteEvent(
                    route_id=route_in.id,
                    driver_id=driver_a,
                    event_type="SPEEDING",
                    occurred_at=t0 - timedelta(minutes=4),
                    event_metadata={"speed_mph": 82.0, "limit_mph": 70.0, "speed_over_mph": 12.0},
                ),
                RouteEvent(
                    route_id=route_in.id,
                    driver_id=driver_a,
                    event_type="SPEEDING",
                    occurred_at=t0 - timedelta(minutes=3),
                    event_metadata={"speed_mph": 83.0, "limit_mph": 70.0, "speed_over_mph": 13.0},
                ),
                RouteEvent(
                    route_id=route_in.id,
                    driver_id=driver_a,
                    event_type="SPEEDING",
                    occurred_at=t0 - timedelta(minutes=2),
                    event_metadata={"speed_mph": 65.0, "limit_mph": 40.0, "speed_over_mph": 25.0},
                ),
                RouteEvent(
                    route_id=route_in.id,
                    driver_id=driver_a,
                    event_type="HARSH_BRAKING",
                    occurred_at=t0 - timedelta(minutes=1),
                    event_metadata={"start_speed_mph": 48.0, "end_speed_mph": 11.0, "severity": "HIGH"},
                ),
                RouteEvent(
                    route_id=route_out.id,
                    driver_id=driver_a,
                    event_type="SPEEDING",
                    occurred_at=t0,
                    event_metadata={"speed_mph": 90.0, "limit_mph": 70.0},
                ),
                RouteEvent(
                    route_id=route_b.id,
                    driver_id=driver_b,
                    event_type="SPEEDING",
                    occurred_at=t0,
                    event_metadata={"speed_mph": 95.0, "limit_mph": 70.0},
                ),
                RouteEvent(
                    route_id=route_b.id,
                    driver_id=driver_b,
                    event_type="HARSH_BRAKING",
                    occurred_at=t0,
                    event_metadata={"start_speed_mph": 50.0, "end_speed_mph": 10.0, "severity": "MEDIUM"},
                ),
            ]
        )
        await db_session.commit()

        win = {"start_date": (service_day - timedelta(days=1)).isoformat(), "end_date": (service_day + timedelta(days=1)).isoformat()}

        speed_p1 = await client.get(f"{DRIVER_PROFILE}/reports/above-70-mph", headers=headers_a, params={**win, "page": 1, "size": 2})
        assert speed_p1.status_code == 200
        sp1 = speed_p1.json()["data"]["table"]
        assert sp1["total"] == 3
        assert len(sp1["items"]) == 2
        assert all(r["route_id"] == route_in.id for r in sp1["items"])
        assert all(float(r["speed_mph"]) > 70 for r in sp1["items"])

        speed_p2 = await client.get(f"{DRIVER_PROFILE}/reports/above-70-mph", headers=headers_a, params={**win, "page": 2, "size": 2})
        assert speed_p2.status_code == 200
        sp2 = speed_p2.json()["data"]["table"]
        assert sp2["total"] == 3
        assert len(sp2["items"]) == 1

        sharp = await client.get(f"{DRIVER_PROFILE}/reports/sharp-brakes", headers=headers_a, params={**win, "page": 1, "size": 20})
        assert sharp.status_code == 200
        sh = sharp.json()["data"]["table"]
        assert sh["total"] == 1
        assert sh["items"][0]["route_id"] == route_in.id
        assert sh["items"][0]["event_type"] == "HARSH_BRAKING"

        other_speed = await client.get(f"{DRIVER_PROFILE}/reports/above-70-mph", headers=headers_b, params={**win, "page": 1, "size": 20})
        assert other_speed.status_code == 200
        ot = other_speed.json()["data"]["table"]
        assert ot["total"] == 1
        assert ot["items"][0]["route_id"] == route_b.id
