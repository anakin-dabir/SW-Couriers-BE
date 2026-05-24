"""Integration API tests — Org Document endpoints.

Covers:
- POST   /v1/organizations/{org_id}/documents               simple upload (Upload Contract form)
- POST   /v1/organizations/{org_id}/documents/operations    full upload (Document Operations form)
- GET    /v1/organizations/{org_id}/documents               list documents
- GET    /v1/organizations/{org_id}/documents/{doc_id}      get single document
- PATCH  /v1/organizations/{org_id}/documents/{doc_id}      update metadata
- DELETE /v1/organizations/{org_id}/documents/{doc_id}      soft delete
- GET    /v1/organizations/{org_id}/documents/activities     recent activity log

R2 storage calls are mocked — no real network I/O required.
All tests use per-test transaction rollback (no persistent state).
"""

import io
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

ORGS = "/v1/organizations"


# ── URL helpers ────────────────────────────────────────────────────────────────


def _docs_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/documents"


def _doc_url(org_id: str, doc_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/{doc_id}"


def _ops_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/operations"


def _activities_url(org_id: str) -> str:
    return f"{ORGS}/{org_id}/documents/activities"


# ── File helpers ───────────────────────────────────────────────────────────────


def _pdf_file(name: str = "contract.pdf") -> tuple:
    return ("document_file", (name, b"%PDF-1.4 fake pdf content", "application/pdf"))


def _jpeg_document_file(name: str = "scan.jpg") -> tuple:
    content = (
        b"\xff\xd8\xff\xe0"
        b"\x00\x10"
        b"JFIF\x00"
        b"\x01\x01"
        b"\x00"
        b"\x00\x01\x00\x01"
        b"\x00\x00"
        b"\xff\xd9"
    )
    return ("document_file", (name, content, "image/jpeg"))


def _heic_file(name: str = "scan.heic") -> tuple:
    return ("document_file", (name, b"\x00\x00\x00 ftypheic fake heic", "image/heic"))


def _invalid_file(name: str = "script.exe") -> tuple:
    return ("document_file", (name, b"MZ fake exe content", "application/octet-stream"))


# ── Form data helpers ──────────────────────────────────────────────────────────


def _simple_form(
    title: str = "Master Service Agreement",
    document_type: str = "MSA",
    expiry_date: str = "2028-01-01",
) -> dict:
    return {"title": title, "document_type": document_type, "expiry_date": expiry_date}


def _ops_form(
    title: str = "Pricing Schedule",
    document_type: str = "PRICING",
    category: str = "CONTRACTS",
    issuing_authority: str | None = "Swift Retail Limited",
    issue_date: str | None = "2024-01-01",
    expiry_date: str | None = "2026-08-12",
    description: str | None = "Annual pricing schedule.",
    confidentiality_level: str | None = "INTERNAL",
    tags: list | None = None,
    notify_client: bool = False,
) -> dict:
    data: dict = {
        "title": title,
        "document_type": document_type,
        "category": category,
        "notify_client": str(notify_client).lower(),
    }
    if issuing_authority is not None:
        data["issuing_authority"] = issuing_authority
    if issue_date is not None:
        data["issue_date"] = issue_date
    if expiry_date is not None:
        data["expiry_date"] = expiry_date
    if description is not None:
        data["description"] = description
    if confidentiality_level is not None:
        data["confidentiality_level"] = confidentiality_level
    if tags is not None:
        data["tags"] = json.dumps(tags)
    return data


def _mock_r2():
    return patch.multiple(
        "app.modules.organizations.service",
        upload_to_r2=AsyncMock(return_value="organizations/fake-org/documents/fake_key.pdf"),
        delete_from_r2=AsyncMock(),
        generate_document_url=MagicMock(return_value="https://r2.example.com/presigned-url"),
    )


# ── Upload helpers ─────────────────────────────────────────────────────────────


async def _upload_simple(
    client: AsyncClient,
    headers: dict,
    org_id: str,
    *,
    file: tuple | None = None,
    form: dict | None = None,
) -> dict:
    files = [file or _pdf_file()]
    data = form or _simple_form()
    with _mock_r2():
        resp = await client.post(_docs_url(org_id), files=files, data=data, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _upload_ops(
    client: AsyncClient,
    headers: dict,
    org_id: str,
    *,
    file: tuple | None = None,
    form: dict | None = None,
) -> dict:
    files = [file or _pdf_file()]
    data = form or _ops_form()
    with _mock_r2():
        resp = await client.post(_ops_url(org_id), files=files, data=data, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ═══════════════════════════════════════════════════
#  SIMPLE UPLOAD (Upload Contract form)
# ═══════════════════════════════════════════════════


class TestSimpleUploadDocument:
    @pytest.mark.asyncio
    async def test_upload_pdf_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)

        assert doc["title"] == "Master Service Agreement"
        assert doc["document_type"] == "MSA"
        assert doc["expiry_date"] == "2028-01-01"
        assert doc["organization_id"] == sample_org.id
        assert doc["status"] == "ACTIVE"
        assert doc["category"] is None
        assert "document_url" in doc
        assert "id" in doc

    @pytest.mark.asyncio
    async def test_upload_jpeg_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(
            client, admin_headers, sample_org.id,
            file=_jpeg_document_file(),
            form=_simple_form(title="SLA Scan", document_type="SLA"),
        )
        assert doc["document_type"] == "SLA"

    @pytest.mark.asyncio
    async def test_upload_heic_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(
            client, admin_headers, sample_org.id,
            file=_heic_file(),
            form=_simple_form(title="NDA Scan", document_type="NDA"),
        )
        assert doc["document_type"] == "NDA"

    @pytest.mark.asyncio
    async def test_upload_all_document_types(self, client: AsyncClient, admin_headers: dict, sample_org):
        for doc_type in (
            "MSA", "SLA", "PRICING", "NDA", "DPA",
            "COMPANY_REGISTRATION_CERT", "VAT_REGISTRATION_CERT",
            "PUBLIC_LIABILITY_INSURANCE", "EMPLOYERS_LIABILITY_INSURANCE",
            "GOODS_IN_TRANSIT_INSURANCE", "BANK_REFERENCE_LETTER",
            "TRADE_REFERENCE_LETTER", "PROOF_OF_ADDRESS",
            "DIRECTOR_ID_VERIFICATION", "FINANCIAL_STATEMENTS",
            "CREDIT_TERMS_CONDITIONS", "LETTER_OF_AUTHORITY", "OTHER",
        ):
            doc = await _upload_simple(
                client, admin_headers, sample_org.id,
                form=_simple_form(title=f"{doc_type} Doc", document_type=doc_type),
            )
            assert doc["document_type"] == doc_type

    @pytest.mark.asyncio
    async def test_upload_invalid_mime_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.post(
                _docs_url(sample_org.id),
                files=[_invalid_file()],
                data=_simple_form(),
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_file_too_large_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        big_content = b"%PDF-1.4 " + b"x" * (26 * 1024 * 1024)
        with _mock_r2():
            resp = await client.post(
                _docs_url(sample_org.id),
                files=[("document_file", ("big.pdf", big_content, "application/pdf"))],
                data=_simple_form(),
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.post(
            _docs_url(sample_org.id),
            files=[_pdf_file()],
            data=_simple_form(),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_upload_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        with _mock_r2():
            resp = await client.post(
                _docs_url(str(uuid.uuid4())),
                files=[_pdf_file()],
                data=_simple_form(),
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_invalid_document_type_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.post(
                _docs_url(sample_org.id),
                files=[_pdf_file()],
                data=_simple_form(document_type="UNKNOWN_TYPE"),
                headers=admin_headers,
            )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════
#  FULL UPLOAD (Document Operations form)
# ═══════════════════════════════════════════════════


class TestDocumentOperationsUpload:
    @pytest.mark.asyncio
    async def test_full_upload_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_ops(client, admin_headers, sample_org.id)

        assert doc["title"] == "Pricing Schedule"
        assert doc["document_type"] == "PRICING"
        assert doc["category"] == "CONTRACTS"
        assert doc["status"] == "ACTIVE"
        assert doc["issuing_authority"] == "Swift Retail Limited"
        assert doc["issue_date"] == "2024-01-01"
        assert doc["expiry_date"] == "2026-08-12"
        assert doc["description"] == "Annual pricing schedule."
        assert doc["confidentiality_level"] == "INTERNAL"
        assert "document_url" in doc

    @pytest.mark.asyncio
    async def test_full_upload_minimal_fields(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Only required fields (title, document_type, category); all optional fields omitted."""
        doc = await _upload_ops(
            client, admin_headers, sample_org.id,
            form=_ops_form(
                title="Company Registration Certificate",
                document_type="COMPANY_REGISTRATION_CERT",
                category="INTERNAL",
                issuing_authority=None,
                issue_date=None,
                expiry_date=None,
                description=None,
                confidentiality_level=None,
            ),
        )
        assert doc["document_type"] == "COMPANY_REGISTRATION_CERT"
        assert doc["category"] == "INTERNAL"
        assert doc["issuing_authority"] is None
        assert doc["expiry_date"] is None
        assert doc["status"] == "ACTIVE"  # no expiry → always ACTIVE

    @pytest.mark.asyncio
    async def test_full_upload_all_categories(self, client: AsyncClient, admin_headers: dict, sample_org):
        for cat in ("CONTRACTS", "INTERNAL", "CLIENT_UPLOADS"):
            doc = await _upload_ops(
                client, admin_headers, sample_org.id,
                form=_ops_form(title=f"{cat} doc", category=cat),
            )
            assert doc["category"] == cat

    @pytest.mark.asyncio
    async def test_full_upload_with_tags(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_ops(
            client, admin_headers, sample_org.id,
            form=_ops_form(tags=["compliance", "2026", "reviewed"]),
        )
        assert doc["tags"] == ["compliance", "2026", "reviewed"]

    @pytest.mark.asyncio
    async def test_full_upload_tags_exceed_limit_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        too_many = [f"tag{i}" for i in range(11)]
        with _mock_r2():
            resp = await client.post(
                _ops_url(sample_org.id),
                files=[_pdf_file()],
                data=_ops_form(tags=too_many),
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_full_upload_all_confidentiality_levels(self, client: AsyncClient, admin_headers: dict, sample_org):
        for level in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "STRICTLY_CONFIDENTIAL"):
            doc = await _upload_ops(
                client, admin_headers, sample_org.id,
                form=_ops_form(confidentiality_level=level),
            )
            assert doc["confidentiality_level"] == level

    @pytest.mark.asyncio
    async def test_full_upload_expiring_soon_status(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Expiry within 30 days → status EXPIRING_SOON."""
        from datetime import date, timedelta
        soon = (date.today() + timedelta(days=10)).isoformat()
        doc = await _upload_ops(
            client, admin_headers, sample_org.id,
            form=_ops_form(expiry_date=soon),
        )
        assert doc["status"] == "EXPIRING_SOON"

    @pytest.mark.asyncio
    async def test_full_upload_expired_status(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Past expiry date → status EXPIRED."""
        doc = await _upload_ops(
            client, admin_headers, sample_org.id,
            form=_ops_form(expiry_date="2020-01-01"),
        )
        assert doc["status"] == "EXPIRED"

    @pytest.mark.asyncio
    async def test_full_upload_invalid_mime_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.post(
                _ops_url(sample_org.id),
                files=[_invalid_file()],
                data=_ops_form(),
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_full_upload_requires_admin(self, client: AsyncClient, sample_org):
        resp = await client.post(
            _ops_url(sample_org.id),
            files=[_pdf_file()],
            data=_ops_form(),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_full_upload_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        with _mock_r2():
            resp = await client.post(
                _ops_url(str(uuid.uuid4())),
                files=[_pdf_file()],
                data=_ops_form(),
                headers=admin_headers,
            )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════
#  LIST DOCUMENTS
# ═══════════════════════════════════════════════════


class TestListDocuments:
    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_list(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.get(_docs_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_returns_uploaded_documents(self, client: AsyncClient, admin_headers: dict, sample_org):
        await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="MSA Doc", document_type="MSA"))
        await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="NDA Doc", document_type="NDA"))

        with _mock_r2():
            resp = await client.get(_docs_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        titles = {d["title"] for d in items}
        assert titles == {"MSA Doc", "NDA Doc"}

    @pytest.mark.asyncio
    async def test_list_includes_new_fields(self, client: AsyncClient, admin_headers: dict, sample_org):
        """All new fields are present in the list response (even if null)."""
        await _upload_simple(client, admin_headers, sample_org.id)

        with _mock_r2():
            resp = await client.get(_docs_url(sample_org.id), headers=admin_headers)
        doc = resp.json()["data"][0]
        for field in ("status", "category", "issuing_authority", "issue_date",
                      "description", "confidentiality_level", "tags", "uploaded_by_email"):
            assert field in doc

    @pytest.mark.asyncio
    async def test_list_is_scoped_to_org(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        await _upload_simple(client, admin_headers, org_a.id)

        with _mock_r2():
            resp = await client.get(_docs_url(org_b.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_excludes_deleted_documents(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            await client.delete(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
            resp = await client.get(_docs_url(sample_org.id), headers=admin_headers)
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_docs_url(sample_org.id))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        with _mock_r2():
            resp = await client.get(_docs_url(str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════
#  GET SINGLE DOCUMENT
# ═══════════════════════════════════════════════════


class TestGetDocument:
    @pytest.mark.asyncio
    async def test_get_existing_document(self, client: AsyncClient, admin_headers: dict, sample_org):
        uploaded = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.get(_doc_url(sample_org.id, uploaded["id"]), headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == uploaded["id"]
        assert "document_url" in data

    @pytest.mark.asyncio
    async def test_get_logs_download_activity(self, client: AsyncClient, admin_headers: dict, sample_org):
        uploaded = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            await client.get(_doc_url(sample_org.id, uploaded["id"]), headers=admin_headers)
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        rows = resp.json()["data"]
        types = [r["activity_type"] for r in rows]
        assert "DOWNLOADED" in types

    @pytest.mark.asyncio
    async def test_get_unknown_document_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.get(_doc_url(sample_org.id, str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_document_wrong_org_returns_404(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        doc = await _upload_simple(client, admin_headers, org_a.id)

        with _mock_r2():
            resp = await client.get(_doc_url(org_b.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_deleted_document_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            await client.delete(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
            resp = await client.get(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_doc_url(sample_org.id, str(uuid.uuid4())))
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  UPDATE DOCUMENT METADATA
# ═══════════════════════════════════════════════════


class TestUpdateDocument:
    @pytest.mark.asyncio
    async def test_update_title(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={"title": "Updated Title", "reason": "Corrected name"},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_document_type(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={"document_type": "DPA", "reason": "Reclassified"},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["document_type"] == "DPA"

    @pytest.mark.asyncio
    async def test_update_expiry_date(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={"expiry_date": "2030-06-15", "reason": "Extended"},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["expiry_date"] == "2030-06-15"
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_update_category_and_tags(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={
                    "category": "INTERNAL",
                    "tags": ["reviewed", "2026"],
                    "reason": "Classified after review",
                },
                headers=admin_headers,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["category"] == "INTERNAL"
        assert data["tags"] == ["reviewed", "2026"]

    @pytest.mark.asyncio
    async def test_update_description_and_confidentiality(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={
                    "description": "Updated description.",
                    "confidentiality_level": "CONFIDENTIAL",
                    "reason": "Add classification",
                },
                headers=admin_headers,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["description"] == "Updated description."
        assert data["confidentiality_level"] == "CONFIDENTIAL"

    @pytest.mark.asyncio
    async def test_update_status_recomputed_on_expiry_change(self, client: AsyncClient, admin_headers: dict, sample_org):
        """Setting expiry_date in the past recomputes status to EXPIRED."""
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, doc["id"]),
                json={"expiry_date": "2020-01-01", "reason": "Back-dated for testing"},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "EXPIRED"

    @pytest.mark.asyncio
    async def test_update_missing_reason_rejected(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        resp = await client.patch(
            _doc_url(sample_org.id, doc["id"]),
            json={"title": "New Title"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_unknown_document_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.patch(
                _doc_url(sample_org.id, str(uuid.uuid4())),
                json={"title": "X", "reason": "test"},
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        resp = await client.patch(
            _doc_url(sample_org.id, doc["id"]),
            json={"title": "X", "reason": "test"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  DELETE DOCUMENT
# ═══════════════════════════════════════════════════


class TestDeleteDocument:
    @pytest.mark.asyncio
    async def test_delete_success(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.delete(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_makes_document_inaccessible(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            await client.delete(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
            resp = await client.get(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_unknown_document_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.delete(_doc_url(sample_org.id, str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id)
        resp = await client.delete(_doc_url(sample_org.id, doc["id"]))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_removes_document_from_list(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc_a = await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="Doc A"))
        doc_b = await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="Doc B"))

        with _mock_r2():
            await client.delete(_doc_url(sample_org.id, doc_a["id"]), headers=admin_headers)
            resp = await client.get(_docs_url(sample_org.id), headers=admin_headers)
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["id"] == doc_b["id"]


# ═══════════════════════════════════════════════════
#  RECENT ACTIVITY
# ═══════════════════════════════════════════════════


class TestDocumentActivities:
    @pytest.mark.asyncio
    async def test_activities_empty_before_any_uploads(self, client: AsyncClient, admin_headers: dict, sample_org):
        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_upload_creates_activity_row(self, client: AsyncClient, admin_headers: dict, sample_org):
        await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="MSA"))
        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        rows = resp.json()["data"]
        assert len(rows) == 1
        assert rows[0]["activity_type"] == "UPLOADED"
        assert rows[0]["document_name"] == "MSA"

    @pytest.mark.asyncio
    async def test_ops_upload_creates_activity_row(self, client: AsyncClient, admin_headers: dict, sample_org):
        await _upload_ops(client, admin_headers, sample_org.id, form=_ops_form(title="Policy"))
        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        rows = resp.json()["data"]
        assert any(r["activity_type"] == "UPLOADED" and r["document_name"] == "Policy" for r in rows)

    @pytest.mark.asyncio
    async def test_delete_creates_deleted_activity_row(self, client: AsyncClient, admin_headers: dict, sample_org):
        doc = await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="To Delete"))
        with _mock_r2():
            await client.delete(_doc_url(sample_org.id, doc["id"]), headers=admin_headers)
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        types = [r["activity_type"] for r in resp.json()["data"]]
        assert "DELETED" in types

    @pytest.mark.asyncio
    async def test_activity_rows_contain_expected_fields(self, client: AsyncClient, admin_headers: dict, sample_org):
        await _upload_simple(client, admin_headers, sample_org.id)
        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        row = resp.json()["data"][0]
        for field in ("id", "organization_id", "activity_type", "actor_email",
                      "actor_role", "document_name", "details", "created_at"):
            assert field in row

    @pytest.mark.asyncio
    async def test_activities_scoped_to_org(self, client: AsyncClient, admin_headers: dict, org_factory):
        org_a = await org_factory()
        org_b = await org_factory()
        await _upload_simple(client, admin_headers, org_a.id)

        with _mock_r2():
            resp = await client.get(_activities_url(org_b.id), headers=admin_headers)
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_activities_ordered_newest_first(self, client: AsyncClient, admin_headers: dict, sample_org):
        await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="First"))
        await _upload_simple(client, admin_headers, sample_org.id, form=_simple_form(title="Second"))

        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id), headers=admin_headers)
        names = [r["document_name"] for r in resp.json()["data"]]
        assert names[0] == "Second"

    @pytest.mark.asyncio
    async def test_activities_limit_query_param(self, client: AsyncClient, admin_headers: dict, sample_org):
        for i in range(5):
            await _upload_simple(
                client, admin_headers, sample_org.id,
                form=_simple_form(title=f"Doc {i}"),
            )
        with _mock_r2():
            resp = await client.get(_activities_url(sample_org.id) + "?limit=3", headers=admin_headers)
        assert len(resp.json()["data"]) == 3

    @pytest.mark.asyncio
    async def test_activities_requires_auth(self, client: AsyncClient, sample_org):
        resp = await client.get(_activities_url(sample_org.id))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_activities_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict):
        with _mock_r2():
            resp = await client.get(_activities_url(str(uuid.uuid4())), headers=admin_headers)
        assert resp.status_code == 404
