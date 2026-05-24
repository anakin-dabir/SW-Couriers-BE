"""Integration tests for the invite activation flow.

The web app reads ?token= on the landing URL, then calls the API with X-Invite-Token.

Flow:
  1. POST /auth/invites/validate → prefill (no second email / OTP)
  2. POST /auth/invites/activate with JSON { password }

All invite API calls use X-Invite-Token.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.modules.auth.models import Invite
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD

AUTH = "/v1/auth"


def _invite_headers(token: str) -> dict[str, str]:
    return {"X-Invite-Token": token}


async def _create_invite(db_session: AsyncSession, user: User, days: int = 7) -> tuple[Invite, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    invite = Invite(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=days),
    )
    db_session.add(invite)
    await db_session.flush()
    await db_session.refresh(invite)
    return invite, raw_token


class TestValidateInvite:
    @pytest.mark.asyncio
    async def test_validate_returns_prefill_without_otp(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(
            email="invite1@example.com",
            first_name="Alice",
            last_name="Smith",
            role="CUSTOMER_B2B",
            status="INACTIVE",
            email_verified=False,
        )
        invite, raw_token = await _create_invite(db_session, user)

        resp = await client.post(f"{AUTH}/invites/validate", headers=_invite_headers(raw_token))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["email"] == "invite1@example.com"
        assert data["first_name"] == "Alice"
        assert data["last_name"] == "Smith"
        assert data["role"] == "CUSTOMER_B2B"

        await db_session.refresh(invite)
        assert invite.verification_code_hash is None
        assert invite.code_expires_at is None

    @pytest.mark.asyncio
    async def test_validate_invalid_token_returns_401(self, client: AsyncClient) -> None:
        unknown = secrets.token_urlsafe(32)
        resp = await client.post(f"{AUTH}/invites/validate", headers=_invite_headers(unknown))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_validate_expired_token_returns_401(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(email="expired@example.com", status="INACTIVE", email_verified=False)
        _, raw_token = await _create_invite(db_session, user, days=-1)

        resp = await client.post(f"{AUTH}/invites/validate", headers=_invite_headers(raw_token))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_validate_can_be_called_multiple_times(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(email="revalidate@example.com", status="INACTIVE", email_verified=False)
        _, raw_token = await _create_invite(db_session, user)

        r1 = await client.post(f"{AUTH}/invites/validate", headers=_invite_headers(raw_token))
        r2 = await client.post(f"{AUTH}/invites/validate", headers=_invite_headers(raw_token))
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["data"] == r2.json()["data"]


class TestActivateInvite:
    @pytest.mark.asyncio
    async def test_activate_sets_password_and_email_verified(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(email="act1@example.com", status="INACTIVE", email_verified=False)
        _, raw_token = await _create_invite(db_session, user)

        resp = await client.post(
            f"{AUTH}/invites/activate",
            headers=_invite_headers(raw_token),
            json={"password": TEST_PASSWORD},
        )
        assert resp.status_code == 201

        await db_session.refresh(user)
        assert user.email_verified is True
        assert user.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_activate_without_prior_validate_succeeds(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(email="act_direct@example.com", status="INACTIVE", email_verified=False)
        _, raw_token = await _create_invite(db_session, user)

        resp = await client.post(
            f"{AUTH}/invites/activate",
            headers=_invite_headers(raw_token),
            json={"password": TEST_PASSWORD},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_activate_twice_returns_401_or_409(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        user = await user_factory(email="act3@example.com", status="INACTIVE", email_verified=False)
        _, raw_token = await _create_invite(db_session, user)

        r1 = await client.post(
            f"{AUTH}/invites/activate",
            headers=_invite_headers(raw_token),
            json={"password": TEST_PASSWORD},
        )
        assert r1.status_code == 201
        r2 = await client.post(
            f"{AUTH}/invites/activate",
            headers=_invite_headers(raw_token),
            json={"password": TEST_PASSWORD},
        )
        assert r2.status_code in (401, 409)
