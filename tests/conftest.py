"""Pytest fixtures for SW Couriers test suite.

Provides:
- Async DB session with per-test transaction rollback
- httpx.AsyncClient wired to the ASGI app (no network needed)
- Factory helpers: user_factory, verified_user, auth tokens
"""

import asyncio
import contextlib
import os
import uuid
from collections.abc import AsyncGenerator, Coroutine
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Load env files so tests get DB URL and secrets. .env first, then .env.local overrides.
# Keep tests on an isolated DB when DATABASE_URL is not explicitly configured.
load_dotenv(".env", override=False)
load_dotenv(".env.local", override=False)

# ── Set test environment before any app imports ──────────
# Force test overrides so they are not overridden by local config.
os.environ["APP_ENV"] = "test"
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-minimum-32-characters-long")
os.environ.setdefault("JWT_REFRESH_SECRET_KEY", "test-jwt-refresh-secret-minimum-32-characters")
# Default to a dedicated test DB to avoid accidental writes to dev data.
# Override DATABASE_URL in CI/local env when needed.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://swcouriers:swcouriers_local@localhost:5432/swcouriers_test",
)


from app.core.database import get_db_session  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.drivers.enums import DriverAccountStatus  # noqa: E402
from app.modules.drivers.models import Driver  # noqa: E402
from app.modules.organizations.models import Organization  # noqa: E402
from app.common.enums import UserRole  # noqa: E402
from app.modules.admins.models import Admin  # noqa: E402
from app.modules.service_tiers.constants import SUPERFAST_AVAILABLE_FOR, SUPERFAST_TIER_NAME
from app.modules.service_tiers.enums import ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier
from app.modules.user.models import User  # noqa: E402

_TEST_DB_URL = os.environ["DATABASE_URL"]

# ── Shared DB engine (session scope) ─────────────────────────────────────────
# Creating/disposing an engine per test dominated suite runtime.


@pytest.fixture(scope="session")
def shared_async_engine():
    """Single AsyncEngine for all tests — avoids per-test engine creation.

    Uses NullPool so connections are not pooled across pytest-asyncio event loops
    (pooled asyncpg connections pin to the loop where they were opened).
    """

    engine = create_async_engine(_TEST_DB_URL, echo=False, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


# Default test password that satisfies all strength validators
TEST_PASSWORD = "SecureTestPass1!"
_TEST_PASSWORD_HASH: str | None = None


class _FakeRedis:
    """Minimal in-memory Redis stand-in for API integration tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:  # noqa: ARG002
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self._data[key] = value

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0

    async def incr(self, key: str) -> int:
        current = int(self._data.get(key, "0")) + 1
        self._data[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return key in self._data

    async def ttl(self, key: str) -> int:
        return -1 if key not in self._data else 300

    async def enqueue_job(self, *args: object, **kwargs: object) -> None:
        """No-op stand-in for ARQ pool.enqueue_job in integration tests."""
        return None


def _get_test_password_hash() -> str:
    """Lazily hash the test password (Argon2id is intentionally slow)."""
    global _TEST_PASSWORD_HASH
    if _TEST_PASSWORD_HASH is None:
        _TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)
    return _TEST_PASSWORD_HASH


# ── Fixtures ─────────────────────────────────────


@pytest_asyncio.fixture
async def db_session(shared_async_engine) -> AsyncGenerator[AsyncSession]:
    """Async DB session for tests.

    Each test gets a fresh AsyncSession. The app can freely use commit()/rollback()
    inside requests without conflicting with an outer transaction context.
    For isolation, point DATABASE_URL at a dedicated test database.
    """
    async with shared_async_engine.connect() as conn:
        # Outer transaction: always rolled back at fixture teardown.
        outer_tx = await conn.begin()
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            autoflush=False,
        ) as session:
            # Use a SAVEPOINT so tests/app code can call commit() safely.
            await session.begin_nested()

            @event.listens_for(session.sync_session, "after_transaction_end")
            def _restart_savepoint(sync_session, transaction):  # type: ignore[no-untyped-def]
                # When the nested transaction ends (e.g. by commit), reopen it.
                if transaction.nested and not transaction._parent.nested:
                    sync_session.begin_nested()

            try:
                yield session
            finally:
                with contextlib.suppress(Exception):
                    await session.close()
                with contextlib.suppress(Exception):
                    await outer_tx.rollback()
    # Engine disposed by shared_async_engine session fixture teardown.


@pytest_asyncio.fixture
async def redis_mock() -> AsyncGenerator[_FakeRedis]:
    """Provide in-memory Redis for code paths that call app.core.redis.get_redis()."""
    fake = _FakeRedis()
    with patch("app.core.redis.get_redis", lambda: fake):
        yield fake


@pytest_asyncio.fixture
async def auth_blacklist_mocks(redis_mock: _FakeRedis):  # noqa: ARG001

    """Patch Redis-backed auth gates so API tests need no Redis.

    Covers token blacklist, session revocation, and fast-path user suspension marker.
    Yields (blacklist_token_mock, is_token_blacklisted_mock). Logout tests can
    assert the mock was called to confirm the code path runs. Real blacklist
    behavior is validated by unit tests when Redis is available.

    """
    with (
        patch("app.common.deps.is_token_blacklisted", new_callable=AsyncMock, return_value=False) as m1,
        patch("app.common.deps.is_session_revoked", new_callable=AsyncMock, return_value=False) as m1b,
        patch("app.common.deps.is_user_suspended", new_callable=AsyncMock, return_value=False) as m1c,
        patch("app.modules.auth.service.is_user_suspended", new_callable=AsyncMock, return_value=False),
        patch("app.modules.auth.service.mark_session_revoked", new_callable=AsyncMock, return_value=None) as m3,
        patch("app.modules.auth.service.blacklist_token", new_callable=AsyncMock, return_value=None) as m2,
    ):
        yield (m2, m1, m1b, m3)


# Skip permission DB/cache lookups in API tests (big speedup for driver and other ACL endpoints).
@pytest_asyncio.fixture
async def permission_mock():
    """Mock PermissionService.check_permission so requests don't hit DB/Redis for ACL checks."""
    with patch(
        "app.modules.permission.service.PermissionService.check_permission",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield


@pytest.fixture(scope="module")
def app():
    """One app instance per test module to avoid repeated create_app() cost."""
    return create_app()


@pytest_asyncio.fixture
async def client(
    app: Any,
    db_session: AsyncSession,
    auth_blacklist_mocks: Any,
    permission_mock: Any,
) -> AsyncGenerator[AsyncClient]:
    """httpx.AsyncClient wired to the FastAPI ASGI app.

    Overrides get_db_session to use the test session. Mocks auth blacklist and
    permission checks so no Redis/extra DB lookups. No network — ASGITransport.
    """

    async def _override_get_db_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver/api") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_real_permissions(
    app: Any,
    db_session: AsyncSession,
    auth_blacklist_mocks: Any,
) -> AsyncGenerator[AsyncClient]:
    """Like ``client`` but enforces real PermissionService ACL (no permission_mock)."""
    async def _override_get_db_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver/api") as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Factory fixtures ─────────────────────────────


@pytest_asyncio.fixture
async def user_factory(db_session: AsyncSession):
    """Factory to create test users with sensible defaults.

    Usage::

        user = await user_factory(email="test@example.com", role="CUSTOMER_B2C")
        user = await user_factory(status="ACTIVE", email_verified=True)
    """
    _counter = 0

    async def _create(**overrides) -> User:
        nonlocal _counter
        _counter += 1
        defaults = {
            "email": f"testuser{_counter}_{uuid.uuid4().hex[:12]}@example.com",
            "password_hash": _get_test_password_hash(),
            "first_name": "Test",
            "last_name": f"User{_counter}",
            "role": "CUSTOMER_B2C",
            "status": "INACTIVE",
            "force_password_change": False,
            "failed_login_attempts": 0,
            "email_verified": False,
        }
        defaults.update(overrides)
        user = User(**defaults)
        db_session.add(user)
        await db_session.flush()
        role_val = defaults["role"]
        if role_val in (UserRole.ADMIN, UserRole.SUPER_ADMIN) or role_val in ("ADMIN", "SUPER_ADMIN"):
            db_session.add(
                Admin(
                    user_id=user.id,
                    address_line_1="1 Test Street",
                    city="London",
                    state="England",
                    postcode="EC1A 1BB",
                )
            )
            await db_session.flush()
        await db_session.refresh(user)
        return user

    return _create


@pytest_asyncio.fixture
async def org_factory(db_session: AsyncSession):
    """Create Organization rows for tests outside tests/organizations/ (bookings, pickup addresses)."""

    async def _create(**overrides) -> Organization:
        ref_suffix = uuid.uuid4().hex[:16]
        defaults = {
            "reference": f"T{ref_suffix}"[:20],
            "trading_name": f"Test Org {ref_suffix[:6]}",
            "legal_entity_name": f"Test Org {ref_suffix[:6]} Limited",
            "companies_house_number": f"CH{ref_suffix[:8]}",
            "vat_number": f"GB{ref_suffix[:9]}",
            "date_of_incorporation": date(2020, 1, 1),
            "industry": "OTHER",
            "company_size": "1-10 employees",
            "reg_address_line_1": "1 Test Street",
            "reg_city": "London",
            "reg_postcode": "EC1A 1BB",
            "status": "ACTIVE",
        }
        defaults.update(overrides)
        org = Organization(**defaults)
        db_session.add(org)
        await db_session.flush()
        await db_session.refresh(org)
        return org

    return _create


@pytest_asyncio.fixture
async def verified_user(user_factory) -> User:
    """A pre-created active, email-verified B2C customer."""
    return await user_factory(status="ACTIVE", email_verified=True)


@pytest_asyncio.fixture
async def driver_user(user_factory) -> User:
    """A pre-created active, email-verified driver account."""
    return await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")


@pytest_asyncio.fixture
async def driver_user_with_profile(user_factory, db_session: AsyncSession) -> User:
    """A driver account with a linked `drivers` profile row.

    Used by auth tests that expect driver logins to succeed only when a driver
    profile exists and is ACTIVE/PENDING_ACTIVATION.
    """
    user = await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")
    driver = Driver(
        user_id=user.id,
        driver_code=f"DR-{user.id[:6].upper()}",
        account_status=DriverAccountStatus.ACTIVE,
    )
    db_session.add(driver)
    await db_session.flush()
    await db_session.refresh(driver)
    return user


@pytest_asyncio.fixture
async def verified_user_token(verified_user: User) -> str:
    """A valid JWT access token for the verified_user (CUSTOMER_B2C client type)."""
    token, _ = create_access_token(
        user_id=verified_user.id,
        role=verified_user.role,
        client_type="CUSTOMER_B2C",
        region_id=verified_user.region_id,
        organization_id=verified_user.organization_id,
    )
    return token


@pytest_asyncio.fixture
async def auth_headers(verified_user_token: str) -> dict[str, str]:
    """Authorization headers with a valid Bearer token and X-Client-Type for verified_user."""
    return {
        "Authorization": f"Bearer {verified_user_token}",
        "X-Client-Type": "CUSTOMER_B2C",
    }


@pytest_asyncio.fixture
async def admin_user(user_factory) -> User:
    """A pre-created active, email-verified admin account."""
    return await user_factory(status="ACTIVE", email_verified=True, role="ADMIN")


@pytest_asyncio.fixture
async def admin_headers(admin_user: User) -> dict[str, str]:
    """Authorization headers with a valid Bearer token for admin user."""
    token, _ = create_access_token(
        user_id=admin_user.id,
        role=admin_user.role,
        client_type="ADMIN",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


@pytest_asyncio.fixture
async def superfast_global_tier(db_session: AsyncSession) -> ServiceTier:
    """Ensure the system Superfast GLOBAL tier exists for integration tests."""
    stmt = select(ServiceTier).where(
        ServiceTier.scope_type == ServiceTierScopeType.GLOBAL.value,
        ServiceTier.scope_org_id.is_(None),
        ServiceTier.tier_name == SUPERFAST_TIER_NAME,
        ServiceTier.available_for == SUPERFAST_AVAILABLE_FOR,
    )
    existing = (await db_session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    tier = ServiceTier(
        tier_name=SUPERFAST_TIER_NAME,
        description="Express delivery tier",
        duration_days=1,
        error_margin_kg=0,
        price_per_kg=Decimal("0"),
        price_per_package=Decimal("125.00"),
        base_price=Decimal("0"),
        scope_type=ServiceTierScopeType.GLOBAL.value,
        scope_org_id=None,
        available_for=SUPERFAST_AVAILABLE_FOR,
        color="#E63946",
        icon="bolt",
        status=ServiceTierStatus.ACTIVE,
    )
    db_session.add(tier)
    await db_session.flush()
    return tier


# ── Concurrency helpers ──────────────────────────


async def run_concurrently(*coros: Coroutine[Any, Any, Any]) -> list[Any | BaseException]:
    """Run N coroutines concurrently, returning results or exceptions.

    Usage in tests::

        results = await run_concurrently(
            service.accept_invite(token, "Pass1!"),
            service.accept_invite(token, "Pass2!"),
        )
        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
    """
    return await asyncio.gather(*coros, return_exceptions=True)
