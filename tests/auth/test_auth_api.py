"""Integration API tests ΓÇö full auth flow against real database.

Tests every auth endpoint end-to-end: register, login, refresh, logout,
password change, and /me. Uses per-test transaction rollback for isolation.
"""

from datetime import UTC, datetime, timedelta

import pytest
import jwt
from httpx import AsyncClient
from sqlalchemy import update

from app.core.config import settings
from app.core.security import TokenType, create_access_token, decode_token
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD

AUTH = "/v1/auth"
AUTH_SESSION = "/v1/auth/session"

DRIVER_CLIENT_HEADERS = {"X-Client-Type": "DRIVER"}
WEB_CUSTOMER_HEADERS = {"X-Client-Type": "CUSTOMER_B2C"}
B2B_CUSTOMER_HEADERS = {"X-Client-Type": "CUSTOMER_B2B"}


def _org_payload(tag: str) -> dict:
    return {
        "trading_name": f"Org {tag}",
        "legal_entity_name": f"Org Legal {tag}",
        "industry": IndustryType.LOGISTICS_TRANSPORT,
        "company_size": CompanySize.EMPLOYEES_11_50,
        "date_of_incorporation": datetime.now(UTC).date(),
        "companies_house_number": f"CH-{tag}",
        "reg_address_line_1": "1 Test Street",
        "reg_city": "London",
        "reg_postcode": "E1 1AA",
    }


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  REGISTRATION
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestRegistration:
    """POST /v1/auth/register ΓÇö user self-registration."""

    @pytest.mark.asyncio
    async def test_register_b2c_customer(self, client: AsyncClient) -> None:
        """B2C customer registers successfully with 201."""
        resp = await client.post(
            f"{AUTH}/register",
            json={
                "email": "newcustomer@example.com",
                "password": TEST_PASSWORD,
                "first_name": "Jane",
                "last_name": "Customer",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["email"] == "newcustomer@example.com"
        assert data["role"] == "CUSTOMER_B2C"
        assert "id" in data
        assert "message" in resp.json()

    @pytest.mark.asyncio
    async def test_register_b2b_customer(self, client: AsyncClient) -> None:
        """B2B customer registers successfully."""
        resp = await client.post(
            f"{AUTH}/register",
            json={
                "email": "b2b@company.com",
                "password": TEST_PASSWORD,
                "first_name": "Business",
                "last_name": "User",
                "role": "CUSTOMER_B2B",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["role"] == "CUSTOMER_B2B"

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client: AsyncClient, verified_user: User) -> None:
        """Duplicate email does not leak existence; returns same success shape (no 409)."""
        resp = await client.post(
            f"{AUTH}/register",
            json={
                "email": verified_user.email,
                "password": TEST_PASSWORD,
                "first_name": "Dup",
                "last_name": "User",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["email"] == verified_user.email
        assert "message" in resp.json()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "role",
        ["ADMIN", "WAREHOUSE_STAFF", "DRIVER"],
    )
    async def test_register_staff_role_rejected(self, client: AsyncClient, role: str) -> None:
        """Staff roles are rejected with 422 ΓÇö admin invite required."""
        resp = await client.post(
            f"{AUTH}/register",
            json={
                "email": f"staff-{role}@example.com",
                "password": TEST_PASSWORD,
                "first_name": "Staff",
                "last_name": "User",
                "role": role,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_register_weak_password_rejected(self, client: AsyncClient) -> None:
        """Weak password is rejected with 422."""
        resp = await client.post(
            f"{AUTH}/register",
            json={
                "email": "weak@example.com",
                "password": "short",
                "first_name": "Weak",
                "last_name": "Pass",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_register_missing_fields(self, client: AsyncClient) -> None:
        """Missing required fields returns 422."""
        resp = await client.post(
            f"{AUTH}/register",
            json={"email": "incomplete@example.com"},
        )
        assert resp.status_code == 422


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  LOGIN
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestLogin:
    """POST /v1/auth/login ΓÇö user authentication."""

    @pytest.mark.asyncio
    async def test_login_unverified_user_rejected(self, client: AsyncClient, user_factory) -> None:
        """Unverified user (inactive, email not verified) cannot login."""
        user = await user_factory(status="INACTIVE", email_verified=False)
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401
        # Generic message to prevent enumeration ΓÇö no "verify" or "email" leak
        msg = resp.json()["message"].lower()
        assert "invalid" in msg or "password" in msg

    @pytest.mark.asyncio
    async def test_login_verified_user_success(self, client: AsyncClient, verified_user: User) -> None:
        """Verified user logs in successfully with tokens."""
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        envelope = resp.json()
        assert "tokens" in envelope
        assert envelope["tokens"]["access_token"]
        assert envelope["tokens"]["access_token_expires_in"] == 60 * 60
        assert envelope.get("tokens", {}).get("refresh_token") is None
        assert envelope["data"]["email"] == verified_user.email
        assert envelope["data"]["role"] == "CUSTOMER_B2C"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient, verified_user: User) -> None:
        """Wrong password returns 401."""
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": "WrongPassword123!"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_email(self, client: AsyncClient) -> None:
        """Non-existent email returns 401."""
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": "nobody@example.com", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_inactive_user_rejected(self, client: AsyncClient, user_factory) -> None:
        """Deactivated user cannot login."""
        user = await user_factory(status="INACTIVE", email_verified=True)
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401
        # Generic message to prevent enumeration ΓÇö no "deactivated" leak
        msg = resp.json()["message"].lower()
        assert "invalid" in msg or "password" in msg

    @pytest.mark.asyncio
    async def test_login_locked_user_rejected(self, client: AsyncClient, user_factory) -> None:
        """Locked user cannot login."""
        user = await user_factory(
            status="ACTIVE",
            email_verified=True,
            locked_until=datetime.now(UTC) + timedelta(minutes=15),
        )
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401
        assert "invalid" in resp.json()["message"].lower() or "password" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_login_anti_enumeration(self, client: AsyncClient, verified_user: User) -> None:
        """Wrong email and wrong password return IDENTICAL error messages."""
        wrong_email_resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": "nonexistent@example.com", "password": "Anything123!"},
        )
        wrong_pass_resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": "WrongPass123!"},
        )
        assert wrong_email_resp.json()["message"] == wrong_pass_resp.json()["message"]

    @pytest.mark.asyncio
    async def test_login_response_user_fields(self, client: AsyncClient, verified_user: User) -> None:
        """Login response data is the user object with expected fields."""
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": TEST_PASSWORD},
        )
        user_data = resp.json()["data"]
        expected_fields = {"id", "email", "first_name", "last_name", "role", "created_at"}
        assert expected_fields.issubset(user_data.keys())

    @pytest.mark.asyncio
    async def test_login_b2b_org_suspended_rejected(self, client: AsyncClient, user_factory, db_session) -> None:
        org = Organization(**_org_payload("B2B-LOCK"), status=OrganizationStatus.SUSPENDED)
        db_session.add(org)
        await db_session.flush()
        user = await user_factory(
            role="CUSTOMER_B2B",
            status="ACTIVE",
            email_verified=True,
            organization_id=org.id,
        )
        resp = await client.post(
            f"{AUTH}/login",
            headers=B2B_CUSTOMER_HEADERS,
            json={"email": user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401

# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  ACCOUNT LOCKOUT
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestLockout:
    """Account lockout after MAX_LOGIN_ATTEMPTS failed logins."""

    @pytest.mark.asyncio
    async def test_lockout_after_five_failed_attempts(self, client: AsyncClient, verified_user: User) -> None:
        """Five wrong password attempts lock the account; 6th returns same 401."""
        email = verified_user.email
        wrong = "WrongPassword123!"
        for _ in range(5):
            resp = await client.post(
                f"{AUTH}/login",
                headers=WEB_CUSTOMER_HEADERS,
                json={"email": email, "password": wrong},
            )
            assert resp.status_code == 401
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": email, "password": wrong},
        )
        assert resp.status_code == 401
        msg = resp.json()["message"].lower()
        assert "invalid" in msg or "password" in msg

    @pytest.mark.asyncio
    async def test_locked_user_cannot_login_with_correct_password(self, client: AsyncClient, verified_user: User) -> None:
        """After lockout, even correct password is rejected until lockout expires."""
        email = verified_user.email
        wrong = "WrongPassword123!"
        for _ in range(5):
            await client.post(
                f"{AUTH}/login",
                headers=WEB_CUSTOMER_HEADERS,
                json={"email": email, "password": wrong},
            )
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_after_lockout_expiry_user_can_login(self, client: AsyncClient, user_factory) -> None:
        """User with expired locked_until (and status still active) can log in."""
        from datetime import UTC, datetime, timedelta

        user = await user_factory(
            status="ACTIVE",
            email_verified=True,
            locked_until=datetime.now(UTC) - timedelta(minutes=1),
            failed_login_attempts=5,
        )
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json().get("tokens", {})

    @pytest.mark.asyncio
    async def test_successful_login_resets_failed_attempts(self, client: AsyncClient, verified_user: User) -> None:
        """One failed attempt then success does not lock the account."""
        wrong = "WrongPassword123!"
        await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": wrong},
        )
        resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  TOKEN REFRESH
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestTokenRefresh:
    """POST /v1/auth/refresh ΓÇö token rotation."""

    async def _login(self, client: AsyncClient, email: str) -> dict:
        """Helper: login as driver and return full response (tokens in .tokens, user in .data)."""
        resp = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": email, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        return resp.json()

    @pytest.mark.asyncio
    async def test_refresh_returns_new_tokens(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """Valid refresh token returns a new token pair."""
        login_data = await self._login(client, driver_user_with_profile.email)
        refresh_token = login_data["tokens"]["refresh_token"]
        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {refresh_token}",
            },
        )
        assert resp.status_code == 200
        envelope = resp.json()
        assert "tokens" in envelope
        assert envelope["tokens"]["access_token"]
        assert envelope["tokens"]["refresh_token"]

    @pytest.mark.asyncio
    async def test_refresh_tokens_are_different(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """New tokens must differ from old ones (rotation)."""
        login_data = await self._login(client, driver_user_with_profile.email)
        refresh_token = login_data["tokens"]["refresh_token"]
        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {refresh_token}",
            },
        )
        new_envelope = resp.json()
        assert new_envelope["tokens"]["access_token"] != login_data["tokens"]["access_token"]
        assert new_envelope["tokens"]["refresh_token"] != login_data["tokens"]["refresh_token"]

    @pytest.mark.asyncio
    async def test_old_refresh_token_rejected_after_rotation(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """Used refresh token is revoked ΓÇö replay returns 401."""
        login_data = await self._login(client, driver_user_with_profile.email)
        old_refresh = login_data["tokens"]["refresh_token"]

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_refresh}",
            },
        )
        assert resp.status_code == 200

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_refresh}",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_garbage_refresh_token_rejected(self, client: AsyncClient) -> None:
        """Random string as refresh token returns 401."""
        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": "Bearer not-a-valid-jwt-token",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_replay_old_refresh_rejected_without_killing_other_sessions(
        self, client: AsyncClient, driver_user_with_profile: User
    ) -> None:
        """Stale refresh after rotation returns 401; other active refresh rows stay valid.

        Intentional revokes (rotation, logout, logout-other) must not trigger a
        blanket revoke-all when the client retries an old refresh token.
        """
        login_data = await self._login(client, driver_user_with_profile.email)
        old_refresh = login_data["tokens"]["refresh_token"]

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_refresh}",
            },
        )
        assert resp.status_code == 200
        new_data = resp.json()

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_refresh}",
            },
        )
        assert resp.status_code == 401

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {new_data['tokens']['refresh_token']}",
            },
        )
        assert resp.status_code == 200

    # True concurrent refresh (two in-flight requests) is not exercised via httpx +
    # `dependency_overrides[get_db_session]` because tests use one shared AsyncSession;
    # SQLAlchemy rejects nested/concurrent flushes ("Session is already flushing").
    # Production uses one session per request. Sequential replay coverage is above.


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  LOGOUT
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestLogout:
    """POST /v1/auth/session/logout and /v1/auth/session/logout-all (refresh token only)."""

    @pytest.mark.asyncio
    async def test_single_logout(self, client: AsyncClient, driver_user_with_profile: User, auth_blacklist_mocks) -> None:
        """Logout revokes the provided refresh token and blacklists the paired access token."""
        blacklist_token_mock = auth_blacklist_mocks[0]
        login_resp = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens = login_resp.json()["tokens"]

        resp = await client.post(
            f"{AUTH_SESSION}/logout",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens['refresh_token']}",
            },
        )
        assert resp.status_code == 200
        blacklist_token_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_fails_after_logout(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """After logout, the refresh token is invalid."""
        login_resp = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens = login_resp.json()["tokens"]

        await client.post(
            f"{AUTH_SESSION}/logout",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens['refresh_token']}",
            },
        )

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens['refresh_token']}",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_all_requires_refresh_token(self, client: AsyncClient) -> None:
        """Logout-all without auth headers returns 401 (missing X-Client-Type ΓåÆ AuthenticationError)."""
        resp = await client.post(f"{AUTH_SESSION}/logout-all")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_all_revokes_all_sessions(self, client: AsyncClient, driver_user_with_profile: User, auth_blacklist_mocks) -> None:
        """Logout-all uses refresh token; revokes all refresh tokens for the user."""
        blacklist_token_mock = auth_blacklist_mocks[0]
        login1 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        login2 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens1 = login1.json()["tokens"]
        tokens2 = login2.json()["tokens"]

        resp = await client.post(
            f"{AUTH_SESSION}/logout-all",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['refresh_token']}",
            },
        )
        assert resp.status_code == 200
        assert blacklist_token_mock.await_count >= 1

        resp1 = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['refresh_token']}",
            },
        )
        resp2 = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens2['refresh_token']}",
            },
        )
        assert resp1.status_code == 401
        assert resp2.status_code == 401


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  GET /ME
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestSessionsManagement:
    """Device-session UX endpoints under `/v1/auth/session`."""

    def _strip_sid_sv(self, access_token: str) -> str:
        payload = decode_token(access_token, TokenType.ACCESS)
        payload.pop("sid", None)
        payload.pop("sv", None)
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )

    @pytest.mark.asyncio
    async def test_get_sessions_marks_current(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login1 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens1 = login1.json()["tokens"]

        login2 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens2 = login2.json()["tokens"]

        resp = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
        )
        assert resp.status_code == 200

        items = resp.json()["data"]["items"]
        assert len(items) == 2
        assert sum(1 for i in items if i["current"] is True) == 1

        for item in items:
            assert "session_id" in item
            assert "device_label" in item
            assert isinstance(item["device_label"], str)
            assert "last_seen_at" in item
            assert "inactivity_expires_at" in item
            assert "current" in item

    @pytest.mark.asyncio
    async def test_logout_other_revokes_other_session(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login1 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens1 = login1.json()["tokens"]

        login2 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens2 = login2.json()["tokens"]

        resp = await client.post(
            f"{AUTH_SESSION}/logout-other",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
        )
        assert resp.status_code == 200

        # The revoked device refresh token cannot be used anymore.
        resp_refresh = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens2['refresh_token']}",
            },
        )
        assert resp_refresh.status_code == 401

        # Only the current session remains active.
        resp2 = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
        )
        assert resp2.status_code == 200
        items = resp2.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["current"] is True

    @pytest.mark.asyncio
    async def test_logout_session_revokes_specific_session(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login1 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens1 = login1.json()["tokens"]

        login2 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens2 = login2.json()["tokens"]

        sessions_resp = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
        )
        assert sessions_resp.status_code == 200
        items = sessions_resp.json()["data"]["items"]
        assert len(items) == 2

        other_session_id = next(i["session_id"] for i in items if i["current"] is False)

        resp = await client.post(
            f"{AUTH_SESSION}/logout-session",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
            json={"session_id": other_session_id},
        )
        assert resp.status_code == 200

        resp_refresh = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens2['refresh_token']}",
            },
        )
        assert resp_refresh.status_code == 401

        resp2 = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens1['access_token']}",
            },
        )
        assert resp2.status_code == 200
        items2 = resp2.json()["data"]["items"]
        assert len(items2) == 1
        assert items2[0]["current"] is True

    @pytest.mark.asyncio
    async def test_backcompat_access_token_without_sid_sv_still_allows_logout_other(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        # Login to create real session rows + refresh-token pairings.
        login1 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens1 = login1.json()["tokens"]

        login2 = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        tokens2 = login2.json()["tokens"]

        # Simulate an older access token that doesn't carry sid/sv but keeps the same jti.
        old_access = self._strip_sid_sv(tokens1["access_token"])

        resp = await client.post(
            f"{AUTH_SESSION}/logout-other",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_access}",
            },
        )
        assert resp.status_code == 200

        resp_refresh = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {tokens2['refresh_token']}",
            },
        )
        assert resp_refresh.status_code == 401

        # Session listing still works; `current` might be false because sid is absent.
        resp2 = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {old_access}",
            },
        )
        assert resp2.status_code == 200
        items = resp2.json()["data"]["items"]
        assert len(items) == 1
        assert all(i["current"] is False for i in items)

    @pytest.mark.asyncio
    async def test_get_sessions_returns_device_display_fields(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        access_token = login.json()["tokens"]["access_token"]

        resp = await client.get(
            AUTH_SESSION,
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {access_token}",
            },
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        item = items[0]
        assert isinstance(item["device_label"], str) and item["device_label"]
        assert "browser_family" in item
        assert "os_family" in item
        assert "device_family" in item
        assert isinstance(item["is_mobile"], bool)
        assert isinstance(item["is_tablet"], bool)
        assert isinstance(item["is_pc"], bool)
        assert "location_label" in item

    @pytest.mark.asyncio
    async def test_sid_revoked_is_rejected_on_next_request(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        access_token = login.json()["tokens"]["access_token"]

        # Simulate Redis session revoke marker being set.
        from unittest.mock import AsyncMock, patch

        with patch("app.common.deps.is_session_revoked", new_callable=AsyncMock, return_value=True):
            resp = await client.get(
                f"{AUTH}/me",
                headers={
                    **DRIVER_CLIENT_HEADERS,
                    "Authorization": f"Bearer {access_token}",
                },
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_sv_mismatch_is_rejected(
        self,
        client: AsyncClient,
        db_session,
        verified_user: User,
        auth_headers: dict[str, str],
        auth_blacklist_mocks,
    ) -> None:
        # Create an sv-carrying token for this user.
        token, _ = create_access_token(
            user_id=verified_user.id,
            role=verified_user.role,
            client_type="CUSTOMER_B2C",
            sid="00000000-0000-0000-0000-000000000000",
            sv=0,
        )

        # Make server-side sv different.
        await db_session.execute(update(User).where(User.id == verified_user.id).values(session_sv=1))
        await db_session.flush()

        resp = await client.get(
            AUTH_SESSION,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_session_invalid_uuid_422(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
        auth_blacklist_mocks,
    ) -> None:
        login = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        access_token = login.json()["tokens"]["access_token"]
        resp = await client.post(
            f"{AUTH_SESSION}/logout-session",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {access_token}",
            },
            json={"session_id": "not-a-uuid"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_sessions_requires_auth(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get(AUTH_SESSION, headers={"X-Client-Type": "CUSTOMER_B2C"})
        assert resp.status_code == 401


class TestGetMe:
    """GET /v1/auth/me ΓÇö current user profile."""

    @pytest.mark.asyncio
    async def test_me_with_valid_token(self, client: AsyncClient, verified_user: User, auth_headers: dict) -> None:
        """Valid token returns user profile."""
        resp = await client.get(f"{AUTH}/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["email"] == verified_user.email
        assert data["first_name"] == verified_user.first_name
        assert data["role"] == verified_user.role

    @pytest.mark.asyncio
    async def test_me_without_token(self, client: AsyncClient) -> None:
        """No auth header returns 401."""
        resp = await client.get(f"{AUTH}/me", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_garbage_token(self, client: AsyncClient) -> None:
        """Invalid JWT returns 401."""
        resp = await client.get(
            f"{AUTH}/me",
            headers={"Authorization": "Bearer this.is.garbage", "X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_expired_token(self, client: AsyncClient, verified_user: User) -> None:
        """Expired access token returns 401."""
        import jwt as pyjwt

        from app.core.config import settings

        payload = {
            "sub": verified_user.id,
            "role": verified_user.role,
            "aud": "CUSTOMER_B2C",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "iat": datetime.now(UTC) - timedelta(minutes=16),
            "jti": "expired-jti",
            "type": "access",
        }
        expired_token = pyjwt.encode(
            payload,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithm="HS256",
        )
        resp = await client.get(
            f"{AUTH}/me",
            headers={
                "Authorization": f"Bearer {expired_token}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_suspended_user_blocked(self, client: AsyncClient, user_factory) -> None:
        """Suspended user with valid token is blocked from /me."""
        user = await user_factory(status="SUSPENDED", email_verified=True)
        token, _ = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type="CUSTOMER_B2C",
            region_id=user.region_id,
            organization_id=user.organization_id,
        )
        resp = await client.get(
            f"{AUTH}/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
        )
        assert resp.status_code == 401
        msg = resp.json()["message"].lower()
        assert "suspended" in msg or "not active" in msg

    @pytest.mark.asyncio
    async def test_me_inactive_user_blocked(self, client: AsyncClient, user_factory) -> None:
        """Inactive (deactivated) user with valid token is blocked."""
        user = await user_factory(status="INACTIVE", email_verified=True)
        token, _ = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type="CUSTOMER_B2C",
            region_id=user.region_id,
            organization_id=user.organization_id,
        )
        resp = await client.get(
            f"{AUTH}/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_pending_user_blocked(self, client: AsyncClient, user_factory) -> None:
        """Inactive user with valid token is blocked."""
        user = await user_factory(status="INACTIVE")
        token, _ = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type="CUSTOMER_B2C",
            region_id=user.region_id,
            organization_id=user.organization_id,
        )
        resp = await client.get(
            f"{AUTH}/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_b2b_org_suspended_blocked(self, client: AsyncClient, user_factory, db_session) -> None:
        org = Organization(**_org_payload("B2B-ME"), status=OrganizationStatus.SUSPENDED)
        db_session.add(org)
        await db_session.flush()
        user = await user_factory(
            role="CUSTOMER_B2B",
            status="ACTIVE",
            email_verified=True,
            organization_id=org.id,
        )
        token, _ = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type="CUSTOMER_B2B",
            region_id=user.region_id,
            organization_id=user.organization_id,
        )
        resp = await client.get(
            f"{AUTH}/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client-Type": "CUSTOMER_B2B",
            },
        )
        assert resp.status_code == 401

# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  PASSWORD CHANGE
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestPasswordChange:
    """POST /v1/auth/change-password ΓÇö authenticated password update."""

    @pytest.mark.asyncio
    async def test_change_password_success(self, client: AsyncClient, verified_user: User) -> None:
        """Valid password change returns 200."""
        login_resp = await client.post(
            f"{AUTH}/login",
            headers=WEB_CUSTOMER_HEADERS,
            json={"email": verified_user.email, "password": TEST_PASSWORD},
        )
        body = login_resp.json()
        resp = await client.post(
            f"{AUTH}/change-password",
            headers={
                "Authorization": f"Bearer {body['tokens']['access_token']}",
                "X-Client-Type": "CUSTOMER_B2C",
            },
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "NewSecurePass456!",
            },
        )
        assert resp.status_code == 200
        assert "changed" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, client: AsyncClient, verified_user: User, auth_headers: dict) -> None:
        """Wrong current password returns 401."""
        resp = await client.post(
            f"{AUTH}/change-password",
            headers=auth_headers,
            json={
                "current_password": "WrongCurrent123!",
                "new_password": "NewSecurePass456!",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_change_password_weak_new(self, client: AsyncClient, verified_user: User, auth_headers: dict) -> None:
        """Weak new password is rejected with 422."""
        resp = await client.post(
            f"{AUTH}/change-password",
            headers=auth_headers,
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "weak",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_change_password_revokes_refresh_tokens(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """After password change, old refresh tokens are revoked."""
        login_resp = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        body = login_resp.json()
        await client.post(
            f"{AUTH}/change-password",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {body['tokens']['access_token']}",
            },
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "NewSecurePass456!",
            },
        )

        resp = await client.post(
            f"{AUTH_SESSION}/refresh",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {body['tokens']['refresh_token']}",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_change_password_requires_auth(self, client: AsyncClient) -> None:
        """Password change without auth returns 401."""
        resp = await client.post(
            f"{AUTH}/change-password",
            headers={"X-Client-Type": "ADMIN"},
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "NewSecurePass456!",
            },
        )
        assert resp.status_code == 401


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
#  CROSS-CUTTING SECURITY
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ


class TestSecurityCrossCutting:
    """Cross-cutting security tests for the auth system."""

    @pytest.mark.asyncio
    async def test_refresh_token_cannot_be_used_as_access_token(self, client: AsyncClient, driver_user_with_profile: User) -> None:
        """Using a refresh token as a Bearer token for /me returns 401."""
        login_resp = await client.post(
            f"{AUTH}/login",
            headers=DRIVER_CLIENT_HEADERS,
            json={"email": driver_user_with_profile.email, "password": TEST_PASSWORD},
        )
        refresh_token = login_resp.json()["tokens"]["refresh_token"]

        resp = await client.get(
            f"{AUTH}/me",
            headers={
                **DRIVER_CLIENT_HEADERS,
                "Authorization": f"Bearer {refresh_token}",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_error_responses_are_consistent_json(self, client: AsyncClient) -> None:
        """All error responses follow the standard envelope."""
        resp = await client.get(f"{AUTH}/me", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401
        data = resp.json()
        assert data["success"] is False
        assert "message" in data
        assert "error" in data
        assert "code" in data["error"]
