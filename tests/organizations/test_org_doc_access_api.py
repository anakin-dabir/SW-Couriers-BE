"""Integration API tests — Document Access OTP endpoints.

Covers:
- POST /v1/organizations/documents/otp/send    — request OTP (rate-limited)
- POST /v1/organizations/documents/otp/verify  — verify OTP → receive doc_access_token
- Missing / invalid / expired X-Doc-Access-Token on document endpoints → 401
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.models import DocAccessToken, DocOtp

OTP_SEND_URL = "/v1/organizations/documents/otp/send"
OTP_VERIFY_URL = "/v1/organizations/documents/otp/verify"

# A real document-endpoint URL — used to verify token validation
_SAMPLE_DOC_LIST_URL = "/v1/organizations/{org_id}/documents"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _jwt_only_headers(admin_user_token: str) -> dict:
    """Headers with JWT but NO X-Doc-Access-Token."""
    return {
        "Authorization": f"Bearer {admin_user_token}",
        "X-Client-Type": "ADMIN",
    }


def _mock_enqueue():
    """Prevent Arq from actually enqueuing email tasks in OTP tests."""
    return patch(
        "app.modules.organizations.doc_access_service.enqueue",
        new_callable=AsyncMock,
        return_value=None,
    )


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def jwt_only_headers(admin_user, admin_headers) -> dict:
    """Admin JWT headers WITHOUT the X-Doc-Access-Token — for negative tests."""
    return {k: v for k, v in admin_headers.items() if k != "X-Doc-Access-Token"}


@pytest_asyncio.fixture
async def expired_doc_token(admin_user, db_session: AsyncSession) -> str:
    """A DocAccessToken that has already expired (created 2 hours ago)."""
    raw = secrets.token_hex(32)
    expires_at = datetime.now(UTC) - timedelta(hours=2)
    row = DocAccessToken(
        user_id=admin_user.id,
        token_hash=hash_token(raw),
        expires_at=expires_at,
        access_scope=DocAccessScope.ORG_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return raw


@pytest_asyncio.fixture
async def valid_otp(admin_user, db_session: AsyncSession) -> str:
    """An unused, non-expired DocOtp for admin_user. Returns the 6-digit code."""
    code = "123456"
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    row = DocOtp(
        user_id=admin_user.id,
        otp_code=code,
        is_used=False,
        expires_at=expires_at,
        access_scope=DocAccessScope.ORG_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return code


@pytest_asyncio.fixture
async def expired_otp(admin_user, db_session: AsyncSession) -> str:
    """A DocOtp that has already expired. Returns the 6-digit code."""
    code = "999999"
    expires_at = datetime.now(UTC) - timedelta(minutes=1)
    row = DocOtp(
        user_id=admin_user.id,
        otp_code=code,
        is_used=False,
        expires_at=expires_at,
        access_scope=DocAccessScope.ORG_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return code


@pytest_asyncio.fixture
async def used_otp(admin_user, db_session: AsyncSession) -> str:
    """A DocOtp that has already been used. Returns the 6-digit code."""
    code = "888888"
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    row = DocOtp(
        user_id=admin_user.id,
        otp_code=code,
        is_used=True,
        expires_at=expires_at,
        access_scope=DocAccessScope.ORG_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return code


# ═══════════════════════════════════════════════════
#  POST /v1/organizations/documents/otp/send
# ═══════════════════════════════════════════════════


class TestSendDocOTP:
    @pytest.mark.asyncio
    async def test_send_otp_success(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue():
            resp = await client.post(OTP_SEND_URL, headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "OTP sent" in body["data"]["message"]

    @pytest.mark.asyncio
    async def test_send_otp_enqueues_email_task(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue() as mock_enqueue:
            await client.post(OTP_SEND_URL, headers=admin_headers)
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs.kwargs["to_email"] is not None
        assert len(call_kwargs.kwargs["otp_code"]) == 6
        assert call_kwargs.kwargs["otp_code"].isdigit()

    @pytest.mark.asyncio
    async def test_send_otp_requires_auth(self, client: AsyncClient):
        resp = await client.post(OTP_SEND_URL)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_send_otp_rate_limit_enforced(self, client: AsyncClient, admin_headers: dict):
        """4th request within 10 minutes must be rejected (limit = 3)."""
        with _mock_enqueue():
            for _ in range(3):
                resp = await client.post(OTP_SEND_URL, headers=admin_headers)
                assert resp.status_code == 200
            # 4th request should be rate-limited
            resp = await client.post(OTP_SEND_URL, headers=admin_headers)
        assert resp.status_code == 422
        assert "Too many OTP" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_send_otp_response_has_message(self, client: AsyncClient, admin_headers: dict):
        with _mock_enqueue():
            resp = await client.post(OTP_SEND_URL, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["message"] != ""


# ═══════════════════════════════════════════════════
#  POST /v1/organizations/documents/otp/verify
# ═══════════════════════════════════════════════════


class TestVerifyDocOTP:
    @pytest.mark.asyncio
    async def test_verify_correct_otp_returns_token(
        self, client: AsyncClient, admin_headers: dict, valid_otp: str
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": valid_otp},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "doc_access_token" in data
        assert len(data["doc_access_token"]) == 64  # 32-byte hex
        assert data["expires_in"] == 3600
        assert "expires_at" in data
        assert "message" in data
        assert data["doc_access_token"][:8] not in data["message"]

    @pytest.mark.asyncio
    async def test_verify_wrong_otp_returns_401(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": "000000"},
            headers=admin_headers,
        )
        assert resp.status_code == 401
        assert "Invalid or expired OTP" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_verify_expired_otp_returns_401(
        self, client: AsyncClient, admin_headers: dict, expired_otp: str
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": expired_otp},
            headers=admin_headers,
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_already_used_otp_returns_401(
        self, client: AsyncClient, admin_headers: dict, used_otp: str
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": used_otp},
            headers=admin_headers,
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_otp_is_single_use(
        self, client: AsyncClient, admin_headers: dict, valid_otp: str
    ):
        """Using the OTP once marks it used; a second attempt must fail."""
        resp = await client.post(OTP_VERIFY_URL, json={"otp": valid_otp}, headers=admin_headers)
        assert resp.status_code == 200

        resp2 = await client.post(OTP_VERIFY_URL, json={"otp": valid_otp}, headers=admin_headers)
        assert resp2.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_non_numeric_otp_rejected(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": "abcdef"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_verify_short_otp_rejected(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": "12345"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_verify_otp_requires_auth(self, client: AsyncClient):
        resp = await client.post(OTP_VERIFY_URL, json={"otp": "123456"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_otp_missing_body_rejected(
        self, client: AsyncClient, admin_headers: dict
    ):
        resp = await client.post(OTP_VERIFY_URL, headers=admin_headers)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════
#  X-Doc-Access-Token validation on document endpoints
# ═══════════════════════════════════════════════════


class TestDocAccessTokenValidation:
    """Verify that document endpoints enforce X-Doc-Access-Token correctly."""

    @pytest.mark.asyncio
    async def test_missing_doc_token_returns_401(
        self, client: AsyncClient, jwt_only_headers: dict, sample_org
    ):
        """JWT present but no X-Doc-Access-Token → 401."""
        resp = await client.get(
            _SAMPLE_DOC_LIST_URL.format(org_id=sample_org.id),
            headers=jwt_only_headers,
        )
        assert resp.status_code == 401
        assert "X-Doc-Access-Token" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_expired_doc_token_returns_401(
        self, client: AsyncClient, jwt_only_headers: dict, expired_doc_token: str, sample_org
    ):
        """JWT present but expired X-Doc-Access-Token → 401."""
        headers = {**jwt_only_headers, "X-Doc-Access-Token": expired_doc_token}
        resp = await client.get(
            _SAMPLE_DOC_LIST_URL.format(org_id=sample_org.id),
            headers=headers,
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_doc_token_returns_401(
        self, client: AsyncClient, jwt_only_headers: dict, sample_org
    ):
        """JWT present but completely wrong token string → 401."""
        headers = {**jwt_only_headers, "X-Doc-Access-Token": "x" * 64}
        resp = await client.get(
            _SAMPLE_DOC_LIST_URL.format(org_id=sample_org.id),
            headers=headers,
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_doc_token_allows_access(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ):
        """Full valid headers (JWT + valid doc token) → 200 on a document endpoint."""
        from unittest.mock import MagicMock
        with patch.multiple(
            "app.modules.organizations.service",
            generate_document_url=MagicMock(return_value="https://r2.example.com/url"),
        ):
            resp = await client.get(
                _SAMPLE_DOC_LIST_URL.format(org_id=sample_org.id),
                headers=admin_headers,
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_otp_send_does_not_require_doc_token(
        self, client: AsyncClient, jwt_only_headers: dict
    ):
        """The OTP send endpoint itself must NOT require X-Doc-Access-Token."""
        with _mock_enqueue():
            resp = await client.post(OTP_SEND_URL, headers=jwt_only_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_otp_verify_does_not_require_doc_token(
        self, client: AsyncClient, jwt_only_headers: dict, valid_otp: str
    ):
        """The OTP verify endpoint itself must NOT require X-Doc-Access-Token."""
        resp = await client.post(
            OTP_VERIFY_URL,
            json={"otp": valid_otp},
            headers=jwt_only_headers,
        )
        assert resp.status_code == 200
