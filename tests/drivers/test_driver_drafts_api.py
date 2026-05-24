"""API tests for driver draft save/submit flow."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.security import create_access_token
from app.common.enums import Job
from app.modules.drivers.models import Driver, DriverDraft
from app.modules.drivers.service import DriverService
from app.modules.drivers.enums import DriverAccountStatus, DriverCapacity
from app.modules.user.models import User

DRIVERS = "/v1/drivers"


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


class TestDriverDraftEndpoints:
    async def _create_driver_draft(
        self,
        *,
        client: AsyncClient,
        headers: dict[str, str],
    ) -> tuple[str, int]:
        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"city": "London"},
        )
        assert create.status_code == 201
        driver_body = create.json()["data"]["driver"]
        return driver_body["id"], int(driver_body["version"])

    def _submit_base_payload(self, *, expected_version: int, email: str = "draftsubmit@example.com") -> dict[str, str]:
        return {
            "email": email,
            "first_name": "Draft",
            "last_name": "Submit",
            "phone": "07123456789",
            "capacity[0]": DriverCapacity.VAN.value,
            "driver_type": "INTERNAL",
            "address_line1": "10 Test Street",
            "state": "England",
            "city": "London",
            "postcode": "SW1A 1AA",
            "max_stops": "30",
            "okay_with_layover": "true",
            "layover_cost_per_night": "85",
            "max_layover_nights": "5",
            "expected_version": str(expected_version),
        }

    @pytest.mark.asyncio
    async def test_create_driver_draft_minimal(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        payload = {
            "address_line1": "1 Draft Way",
        }
        resp = await client.post(f"{DRIVERS}/drafts", headers=headers, data=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["draft_id"].startswith("DF-")
        assert data["driver"]["account_status"] == "DRAFT"

        persisted = await db_session.get(Driver, data["driver"]["id"])
        assert persisted is not None
        assert persisted.account_status == "DRAFT"

    @pytest.mark.asyncio
    async def test_create_driver_draft_with_layover_fields_only(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={
                "okay_with_layover": "true",
                "layover_cost_per_night": "95.5",
                "max_layover_nights": "4",
            },
        )
        assert resp.status_code == 201, resp.text
        driver = resp.json()["data"]["driver"]
        assert driver["okay_with_layover"] is True
        assert driver["layover_cost_per_night"] == "95.50"
        assert driver["max_layover_nights"] == 4

    @pytest.mark.asyncio
    async def test_update_driver_draft_rejects_empty_body(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"city": "London"},
        )
        driver_id = create.json()["data"]["driver"]["id"]

        resp = await client.patch(f"{DRIVERS}/drafts/{driver_id}", headers=headers, data={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_driver_draft_by_driver_id_happy_path(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, _expected_version = await self._create_driver_draft(client=client, headers=headers)
        resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["draft_id"].startswith("DF-")
        assert body["driver"]["id"] == driver_id

    @pytest.mark.asyncio
    async def test_draft_responses_include_documents_list(
        self,
        client: AsyncClient,
        user_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Draft create/patch can include signed URLs; draft GET must not expose document URLs."""

        def _fake_get_file_url(_self, file_key, *, expiry_seconds=3600, content_type=None):
            return f"https://signed.test/{file_key}" if file_key else None

        monkeypatch.setattr(DriverService, "get_file_url", _fake_get_file_url)

        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"city": "London"},
        )
        assert create.status_code == 201
        driver = create.json()["data"]["driver"]
        assert driver["documents"] is not None
        assert driver["documents"]["items"] == []

        driver_id = driver["id"]
        meta = '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-06-01"}]'
        files = [("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf"))]
        patch = await client.patch(
            f"{DRIVERS}/drafts/{driver_id}",
            headers=headers,
            data={
                "expected_version": str(driver["version"]),
                "documents_metadata": meta,
            },
            files=files,
        )
        assert patch.status_code == 200, patch.text
        pdriver = patch.json()["data"]["driver"]
        assert pdriver["documents"] is not None
        items = pdriver["documents"]["items"]
        assert len(items) == 1
        assert items[0]["document_type"] == "DRIVING_LICENCE"
        assert items[0]["file_url"] is not None
        assert str(items[0]["file_url"]).startswith("https://signed.test/")

        get_resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)
        assert get_resp.status_code == 200
        gitems = get_resp.json()["data"]["driver"]["documents"]["items"]
        assert len(gitems) == 1
        assert gitems[0]["file_url"] is None

    @pytest.mark.asyncio
    async def test_get_driver_draft_hydrates_profile_from_jsonb_when_driver_columns_empty(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """GET draft must read form fields from driver_drafts.draft_data, not only drivers.*."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        payload = {
            "email": "jsonb_hydrate@example.com",
            "first_name": "Jsonb",
            "last_name": "Hydrate",
            "phone": "07900900900",
            "capacity[0]": DriverCapacity.TRUCK.value,
            "driver_type": "EXTERNAL",
            "address_line1": "42 JSONB Lane",
            "address_line2": "Flat B",
            "country": "UK",
            "state": "Cambs",
            "city": "Jsonbton",
            "postcode": "CB1 2AA",
            "license_number": "LIC-JSONB-001",
            "license_category": "C",
            "max_stops": "45",
            "notes": "Draft notes from JSONB path",
        }
        create = await client.post(f"{DRIVERS}/drafts", headers=headers, data=payload)
        assert create.status_code == 201, create.text
        driver_id = create.json()["data"]["driver"]["id"]

        draft_row = await db_session.scalar(select(DriverDraft).where(DriverDraft.driver_id == driver_id))
        assert draft_row is not None
        assert draft_row.draft_data.get("city") == "Jsonbton"

        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        driver.address_line1 = None
        driver.address_line2 = None
        driver.city = None
        driver.postcode = None
        driver.country = None
        driver.state = None
        driver.capacities = None
        driver.driver_type = None
        driver.license_number = None
        driver.license_category = None
        driver.max_stops = None
        driver.notes = None
        await db_session.commit()

        resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)
        assert resp.status_code == 200
        d = resp.json()["data"]["driver"]
        assert d["id"] == driver_id
        u = d["user"]
        assert u is not None
        assert u["email"] == "jsonb_hydrate@example.com"
        assert u["first_name"] == "Jsonb"
        assert u["last_name"] == "Hydrate"
        assert u["phone"] == "07900900900"
        assert d["city"] == "Jsonbton"
        assert d["address_line1"] == "42 JSONB Lane"
        assert d["address_line2"] == "Flat B"
        assert d["country"] == "UK"
        assert d["state"] == "Cambs"
        assert d["postcode"] == "CB1 2AA"
        assert d["driver_type"] == "EXTERNAL"
        assert d["capacities"] == [DriverCapacity.TRUCK.value]
        assert d["license_number"] == "LIC-JSONB-001"
        assert d["license_category"] == "C"
        assert d["max_stops"] == 45
        assert d["notes"] == "Draft notes from JSONB path"

    @pytest.mark.asyncio
    async def test_get_driver_draft_hydrates_layover_from_jsonb_when_driver_columns_stale(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """Unlinked drafts: layover in draft_data must win if drivers.* columns drift."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={
                "address_line1": "1 Layover Lane",
                "okay_with_layover": "true",
                "layover_cost_per_night": "88.25",
                "max_layover_nights": "6",
            },
        )
        assert create.status_code == 201, create.text
        driver_id = create.json()["data"]["driver"]["id"]

        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        driver.okay_with_layover = False
        driver.layover_cost_per_night = Decimal("0")
        driver.max_layover_nights = 0
        await db_session.commit()

        draft_resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)
        assert draft_resp.status_code == 200, draft_resp.text
        d = draft_resp.json()["data"]["driver"]
        assert d["okay_with_layover"] is True
        assert d["layover_cost_per_night"] == "88.25"
        assert d["max_layover_nights"] == 6

        cfg = await client.get(f"{DRIVERS}/{driver_id}/configuration", headers=headers)
        assert cfg.status_code == 200, cfg.text
        c = cfg.json()["data"]
        assert c["okay_with_layover"] is True
        assert c["layover_cost_per_night"] == "88.25"
        assert c["max_layover_nights"] == 6

    @pytest.mark.asyncio
    async def test_get_driver_draft_includes_name_and_phone_from_jsonb_without_email(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Detail `user` must not require email; name/phone from draft_data alone must hydrate."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={
                "first_name": "NoEmail",
                "last_name": "Yet",
                "phone": "07222222222",
                "city": "Leeds",
            },
        )
        assert create.status_code == 201, create.text
        driver_id = create.json()["data"]["driver"]["id"]

        draft_resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)
        assert draft_resp.status_code == 200, draft_resp.text
        u_draft = draft_resp.json()["data"]["driver"]["user"]
        assert u_draft is not None
        assert u_draft.get("email") in (None, "")
        assert u_draft["first_name"] == "NoEmail"
        assert u_draft["last_name"] == "Yet"
        assert u_draft["phone"] == "07222222222"

        detail_resp = await client.get(f"{DRIVERS}/{driver_id}", headers=headers)
        assert detail_resp.status_code == 200, detail_resp.text
        u_detail = detail_resp.json()["data"]["user"]
        assert u_detail is not None
        assert u_detail.get("email") in (None, "")
        assert u_detail["first_name"] == "NoEmail"
        assert u_detail["last_name"] == "Yet"
        assert u_detail["phone"] == "07222222222"

    @pytest.mark.asyncio
    async def test_get_driver_draft_by_driver_id_after_submit_still_works(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)
        payload = self._submit_base_payload(expected_version=expected_version, email="draftsubmit_getdraft@example.com")

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock) as _:
            submit = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
        assert submit.status_code == 200

        resp = await client.get(f"{DRIVERS}/drafts/{driver_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["draft_id"].startswith("DF-")
        assert body["driver"]["id"] == driver_id

    @pytest.mark.asyncio
    async def test_submit_driver_draft_happy_path(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"city": "London"},
        )
        driver_body = create.json()["data"]["driver"]
        driver_id = driver_body["id"]
        expected_version = driver_body["version"]

        submit_data = {
            "email": "draftsubmit@example.com",
            "first_name": "Draft",
            "last_name": "Submit",
            "phone": "07123456789",
            "capacity[0]": "VAN",
            "driver_type": "INTERNAL",
            "address_line1": "10 Test Street",
            "state": "England",
            "city": "London",
            "postcode": "SW1A 1AA",
            "max_stops": "30",
            "okay_with_layover": True,
            "layover_cost_per_night": "72.50",
            "max_layover_nights": 4,
            "expected_version": int(expected_version),
        }

        captured_links: list[str] = []

        async def capture_enqueue(task_name: str, *args: object, **kwargs: object):
            # enqueue(Job.SEND_DRIVER_ACTIVATION_EMAIL, invite_id, email, first_name, link, ...)
            if task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL and len(args) >= 4:
                captured_links.append(str(args[3]))
            return None

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
            resp = await client.post(
                f"{DRIVERS}/{driver_id}/submit",
                headers=headers,
                data=submit_data,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["draft_id"].startswith("DF-")
        assert data["driver"]["account_status"] == "PENDING_ACTIVATION"
        assert data["driver"]["okay_with_layover"] is True
        assert data["driver"]["layover_cost_per_night"] == "72.50"
        assert data["driver"]["max_layover_nights"] == 4

        row_before_login = await db_session.get(Driver, driver_id)
        assert row_before_login is not None
        assert row_before_login.okay_with_layover is True
        assert row_before_login.layover_cost_per_night == Decimal("72.50")
        assert row_before_login.max_layover_nights == 4

        assert captured_links, "expected activation email to be enqueued"
        token = parse_qs(urlparse(captured_links[0]).query)["token"][0]
        chosen = "DraftSubmitPass9!"
        sp = await client.post(
            "/v1/auth/driver-activation/set-password",
            headers={"X-Invite-Token": token},
            json={"password": chosen},
        )
        assert sp.status_code == 201
        submitted_driver_id = data["driver"]["id"]

        # First login should activate the driver.
        login_resp = await client.post(
            "/v1/auth/login",
            json={"email": submit_data["email"], "password": chosen},
            headers={"X-Client-Type": "DRIVER"},
        )
        assert login_resp.status_code == 200
        login_body = login_resp.json()
        assert login_body["data"]["requires_password_change"] is False

        driver_row_after_login = await db_session.get(Driver, submitted_driver_id)
        assert driver_row_after_login is not None
        assert driver_row_after_login.account_status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_empty_email(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        _ = db_session  # ensure fixture is initialized for isolation
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version, email="")
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_missing_expected_version(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)
        payload = self._submit_base_payload(expected_version=expected_version)
        payload.pop("expected_version")

        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_blank_first_name(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        _ = db_session
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)
        payload = self._submit_base_payload(expected_version=expected_version)
        payload["first_name"] = "   "

        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_blank_capacity_0(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)
        payload = self._submit_base_payload(expected_version=expected_version)
        payload["capacity[0]"] = ""

        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_invalid_driver_type(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        payload["driver_type"] = "NOT_A_REAL_TYPE"
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_missing_capacity_0(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        payload.pop("capacity[0]")
        payload["capacity[1]"] = DriverCapacity.VAN.value
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_invalid_capacity_value(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        payload["capacity[0]"] = "ALIEN_CAPACITY"
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_documents_metadata_without_documents(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        payload["documents_metadata"] = '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]'

        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_documents_without_documents_metadata(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        files = [
            ("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf")),
        ]
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload, files=files)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_documents_metadata_with_expired_date(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        payload = self._submit_base_payload(expected_version=expected_version)
        expired = (date.today() - timedelta(days=1)).isoformat()
        payload["documents_metadata"] = f'[{{"document_type":"DRIVING_LICENCE","expiry_date":"{expired}"}}]'

        files = [
            ("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf")),
        ]
        resp = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload, files=files)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_driver_draft_idempotent_when_already_submitted(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver_id, expected_version = await self._create_driver_draft(client=client, headers=headers)

        email = "draftsubmit_idempotent@example.com"
        payload = self._submit_base_payload(expected_version=expected_version, email=email)

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock) as _:
            resp1 = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)
            resp2 = await client.post(f"{DRIVERS}/{driver_id}/submit", headers=headers, data=payload)

        assert resp1.status_code == 200, resp1.text
        assert resp2.status_code == 200, resp2.text
        assert "already submitted" in resp2.json()["message"]

        user_count = await db_session.scalar(select(func.count()).where(User.email == email))
        assert user_count == 1

    @pytest.mark.asyncio
    async def test_submit_driver_draft_rejects_when_not_in_draft_state(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        driver = Driver(account_status=DriverAccountStatus.ACTIVE)
        db_session.add(driver)
        await db_session.flush()
        await db_session.refresh(driver)

        payload = self._submit_base_payload(expected_version=0)
        resp = await client.post(f"{DRIVERS}/{driver.id}/submit", headers=headers, data=payload)

        assert resp.status_code == 422

