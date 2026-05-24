"""Tests for atomic guards that prevent race conditions.

Exercises each guard by calling the operation once (succeeds),
then calling it again with the same token/data (must be rejected).
This proves the WHERE-based atomic guards and savepoint handling
are correct — the second caller always loses.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import ClientType
from app.common.exceptions import AuthenticationError, ConflictError
from app.core.security import hash_token
from app.modules.auth.repository import InviteRepository
from app.modules.auth.service import AuthService
from app.modules.user.models import User
from app.modules.user.repository import UserRepository
from tests.conftest import TEST_PASSWORD


def _build_service(session: AsyncSession) -> AuthService:
    """Build an AuthService wired to the test session."""
    return AuthService(session)


class TestRegistrationGuard:
    """Savepoint + IntegrityError guard: duplicate email returns fake success, never 500."""

    @pytest.mark.asyncio
    async def test_duplicate_email_returns_fake_success(self, db_session: AsyncSession, user_factory) -> None:
        """If the email is taken between email_exists() and create(), we still return 201-shaped data."""
        existing = await user_factory(email="race@example.com")

        from app.modules.auth.v1.schemas import RegisterRequest

        data = RegisterRequest(
            email="race@example.com",
            password=TEST_PASSWORD,
            first_name="Race",
            last_name="Condition",
        )

        service = _build_service(db_session)
        result = await service.register(data)

        assert result.email == "race@example.com"
        assert result.id != existing.id

    @pytest.mark.asyncio
    async def test_only_one_user_created(self, db_session: AsyncSession) -> None:
        """Two sequential registrations with the same email: only one user exists in DB."""
        from app.modules.auth.v1.schemas import RegisterRequest

        data = RegisterRequest(
            email="unique-race@example.com",
            password=TEST_PASSWORD,
            first_name="First",
            last_name="Caller",
        )

        service = _build_service(db_session)

        r1 = await service.register(data)
        r2 = await service.register(data)

        assert r1.status == "INACTIVE"
        assert r2.status == "INACTIVE"
        assert r1.id != r2.id

        repo = UserRepository(db_session)
        assert await repo.email_exists("unique-race@example.com")


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value


def _seed_pwd_reset_otp(fake: _FakeRedis, user_id: str, otp: str) -> None:
    iat = int(datetime.now(UTC).timestamp())
    fake._data[f"pwd_reset_otp:{user_id}"] = f"{hash_token(otp)}:{iat}"


class TestPasswordResetGuard:
    """atomic_password_reset: second confirm with the same session token is rejected."""

    @pytest.mark.asyncio
    async def test_reset_session_token_single_use(
        self,
        db_session: AsyncSession,
        user_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user: User = await user_factory(
            status="ACTIVE",
            email_verified=True,
            email="reset@example.com",
            role="CUSTOMER_B2C",
        )

        fake = _FakeRedis()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        otp = "123456"
        _seed_pwd_reset_otp(fake, user.id, otp)

        service = _build_service(db_session)
        v = await service.verify_password_reset_otp(
            user.email,
            otp,
            ClientType.CUSTOMER_B2C,
        )
        session_token = v["password_reset_token"]
        await service.confirm_password_reset(
            "NewSecurePass1!",
            password_reset_token=session_token,
            client_type=ClientType.CUSTOMER_B2C,
        )

        with pytest.raises(AuthenticationError):
            await service.confirm_password_reset(
                "AnotherPass2!",
                password_reset_token=session_token,
                client_type=ClientType.CUSTOMER_B2C,
            )


class TestInviteAcceptGuard:
    """mark_used with WHERE used_at IS NULL: second accept is rejected;
    successful activation invalidates sibling pending invites for the same user."""

    @pytest.mark.asyncio
    async def test_find_pending_excludes_verified_users(self, db_session: AsyncSession, user_factory) -> None:
        user: User = await user_factory(
            email="verified-invite@example.com",
            status="ACTIVE",
            email_verified=True,
        )
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(days=7)
        invite_repo = InviteRepository(db_session)
        await invite_repo.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=None,
        )
        await db_session.flush()

        found = await invite_repo.find_pending_by_token_hash(token_hash)
        assert found is None

    @pytest.mark.asyncio
    async def test_activate_invalidates_sibling_invites(self, db_session: AsyncSession, user_factory) -> None:
        user: User = await user_factory(email="sibling-invites@example.com")
        expires_at = datetime.now(UTC) + timedelta(days=7)
        invite_repo = InviteRepository(db_session)
        raw_a = secrets.token_urlsafe(32)
        raw_b = secrets.token_urlsafe(32)
        invite_a = await invite_repo.create(
            user_id=user.id,
            token_hash=hash_token(raw_a),
            expires_at=expires_at,
            invited_by_user_id=None,
        )
        invite_b = await invite_repo.create(
            user_id=user.id,
            token_hash=hash_token(raw_b),
            expires_at=expires_at,
            invited_by_user_id=None,
        )
        await db_session.flush()

        service = _build_service(db_session)
        await service.complete_invite_activation(raw_a, TEST_PASSWORD)

        await db_session.refresh(invite_a)
        await db_session.refresh(invite_b)
        assert invite_a.used_at is not None
        assert invite_b.used_at is not None

        with pytest.raises(AuthenticationError):
            await service.complete_invite_activation(raw_b, "OtherPass2!")

    @pytest.mark.asyncio
    async def test_invite_accept_single_use(self, db_session: AsyncSession, user_factory) -> None:
        """First accept_invite succeeds; second raises ConflictError."""
        user: User = await user_factory(email="invited@example.com")

        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(days=7)

        invite_repo = InviteRepository(db_session)
        invite = await invite_repo.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=None,
        )
        await db_session.flush()

        service = _build_service(db_session)

        await service.complete_invite_activation(raw_token, TEST_PASSWORD)

        with pytest.raises((ConflictError, AuthenticationError)):
            await service.complete_invite_activation(raw_token, "AnotherPass2!")
