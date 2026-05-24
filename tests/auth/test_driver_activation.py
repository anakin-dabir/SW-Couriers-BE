"""Driver activation (deep link) public API tests.

The mobile/Web client reads ``token`` (and optionally ``email``) from the landing URL query string,
then calls this API with header ``X-Invite-Token`` only — never with the token in the JSON body.

Test flow:
  1. ``POST …/driver-activation/validate`` — 200 + ``data.valid`` / ``data.reason`` (token not consumed).
  2. ``POST …/driver-activation/set-password`` — JSON ``{ \"password\": \"…\" }`` + ``X-Invite-Token`` — 201.
  3. ``POST …/auth/login`` with ``X-Client-Type: DRIVER``.
  Optional: ``POST …/driver-activation/resend`` with ``{ \"email\": \"…\" }`` — always 200; enqueue only if eligible.

Run focused suite:

    poetry run pytest tests/auth/test_driver_activation.py -m driver_activation -v
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import Job
from app.core.security import hash_token, verify_password
from app.modules.auth.models import Invite
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD

DRIVERS = "/v1/drivers"
AUTH = "/v1/auth"

pytestmark = pytest.mark.driver_activation


def _invite_headers(token: str) -> dict[str, str]:
    return {"X-Invite-Token": token}


async def _pending_driver_activation_invite(
    db_session: AsyncSession,
    user_factory,
    *,
    expire_days: int = 7,
    email_verified: bool = False,
) -> tuple[User, Invite, str]:
    """Seed a driver awaiting activation plus a matching Invite row (raw secret token returned)."""
    suffix = uuid.uuid4().hex[:12]
    user = await user_factory(
        email=f"drv.act.{suffix}@example.com",
        role="DRIVER",
        status="INACTIVE",
        email_verified=email_verified,
        first_name="Deep",
        last_name="Link",
    )
    if not email_verified:
        db_session.add(
            Driver(
                user_id=user.id,
                driver_code=f"DL{suffix.upper()}"[:20],
                account_status=DriverAccountStatus.PENDING_ACTIVATION,
            )
        )
        await db_session.flush()

    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    invite = Invite(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=expire_days),
    )
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite)
    return user, invite, raw_token


async def _inactive_customer_invite(
    db_session: AsyncSession,
    user_factory,
    *,
    expire_days: int = 7,
) -> tuple[User, str]:
    suffix = uuid.uuid4().hex[:12]
    user = await user_factory(
        email=f"cust.inv.{suffix}@example.com",
        role="CUSTOMER_B2C",
        status="INACTIVE",
        email_verified=False,
    )
    raw_token = secrets.token_urlsafe(32)
    invite = Invite(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(days=expire_days),
    )
    db_session.add(invite)
    await db_session.flush()
    return user, raw_token


def _fake_redis_rate_limit():
    """In-memory INCR so resend hourly cap applies without init_redis() in tests."""

    counters: dict[str, int] = {}

    class _R:
        async def incr(self, key: str) -> int:
            counters[key] = counters.get(key, 0) + 1
            return counters[key]

        async def expire(self, key: str, ttl: int) -> bool:
            return True

    return _R()


@pytest.mark.asyncio
async def test_driver_activation_validate_set_password_login(
    client: AsyncClient,
    user_factory,
    auth_blacklist_mocks,
    db_session,
) -> None:
    """Create driver via admin API → activation job carries link → set password via public API → login."""
    from tests.drivers.test_drivers_api import (  # noqa: PLC0415 — reuse multipart helpers
        _ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
        _admin_headers,
        _licence_files,
    )

    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    captured: list[tuple[object, ...]] = []

    async def capture_enqueue(task_name: str, *args: object, **kwargs: object):
        captured.append((task_name, *args))
        return None

    with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=capture_enqueue):
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                "email": "driver.act@example.com",
                "first_name": "Act",
                "last_name": "Test",
                "phone": "07111111112",
                "state": "England",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "1 Act St",
                "city": "London",
                "postcode": "E1 1AB",
                "max_stops": "30",
                "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
            },
            files=_licence_files(),
        )
    assert resp.status_code == 201
    assert len(captured) == 1
    task_name, invite_id, to_email, _fn, link, *_rest = captured[0]
    assert task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL
    assert to_email == "driver.act@example.com"
    assert invite_id
    parsed = urlparse(str(link))
    qs = parse_qs(parsed.query)
    token = qs["token"][0]
    assert qs["email"][0] == "driver.act@example.com"

    val = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers={"X-Invite-Token": token},
    )
    assert val.status_code == 200
    body = val.json()
    assert body["success"] is True
    assert body["data"]["valid"] is True
    assert body["data"]["email"] == "driver.act@example.com"

    new_pw = "BrandNewSecure9!Pass"
    sp = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers={"X-Invite-Token": token},
        json={"password": new_pw},
    )
    assert sp.status_code == 201

    login = await client.post(
        f"{AUTH}/login",
        json={"email": "driver.act@example.com", "password": new_pw},
        headers={"X-Client-Type": "DRIVER"},
    )
    assert login.status_code == 200
    assert login.json()["data"]["requires_password_change"] is False

    u = await db_session.scalar(select(User).where(User.email == "driver.act@example.com"))
    assert u is not None
    assert u.email_verified is True
    assert verify_password(new_pw, u.password_hash)

    inv = await db_session.scalar(select(Invite).where(Invite.id == invite_id))
    assert inv is not None
    assert inv.used_at is not None


@pytest.mark.asyncio
async def test_driver_activation_resend_rate_limited(
    client: AsyncClient,
    user_factory,
    auth_blacklist_mocks,
) -> None:
    from tests.drivers.test_drivers_api import (  # noqa: PLC0415
        _ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
        _admin_headers,
        _licence_files,
    )

    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    # Redis is not initialized in ASGI tests → patch a fake client so hourly resend cap still runs.
    with (
        patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None),
        patch("app.modules.auth.service.get_redis", return_value=_fake_redis_rate_limit()),
    ):
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data={
                **_ADD_NEW_DRIVER_OPERATIONAL_DEFAULTS,
                "email": "driver.resend@example.com",
                "first_name": "Re",
                "last_name": "Send",
                "phone": "07111111113",
                "state": "England",
                "capacity[0]": "VAN",
                "driver_type": "INTERNAL",
                "address_line1": "2 Resend St",
                "city": "London",
                "postcode": "E1 1AC",
                "max_stops": "30",
                "documents_metadata": '[{"document_type":"DRIVING_LICENCE","expiry_date":"2030-01-01"}]',
            },
            files=_licence_files(),
        )
        assert resp.status_code == 201

        last_status = 200
        for _ in range(8):
            r = await client.post(
                f"{AUTH}/driver-activation/resend",
                json={"email": "driver.resend@example.com"},
            )
            last_status = r.status_code
            if last_status == 429:
                break
        assert last_status == 429


@pytest.mark.asyncio
async def test_driver_activation_validate_requires_invite_header(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    r = await client.post(f"{AUTH}/driver-activation/validate")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_driver_activation_validate_rejects_short_header_token(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    r = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers={"X-Invite-Token": "x" * 39},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_driver_activation_set_password_requires_invite_header(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    r = await client.post(
        f"{AUTH}/driver-activation/set-password",
        json={"password": "ValidPass123!@#"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_driver_activation_set_password_weak_password_422(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    tok = secrets.token_urlsafe(32)
    r = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers={"X-Invite-Token": tok},
        json={"password": "short"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_driver_activation_validate_unknown_token_returns_invalid_payload(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    unknown = secrets.token_urlsafe(32)
    resp = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers=_invite_headers(unknown),
    )
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["valid"] is False
    assert payload["reason"] == "INVALID"


@pytest.mark.asyncio
async def test_driver_activation_validate_expired_reason(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, _, raw = await _pending_driver_activation_invite(db_session, user_factory, expire_days=-1)
    resp = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers=_invite_headers(raw),
    )
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["valid"] is False
    assert payload["reason"] == "EXPIRED"


@pytest.mark.asyncio
async def test_driver_activation_validate_already_activated_reason(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, _, raw = await _pending_driver_activation_invite(db_session, user_factory, email_verified=True)
    resp = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers=_invite_headers(raw),
    )
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["valid"] is False
    assert payload["reason"] == "ALREADY_ACTIVATED"


@pytest.mark.asyncio
async def test_driver_activation_validate_idempotent_twice_same_response(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, _, raw = await _pending_driver_activation_invite(db_session, user_factory)
    r1 = await client.post(f"{AUTH}/driver-activation/validate", headers=_invite_headers(raw))
    r2 = await client.post(f"{AUTH}/driver-activation/validate", headers=_invite_headers(raw))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["data"]["valid"] is True
    assert r2.json()["data"] == r1.json()["data"]


@pytest.mark.asyncio
async def test_driver_activation_validate_non_driver_role_returns_invalid(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, raw = await _inactive_customer_invite(db_session, user_factory)
    resp = await client.post(f"{AUTH}/driver-activation/validate", headers=_invite_headers(raw))
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["valid"] is False
    assert payload["reason"] == "INVALID"


@pytest.mark.asyncio
async def test_driver_activation_validate_invite_header_too_long_returns_422(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    r = await client.post(
        f"{AUTH}/driver-activation/validate",
        headers={"X-Invite-Token": "a" * 257},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_driver_activation_set_password_uses_only_header_invite_token(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, _, raw = await _pending_driver_activation_invite(db_session, user_factory)
    wrong_header = secrets.token_urlsafe(32)
    resp = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers=_invite_headers(wrong_header),
        json={"password": TEST_PASSWORD, "token": raw},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_driver_activation_set_password_unknown_token_returns_401(
    client: AsyncClient,
    auth_blacklist_mocks,
) -> None:
    bogus = secrets.token_urlsafe(32)
    resp = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers=_invite_headers(bogus),
        json={"password": TEST_PASSWORD},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_driver_activation_set_password_second_call_returns_401_or_409(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    _, _, raw = await _pending_driver_activation_invite(db_session, user_factory)
    r1 = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers=_invite_headers(raw),
        json={"password": TEST_PASSWORD},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        f"{AUTH}/driver-activation/set-password",
        headers=_invite_headers(raw),
        json={"password": TEST_PASSWORD},
    )
    assert r2.status_code in (401, 409)


@pytest.mark.asyncio
async def test_driver_activation_resend_enqueues_activation_email_when_eligible(
    client: AsyncClient,
    auth_blacklist_mocks,
    db_session: AsyncSession,
    user_factory,
) -> None:
    captured: list[tuple[str, tuple[object, ...]]] = []

    async def _capture(task_name: str, *args: object, **_kw: object) -> None:
        captured.append((task_name, args))

    user, _invite, _raw = await _pending_driver_activation_invite(db_session, user_factory)
    em = str(user.email).strip().lower()
    with (
        patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, side_effect=_capture),
        patch("app.modules.auth.service.get_redis", return_value=_fake_redis_rate_limit()),
    ):
        r = await client.post(f"{AUTH}/driver-activation/resend", json={"email": em})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert captured, "expected resend to enqueue activation email task"
    task_name, args = captured[-1]
    assert task_name == Job.SEND_DRIVER_ACTIVATION_EMAIL
    assert args[1] == em
