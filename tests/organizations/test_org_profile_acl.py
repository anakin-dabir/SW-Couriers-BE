"""B2B org profile gate: ORG_PROFILE permission + ACCOUNT_OWNER bypass.

Covers profile-completion, notification preferences, and ACL behaviour.
"""

import uuid
from collections.abc import AsyncGenerator
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.core.database import get_db_session
from app.core.security import create_access_token
from app.modules.organizations.enums import ContactRole, ContactStatus
from app.modules.organizations.models import OrgContact, Organization
from app.modules.permission.service import PermissionService
from app.modules.user.models import User

ORGS = "/v1/organizations"


@pytest_asyncio.fixture
async def client_real_acl(
    app: Any,
    db_session: AsyncSession,
    auth_blacklist_mocks: Any,
) -> AsyncGenerator[AsyncClient]:
    """Same as global ``client`` but **without** mocking ``PermissionService.check_permission``."""

    async def _override_get_db_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver/api") as ac:
        yield ac
    app.dependency_overrides.clear()


def _receiver_notifications_item(profile_completion_body: dict) -> dict:
    for item in profile_completion_body["data"]["items"]:
        if item["key"] == "receiver_notifications":
            return item
    raise AssertionError("receiver_notifications item missing")


def _b2b_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="CUSTOMER_B2B",
        region_id=None,
        organization_id=user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2B",
    }


async def _make_contact(
    db_session: AsyncSession,
    user_factory,
    org: Organization,
    *,
    contact_role: str = ContactRole.ACCOUNT_OWNER.value,
    is_primary: bool = False,
    status: str = ContactStatus.ACTIVE.value,
) -> tuple[User, OrgContact]:
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    contact = OrgContact(
        organization_id=org.id,
        user_id=user.id,
        contact_number=f"+447700{uuid.uuid4().int % 1000000:06d}",
        contact_role=contact_role,
        status=status,
        is_primary=is_primary,
    )
    db_session.add(contact)
    await db_session.flush()
    await db_session.refresh(contact)
    return user, contact


@pytest.mark.asyncio
async def test_admin_get_profile_completion(
    client: AsyncClient,
    admin_headers: dict,
    sample_org: Organization,
) -> None:
    resp = await client.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "percent_complete" in data
    assert "completed_weight" in data
    assert "total_weight" in data
    assert "items" in data
    assert len(data["items"]) == 8


@pytest.mark.asyncio
async def test_b2b_account_owner_can_get_full_profile(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    resp = await client.get(f"{ORGS}/{sample_org.id}/profile", headers=_b2b_headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "organization" in body["data"]
    assert "pickup_addresses" in body["data"]
    assert isinstance(body["data"]["pickup_addresses"], list)


@pytest.mark.asyncio
async def test_b2b_account_owner_get_profile_completion(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    resp = await client.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=_b2b_headers(owner))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_b2b_delegate_without_org_profile_forbidden_on_read(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    """BILLING contact has ORG_PROFILE NONE by default."""
    await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    billing_user, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.BILLING.value)
    resp = await client_real_acl.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=_b2b_headers(billing_user))
    assert resp.status_code == 403
    resp_profile = await client_real_acl.get(f"{ORGS}/{sample_org.id}/profile", headers=_b2b_headers(billing_user))
    assert resp_profile.status_code == 403


@pytest.mark.asyncio
async def test_b2b_delegate_with_org_profile_read(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    billing_user, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.BILLING.value)
    perm = PermissionService(db_session)
    await perm.set_permission(billing_user.id, Resource.ORG_PROFILE, PermissionLevel.READ, granted_by=billing_user.id)

    resp = await client_real_acl.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=_b2b_headers(billing_user))
    assert resp.status_code == 200
    resp_profile = await client_real_acl.get(f"{ORGS}/{sample_org.id}/profile", headers=_b2b_headers(billing_user))
    assert resp_profile.status_code == 200
    body = resp_profile.json()
    assert body["success"] is True
    assert "organization" in body["data"]
    assert "pickup_addresses" in body["data"]


@pytest.mark.asyncio
async def test_b2b_delegate_with_org_profile_write_can_upload_logo(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    billing_user, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.BILLING.value)
    perm = PermissionService(db_session)
    await perm.set_permission(billing_user.id, Resource.ORG_PROFILE, PermissionLevel.WRITE, granted_by=billing_user.id)

    fake_result = MagicMock()
    fake_result.id = "cf-delegate-logo-id"
    fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    with (
        patch("app.modules.organizations.service.upload_image", new_callable=AsyncMock, return_value=fake_result),
        patch("app.modules.organizations.service.generate_image_url", return_value="https://imagedelivery.net/test/cf-delegate-logo-id/public"),
    ):
        resp = await client_real_acl.patch(
            f"{ORGS}/{sample_org.id}/logo",
            files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
            headers=_b2b_headers(billing_user),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["logo_url"] is not None


@pytest.mark.asyncio
async def test_b2b_delegate_with_org_profile_write_can_save_profile_with_logo(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    billing_user, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.BILLING.value)
    perm = PermissionService(db_session)
    await perm.set_permission(billing_user.id, Resource.ORG_PROFILE, PermissionLevel.WRITE, granted_by=billing_user.id)

    fake_result = MagicMock()
    fake_result.id = "cf-multipart-logo-id"
    fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    payload = {
        "trading_name": "Updated By Multipart",
        "website": "https://example.org",
        "vat_number": "GB123456789",
        "registered_address": {
            "address_line_1": "1 Updated Street",
            "city": "London",
            "postcode": "EC1A 1BB",
            "country": "United Kingdom",
        },
        "pickup_addresses": [
            {
                "label": "Main Warehouse",
                "same_as_registered_address": True,
                "is_default": True,
            }
        ],
    }
    with (
        patch("app.modules.organizations.service.upload_image", new_callable=AsyncMock, return_value=fake_result),
        patch("app.modules.organizations.service.generate_image_url", return_value="https://imagedelivery.net/test/cf-multipart-logo-id/public"),
    ):
        resp = await client_real_acl.patch(
            f"{ORGS}/{sample_org.id}/profile",
            data={"payload": json.dumps(payload)},
            files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
            headers=_b2b_headers(billing_user),
        )

    assert resp.status_code == 200, resp.text
    org = resp.json()["data"]["organization"]
    assert org["trading_name"] == "Updated By Multipart"
    assert org["logo_url"] is not None
    response_pickups = resp.json()["data"]["pickup_addresses"]
    assert len(response_pickups) == 1
    assert response_pickups[0]["label"] == "Main Warehouse"
    assert response_pickups[0]["is_default"] is True

    pickup_resp = await client_real_acl.get(
        f"{ORGS}/{sample_org.id}/pickup-addresses",
        headers=_b2b_headers(billing_user),
    )
    assert pickup_resp.status_code == 200, pickup_resp.text
    addresses = pickup_resp.json()["data"]
    assert len(addresses) == 1
    assert addresses[0]["label"] == "Main Warehouse"
    assert addresses[0]["is_default"] is True


@pytest.mark.asyncio
async def test_profile_trading_same_as_registered_copies_address(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    payload = {
        "registered_address": {
            "address_line_1": "99 Trade Street",
            "address_line_2": "Suite 1",
            "city": "Leeds",
            "state": "West Yorkshire",
            "postcode": "LS1 4DY",
            "country": "United Kingdom",
        },
        "trading_same_as_registered_address": True,
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 200, resp.text
    org = resp.json()["data"]["organization"]
    assert org["reg_address_line_1"] == "99 Trade Street"
    assert org["trading_address_line_1"] == "99 Trade Street"
    assert org["trading_address_city"] == "Leeds"
    assert "eori_number" in org


@pytest.mark.asyncio
async def test_profile_trading_same_and_trading_body_rejected(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    payload = {
        "trading_same_as_registered_address": True,
        "trading_address": {
            "address_line_1": "1 Other",
            "city": "London",
            "postcode": "EC1A 1BB",
            "country": "United Kingdom",
        },
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["success"] is False
    details = body.get("error", {}).get("details") or []
    flat = json.dumps(details)
    assert "trading_same_as_registered_address" in flat or "trading_address" in flat


@pytest.mark.asyncio
async def test_profile_pickup_same_as_flags_mutually_exclusive_rejected(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    payload = {
        "pickup_addresses": [
            {
                "label": "Bad Pickup",
                "same_as_registered_address": True,
                "same_as_trading_address": True,
                "is_default": True,
            }
        ]
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 422, resp.text
    details = resp.json().get("error", {}).get("details") or []
    flat = json.dumps(details)
    assert "same_as_registered_address" in flat and "same_as_trading_address" in flat


@pytest.mark.asyncio
async def test_profile_pickup_manual_fields_required_when_same_as_flags_false(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    payload = {
        "pickup_addresses": [
            {
                "label": "Bad Manual Pickup",
                "same_as_registered_address": False,
                "same_as_trading_address": False,
                "is_default": True,
            }
        ]
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 422, resp.text
    details = resp.json().get("error", {}).get("details") or []
    flat = json.dumps(details)
    assert "line_1" in flat or "city" in flat or "postcode" in flat


@pytest.mark.asyncio
async def test_profile_pickup_same_as_registered_rejected_when_org_registered_missing(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    sample_org.reg_address_line_1 = None
    sample_org.reg_city = None
    sample_org.reg_postcode = None
    sample_org.reg_country = None
    await db_session.flush()

    payload = {
        "pickup_addresses": [
            {
                "label": "Same As Registered",
                "same_as_registered_address": True,
                "is_default": True,
            }
        ]
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 422, resp.text
    flat = json.dumps(resp.json().get("error", {}).get("details") or [])
    assert "same as registered" in flat.lower() and "incomplete" in flat.lower()


@pytest.mark.asyncio
async def test_profile_pickup_same_as_trading_rejected_when_org_trading_missing(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    owner, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    sample_org.trading_address_line_1 = None
    sample_org.trading_address_city = None
    sample_org.trading_address_postcode = None
    sample_org.trading_address_country = None
    await db_session.flush()

    payload = {
        "pickup_addresses": [
            {
                "label": "Same As Trading",
                "same_as_trading_address": True,
                "is_default": True,
            }
        ]
    }
    resp = await client_real_acl.patch(
        f"{ORGS}/{sample_org.id}/profile",
        data={"payload": json.dumps(payload)},
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 422, resp.text
    flat = json.dumps(resp.json().get("error", {}).get("details") or [])
    assert "same as trading" in flat.lower() and "incomplete" in flat.lower()


@pytest.mark.asyncio
async def test_profile_completion_receiver_notifications_requires_saved_prefs(
    client: AsyncClient,
    admin_headers: dict,
    db_session: AsyncSession,
    sample_org: Organization,
) -> None:
    """Checklist marks receiver notifications complete only after a RECIPIENT org-pref row exists."""
    from app.modules.notifications.enums import NotificationEvent, NotificationType
    from app.modules.notifications.repository import OrgNotificationPreferenceRepository

    comp_before = await client.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=admin_headers)
    assert comp_before.status_code == 200
    assert _receiver_notifications_item(comp_before.json())["completed"] is False

    await OrgNotificationPreferenceRepository(db_session).upsert(
        organization_id=sample_org.id,
        notification_type=NotificationType.RECIPIENT,
        event=NotificationEvent.RECIPIENT_DELIVERED,
        values={"email_enabled": True},
    )
    await db_session.commit()

    comp_after = await client.get(f"{ORGS}/{sample_org.id}/profile-completion", headers=admin_headers)
    assert comp_after.status_code == 200
    assert _receiver_notifications_item(comp_after.json())["completed"] is True


@pytest.mark.asyncio
async def test_pickup_addresses_list_requires_org_profile_read(
    client_real_acl: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    sample_org: Organization,
) -> None:
    await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.ACCOUNT_OWNER.value, is_primary=True)
    billing_user, _ = await _make_contact(db_session, user_factory, sample_org, contact_role=ContactRole.BILLING.value)
    resp = await client_real_acl.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=_b2b_headers(billing_user))
    assert resp.status_code == 403
