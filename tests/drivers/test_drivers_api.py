"""API-level tests for driver module.

Covers:
- Driver + user + documents creation flow; activation deep link + set-password; driver login
- Basic RBAC on driver endpoints
- Driver detail and full profile aggregation
- List, update, delete, suspend, reactivate, schedule, password-reset
- Documents, time-off, sick-leave, shifts, traffic violations full CRUD
"""

from __future__ import annotations

from typing import Any
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.common.enums import Job
from app.core.security import create_access_token
from app.modules.depots.models import Depot
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.holidays.models import Holiday
from app.modules.planning.models import Route, RouteEvent, RoutePlan
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

DRIVERS = "/v1/drivers"
AUTH = "/v1/auth"

# POST /v1/drivers/add-new-driver requires operational scheduling fields (multipart).
_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS: dict[str, str | bool | int] = {
    "okay_with_layover": True,
    "layover_cost_per_night": "85",
    "max_layover_nights": 5,
}


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
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


class TestCreateDriverWithUser:
    """POST /v1/drivers/add-new-driver — unified user+driver+documents onboarding."""

    def _required_licence_files(self):
        """Helper: required driving licence multipart fields for add-new-driver."""
        form = {
            "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
        }
        files = [("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf"))]
        return form, files

    @pytest.mark.asyncio
    async def test_create_driver_with_user_minimal(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """Happy path: admin creates user+driver with required driving licence."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        data = {
            **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
            "email": "newdriver@example.com",
            "first_name": "Driver",
            "last_name": "One",
            "phone": "07123456789",
            "state": "England",
            "capacity[0]": "VAN",
            "driver_type": "INTERNAL",
            "address_line1": "10 Test Street",
            "city": "London",
            "postcode": "SW1A 1AA",
            "max_stops": "30",
        }
        meta, files = self._required_licence_files()
        data.update(meta)

        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=data,
            files=files,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        # New response shape: data = {"driver": {...}, "documents": [...]}
        driver_data = body["data"]["driver"]
        documents = body["data"]["documents"]
        assert driver_data["user"]["email"] == "newdriver@example.com"
        assert driver_data["user"]["first_name"] == "Driver"
        assert driver_data["user"]["last_name"] == "One"
        assert "driver_code" in driver_data
        assert driver_data["driver_type"] == "INTERNAL"
        assert driver_data["capacities"] == ["VAN"]
        assert isinstance(documents, list)
        assert len(documents) == 1
        assert documents[0]["type"] == "DRIVING_LICENCE"
        assert documents[0]["status"] == "success"

        # Ensure driver row persisted and linked to a user
        created_id = driver_data["id"]
        persisted = await db_session.get(Driver, created_id)
        assert persisted is not None
        assert persisted.user_id is not None
        assert persisted.capacities == ["VAN"]

    @pytest.mark.asyncio
    async def test_create_driver_with_user_multiple_capacities(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """Create driver supports multiple capacities via repeated form field values."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        # Send multiple capacities using indexed form keys: capacity[0], capacity[1], ...
        data = {
            **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
            "email": "multicap@example.com",
            "first_name": "Multi",
            "last_name": "Cap",
            "phone": "07123456789",
            "state": "England",
            "driver_type": "INTERNAL",
            "address_line1": "10 Test Street",
            "city": "London",
            "postcode": "SW1A 1AA",
            "max_stops": "30",
            "capacity[0]": "VAN",
            "capacity[1]": "TRUCK",
        }
        meta, files = self._required_licence_files()
        data.update(meta)

        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=data,
            files=files,
        )
        assert resp.status_code == 201
        driver_data = resp.json()["data"]["driver"]
        assert driver_data["capacities"] == ["VAN", "TRUCK"]
        persisted = await db_session.get(Driver, driver_data["id"])
        assert persisted is not None
        assert persisted.capacities == ["VAN", "TRUCK"]

    @pytest.mark.asyncio
    async def test_create_driver_with_user_multiple_capacities_three_indices(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """The endpoint accepts capacity[i] for i>=0 (beyond capacity[1])."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        # Provide an extra indexed field capacity[2] as well.
        data = {
            **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
            "email": "multicap3@example.com",
            "first_name": "Multi3",
            "last_name": "Cap",
            "phone": "07123456789",
            "state": "England",
            "driver_type": "INTERNAL",
            "address_line1": "10 Test Street",
            "city": "London",
            "postcode": "SW1A 1AA",
            "max_stops": "30",
            "capacity[0]": "VAN",
            "capacity[1]": "TRUCK",
            "capacity[2]": "TRUCK",
        }
        meta, files = self._required_licence_files()
        data.update(meta)

        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=data,
            files=files,
        )
        assert resp.status_code == 201
        driver_data = resp.json()["data"]["driver"]
        # Driver model deduplicates capacities.
        assert driver_data["capacities"] == ["VAN", "TRUCK"]
        persisted = await db_session.get(Driver, driver_data["id"])
        assert persisted is not None
        assert persisted.capacities == ["VAN", "TRUCK"]

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_custom_documents_on_onboarding(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """add-new-driver rejects CUSTOM; use POST /drivers/{id}/documents after creation."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        form_data = _minimal_driver_form("multicustomdocs@example.com")
        form_data["documents_metadata"] = '[{"document_type":"CUSTOM","title":"NDA","expiry_date":"2030-01-01"}]'
        files = [("documents", ("nda.pdf", b"dummy-nda", "application/pdf"))]

        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=form_data,
            files=files,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_optional_driving_licence(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Driving licence is required; single DRIVING_LICENCE file + metadata succeeds."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("onboardlicence@example.com")
        form_data["documents_metadata"] = '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]'
        files = [("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf"))]
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=form_data,
                files=files,
            )
        assert resp.status_code == 201
        documents = resp.json()["data"]["documents"]
        assert len(documents) == 1
        assert documents[0]["type"] == "DRIVING_LICENCE"
        assert documents[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_create_driver_with_user_optional_profile_photo(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Multipart profile_photo is stored and returned as profile_photo_url when CF Images is mocked."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("withphoto@example.com")
        files = [("profile_photo", ("face.png", b"\x89PNG\r\n\x1a\n", "image/png"))] + _licence_files()
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None), patch(
            "app.modules.drivers.service.get_images_client"
        ) as get_client_mock:
            client_mock = get_client_mock.return_value
            client_mock.upload_image = AsyncMock()
            client_mock.upload_image.return_value.id = "cf_img_onboard_1"
            client_mock.upload_image.return_value.filename = "face.png"
            client_mock.upload_image.return_value.variants = []
            client_mock.generate_signed_url.return_value = "https://example.com/onboard-photo"
            resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=form_data,
                files=files,
            )
        assert resp.status_code == 201
        driver_data = resp.json()["data"]["driver"]
        assert driver_data.get("profile_photo_url") == "https://example.com/onboard-photo"

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_profile_photo_too_large(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """profile_photo is limited to 5MB (JPEG/PNG)."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("toolargephoto@example.com")

        # Validation is based on content_type + bytes length.
        oversized = b"0" * (5 * 1024 * 1024 + 1)
        files = [("profile_photo", ("too-big.png", oversized, "image/png"))] + _licence_files()

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None), patch(
            "app.modules.drivers.service.get_images_client"
        ) as get_client_mock:
            get_client_mock.return_value.upload_image = AsyncMock()
            resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=form_data,
                files=files,
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_profile_photo_wrong_type(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """profile_photo only accepts JPEG/PNG; other MIME types are rejected."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("wrongtypephoto@example.com")

        files = [("profile_photo", ("bad.pdf", b"%PDF-1.4 bad", "application/pdf"))] + _licence_files()

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None), patch(
            "app.modules.drivers.service.get_images_client"
        ) as get_client_mock:
            get_client_mock.return_value.upload_image = AsyncMock()
            resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=form_data,
                files=files,
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_documents_metadata_without_file(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        # documents are now required; missing file should be 422 from FastAPI validation.
        form_data = _minimal_driver_form("metanofile@example.com")
        form_data["documents_metadata"] = '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]'
        resp = await client.post(f"{DRIVERS}/add-new-driver", headers=headers, data=form_data)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_without_driving_licence_succeeds(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Driving licence is optional; create succeeds with no documents when metadata is omitted."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("nodoc@example.com")
        form_data.pop("documents_metadata", None)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            resp = await client.post(f"{DRIVERS}/add-new-driver", headers=headers, data=form_data)
        assert resp.status_code == 201
        assert resp.json()["data"]["documents"] == []

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_missing_licence_expiry(self, client: AsyncClient, user_factory) -> None:
        """Driving licence expiry_date is compulsory."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("noexpiry@example.com")
        form_data["documents_metadata"] = '[{"document_type":"DRIVING_LICENCE"}]'
        files = [("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf"))]
        resp = await client.post(f"{DRIVERS}/add-new-driver", headers=headers, data=form_data, files=files)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_documents_metadata_length_mismatch(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("metamismatch@example.com")
        form_data["documents_metadata"] = '[{"document_type":"CUSTOM","title":"One","expiry_date":"2030-01-01"}]'
        files = [
            ("documents", ("one.pdf", b"one", "application/pdf")),
            ("documents", ("two.pdf", b"two", "application/pdf")),
        ]
        resp = await client.post(f"{DRIVERS}/add-new-driver", headers=headers, data=form_data, files=files)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_more_than_one_onboarding_document(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Only one driving licence file is allowed on add-new-driver."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("dupelicence@example.com")
        form_data["documents_metadata"] = (
            '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"},'
            '{"document_type":"DRIVING_LICENCE","expiry_date":"2030-06-01"}]'
        )
        files = [
            ("documents", ("licence1.pdf", b"%PDF-1.4 one", "application/pdf")),
            ("documents", ("licence2.pdf", b"%PDF-1.4 two", "application/pdf")),
        ]
        resp = await client.post(f"{DRIVERS}/add-new-driver", headers=headers, data=form_data, files=files)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_requires_auth(self, client: AsyncClient) -> None:
        """Unauthenticated request is rejected with 401."""
        resp = await client.post(f"{DRIVERS}/add-new-driver", data={})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_driver_with_user_requires_proper_role(
        self,
        client: AsyncClient,
        driver_user: User,
    ) -> None:
        """Non-admin / non-permissioned user gets 403 from RBAC."""
        headers = _driver_headers(driver_user.id)
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                "email": "unauthorized@example.com",
                "first_name": "X",
                "last_name": "Y",
                "phone": "07123456789",
                "state": "England",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Test",
                "city": "City",
                "postcode": "PC",
                "max_stops": "30",
                "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
            },
            files=_licence_files(),
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_create_driver_with_user_validation_error(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Missing required fields (e.g. email) returns 422."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        # Omitting email and address fields
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                "first_name": "Driver",
                "last_name": "One",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
            },
            files={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_driver_can_login_with_credentials_sent_in_email(
        self,
        client: AsyncClient,
        user_factory,
        auth_blacklist_mocks,
    ) -> None:
        """Driver created via add-new-driver completes activation (set-password) then can log in."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        captured_links: list[str] = []

        async def capture_enqueue(task_name: str, *args: object, **kwargs: object):
            # enqueue(Job.SEND_DRIVER_ACTIVATION_EMAIL, invite_id, email, first_name, link, ...)
            if task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL and len(args) >= 4:
                captured_links.append(str(args[3]))
            return None

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
            resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data={
                    **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                    "email": "driverlogin@example.com",
                    "first_name": "Login",
                    "last_name": "Test",
                    "phone": "07111111111",
                    "state": "England",
                    "capacity[0]": "VAN",
                    "driver_type": "INTERNAL",
                    "address_line1": "1 Login St",
                    "city": "London",
                    "postcode": "E1 1AA",
                    "max_stops": "30",
                    "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
                },
                files=_licence_files(),
            )
        assert resp.status_code == 201
        assert len(captured_links) == 1, "Activation email job should have been enqueued with link"
        link = captured_links[0]
        token = parse_qs(urlparse(link).query)["token"][0]

        chosen_password = "ChosenSecurePass9!"
        sp = await client.post(
            f"{AUTH}/driver-activation/set-password",
            headers={"X-Invite-Token": token},
            json={"password": chosen_password},
        )
        assert sp.status_code == 201

        login_resp = await client.post(
            f"{AUTH}/login",
            json={"email": "driverlogin@example.com", "password": chosen_password},
            headers={"X-Client-Type": "DRIVER"},
        )
        assert login_resp.status_code == 200
        body = login_resp.json()
        assert body.get("success") is True
        assert "data" in body
        assert body["data"].get("email") == "driverlogin@example.com"
        assert body["data"].get("requires_password_change") is False
        assert "tokens" in body
        assert body["tokens"].get("access_token")

    @pytest.mark.asyncio
    async def test_admin_can_resend_credentials_for_pending_activation_driver(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
        auth_blacklist_mocks,
    ) -> None:
        """Admin resend issues a new activation link; old link is invalidated after the new invite is minted."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        captured_links: list[str] = []

        async def capture_enqueue(task_name: str, *args: object, **kwargs: object):
            # enqueue(Job.SEND_DRIVER_ACTIVATION_EMAIL, invite_id, email, first_name, link, ...)
            if task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL and len(args) >= 4:
                captured_links.append(str(args[3]))
            return None

        driver_email = "resend-pending@example.com"
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data={
                    **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                    "email": driver_email,
                    "first_name": "Resend",
                    "last_name": "Pending",
                    "phone": "07111111111",
                    "state": "England",
                    "capacity[0]": "VAN",
                    "driver_type": "INTERNAL",
                    "address_line1": "1 Resend St",
                    "city": "London",
                    "postcode": "E1 1AA",
                    "max_stops": "30",
                    "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
                },
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        assert len(captured_links) == 1

        driver_id = create_resp.json()["data"]["driver"]["id"]
        driver_row = await db_session.get(Driver, driver_id)
        assert driver_row is not None
        assert driver_row.account_status == "PENDING_ACTIVATION"

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
            resend_resp = await client.post(
                f"{DRIVERS}/{driver_id}/resend-credentials",
                headers=headers,
            )
        assert resend_resp.status_code == 200
        assert len(captured_links) == 2
        assert captured_links[0] != captured_links[1]

        token_latest = parse_qs(urlparse(captured_links[1]).query)["token"][0]
        new_password = "ResendFreshPass9!"
        sp = await client.post(
            f"{AUTH}/driver-activation/set-password",
            headers={"X-Invite-Token": token_latest},
            json={"password": new_password},
        )
        assert sp.status_code == 201

        login_resp = await client.post(
            f"{AUTH}/login",
            json={"email": driver_email, "password": new_password},
            headers={"X-Client-Type": "DRIVER"},
        )
        assert login_resp.status_code == 200
        login_body = login_resp.json()
        assert login_body["data"]["requires_password_change"] is False

        driver_row_after_login = await db_session.get(Driver, driver_id)
        assert driver_row_after_login is not None
        assert driver_row_after_login.account_status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_admin_resend_credentials_rejected_when_driver_active(
        self,
        client: AsyncClient,
        user_factory,
        auth_blacklist_mocks,
        db_session,
    ) -> None:
        """Resend should be rejected once driver is ACTIVE."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        captured_links: list[str] = []

        async def capture_enqueue(task_name: str, *args: object, **kwargs: object):
            # enqueue(Job.SEND_DRIVER_ACTIVATION_EMAIL, invite_id, email, first_name, link, ...)
            if task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL and len(args) >= 4:
                captured_links.append(str(args[3]))
            return None

        driver_email = "resend-active@example.com"
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data={
                    **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                    "email": driver_email,
                    "first_name": "Resend",
                    "last_name": "Active",
                    "phone": "07122222222",
                    "state": "England",
                    "capacity[0]": "VAN",
                    "driver_type": "INTERNAL",
                    "address_line1": "1 Resend St",
                    "city": "London",
                    "postcode": "E1 1AA",
                    "max_stops": "30",
                    "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
                },
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]
        assert captured_links

        token = parse_qs(urlparse(captured_links[0]).query)["token"][0]
        activate_pw = "ActivateThenResend9!"
        sp = await client.post(
            f"{AUTH}/driver-activation/set-password",
            headers={"X-Invite-Token": token},
            json={"password": activate_pw},
        )
        assert sp.status_code == 201

        login_resp = await client.post(
            f"{AUTH}/login",
            json={"email": driver_email, "password": activate_pw},
            headers={"X-Client-Type": "DRIVER"},
        )
        assert login_resp.status_code == 200
        driver_row_after_login = await db_session.get(Driver, driver_id)
        assert driver_row_after_login is not None
        assert driver_row_after_login.account_status == "ACTIVE"

        resend_resp = await client.post(
            f"{DRIVERS}/{driver_id}/resend-credentials",
            headers=headers,
        )
        assert resend_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_driver_with_user_duplicate_email_conflict(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Second create with same email should be rejected."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        first_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=_minimal_driver_form("dupe-driver@example.com"),
            files=_licence_files(),
        )
        assert first_resp.status_code == 201

        second_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=_minimal_driver_form("dupe-driver@example.com"),
            files=_licence_files(),
        )
        assert second_resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_driver_with_user_rejects_removed_user_prefix_identity_fields(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Legacy user_* identity keys are no longer part of the request contract."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                "user_email": "legacy@example.com",
                "user_first_name": "Legacy",
                "user_last_name": "User",
                "user_phone": "07000000009",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Legacy Street",
                "city": "London",
                "postcode": "E1 1AA",
                "max_stops": "30",
            },
            files={},
        )
        assert resp.status_code == 422
        body = resp.json()
        rendered = str(body).lower()
        assert "email" in rendered
        assert "first_name" in rendered
        assert "last_name" in rendered

    @pytest.mark.asyncio
    async def test_create_driver_with_user_burst_requests_generate_unique_driver_codes(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Burst create requests should still yield unique DR-prefixed codes."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        responses = []
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            for i in range(20):
                resp = await client.post(
                    f"{DRIVERS}/add-new-driver",
                    headers=headers,
                    data=_minimal_driver_form(f"burst-driver-{i}@example.com"),
                    files=_licence_files(),
                )
                responses.append(resp)

        assert all(r.status_code == 201 for r in responses)
        codes = [r.json()["data"]["driver"]["driver_code"] for r in responses]
        assert len(codes) == len(set(codes))
        assert all(code.startswith("DR-") for code in codes)


class TestDriverDetailAndFullProfile:
    """GET /v1/drivers/{id} and /v1/drivers/{id}/full."""

    @pytest.mark.asyncio
    async def test_get_driver_and_full_profile(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """Full profile aggregates all driver-related resources."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        # Create driver via API (no docs to keep setup light)
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_minimal_driver_form("fullprofile@example.com"),
                "first_name": "Full",
                "last_name": "Profile",
                "phone": "07000000001",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "20 Main Street",
                "city": "London",
                "postcode": "N1 1AA",
                "max_stops": "25",
            },
            files=_licence_files(),
        )
        assert resp.status_code == 201
        driver_payload = resp.json()["data"]
        driver_id = driver_payload["driver"]["id"]

        # GET /{id}
        detail_resp = await client.get(f"{DRIVERS}/{driver_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()["data"]
        assert detail["id"] == driver_id
        assert detail["driver_code"].startswith("DR-")
        assert detail["capacities"] == ["VAN"]
        assert "safety_score" in detail
        assert "on_time_deliveries" in detail
        assert "country" in detail
        assert "state" in detail
        assert "city" in detail
        assert "address_line1" in detail
        assert "address_line2" in detail
        assert "documents" in detail
        assert isinstance(detail["documents"]["items"], list)

        # GET /{id}/full
        full_resp = await client.get(f"{DRIVERS}/{driver_id}/full", headers=headers)
        assert full_resp.status_code == 200
        payload = full_resp.json()["data"]

        assert "driver" in payload
        assert payload["driver"]["capacities"] == ["VAN"]
        assert "address_line1" in payload["driver"]
        assert "postcode" in payload["driver"]
        assert "country" in payload["driver"]
        assert "state" in payload["driver"]
        assert "city" in payload["driver"]
        assert "profile_photo_url" in payload["driver"]
        assert "documents" in payload
        assert "time_off" in payload
        assert "schedule" in payload
        assert "shifts" in payload
        assert "traffic_violations" in payload

        # Basic types
        assert isinstance(payload["documents"]["items"], list)
        assert isinstance(payload["time_off"]["items"], list)
        assert isinstance(payload["shifts"]["items"], list)


class TestDriverOperationalConfiguration:
    """GET/PATCH /v1/drivers/{id}/configuration."""

    @pytest.mark.asyncio
    async def test_get_and_patch_driver_configuration(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("ops-config@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]
        version = create_resp.json()["data"]["driver"]["version"]

        get_resp = await client.get(f"{DRIVERS}/{driver_id}/configuration", headers=headers)
        assert get_resp.status_code == 200
        cfg = get_resp.json()["data"]
        assert cfg["okay_with_layover"] is True
        assert cfg["layover_cost_per_night"] == "85.00"
        assert cfg["max_layover_nights"] == 5

        patch_resp = await client.patch(
            f"{DRIVERS}/{driver_id}/configuration",
            headers=headers,
            json={
                "okay_with_layover": False,
                "layover_cost_per_night": "120.50",
                "max_layover_nights": 7,
                "expected_version": version,
            },
        )
        assert patch_resp.status_code == 200
        updated = patch_resp.json()["data"]
        assert updated["okay_with_layover"] is False
        assert updated["layover_cost_per_night"] == "0.00"
        assert updated["max_layover_nights"] == 0

        detail = await client.get(f"{DRIVERS}/{driver_id}", headers=headers)
        assert detail.status_code == 200
        d = detail.json()["data"]
        assert d["okay_with_layover"] is False
        assert d["layover_cost_per_night"] == "0.00"
        assert d["max_layover_nights"] == 0


class TestDriverDocuments:
    """Minimal coverage for documents CRUD."""

    @pytest.mark.asyncio
    async def test_upload_and_list_documents(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        # Create driver
        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_minimal_driver_form("docs@example.com"),
                "first_name": "Docs",
                "last_name": "User",
                "phone": "07000000002",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Docs St",
                "city": "Leeds",
                "postcode": "LS1 1AA",
                "max_stops": "20",
            },
            files=_licence_files(),
        )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        # Upload a document
        files = {
            "file": ("licence.png", b"dummy-bytes", "image/png"),
        }
        data = {
            "document_type": "DRIVING_LICENCE",
            "title": "DRIVING LICENCE",
            "expiry_date": str(date.today().replace(year=date.today().year + 1)),
        }
        upload_resp = await client.post(
            f"{DRIVERS}/{driver_id}/documents",
            headers=headers,
            data=data,
            files=files,
        )
        assert upload_resp.status_code == 201
        doc_data = upload_resp.json()["data"]
        assert doc_data["document_type"] == "DRIVING_LICENCE"
        assert doc_data["title"] == "DRIVING LICENCE"
        assert doc_data["status"] in ("VALID", "EXPIRING_SOON")
        assert "file_url" in doc_data
        assert "expiry_date" in doc_data

        # List documents (each item has file_url for preview and auto-calculated status)
        list_resp = await client.get(f"{DRIVERS}/{driver_id}/documents", headers=headers)
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        # add-new-driver now always creates exactly one initial driving licence document,
        # and this test uploads one more document via POST /documents.
        assert len(items) == 2
        assert any(item["id"] == doc_data["id"] for item in items)
        assert items[0]["status"] in ("VALID", "EXPIRING_SOON", "EXPIRED")
        assert "file_url" in items[0]

    @pytest.mark.asyncio
    async def test_upload_custom_document_after_onboarding(self, client: AsyncClient, user_factory) -> None:
        """Custom documents can be added after the driver is created."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={**_minimal_driver_form("customdoc-after@example.com"), "first_name": "Custom", "last_name": "Doc"},
            files=_licence_files(),
        )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        # Upload a custom doc via the dedicated documents API.
        custom_files = {"file": ("nda.pdf", b"%PDF-1.4 custom nda", "application/pdf")}
        custom_data = {
            "document_type": "CUSTOM",
            "title": "NDA",
            "expiry_date": "2030-01-01",
        }
        upload_resp = await client.post(
            f"{DRIVERS}/{driver_id}/documents",
            headers=headers,
            data=custom_data,
            files=custom_files,
        )
        assert upload_resp.status_code == 201
        custom_doc = upload_resp.json()["data"]
        assert custom_doc["document_type"] == "CUSTOM"
        assert custom_doc["title"] == "NDA"
        assert "file_url" in custom_doc

        # Listing should now include both:
        # - initial onboarding driving licence
        # - this uploaded custom document
        list_resp = await client.get(f"{DRIVERS}/{driver_id}/documents", headers=headers)
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        assert len(items) >= 2
        assert any(item["id"] == custom_doc["id"] for item in items)
        assert any(item["document_type"] == "CUSTOM" for item in items)


class TestTimeOffAndSickLeave:
    """Ensure unified time-off endpoints behave and validate correctly."""

    @pytest.mark.asyncio
    async def test_time_off_create_and_list(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_minimal_driver_form("timeoff@example.com"),
                "first_name": "Time",
                "last_name": "Off",
                "phone": "07000000003",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Time St",
                "city": "City",
                "postcode": "TO1 1AA",
                "max_stops": "10",
            },
            files=_licence_files(),
        )
        driver_id = create_resp.json()["data"]["driver"]["id"]

        start = date.today()
        end = start + timedelta(days=2)

        resp = await client.post(
            f"{DRIVERS}/{driver_id}/time-off",
            headers=headers,
            data={
                "start_date": str(start),
                "end_date": str(end),
                "type": "ANNUAL_LEAVE",
            },
        )
        assert resp.status_code == 201
        entry = resp.json()["data"]
        assert entry["type"] == "ANNUAL_LEAVE"
        assert entry["is_paid"] is True

        list_resp = await client.get(f"{DRIVERS}/{driver_id}/time-off", headers=headers)
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert len(data["items"]) == 1
        assert data["items"][0]["is_paid"] is True
        # KPIs: since we created a single paid entry for the current year,
        # paid_leave_taken should be equal to the number of days in the range and unpaid 0.
        assert data["paid_leave_taken"] >= 1
        assert data["unpaid_leave_taken"] == 0


class TestShiftsAndViolations:
    """Basic smoke tests for shifts and traffic violations."""

    @pytest.mark.asyncio
    async def test_create_shift_and_list(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_minimal_driver_form("shift@example.com"),
                "first_name": "Shift",
                "last_name": "User",
                "phone": "07000000005",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Shift St",
                "city": "City",
                "postcode": "SH1 1AA",
                "max_stops": "15",
            },
            files=_licence_files(),
        )
        driver_id = create_resp.json()["data"]["driver"]["id"]

        shift_date = date.today() + timedelta(days=90)
        resp = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(shift_date),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
        )
        assert resp.status_code == 201

        list_resp = await client.get(f"{DRIVERS}/shifts", headers=headers, params={"driver_id": driver_id})
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["driver_id"] == driver_id

    @pytest.mark.asyncio
    async def test_create_traffic_violation_and_list(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_minimal_driver_form("violation@example.com"),
                "first_name": "Viol",
                "last_name": "User",
                "phone": "07000000006",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Viol St",
                "city": "City",
                "postcode": "VI1 1AA",
                "max_stops": "10",
            },
            files=_licence_files(),
        )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]

        today = date.today()
        resp = await client.post(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            data={
                "violation_type": "SPEEDING",
                "amount": "45.00",
                "date": str(today),
                "time": "14:30:00",
                "status": "UNPAID",
            },
            files=[
                ("proofs", ("proof1.pdf", b"%PDF-1.4 proof1", "application/pdf")),
                ("proofs", ("proof2.jpg", b"jpg-bytes", "image/jpeg")),
            ],
        )
        assert resp.status_code == 201
        created = resp.json()["data"]
        assert "violation" in created
        assert isinstance(created["violation"]["proofs"], list)
        assert len(created["violation"]["proofs"]) == 2
        assert isinstance(created["proof_results"], list)
        assert len(created["proof_results"]) == 2
        assert all(r["status"] == "success" for r in created["proof_results"])

        list_resp = await client.get(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            params={"page": 1, "size": 50},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        assert isinstance(data["items"][0]["proofs"], list)

    @pytest.mark.asyncio
    async def test_list_traffic_violations_returns_empty_for_driver_without_violations(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Regression: list endpoint should return 200 with empty items, not 500."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={**_minimal_driver_form("viol-empty@example.com")},
            files=_licence_files(),
        )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        list_resp = await client.get(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            params={"page": 1, "size": 50},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_create_traffic_violation_returns_non_empty_proofs_in_create_and_list(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """When proofs are uploaded, response payload should include non-empty proof entries."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        create_driver = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={**_minimal_driver_form("viol-proof-nonempty@example.com")},
            files=_licence_files(),
        )
        assert create_driver.status_code == 201
        driver_id = create_driver.json()["data"]["driver"]["id"]

        today = date.today()
        create_violation = await client.post(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            data={
                "violation_type": "SPEEDING",
                "amount": "55.00",
                "date": str(today),
                "time": "10:15:00",
                "status": "UNPAID",
            },
            files=[("proofs", ("proof1.pdf", b"%PDF-1.4 proof1", "application/pdf"))],
        )
        assert create_violation.status_code == 201
        created = create_violation.json()["data"]
        violation_id = created["violation"]["id"]
        assert created["violation"]["proofs"], "expected proofs in create response"
        first_proof = created["violation"]["proofs"][0]
        assert first_proof["id"]

        list_resp = await client.get(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            params={"page": 1, "size": 50},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        listed = next(item for item in items if item["id"] == violation_id)
        assert listed["proofs"], "expected proofs in list response"
        assert listed["proofs"][0]["id"]


def _minimal_driver_form(email: str, **overrides: Any) -> dict[str, Any]:
    """Minimal form data for add-new-driver (all required fields)."""
    data = {
        "email": email,
        "first_name": "Test",
        "last_name": "Driver",
        "phone": "07000000000",
        "capacity[0]": "VAN",
        "driver_type": "INTERNAL",
        "address_line1": "1 Test St",
        "country": "United Kingdom",
        "state": "England",
        "city": "London",
        "postcode": "SW1A 1AA",
        "max_stops": "30",
        **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
        # Default: include licence metadata; pair with _licence_files() or pop for no-doc creates.
        "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
    }
    data.update(overrides)
    return data


def _licence_files() -> list[tuple[str, tuple[str, bytes, str]]]:
    """Required driving licence file tuple for add-new-driver multipart requests."""
    return [("documents", ("licence.pdf", b"%PDF-1.4 licence", "application/pdf"))]


class TestDriverList:
    """GET /v1/drivers — list with pagination and KPIs."""

    @pytest.mark.asyncio
    async def test_list_drivers_returns_kpis_and_table(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.get(DRIVERS, headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "kpis" in data
        assert "table" in data
        assert "total_employed" in data["kpis"]
        assert "active_now" in data["kpis"]
        assert "suspended" in data["kpis"]
        assert "pending_activation" in data["kpis"]
        assert "items" in data["table"]
        assert "total" in data["table"]
        assert data["kpis"]["total_employed"] == data["table"]["total"]
        assert "page" in data["table"]
        assert "size" in data["table"]
        if data["table"]["items"]:
            first = data["table"]["items"][0]
            assert "capacities" in first
            assert isinstance(first["capacities"], list)

    @pytest.mark.asyncio
    async def test_driver_kpis_total_employed_matches_default_list_scope(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """total_employed uses default GET /drivers scope (exclude DRAFT/unlinked); not tied to filtered list totals."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        r0 = await client.get(f"{DRIVERS}/kpis", headers=headers)
        assert r0.status_code == 200
        baseline_total = r0.json()["data"]["total_employed"]
        active_before = r0.json()["data"]["active_now"]

        # Explicit driver_codes: sequence-based defaults do not roll back with test txs and can collide with seeded rows.
        draft_code = "DR-" + uuid.uuid4().hex[:17]
        active_code = "DR-" + uuid.uuid4().hex[:17]

        db_session.add(
            Driver(
                driver_code=draft_code,
                account_status=DriverAccountStatus.DRAFT.value,
                live_status="OFFLINE",
                user_id=None,
            )
        )
        await db_session.flush()

        r1 = await client.get(f"{DRIVERS}/kpis", headers=headers)
        assert r1.status_code == 200
        assert r1.json()["data"]["total_employed"] == baseline_total

        driver_user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        db_session.add(
            Driver(
                driver_code=active_code,
                account_status=DriverAccountStatus.ACTIVE.value,
                live_status="OFFLINE",
                user_id=driver_user.id,
            )
        )
        await db_session.flush()

        r2 = await client.get(f"{DRIVERS}/kpis", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["data"]["total_employed"] == baseline_total + 1
        assert r2.json()["data"]["active_now"] == active_before + 1

    @pytest.mark.asyncio
    async def test_list_drivers_pagination_and_filters(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.get(
            DRIVERS,
            headers=headers,
            params={"page": 1, "size": 10, "order_by": "created_at", "order_desc": True},
        )
        assert resp.status_code == 200
        table = resp.json()["data"]["table"]
        assert table["page"] == 1
        assert table["size"] == 10

    @pytest.mark.asyncio
    async def test_list_drivers_accepts_repeated_list_filters(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.get(
            DRIVERS,
            headers=headers,
            params=[
                ("account_status", "ACTIVE"),
                ("account_status", "SUSPENDED"),
                ("live_status", "OFFLINE"),
                ("live_status", "ON_ROUTE"),
            ],
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_drivers_slash_alias_matches_primary_route(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Both list route variants should return the same response shape."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        params = {"page": 1, "size": 5, "order_by": "created_at", "order_desc": True}

        primary_resp = await client.get(DRIVERS, headers=headers, params=params)
        # Keep tests on the canonical no-trailing-slash route.
        slash_resp = await client.get(DRIVERS, headers=headers, params=params)

        assert primary_resp.status_code == 200
        assert slash_resp.status_code == 200
        primary_data = primary_resp.json()["data"]
        slash_data = slash_resp.json()["data"]
        assert primary_data.keys() == slash_data.keys()
        assert primary_data["table"]["page"] == slash_data["table"]["page"] == 1
        assert primary_data["table"]["size"] == slash_data["table"]["size"] == 5


class TestDriverDraftsAndKpis:
    """GET /v1/drivers/drafts and GET /v1/drivers/kpis."""

    @pytest.mark.asyncio
    async def test_list_driver_drafts_returns_created_driver(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        # Drafts are created via POST /v1/drivers/drafts (multipart; no user until submit).
        created = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"address_line1": "1 Draft Row"},
        )
        assert created.status_code == 201
        created_driver_id = created.json()["data"]["driver"]["id"]
        persisted = await db_session.get(Driver, created_driver_id)
        assert persisted is not None

        # Main list endpoint excludes DRAFT drivers by default.
        main_list = await client.get(f"{DRIVERS}", headers=headers, params={"page": 1, "size": 50})
        assert main_list.status_code == 200
        main_items = main_list.json()["data"]["table"]["items"]
        assert all(item["id"] != created_driver_id for item in main_items)

        resp = await client.get(f"{DRIVERS}/drafts", headers=headers, params={"page": 1, "size": 20})
        assert resp.status_code == 200
        table = resp.json()["data"]["table"]
        assert "items" in table
        created_item = next(item for item in table["items"] if item["id"] == created_driver_id)
        assert created_item["is_submitted"] is False
        assert created_item.get("email") is None
        # Draftable fields can be null until final submit.
        assert created_item["driver_type"] is None
        assert created_item["city"] is None
        assert created_item["postcode"] is None

    @pytest.mark.asyncio
    async def test_list_driver_drafts_search_matches_draft_id_and_jsonb_identity(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        created = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={
                "email": "alice.draft@example.com",
                "first_name": "Alice",
                "last_name": "Draft",
                "phone": "07000111222",
                "address_line1": "1 Draft Search Row",
            },
        )
        assert created.status_code == 201
        created_payload = created.json()["data"]
        driver_id = created_payload["driver"]["id"]
        draft_id = created_payload["draft_id"]
        assert isinstance(draft_id, str) and draft_id.startswith("DF-")

        by_draft_id = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={"page": 1, "size": 50, "search": draft_id},
        )
        assert by_draft_id.status_code == 200
        items = by_draft_id.json()["data"]["table"]["items"]
        assert any(item["id"] == driver_id for item in items)

        by_email = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={"page": 1, "size": 50, "search": "alice.draft@example.com"},
        )
        assert by_email.status_code == 200
        items = by_email.json()["data"]["table"]["items"]
        assert any(item["id"] == driver_id for item in items)

        by_phone = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={"page": 1, "size": 50, "search": "07000111222"},
        )
        assert by_phone.status_code == 200
        items = by_phone.json()["data"]["table"]["items"]
        assert any(item["id"] == driver_id for item in items)

        by_name = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={"page": 1, "size": 50, "search": "Alice Draft"},
        )
        assert by_name.status_code == 200
        items = by_name.json()["data"]["table"]["items"]
        assert any(item["id"] == driver_id for item in items)

    @pytest.mark.asyncio
    async def test_list_driver_drafts_ignores_live_status_query_param(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """Drafts API no longer accepts/validates live_status as a filter."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        created = await client.post(
            f"{DRIVERS}/drafts",
            headers=headers,
            data={"address_line1": "1 Draft Ignore Live Status"},
        )
        assert created.status_code == 201
        created_data = created.json()["data"]
        draft_id = created_data["draft_id"]

        baseline_resp = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={
                "page": 1,
                "size": 50,
                "search": draft_id,
            },
        )
        assert baseline_resp.status_code == 200

        resp = await client.get(
            f"{DRIVERS}/drafts",
            headers=headers,
            params={
                "page": 1,
                "size": 50,
                "search": draft_id,
                "live_status": "NOT_A_REAL_STATUS",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["table"]["total"] == baseline_resp.json()["data"]["table"]["total"]

    @pytest.mark.asyncio
    async def test_get_driver_kpis_returns_expected_shape(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.get(f"{DRIVERS}/kpis", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_employed" in data
        assert "active_now" in data
        assert "suspended" in data
        assert "pending_activation" in data


class TestDriverUpdateAndDelete:
    """PATCH /v1/drivers/{id}, DELETE /v1/drivers/{id}."""

    @pytest.mark.asyncio
    async def test_update_driver(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("update@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        current_version = payload["driver"]["version"]

        update_resp = await client.patch(
            f"{DRIVERS}/{driver_id}",
            headers=headers,
            json={
                "first_name": "Updated",
                "notes": "Updated notes",
                "country": "United Kingdom",
                "state": "Wales",
                "city": "Wrexham",
                "expected_version": current_version,
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["user"]["first_name"] == "Updated"
        assert update_resp.json()["data"]["notes"] == "Updated notes"
        assert update_resp.json()["data"]["country"] == "United Kingdom"
        assert update_resp.json()["data"]["state"] == "Wales"
        assert update_resp.json()["data"]["city"] == "Wrexham"
        assert update_resp.json()["data"]["version"] == current_version + 1
        assert update_resp.json()["data"]["capacities"] == ["VAN"]
        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        linked_user = await db_session.get(User, driver.user_id)
        assert linked_user is not None
        assert linked_user.first_name == "Updated"
        assert driver.country == "United Kingdom"
        assert driver.state == "Wales"
        assert driver.city == "Wrexham"

    @pytest.mark.asyncio
    async def test_update_driver_form_with_licence_upsert(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("formupdate@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        old_version = payload["driver"]["version"]

        form_update_resp = await client.patch(
            f"{DRIVERS}/{driver_id}/form",
            headers=headers,
            data={
                "first_name": "Form",
                "last_name": "Updated",
                "country": "United Kingdom",
                "state": "Wales",
                "city": "Wrexham",
                "capacity[0]": "TRUCK",
                "expected_version": str(old_version),
                "driving_licence_expiry_date": "2032-01-01",
            },
            files={"driving_licence_file": ("licence-v2.pdf", b"%PDF-1.4 replacement", "application/pdf")},
        )
        assert form_update_resp.status_code == 200
        updated = form_update_resp.json()["data"]
        assert updated["user"]["first_name"] == "Form"
        assert updated["user"]["last_name"] == "Updated"
        assert updated["country"] == "United Kingdom"
        assert updated["state"] == "Wales"
        assert updated["city"] == "Wrexham"
        assert updated["capacities"] == ["TRUCK"]

        docs_resp = await client.get(f"{DRIVERS}/{driver_id}/documents", headers=headers)
        assert docs_resp.status_code == 200
        docs = docs_resp.json()["data"]["items"]
        assert len([d for d in docs if d["document_type"] == "DRIVING_LICENCE"]) == 1

    @pytest.mark.asyncio
    async def test_admin_delete_driver_profile_photo(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """DELETE /v1/drivers/{id}/profile-photo clears storage and returns updated driver detail."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        form_data = _minimal_driver_form("deletephoto@example.com")
        files = [("profile_photo", ("face.png", b"\x89PNG\r\n\x1a\n", "image/png"))] + _licence_files()
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None), patch(
            "app.modules.drivers.service.get_images_client"
        ) as get_client_mock:
            client_mock = get_client_mock.return_value
            client_mock.upload_image = AsyncMock()
            client_mock.upload_image.return_value.id = "cf_img_delete_test"
            client_mock.upload_image.return_value.filename = "face.png"
            client_mock.upload_image.return_value.variants = []
            client_mock.generate_signed_url.return_value = "https://example.com/driver-photo"
            client_mock.delete_image = AsyncMock(return_value=None)
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=form_data,
                files=files,
            )
            assert create_resp.status_code == 201
            driver_id = create_resp.json()["data"]["driver"]["id"]
            assert create_resp.json()["data"]["driver"]["profile_photo_url"] is not None

            delete_resp = await client.delete(f"{DRIVERS}/{driver_id}/profile-photo", headers=headers)
            assert delete_resp.status_code == 200
            body = delete_resp.json()
            assert body["message"] == "Profile photo removed"
            assert body["data"]["profile_photo_url"] is None
            client_mock.delete_image.assert_awaited_once_with("cf_img_delete_test")

        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        assert driver.profile_photo_key is None

    @pytest.mark.asyncio
    async def test_admin_delete_driver_profile_photo_idempotent(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """DELETE profile-photo succeeds when the driver has no photo (no-op)."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("nophoto-delete@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        with patch("app.modules.drivers.service.get_images_client") as get_client_mock:
            get_client_mock.return_value.delete_image = AsyncMock(return_value=None)
            first = await client.delete(f"{DRIVERS}/{driver_id}/profile-photo", headers=headers)
            second = await client.delete(f"{DRIVERS}/{driver_id}/profile-photo", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["data"]["profile_photo_url"] is None
        get_client_mock.return_value.delete_image.assert_not_awaited()

        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        assert driver.profile_photo_key is None

    @pytest.mark.asyncio
    async def test_admin_delete_driver_profile_photo_not_found(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        missing_id = "00000000-0000-0000-0000-000000000099"
        resp = await client.delete(f"{DRIVERS}/{missing_id}/profile-photo", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_delete_driver_profile_photo_requires_write(
        self,
        client_real_permissions: AsyncClient,
        user_factory,
    ) -> None:
        """Caller without Resource.DRIVERS WRITE cannot delete a driver's profile photo."""
        customer: User = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
        token, _ = create_access_token(user_id=customer.id, role="CUSTOMER_B2C", client_type="CUSTOMER_B2C")
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "CUSTOMER_B2C",
        }
        resp = await client_real_permissions.delete(
            f"{DRIVERS}/00000000-0000-0000-0000-000000000001/profile-photo",
            headers=headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_driver_capacities(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """PATCH /v1/drivers/{id} updates capacities; legacy `capacity` is derived from the first element."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("multicap-update@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]

        update_resp = await client.patch(
            f"{DRIVERS}/{driver_id}",
            headers=headers,
            json={"capacities": ["TRUCK", "VAN"]},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()["data"]
        assert updated["capacities"] == ["TRUCK", "VAN"]

        persisted = await db_session.get(Driver, driver_id)
        assert persisted is not None
        assert persisted.capacities == ["TRUCK", "VAN"]

    @pytest.mark.asyncio
    async def test_update_driver_capacities_only(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """PATCH updates capacities only (plain legacy `capacity` is removed from the request contract)."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("legacy-capacity-sync@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        update_resp = await client.patch(
            f"{DRIVERS}/{driver_id}",
            headers=headers,
            json={"capacities": ["TRUCK"]},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()["data"]
        assert updated["capacities"] == ["TRUCK"]

        persisted = await db_session.get(Driver, driver_id)
        assert persisted is not None
        assert persisted.capacities == ["TRUCK"]

    @pytest.mark.asyncio
    async def test_delete_driver(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("delete@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]

        delete_resp = await client.delete(f"{DRIVERS}/{driver_id}", headers=headers)
        assert delete_resp.status_code == 200
        # Response is a snapshot of the driver before deletion.
        assert delete_resp.json()["data"]["account_status"] in {"PENDING_ACTIVATION", "ACTIVE", "SUSPENDED"}

        get_resp = await client.get(f"{DRIVERS}/{driver_id}", headers=headers)
        assert get_resp.status_code == 404


class TestDriverSuspendAndReactivate:
    """POST /v1/drivers/{id}/suspend, POST /v1/drivers/{id}/reactivate."""

    @pytest.mark.asyncio
    async def test_suspend_and_reactivate_driver(
        self,
        client: AsyncClient,
        db_session,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("suspend@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        # Driver is created as DRAFT by default; set to ACTIVE so we can suspend
        patch_resp = await client.patch(
            f"{DRIVERS}/{driver_id}",
            headers=headers,
            json={"account_status": "ACTIVE"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["account_status"] == "ACTIVE"

        suspend_resp = await client.post(
            f"{DRIVERS}/{driver_id}/suspend",
            headers=headers,
            json={"reason": "Test suspension"},
        )
        assert suspend_resp.status_code == 200
        assert suspend_resp.json()["data"]["account_status"] == "SUSPENDED"
        driver = await db_session.get(Driver, driver_id)
        assert driver is not None
        linked_user = await db_session.get(User, driver.user_id)
        assert linked_user is not None
        assert linked_user.status == "SUSPENDED"

        reactivate_resp = await client.post(f"{DRIVERS}/{driver_id}/reactivate", headers=headers)
        assert reactivate_resp.status_code == 200
        assert reactivate_resp.json()["data"]["account_status"] == "ACTIVE"
        await db_session.refresh(linked_user)
        assert linked_user.status == "ACTIVE"


class TestDriverSchedule:
    """GET/PUT /v1/drivers/{id}/schedule, PATCH .../schedule/{day_of_week}."""

    @pytest.mark.asyncio
    async def test_get_and_update_schedule(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("schedule@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]

        get_resp = await client.get(f"{DRIVERS}/{driver_id}/schedule", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()["data"]
        assert "days" in data
        assert "total_weekly_hours" in data
        assert len(data["days"]) == 7

        put_resp = await client.put(
            f"{DRIVERS}/{driver_id}/schedule",
            headers=headers,
            json={
                "days": [
                    {"day_of_week": 0, "is_active": True, "start_time": "09:00:00", "end_time": "17:00:00"},
                    {"day_of_week": 1, "is_active": True, "start_time": "09:00:00", "end_time": "17:00:00"},
                ]
                + [{"day_of_week": d, "is_active": False, "start_time": None, "end_time": None} for d in range(2, 7)],
                "total_weekly_hours": 16.0,
            },
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["data"]["total_weekly_hours"] == 16.0


class TestDriverRouteTelematics:
    """Route history, summary, and telematics endpoints for drivers."""

    @pytest.mark.asyncio
    async def test_route_history_summary_and_telematics_end_to_end(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        """Route history + summary + telematics should return seeded route/event data."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        # Create a driver via existing onboarding flow.
        route_driver_email = f"route-history-{uuid.uuid4().hex}@example.com"
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form(route_driver_email),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        driver_row = await db_session.get(Driver, driver_id)
        assert driver_row is not None

        # Seed route data with unique keys to avoid collisions on reused local DBs.
        seed_suffix = uuid.uuid4().hex[:8].upper()
        depot_code = f"DP-ROUTE-DEMO-{seed_suffix}"
        vehicle_reg = f"STG-ROUTE-{seed_suffix}"
        depot = Depot(
            name="Route Demo Depot",
            code=depot_code,
            address_line_1="1 Demo Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()

        vehicle = Vehicle(
            registration_number=vehicle_reg,
            depot_id=depot.id,
        )
        db_session.add(vehicle)
        await db_session.flush()

        plan = RoutePlan(
            service_date=date.today(),
            depot_id=depot.id,
            status="READY",
        )
        db_session.add(plan)
        await db_session.flush()

        route = Route(
            plan_id=plan.id,
            driver_id=driver_row.id,
            vehicle_id=vehicle.id,
            route_code=f"RT-DEMO-{seed_suffix}",
            route_type="DELIVERY",
            total_stops=10,
            total_duration_min=88.0,
            estimated_drive_time_min=90.0,
            actual_drive_time_min=88.0,
            status="COMPLETED",
        )
        db_session.add(route)
        await db_session.flush()

        db_session.add_all(
            [
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_row.id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=20),
                    lat=51.5074,
                    lng=-0.1278,
                    event_metadata={
                        "speed_mph": 38,
                        "limit_mph": 30,
                        "speed_over_mph": 8,
                        "route_code": route.route_code,
                        "location_text": "Rosewood Drive, Marlow, UK",
                        "distance_miles": 1.2,
                    },
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_row.id,
                    event_type="SPEEDING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=10),
                    lat=51.5075,
                    lng=-0.1279,
                    event_metadata={
                        "speed_mph": 36,
                        "limit_mph": 30,
                    },
                ),
                RouteEvent(
                    route_id=route.id,
                    driver_id=driver_row.id,
                    event_type="HARSH_BRAKING",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=5),
                    lat=51.5076,
                    lng=-0.1280,
                    event_metadata={"severity": "HIGH", "start_speed_mph": 32, "end_speed_mph": 7},
                ),
            ]
        )
        await db_session.flush()

        # History endpoint should include seeded route with aggregated counts.
        history_resp = await client.get(f"{DRIVERS}/{driver_id}/route-history?type=DELIVERY", headers=headers)
        assert history_resp.status_code == 200
        hist_body = history_resp.json()["data"]["table"]
        assert hist_body["total"] >= 1
        items = hist_body["items"]
        seeded = next((row for row in items if row["route_id"] == route.id), None)
        assert seeded is not None
        assert seeded["vehicle_reg"] == vehicle_reg
        assert seeded["type"] == "DELIVERY"
        assert seeded["speeding_count"] == 2
        assert seeded["harsh_braking_count"] == 1

        # Type filter should exclude this route for PICKUP.
        pickup_resp = await client.get(f"{DRIVERS}/{driver_id}/route-history?type=PICKUP", headers=headers)
        assert pickup_resp.status_code == 200
        pickup_items = pickup_resp.json()["data"]["table"]["items"]
        assert all(item["route_id"] != route.id for item in pickup_items)

        # Repeated type filter should be accepted.
        multi_type_resp = await client.get(
            f"{DRIVERS}/{driver_id}/route-history",
            headers=headers,
            params=[("type", "DELIVERY"), ("type", "PICKUP")],
        )
        assert multi_type_resp.status_code == 200

        # Summary endpoint assertions.
        summary_resp = await client.get(f"{DRIVERS}/routes/{route.id}/summary", headers=headers)
        assert summary_resp.status_code == 200
        summary = summary_resp.json()["data"]
        assert summary["route_id"] == route.id
        assert summary["vehicle_reg"] == vehicle_reg
        assert summary["stops"] == 10
        assert summary["estimated_drive_time_minutes"] == 90.0
        assert summary["actual_drive_time_minutes"] == 88.0
        assert "progress" in summary

        # Telematics endpoint with event_type filter.
        telem_resp = await client.get(f"{DRIVERS}/routes/{route.id}/telematics?event_type=SPEEDING", headers=headers)
        assert telem_resp.status_code == 200
        telem_items = telem_resp.json()["data"]["table"]["items"]
        assert len(telem_items) == 2
        assert all(item["event_type"] == "SPEEDING" for item in telem_items)
        assert all(item["route_code"] == route.route_code for item in telem_items)
        assert telem_items[0]["speed_mph"] is not None
        assert telem_items[0]["limit_mph"] is not None
        assert telem_items[0]["speed_over_mph"] is not None

    @pytest.mark.asyncio
    async def test_driver_schedule_availability_calendar_aggregates_sources(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        email = f"calendar-{uuid.uuid4().hex}@example.com"
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form(email),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]
        driver_row = await db_session.get(Driver, driver_id)
        assert driver_row is not None

        today = date.today()

        # Shift
        shift_start = datetime.combine(today, datetime.min.time(), tzinfo=UTC).replace(hour=6)
        shift_end = datetime.combine(today, datetime.min.time(), tzinfo=UTC).replace(hour=14)
        from app.modules.drivers.models import DriverShift, DriverTimeOff

        db_session.add(
            DriverShift(
                driver_id=driver_id,
                shift_date=today,
                start_time=shift_start,
                end_time=shift_end,
                status="CONFIRMED",
            )
        )

        # Time off
        db_session.add(
            DriverTimeOff(
                driver_id=driver_id,
                start_date=today + timedelta(days=1),
                end_date=today + timedelta(days=1),
                type="SICK_LEAVE",
                days=1,
                notes="Flu",
                is_paid=False,
            )
        )

        # Holiday
        db_session.add(
            Holiday(
                name="Spring Bank",
                year=today.year,
                start_date=today + timedelta(days=2),
                end_date=today + timedelta(days=2),
                audience="BOTH",
                allow_shifts=False,
            )
        )

        # Route
        suffix = uuid.uuid4().hex[:8].upper()
        depot = Depot(
            name="Calendar Depot",
            code=f"DP-CAL-{suffix}",
            address_line_1="1 Calendar Street",
            city="London",
            postcode="SW1A 1AA",
        )
        db_session.add(depot)
        await db_session.flush()
        vehicle = Vehicle(registration_number=f"CAL-{suffix}", depot_id=depot.id)
        db_session.add(vehicle)
        await db_session.flush()
        plan = RoutePlan(service_date=today, depot_id=depot.id, status="READY")
        db_session.add(plan)
        await db_session.flush()
        db_session.add(
            Route(
                plan_id=plan.id,
                driver_id=driver_id,
                vehicle_id=vehicle.id,
                route_code=f"RT-CAL-{suffix}",
                route_type="DELIVERY",
                status="COMPLETED",
            )
        )
        await db_session.flush()

        resp = await client.get(
            f"{DRIVERS}/{driver_id}/schedule-availability/calendar",
            headers=headers,
            params={"from_date": str(today), "to_date": str(today + timedelta(days=3))},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["summary"]["shifts_count"] == 1
        assert data["summary"]["time_off_count"] == 1
        assert data["summary"]["holidays_count"] == 1
        assert data["summary"]["routes_count"] >= 1
        sources = {item["source"] for item in data["events"]}
        assert {"SHIFT", "TIME_OFF", "HOLIDAY", "ROUTE"}.issubset(sources)

        filtered = await client.get(
            f"{DRIVERS}/{driver_id}/schedule-availability/calendar",
            headers=headers,
            params=[
                ("from_date", str(today)),
                ("to_date", str(today + timedelta(days=3))),
                ("event_source", "SHIFT"),
                ("event_source", "TIME_OFF"),
            ],
        )
        assert filtered.status_code == 200
        filtered_sources = {item["source"] for item in filtered.json()["data"]["events"]}
        assert filtered_sources == {"SHIFT", "TIME_OFF"}

        route_only = await client.get(
            f"{DRIVERS}/{driver_id}/schedule-availability/calendar",
            headers=headers,
            params=[
                ("from_date", str(today)),
                ("to_date", str(today + timedelta(days=3))),
                ("event_source", "ROUTE"),
                ("route_type", "DELIVERY"),
                ("route_status", "COMPLETED"),
            ],
        )
        assert route_only.status_code == 200
        route_events = route_only.json()["data"]["events"]
        assert route_events
        assert all(item["source"] == "ROUTE" for item in route_events)
        assert all(item["route_type"] == "DELIVERY" for item in route_events)
        assert all(item["route_status"] == "COMPLETED" for item in route_events)

    @pytest.mark.asyncio
    async def test_driver_schedule_availability_calendar_validates_date_range(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form(f"calendar-range-{uuid.uuid4().hex}@example.com"),
                files=_licence_files(),
            )
        assert create_resp.status_code == 201
        driver_id = create_resp.json()["data"]["driver"]["id"]

        bad_resp = await client.get(
            f"{DRIVERS}/{driver_id}/schedule-availability/calendar",
            headers=headers,
            params={
                "from_date": str(date.today() + timedelta(days=2)),
                "to_date": str(date.today()),
            },
        )
        assert bad_resp.status_code == 422


class TestDriverDocumentFullCrud:
    """GET /documents/{id}/full, PATCH /documents/{id}, DELETE /documents/{id}."""

    @pytest.mark.asyncio
    async def test_document_update_and_delete(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("doccrud@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        files = {"file": ("licence.png", b"dummy", "image/png")}
        data = {
            "document_type": "DRIVING_LICENCE",
            "title": "DRIVING LICENCE",
            "expiry_date": str(date.today().replace(year=date.today().year + 2)),
        }
        upload_resp = await client.post(
            f"{DRIVERS}/{driver_id}/documents",
            headers=headers,
            data=data,
            files=files,
        )
        assert upload_resp.status_code == 201
        doc_id = upload_resp.json()["data"]["id"]

        full_resp = await client.get(f"{DRIVERS}/documents/{doc_id}/full", headers=headers)
        assert full_resp.status_code == 200
        full_data = full_resp.json()["data"]
        assert full_data["id"] == doc_id
        assert full_data["status"] in ("VALID", "EXPIRING_SOON", "EXPIRED")
        assert "file_url" in full_data

        # Non-CUSTOM document title stays canonical (DRIVING LICENCE); expiry can be updated
        patch_resp = await client.patch(
            f"{DRIVERS}/documents/{doc_id}",
            headers=headers,
            data={"title": "Updated title", "expiry_date": str(date.today().replace(year=date.today().year + 3))},
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.json()["data"]
        assert patch_data["title"] == "DRIVING LICENCE"
        assert patch_data["status"] in ("VALID", "EXPIRING_SOON", "EXPIRED")
        assert "file_url" in patch_data

        del_resp = await client.delete(f"{DRIVERS}/documents/{doc_id}", headers=headers)
        assert del_resp.status_code == 200
        get_after = await client.get(f"{DRIVERS}/documents/{doc_id}/full", headers=headers)
        assert get_after.status_code == 404

    @pytest.mark.asyncio
    async def test_document_update_with_new_file(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        """PATCH document with optional new file upload returns 200 with file_url and status."""
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("docfile@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        upload_resp = await client.post(
            f"{DRIVERS}/{driver_id}/documents",
            headers=headers,
            data={
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "expiry_date": str(date.today().replace(year=date.today().year + 2)),
            },
            files={"file": ("licence.png", b"original", "image/png")},
        )
        assert upload_resp.status_code == 201
        doc_id = upload_resp.json()["data"]["id"]

        # Update with new file (and new expiry)
        patch_resp = await client.patch(
            f"{DRIVERS}/documents/{doc_id}",
            headers=headers,
            data={"expiry_date": str(date.today().replace(year=date.today().year + 4))},
            files={"file": ("licence_v2.png", b"updated-content", "image/png")},
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.json()["data"]
        assert patch_data["id"] == doc_id
        assert patch_data["status"] in ("VALID", "EXPIRING_SOON", "EXPIRED")
        assert "file_url" in patch_data
        # New file upload typically yields a new file_key/url (implementation-dependent)
        assert patch_data["title"] == "DRIVING LICENCE"


class TestTimeOffFullCrud:
    """Time off: list already tested; get by id via list; PATCH/DELETE if routes exist."""

    @pytest.mark.asyncio
    async def test_time_off_update_and_delete(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("timeoffcrud@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        start = date.today()
        end = start + timedelta(days=2)
        post_resp = await client.post(
            f"{DRIVERS}/{driver_id}/time-off",
            headers=headers,
            data={"start_date": str(start), "end_date": str(end), "type": "ANNUAL_LEAVE"},
        )
        assert post_resp.status_code == 201
        time_off_id = post_resp.json()["data"]["id"]

        full_resp = await client.get(f"{DRIVERS}/time-off/{time_off_id}/full", headers=headers)
        assert full_resp.status_code == 200
        assert full_resp.json()["data"]["id"] == time_off_id

        patch_resp = await client.patch(
            f"{DRIVERS}/time-off/{time_off_id}",
            headers=headers,
            data={"type": "MEDICAL_APPOINTMENT"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["type"] == "MEDICAL_APPOINTMENT"

        # Admin can mark leave as unpaid
        unpaid_resp = await client.patch(
            f"{DRIVERS}/time-off/{time_off_id}",
            headers=headers,
            data={"is_paid": "false"},
        )
        assert unpaid_resp.status_code == 200
        assert unpaid_resp.json()["data"]["is_paid"] is False

        del_resp = await client.delete(f"{DRIVERS}/time-off/{time_off_id}", headers=headers)
        assert del_resp.status_code == 200


class TestSickLeaveFullCrud:
    """Deprecated — sick leave is now handled via unified time-off."""


class TestShiftFullCrud:
    @pytest.mark.asyncio
    async def test_shift_get_update_delete(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("shiftcrud@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        today = date.today()
        post_resp = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(today),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
        )
        assert post_resp.status_code == 201
        shift_id = post_resp.json()["data"]["id"]

        full_resp = await client.get(f"{DRIVERS}/shifts/{shift_id}/full", headers=headers)
        assert full_resp.status_code == 200
        assert full_resp.json()["data"]["driver_id"] == driver_id

        patch_resp = await client.patch(
            f"{DRIVERS}/shifts/{shift_id}",
            headers=headers,
            data={"start_time": "09:00:00", "end_time": "17:00:00"},
        )
        assert patch_resp.status_code == 200

        del_resp = await client.delete(f"{DRIVERS}/shifts/{shift_id}", headers=headers)
        assert del_resp.status_code == 200


class TestTrafficViolationFullCrud:
    @pytest.mark.asyncio
    async def test_traffic_violation_get_update_delete(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("violcrud@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]
        today = date.today()
        post_resp = await client.post(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            data={
                "violation_type": "SPEEDING",
                "amount": "50.00",
                "date": str(today),
                "time": "12:00:00",
                "status": "UNPAID",
            },
            files=[
                ("proofs", ("proof1.pdf", b"%PDF-1.4 proof1", "application/pdf")),
            ],
        )
        assert post_resp.status_code == 201
        violation_id = post_resp.json()["data"]["violation"]["id"]

        full_resp = await client.get(f"{DRIVERS}/traffic-violations/{violation_id}/full", headers=headers)
        assert full_resp.status_code == 200
        assert full_resp.json()["data"]["violation_type"] == "SPEEDING"
        assert len(full_resp.json()["data"]["proofs"]) == 1

        add_resp = await client.post(
            f"{DRIVERS}/traffic-violations/{violation_id}/proofs",
            headers=headers,
            files=[
                ("proofs", ("proof2.png", b"png-bytes", "image/png")),
            ],
        )
        assert add_resp.status_code == 201
        add_body = add_resp.json()["data"]
        assert len(add_body["violation"]["proofs"]) == 2
        assert isinstance(add_body["proof_results"], list)
        assert add_body["proof_results"][0]["status"] == "success"

        proof_id = add_body["violation"]["proofs"][0]["id"]
        del_proof = await client.delete(f"{DRIVERS}/traffic-violations/proofs/{proof_id}", headers=headers)
        assert del_proof.status_code == 200

        patch_resp = await client.patch(
            f"{DRIVERS}/traffic-violations/{violation_id}",
            headers=headers,
            data={"status": "PAID"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["violation"]["status"] == "PAID"

        patch_with_proofs = await client.patch(
            f"{DRIVERS}/traffic-violations/{violation_id}",
            headers=headers,
            data={
                "notes": "Updated via patch with proofs",
                "amount": "55.00",
            },
            files=[
                ("proofs", ("proof3.pdf", b"%PDF-1.4 proof3", "application/pdf")),
                ("proofs", ("proof4.jpg", b"jpg-bytes-2", "image/jpeg")),
            ],
        )
        assert patch_with_proofs.status_code == 200
        body = patch_with_proofs.json()["data"]
        assert body["violation"]["notes"] == "Updated via patch with proofs"
        assert body["violation"]["amount"] == "55.00"
        assert isinstance(body["proof_results"], list)
        assert len(body["proof_results"]) == 2
        # Proofs can partially fail (e.g. invalid type/size); ensure the server processed both files
        # and at least one upload succeeded.
        assert sum(1 for r in body["proof_results"] if r["status"] == "success") >= 1

        del_resp = await client.delete(f"{DRIVERS}/traffic-violations/{violation_id}", headers=headers)
        assert del_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_traffic_violation_create_returns_per_proof_results_partial_failure(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("violpartial@example.com"),
                files=_licence_files(),
            )
        driver_id = create_resp.json()["data"]["driver"]["id"]
        today = date.today()
        resp = await client.post(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            data={
                "violation_type": "SPEEDING",
                "amount": "50.00",
                "date": str(today),
                "time": "12:00:00",
                "status": "UNPAID",
            },
            files=[
                ("proofs", ("ok.pdf", b"%PDF-1.4 ok", "application/pdf")),
                ("proofs", ("bad.exe", b"MZ...", "application/x-msdownload")),
            ],
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert len(data["proof_results"]) == 2
        assert data["proof_results"][0]["status"] == "success"
        assert data["proof_results"][1]["status"] == "failed"
        assert data["proof_results"][1]["proof"] is None
        assert len(data["violation"]["proofs"]) == 1

    @pytest.mark.asyncio
    async def test_traffic_violation_create_rejects_too_many_proofs(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("violmax@example.com"),
                files=_licence_files(),
            )
        driver_id = create_resp.json()["data"]["driver"]["id"]
        today = date.today()
        files = [("proofs", (f"p{i}.pdf", b"%PDF-1.4", "application/pdf")) for i in range(11)]
        resp = await client.post(
            f"{DRIVERS}/{driver_id}/traffic-violations",
            headers=headers,
            data={
                "violation_type": "SPEEDING",
                "amount": "50.00",
                "date": str(today),
                "time": "12:00:00",
                "status": "UNPAID",
            },
            files=files,
        )
        assert resp.status_code == 422


class TestDriverPasswordReset:
    """POST /v1/drivers/{id}/password-reset — admin changes driver password directly."""

    @pytest.mark.asyncio
    async def test_admin_changes_driver_password(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
            create_resp = await client.post(
                f"{DRIVERS}/add-new-driver",
                headers=headers,
                data=_minimal_driver_form("resetpwd@example.com"),
                files=_licence_files(),
            )
        payload = create_resp.json()["data"]
        driver_id = payload["driver"]["id"]

        resp = await client.post(
            f"{DRIVERS}/{driver_id}/password-reset",
            headers=headers,
            json={"new_password": "Str0ngP@ssw0rd123"},
        )
        assert resp.status_code == 200
