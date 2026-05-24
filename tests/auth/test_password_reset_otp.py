"""Password reset: verify OTP (session token) then confirm with Redis."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import ClientType
from app.common.exceptions import AuthenticationError
from app.core.security import hash_token, verify_password
from app.modules.auth.service import AuthService
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value


def _seed_otp(fake: _FakeRedis, user_id: str, otp: str) -> None:
    iat = int(datetime.now(UTC).timestamp())
    key = f"pwd_reset_otp:{user_id}"
    fake._data[key] = f"{hash_token(otp)}:{iat}"


@pytest.mark.asyncio
async def test_verify_then_confirm_succeeds(
    db_session: AsyncSession,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user: User = await user_factory(
        status="ACTIVE",
        email_verified=True,
        email="otp-flow@example.com",
        role="CUSTOMER_B2C",
    )
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    otp = "123456"
    _seed_otp(fake, user.id, otp)

    service = AuthService(db_session)
    out = await service.verify_password_reset_otp(
        user.email,
        otp,
        ClientType.CUSTOMER_B2C,
    )
    assert "password_reset_token" in out
    assert len(out["password_reset_token"]) == 64
    assert f"pwd_reset_otp:{user.id}" not in fake._data

    await service.confirm_password_reset(
        TEST_PASSWORD,
        password_reset_token=out["password_reset_token"],
        client_type=ClientType.CUSTOMER_B2C,
    )
    await db_session.refresh(user)
    assert verify_password(TEST_PASSWORD, user.password_hash)


@pytest.mark.asyncio
async def test_wrong_otp_rejected(
    db_session: AsyncSession,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user: User = await user_factory(
        status="ACTIVE",
        email_verified=True,
        email="otp-bad@example.com",
        role="CUSTOMER_B2C",
    )
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    _seed_otp(fake, user.id, "111111")

    service = AuthService(db_session)
    with pytest.raises(AuthenticationError):
        await service.verify_password_reset_otp(
            user.email,
            "999999",
            ClientType.CUSTOMER_B2C,
        )
    assert f"pwd_reset_otp:{user.id}" in fake._data
