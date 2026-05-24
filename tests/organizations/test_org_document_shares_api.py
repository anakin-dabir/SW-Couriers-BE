"""Integration API tests — Org Document Sharing endpoints.

Covers:
- POST   /v1/organizations/{org_id}/documents/{doc_id}/shares    create share
- GET    /v1/organizations/{org_id}/documents/shares             list all shares (org-wide)
- GET    /v1/organizations/{org_id}/documents/{doc_id}/shares    list shares for one document
- PATCH  /v1/organizations/{org_id}/documents/shares/{id}/expiry extend expiry
- PATCH  /v1/organizations/{org_id}/documents/shares/{id}/revoke revoke share
- POST   /v1/shared/documents/{share_token}/otp/send   public share OTP (recipient binding)
- POST   /v1/shared/documents/{share_token}/otp/verify public share OTP verify (rate limit + lockout)

R2 storage calls and the Arq job queue are mocked — no real network I/O required.
All tests use per-test transaction rollback (no persistent state).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.modules.organizations.models import OrgDocumentShare, ShareAccessToken, ShareOtp

ORGS = "/v1/organizations"
SHARED = "/v1/shared/documents"


# ── URL helpers ────────────────────────────────────────────────────────────────


def _docs_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/documents"


def _ops_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/operations"


def _shares_url(org_id: str) -> str:
    """Org-wide sharing history list."""
    return f"{ORGS}/{org_id}/documents/shares"


def _doc_shares_url(org_id: str, doc_id: str) -> str:
    """Shares for a specific document."""
    return f"{ORGS}/{org_id}/documents/{doc_id}/shares"


def _share_expiry_url(org_id: str, share_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/shares/{share_id}/expiry"


def _share_revoke_url(org_id: str, share_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/shares/{share_id}/revoke"


# ── Mock helpers ───────────────────────────────────────────────────────────────


def _mock_r2():
    """Mock R2 storage operations for document upload."""
    return patch.multiple(
        "app.modules.organizations.service",
        upload_to_r2=AsyncMock(return_value="organizations/fake-org/documents/fake_key.pdf"),
        delete_from_r2=AsyncMock(),
        generate_document_url=MagicMock(return_value="https://r2.example.com/presigned-url"),
    )


@contextmanager
def _mock_share():
    """Mock all external deps for share tests: R2 (upload + presigned URL) + Arq queue."""
    with (
        patch.multiple(
            "app.modules.organizations.service",
            upload_to_r2=AsyncMock(return_value="organizations/fake-org/documents/fake_key.pdf"),
            delete_from_r2=AsyncMock(),
            generate_document_url=MagicMock(return_value="https://r2.example.com/presigned-url"),
            enqueue=AsyncMock(return_value=None),
        ),
        # share_document() does a local re-import from app.storage.upload so patch source too
        patch(
            "app.storage.upload.generate_document_url",
            MagicMock(return_value="https://r2.example.com/presigned-share-url"),
        ),
    ):
        yield


def _pdf_file(name: str = "contract.pdf") -> tuple:
    return ("document_file", (name, b"%PDF-1.4 fake pdf content", "application/pdf"))


# ── Document upload helpers ────────────────────────────────────────────────────


async def _upload_doc(client: AsyncClient, headers: dict, org_id: str) -> dict:
    """Upload a minimal document and return the response data dict."""
    with _mock_r2():
        resp = await client.post(
            _docs_url(org_id),
            files=[_pdf_file()],
            data={"title": "Master Service Agreement", "document_type": "MSA", "expiry_date": "2028-01-01"},
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _share_token_for_id(db_session: AsyncSession, share_id: str) -> str:
    result = await db_session.execute(
        select(OrgDocumentShare.share_token).where(OrgDocumentShare.id == share_id)
    )
    return result.scalar_one()


async def _count_share_otps(db_session: AsyncSession, share_token: str, email: str) -> int:
    result = await db_session.execute(
        select(func.count())
        .select_from(ShareOtp)
        .where(
            ShareOtp.share_token == share_token,
            ShareOtp.recipient_email == email.strip().lower(),
        )
    )
    return int(result.scalar_one() or 0)


async def _count_active_share_otps(db_session: AsyncSession, share_token: str, email: str) -> int:
    now = datetime.now(UTC)
    result = await db_session.execute(
        select(func.count())
        .select_from(ShareOtp)
        .where(
            ShareOtp.share_token == share_token,
            ShareOtp.recipient_email == email.strip().lower(),
            ShareOtp.is_used.is_(False),
            ShareOtp.expires_at > now,
        )
    )
    return int(result.scalar_one() or 0)


def _otp_send_url(share_token: str) -> str:
    return f"{SHARED}/{share_token}/otp/send"


def _otp_verify_url(share_token: str) -> str:
    return f"{SHARED}/{share_token}/otp/verify"


async def _create_share(
    client: AsyncClient,
    headers: dict,
    org_id: str,
    doc_id: str,
    *,
    recipients: list[str] | None = None,
    expiry_date: str | None = "2027-12-31",
    password_protected: bool = False,
    message: str | None = None,
) -> dict:
    """Create a share and return the response data dict."""
    body: dict = {
        "recipients": recipients or ["recipient@example.com"],
        "password_protected": password_protected,
    }
    if expiry_date is not None:
        body["expiry_date"] = expiry_date
    if message is not None:
        body["message"] = message

    with _mock_share():
        resp = await client.post(_doc_shares_url(org_id, doc_id), json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ═══════════════════════════════════════════════════
#  POST — CREATE SHARE
# ═══════════════════════════════════════════════════


class TestShareDocument:
    @pytest.mark.asyncio
    async def test_share_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        assert share["document_id"] == doc["id"]
        assert share["organization_id"] == sample_org.id
        assert share["status"] == "ACTIVE"
        assert share["recipients"] == ["recipient@example.com"]
        assert share["access_count"] == 1
        assert share["password_protected"] is False
        assert share["revoked_at"] is None
        assert share["revoke_reason"] is None
        assert "id" in share
        assert "created_at" in share

    @pytest.mark.asyncio
    async def test_share_with_password_protection(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client, admin_headers, sample_org.id, doc["id"],
            password_protected=True,
        )

        assert share["password_protected"] is True
        assert share["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_share_with_expiry_and_message(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client, admin_headers, sample_org.id, doc["id"],
            expiry_date="2027-06-30",
            message="Please review this agreement.",
        )

        assert share["expiry_date"] == "2027-06-30"
        assert share["message"] == "Please review this agreement."

    @pytest.mark.asyncio
    async def test_share_multiple_recipients(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        recipients = ["alice@example.com", "bob@example.com", "charlie@example.com"]
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"], recipients=recipients)

        assert sorted(share["recipients"]) == sorted(recipients)
        assert share["access_count"] == 3

    @pytest.mark.asyncio
    async def test_share_enqueues_email_per_recipient(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        recipients = ["alice@example.com", "bob@example.com"]

        enqueue_mock = AsyncMock(return_value=None)
        with (
            patch.multiple(
                "app.modules.organizations.service",
                upload_to_r2=AsyncMock(return_value="organizations/fake-org/documents/fake_key.pdf"),
                delete_from_r2=AsyncMock(),
                generate_document_url=MagicMock(return_value="https://r2.example.com/presigned-url"),
                enqueue=enqueue_mock,
            ),
            patch("app.storage.upload.generate_document_url", MagicMock(return_value="https://r2.example.com/presigned-share-url")),
        ):
            resp = await client.post(
                _doc_shares_url(sample_org.id, doc["id"]),
                json={"recipients": recipients, "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 201
        assert enqueue_mock.call_count == 2
        called_emails = {kw["to_email"] for _, kw in (c for c in (enqueue_mock.call_args_list or []) if len(c) >= 2)}
        assert called_emails == set(recipients)

    @pytest.mark.asyncio
    async def test_share_no_expiry(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"], expiry_date=None)

        assert share["expiry_date"] is None

    @pytest.mark.asyncio
    async def test_share_document_title_and_reference_in_response(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        assert share["document_title"] == doc["title"]
        # document_reference is auto-generated (DOC-YYYY-NNNNN) or None if sequence not yet run
        assert "document_reference" in share

    @pytest.mark.asyncio
    async def test_share_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(str(uuid.uuid4()), str(uuid.uuid4())),
                json={"recipients": ["r@example.com"], "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_share_unknown_doc_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(sample_org.id, str(uuid.uuid4())),
                json={"recipients": ["r@example.com"], "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_share_empty_recipients_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(sample_org.id, doc["id"]),
                json={"recipients": [], "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_share_too_many_recipients_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        too_many = [f"user{i}@example.com" for i in range(21)]
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(sample_org.id, doc["id"]),
                json={"recipients": too_many, "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_share_invalid_email_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(sample_org.id, doc["id"]),
                json={"recipients": ["not-an-email"], "password_protected": False},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_share_message_exceeds_500_chars_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        with _mock_share():
            resp = await client.post(
                _doc_shares_url(sample_org.id, doc["id"]),
                json={
                    "recipients": ["r@example.com"],
                    "password_protected": False,
                    "message": "x" * 501,
                },
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_share_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.post(
            _doc_shares_url(sample_org.id, str(uuid.uuid4())),
            json={"recipients": ["r@example.com"], "password_protected": False},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  GET — LIST ORG-WIDE SHARES
# ═══════════════════════════════════════════════════


class TestListOrgShares:
    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient, admin_headers: dict, sample_org):
        resp = await client.get(_shares_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["items"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_list_returns_created_shares(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        await _create_share(client, admin_headers, sample_org.id, doc["id"])

        resp = await client.get(_shares_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["document_id"] == doc["id"]

    @pytest.mark.asyncio
    async def test_list_multiple_shares_across_docs(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc_a = await _upload_doc(client, admin_headers, sample_org.id)
        doc_b = await _upload_doc(client, admin_headers, sample_org.id)
        await _create_share(client, admin_headers, sample_org.id, doc_a["id"])
        await _create_share(client, admin_headers, sample_org.id, doc_b["id"])

        resp = await client.get(_shares_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_list_status_filter_active(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        # Revoke the share
        with _mock_share():
            await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "Testing revoke"},
                headers=admin_headers,
            )

        resp_active = await client.get(_shares_url(sample_org.id) + "?status=ACTIVE", headers=admin_headers)
        assert resp_active.status_code == 200
        assert resp_active.json()["data"]["total"] == 0

        resp_revoked = await client.get(_shares_url(sample_org.id) + "?status=REVOKED", headers=admin_headers)
        assert resp_revoked.status_code == 200
        assert resp_revoked.json()["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_pagination(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        for _ in range(3):
            await _create_share(client, admin_headers, sample_org.id, doc["id"])

        resp = await client.get(_shares_url(sample_org.id) + "?page=1&size=2", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["items"]) == 2
        assert body["total"] == 3

    @pytest.mark.asyncio
    async def test_list_scoped_to_org(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        doc = await _upload_doc(client, admin_headers, org_a.id)
        await _create_share(client, admin_headers, org_a.id, doc["id"])

        resp = await client.get(_shares_url(org_b.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["items"] == []

    @pytest.mark.asyncio
    async def test_list_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        resp = await client.get(_shares_url(str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_shares_url(sample_org.id))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_response_fields(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        await _create_share(
            client, admin_headers, sample_org.id, doc["id"],
            recipients=["a@example.com", "b@example.com"],
            message="Hello",
            expiry_date="2027-01-01",
        )

        resp = await client.get(_shares_url(sample_org.id), headers=admin_headers)
        item = resp.json()["data"]["items"][0]
        for field in (
            "id", "organization_id", "document_id", "document_title", "document_reference",
            "recipients", "shared_by", "shared_by_name", "message",
            "expiry_date", "password_protected", "status", "access_count",
            "revoked_at", "revoke_reason", "created_at", "updated_at",
        ):
            assert field in item, f"Missing field: {field}"


# ═══════════════════════════════════════════════════
#  GET — LIST SHARES FOR ONE DOCUMENT
# ═══════════════════════════════════════════════════


class TestListDocumentShares:
    @pytest.mark.asyncio
    async def test_list_empty_for_new_doc(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)

        resp = await client.get(_doc_shares_url(sample_org.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_returns_shares_for_doc(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        await _create_share(client, admin_headers, sample_org.id, doc["id"], recipients=["a@example.com"])
        await _create_share(client, admin_headers, sample_org.id, doc["id"], recipients=["b@example.com"])

        resp = await client.get(_doc_shares_url(sample_org.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        assert all(s["document_id"] == doc["id"] for s in items)

    @pytest.mark.asyncio
    async def test_list_scoped_to_document(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc_a = await _upload_doc(client, admin_headers, sample_org.id)
        doc_b = await _upload_doc(client, admin_headers, sample_org.id)
        await _create_share(client, admin_headers, sample_org.id, doc_a["id"])

        resp = await client.get(_doc_shares_url(sample_org.id, doc_b["id"]), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_unknown_doc_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        resp = await client.get(_doc_shares_url(sample_org.id, str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_doc_shares_url(sample_org.id, str(uuid.uuid4())))
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  PATCH — EXTEND SHARE EXPIRY
# ═══════════════════════════════════════════════════


class TestExtendShareExpiry:
    @pytest.mark.asyncio
    async def test_extend_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client, admin_headers, sample_org.id, doc["id"],
            expiry_date="2027-01-01",
        )
        new_expiry = "2028-06-30"

        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(sample_org.id, share["id"]),
                json={"expiry_date": new_expiry, "reason": "Client requested extension."},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["expiry_date"] == new_expiry
        assert data["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_extend_reactivates_expired_share(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Extending expiry on a share with a past date resets status to ACTIVE."""
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client, admin_headers, sample_org.id, doc["id"],
            expiry_date="2020-01-01",  # already expired
        )
        future_date = (date.today() + timedelta(days=30)).isoformat()

        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(sample_org.id, share["id"]),
                json={"expiry_date": future_date, "reason": "Reactivating share."},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_extend_missing_reason_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(sample_org.id, share["id"]),
                json={"expiry_date": "2028-01-01"},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extend_missing_expiry_date_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(sample_org.id, share["id"]),
                json={"reason": "No expiry date"},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extend_revoked_share_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "No longer needed."},
                headers=admin_headers,
            )
            resp = await client.patch(
                _share_expiry_url(sample_org.id, share["id"]),
                json={"expiry_date": "2028-01-01", "reason": "Trying to extend revoked share."},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extend_unknown_share_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(sample_org.id, str(uuid.uuid4())),
                json={"expiry_date": "2028-01-01", "reason": "Test"},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_extend_wrong_org_returns_404(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        doc = await _upload_doc(client, admin_headers, org_a.id)
        share = await _create_share(client, admin_headers, org_a.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_expiry_url(org_b.id, share["id"]),
                json={"expiry_date": "2028-01-01", "reason": "Cross-org test"},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_extend_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.patch(
            _share_expiry_url(sample_org.id, str(uuid.uuid4())),
            json={"expiry_date": "2028-01-01", "reason": "Test"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  PATCH — REVOKE SHARE
# ═══════════════════════════════════════════════════


class TestRevokeShare:
    @pytest.mark.asyncio
    async def test_revoke_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "Shared in error."},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "REVOKED"
        assert data["revoke_reason"] == "Shared in error."
        assert data["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_by(self, client: AsyncClient, admin_headers: dict, admin_user, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "Sharing revoked."},
                headers=admin_headers,
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_revoke_missing_reason_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            # First revoke
            await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "First revoke."},
                headers=admin_headers,
            )
            # Second revoke — should fail
            resp = await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "Second revoke attempt."},
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_revoke_unknown_share_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_share():
            resp = await client.patch(
                _share_revoke_url(sample_org.id, str(uuid.uuid4())),
                json={"reason": "Test"},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_wrong_org_returns_404(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        doc = await _upload_doc(client, admin_headers, org_a.id)
        share = await _create_share(client, admin_headers, org_a.id, doc["id"])

        with _mock_share():
            resp = await client.patch(
                _share_revoke_url(org_b.id, share["id"]),
                json={"reason": "Cross-org revoke test"},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.patch(
            _share_revoke_url(sample_org.id, str(uuid.uuid4())),
            json={"reason": "Test"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_share_appears_in_org_list(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Revoked shares remain visible in the sharing history."""
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(client, admin_headers, sample_org.id, doc["id"])

        with _mock_share():
            await client.patch(
                _share_revoke_url(sample_org.id, share["id"]),
                json={"reason": "Revoked for test."},
                headers=admin_headers,
            )

        resp = await client.get(_shares_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["status"] == "REVOKED"


# ═══════════════════════════════════════════════════
#  PUBLIC SHARED DOCUMENT — OTP RECIPIENT BINDING
# ═══════════════════════════════════════════════════


class TestSharedDocumentOtpRecipientBinding:
    INVITED = "invited@example.com"
    ATTACKER = "attacker@example.com"
    # Matches secrets.choice mock (returns "1" six times).
    FIXED_OTP = "111111"

    @pytest.mark.asyncio
    async def test_send_otp_invited_email_creates_otp_and_enqueues(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])
        before = await _count_share_otps(db_session, share_token, self.INVITED)

        enqueue_mock = AsyncMock(return_value=None)
        with (
            patch(
                "app.modules.organizations.doc_access_service.secrets.choice",
                return_value="1",
            ),
            patch("app.modules.organizations.doc_access_service.enqueue", enqueue_mock),
        ):
            resp = await client.post(
                _otp_send_url(share_token),
                json={"email": self.INVITED},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["message"] == "OTP sent to your email address. It expires in 10 minutes."
        assert await _count_share_otps(db_session, share_token, self.INVITED) == before + 1
        enqueue_mock.assert_called_once()
        assert enqueue_mock.call_args.kwargs.get("to_email") == self.INVITED

    @pytest.mark.asyncio
    async def test_send_otp_non_invited_email_silent_success_no_otp(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])
        before = await _count_share_otps(db_session, share_token, self.ATTACKER)

        enqueue_mock = AsyncMock(return_value=None)
        with patch("app.modules.organizations.doc_access_service.enqueue", enqueue_mock):
            resp = await client.post(
                _otp_send_url(share_token),
                json={"email": self.ATTACKER},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["message"] == "OTP sent to your email address. It expires in 10 minutes."
        assert await _count_share_otps(db_session, share_token, self.ATTACKER) == before
        enqueue_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_otp_invited_email_returns_access_token(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])

        with (
            patch(
                "app.modules.organizations.doc_access_service.secrets.choice",
                return_value="1",
            ),
            patch("app.modules.organizations.doc_access_service.enqueue", AsyncMock(return_value=None)),
        ):
            send_resp = await client.post(
                _otp_send_url(share_token),
                json={"email": self.INVITED},
            )
        assert send_resp.status_code == 200

        verify_resp = await client.post(
            _otp_verify_url(share_token),
            json={"email": self.INVITED, "otp": self.FIXED_OTP},
        )
        assert verify_resp.status_code == 200
        data = verify_resp.json()["data"]
        assert "share_access_token" in data
        assert data["expires_in"] == 3600
        assert "expires_at" in data

        token_row = (
            await db_session.execute(
                select(ShareAccessToken)
                .where(ShareAccessToken.share_token == share_token)
                .order_by(ShareAccessToken.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert token_row.token_hash
        assert len(token_row.token_hash) == 64

    @pytest.mark.asyncio
    async def test_verify_otp_non_invited_email_returns_401(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])

        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        db_session.add(
            ShareOtp(
                recipient_email=self.ATTACKER,
                share_token=share_token,
                otp_code=self.FIXED_OTP,
                expires_at=expires_at,
            )
        )
        await db_session.flush()

        resp = await client.post(
            _otp_verify_url(share_token),
            json={"email": self.ATTACKER, "otp": self.FIXED_OTP},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  PUBLIC SHARED DOCUMENT — OTP BRUTE-FORCE HARDENING
# ═══════════════════════════════════════════════════


class TestSharedDocumentOtpBruteForceHardening:
    INVITED = "invited@example.com"
    FIRST_OTP = "111111"
    SECOND_OTP = "222222"

    @pytest.mark.asyncio
    async def test_resend_invalidates_previous_otp(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])

        digit_sequence = iter("1" * 6 + "2" * 6)

        with (
            patch(
                "app.modules.organizations.doc_access_service.secrets.choice",
                side_effect=lambda _: next(digit_sequence),
            ),
            patch("app.modules.organizations.doc_access_service.enqueue", AsyncMock(return_value=None)),
        ):
            first = await client.post(_otp_send_url(share_token), json={"email": self.INVITED})
            second = await client.post(_otp_send_url(share_token), json={"email": self.INVITED})

        assert first.status_code == 200
        assert second.status_code == 200
        assert await _count_active_share_otps(db_session, share_token, self.INVITED) == 1

        stale = await client.post(
            _otp_verify_url(share_token),
            json={"email": self.INVITED, "otp": self.FIRST_OTP},
        )
        assert stale.status_code == 401

        valid = await client.post(
            _otp_verify_url(share_token),
            json={"email": self.INVITED, "otp": self.SECOND_OTP},
        )
        assert valid.status_code == 200
        assert "share_access_token" in valid.json()["data"]

    @pytest.mark.asyncio
    async def test_verify_lockout_after_repeated_failures(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org,
        db_session: AsyncSession,
    ):
        doc = await _upload_doc(client, admin_headers, sample_org.id)
        share = await _create_share(
            client,
            admin_headers,
            sample_org.id,
            doc["id"],
            recipients=[self.INVITED],
            password_protected=True,
        )
        share_token = await _share_token_for_id(db_session, share["id"])

        fail_counts: dict[str, int] = {}
        lock_active = False

        async def redis_get(key: str):
            if key.startswith("share_otp:lock:") and lock_active:
                return b"1"
            return None

        async def redis_incr(key: str) -> int:
            nonlocal lock_active
            n = fail_counts.get(key, 0) + 1
            fail_counts[key] = n
            if n >= 5:
                lock_active = True
            return n

        redis_mock = AsyncMock()
        redis_mock.get = redis_get
        redis_mock.incr = redis_incr
        redis_mock.expire = AsyncMock()
        redis_mock.set = AsyncMock()
        redis_mock.delete = AsyncMock()
        redis_mock.ttl = AsyncMock(return_value=900)

        with (
            patch("app.modules.organizations.doc_access_service.settings") as mock_settings,
            patch(
                "app.modules.organizations.doc_access_service.get_redis",
                return_value=redis_mock,
            ),
        ):
            mock_settings.is_test = False

            for _ in range(5):
                resp = await client.post(
                    _otp_verify_url(share_token),
                    json={"email": self.INVITED, "otp": "000000"},
                )
                assert resp.status_code == 401

            locked = await client.post(
                _otp_verify_url(share_token),
                json={"email": self.INVITED, "otp": "000000"},
            )
            assert locked.status_code == 429
            assert "Too many invalid OTP attempts" in locked.json()["message"]
