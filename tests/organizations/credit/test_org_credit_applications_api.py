"""HTTP API tests for org credit applications (FastAPI + AsyncClient).

Covers draft CRUD, direct submit, list/detail, reviewer workflow, trade-reference
verification, credit check run/refresh, ready-for-decision, approve/reject/cancel, delete.

Creditsafe is stubbed so tests do not require real API credentials.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.integrations.creditsafe.client import _STUB_REPORT
from app.modules.organizations.enums import ContactRole, ContactStatus
from app.modules.organizations.models import OrgContact, Organization
from app.modules.user.models import User

ORGS = "/v1/organizations"


def _applications_base(org_id: str) -> str:
    return f"{ORGS}/{org_id}/credit/applications"


def _drafts_base(org_id: str) -> str:
    return f"{ORGS}/{org_id}/credit/applications/drafts"


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


async def _b2b_account_owner(db_session, user_factory, org: Organization) -> User:
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
        contact_role=ContactRole.ACCOUNT_OWNER.value,
        status=ContactStatus.ACTIVE.value,
        is_primary=True,
    )
    db_session.add(contact)
    await db_session.flush()
    return user


def _valid_create_payload() -> dict:
    """Flat body matching CreateCreditApplicationRequest + submission validation."""
    return {
        "company_registration_number": "12345678",
        "vat_registration_number": "GB123456789",
        "years_trading": 5,
        "annual_turnover": "100000.00",
        "net_profit": "25000.00",
        "bank_name": "HSBC",
        "bank_sort_code": "40-12-34",
        "bank_account_number_last4": "5678",
        "bank_account_type": "BUSINESS_SAVINGS",
        "requested_credit_limit": "50000.00",
        "requested_payment_terms_days": 30,
        "expected_monthly_spend": "5000.00",
        "director_signatory_name": "John Doe",
        "director_signatory_position": "Director",
        "consent_credit_check": True,
        "consent_terms_and_conditions": True,
        "consent_data_processing": True,
        "trade_references": [
            {
                "company_name": "Acme Ltd",
                "contact_person": "Alice Brown",
                "contact_email": "a@example.com",
                "contact_phone": "+442079460001",
                "relationship_duration": "2_TO_5_YEARS",
            },
            {
                "company_name": "Beta Co",
                "contact_person": "Bob Green",
                "contact_email": "b@example.com",
                "contact_phone": "+442079460002",
                "relationship_duration": "1_TO_2_YEARS",
            },
        ],
    }


@pytest.fixture
def creditsafe_run_stub():
    """Stub Creditsafe assessment so run/refresh never hit the network."""
    with patch(
        "app.modules.org_credit_applications.service.run_credit_assessment",
        new_callable=AsyncMock,
        return_value=("stub-connect-id", dict(_STUB_REPORT)),
    ) as m:
        yield m


class TestCreditApplicationDraftsApi:
    @pytest.mark.asyncio
    async def test_save_list_get_patch_delete_draft(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        base = _drafts_base(org_id)

        save = await client.post(
            base,
            headers=admin_headers,
            files={
                "draft_data": (
                    None,
                    json.dumps({
                        "annual_turnover": "100000.00",
                        "bank_name": "HSBC",
                    }),
                    "application/json",
                ),
            },
        )
        assert save.status_code == 201
        saved = save.json()["data"]
        assert "id" in saved
        assert saved["draft_number"].startswith("CAD-")

        lst = await client.get(base, headers=admin_headers)
        assert lst.status_code == 200
        assert lst.json()["data"]["total"] >= 1

        draft_id = saved["id"]
        detail = await client.get(f"{base}/{draft_id}", headers=admin_headers)
        assert detail.status_code == 200
        body = detail.json()["data"]
        assert body["id"] == draft_id
        assert "application" in body
        assert body["application"]["annual_turnover"] is not None

        patch_resp = await client.patch(
            f"{base}/{draft_id}",
            headers=admin_headers,
            files={
                "draft_data": (
                    None,
                    json.dumps({"bank_sort_code": "40-00-00"}),
                    "application/json",
                ),
            },
        )
        assert patch_resp.status_code == 200

        delete = await client.delete(f"{base}/{draft_id}", headers=admin_headers)
        assert delete.status_code == 200

        gone = await client.get(f"{base}/{draft_id}", headers=admin_headers)
        assert gone.status_code == 404

    @pytest.mark.asyncio
    async def test_publish_draft_submits_application(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        base = _drafts_base(org_id)

        save = await client.post(
            base,
            headers=admin_headers,
            files={
                "draft_data": (
                    None,
                    json.dumps({"company_registration_number": "87654321"}),
                    "application/json",
                ),
            },
        )
        assert save.status_code == 201
        draft_id = save.json()["data"]["id"]

        pub = await client.post(
            f"{base}/{draft_id}/publish",
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(_valid_create_payload()),
                    "application/json",
                ),
            },
        )
        assert pub.status_code == 200
        assert pub.json()["success"] is True


class TestCreditApplicationCreateAndListApi:
    @pytest.mark.asyncio
    async def test_create_returns_id_and_number(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        resp = await client.post(
            _applications_base(org_id),
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(_valid_create_payload()),
                    "application/json",
                ),
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "id" in data
        assert data.get("application_number") is not None

    @pytest.mark.asyncio
    async def test_create_validation_error(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        bad = {"trade_references": [{"company_name": "Only"}]}
        resp = await client.post(
            _applications_base(org_id),
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(bad),
                    "application/json",
                ),
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_applications(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        await client.post(
            _applications_base(org_id),
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(_valid_create_payload()),
                    "application/json",
                ),
            },
        )
        lst = await client.get(
            _applications_base(org_id),
            headers=admin_headers,
            params={"page": 1, "size": 20},
        )
        assert lst.status_code == 200
        payload = lst.json()["data"]
        assert payload["total"] >= 1
        assert len(payload["items"]) >= 1


class TestCreditApplicationDetailApi:
    @pytest.mark.asyncio
    async def test_get_detail_includes_credit_report_key(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        create = await client.post(
            _applications_base(org_id),
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(_valid_create_payload()),
                    "application/json",
                ),
            },
        )
        app_id = create.json()["data"]["id"]

        detail = await client.get(
            f"{_applications_base(org_id)}/{app_id}",
            headers=admin_headers,
        )
        assert detail.status_code == 200
        data = detail.json()["data"]
        assert data["id"] == app_id
        assert "credit_report" in data
        assert "cooldown" not in data
        assert isinstance(data["trade_references"], list)
        assert len(data["trade_references"]) == 2
        for key in (
            "approved_at",
            "approved_by",
            "rejected_at",
            "rejected_by",
            "cancelled_at",
            "cancelled_by",
            "withdrawn_at",
            "withdrawn_by",
        ):
            assert key in data


class TestCreditApplicationWorkflowApi:
    async def _create_submitted(self, client: AsyncClient, admin_headers: dict, org_id: str) -> str:
        r = await client.post(
            _applications_base(org_id),
            headers=admin_headers,
            files={
                "application_data": (
                    None,
                    json.dumps(_valid_create_payload()),
                    "application/json",
                ),
            },
        )
        assert r.status_code == 201
        return r.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_assign_reviewer_returns_message(
        self, client: AsyncClient, admin_headers: dict, admin_user: User, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        r = await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )
        assert r.status_code == 200
        assert r.json()["success"] is True

        d = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        assert d.json()["data"]["status"] == "REVIEWER_ASSIGNED"

    @pytest.mark.asyncio
    async def test_verify_trade_reference(
        self, client: AsyncClient, admin_headers: dict, admin_user: User, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )

        detail = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        ref_id = detail.json()["data"]["trade_references"][0]["id"]

        vr = await client.patch(
            f"{_applications_base(org_id)}/{app_id}/trade-references/{ref_id}/verify",
            headers=admin_headers,
            json={"verification_status": "VERIFIED"},
        )
        assert vr.status_code == 200

    @pytest.mark.asyncio
    async def test_run_credit_check_returns_credit_check_result(
        self,
        client: AsyncClient,
        admin_headers: dict,
        admin_user: User,
        sample_org,
        creditsafe_run_stub: AsyncMock,
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )

        run = await client.post(
            f"{_applications_base(org_id)}/{app_id}/credit-check/run",
            headers=admin_headers,
        )
        assert run.status_code == 200
        payload = run.json()["data"]
        assert payload["outcome"] == "COMPLETED"
        assert payload["report"] is not None
        assert "score" in payload["report"]
        assert "risk_indicators" in payload["report"]

    @pytest.mark.asyncio
    async def test_refresh_credit_check_invalid_state_returns_422(
        self,
        client: AsyncClient,
        admin_headers: dict,
        admin_user: User,
        sample_org,
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)
        await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )

        refresh = await client.post(
            f"{_applications_base(org_id)}/{app_id}/credit-check/refresh",
            headers=admin_headers,
        )
        assert refresh.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_company_financial_info(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        r = await client.patch(
            f"{_applications_base(org_id)}/{app_id}/company-financial-info",
            headers=admin_headers,
            json={"years_trading": 7},
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_full_approval_flow(
        self,
        client: AsyncClient,
        admin_headers: dict,
        admin_user: User,
        sample_org,
        creditsafe_run_stub: AsyncMock,
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )

        detail = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        refs = detail.json()["data"]["trade_references"]
        for ref in refs:
            await client.patch(
                f"{_applications_base(org_id)}/{app_id}/trade-references/{ref['id']}/verify",
                headers=admin_headers,
                json={"verification_status": "VERIFIED"},
            )

        await client.post(
            f"{_applications_base(org_id)}/{app_id}/credit-check/run",
            headers=admin_headers,
        )

        ready = await client.post(
            f"{_applications_base(org_id)}/{app_id}/ready-for-decision",
            headers=admin_headers,
        )
        assert ready.status_code == 200

        appr = await client.post(
            f"{_applications_base(org_id)}/{app_id}/approve",
            headers=admin_headers,
            json={
                "approved_credit_limit": "25000.00",
                "approved_payment_terms_days": 30,
                "review_frequency": "QUARTERLY",
                "approval_notes": "Ok.",
            },
        )
        assert appr.status_code == 200

        final = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        data = final.json()["data"]
        assert data["status"] == "APPROVED"
        assert data["approved_at"] is not None
        assert data["approved_by"]["id"] == admin_user.id
        assert data["decided_at"] is not None

    @pytest.mark.asyncio
    async def test_reject(
        self, client: AsyncClient, admin_headers: dict, admin_user: User, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        await client.post(
            f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
            headers=admin_headers,
            json={"reviewer_user_id": admin_user.id},
        )

        r = await client.post(
            f"{_applications_base(org_id)}/{app_id}/reject",
            headers=admin_headers,
            json={
                "rejection_category": "POOR_FINANCIAL_STANDING",
                "detailed_reason": "Below threshold.",
            },
        )
        assert r.status_code == 200

        d = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        data = d.json()["data"]
        assert data["status"] == "REJECTED"
        assert data["rejected_at"] is not None
        assert data["rejected_by"]["id"] == admin_user.id
        assert data["decided_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel(
        self, client: AsyncClient, admin_headers: dict, admin_user: User, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        r = await client.post(
            f"{_applications_base(org_id)}/{app_id}/cancel",
            headers=admin_headers,
            json={"reason": "Duplicate application"},
        )
        assert r.status_code == 200

        d = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        data = d.json()["data"]
        assert data["status"] == "CANCELLED"
        assert data["cancelled_at"] is not None
        assert data["cancelled_by"]["id"] == admin_user.id
        assert data["decided_at"] is not None

    @pytest.mark.asyncio
    async def test_withdraw_sets_withdrawn_actor(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session,
        user_factory,
        sample_org: Organization,
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)
        owner = await _b2b_account_owner(db_session, user_factory, sample_org)
        b2b_h = _b2b_headers(owner)

        w = await client.post(
            f"{_applications_base(org_id)}/{app_id}/withdraw",
            headers=b2b_h,
        )
        assert w.status_code == 200

        d = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        data = d.json()["data"]
        assert data["status"] == "WITHDRAWN"
        assert data["withdrawn_at"] is not None
        assert data["withdrawn_by"]["id"] == owner.id

    @pytest.mark.asyncio
    async def test_cancel_without_reason_422(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        r = await client.post(
            f"{_applications_base(org_id)}/{app_id}/cancel",
            headers=admin_headers,
            json={},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_application(
        self, client: AsyncClient, admin_headers: dict, sample_org
    ) -> None:
        org_id = sample_org.id
        app_id = await self._create_submitted(client, admin_headers, org_id)

        delete = await client.delete(
            f"{_applications_base(org_id)}/{app_id}",
            headers=admin_headers,
        )
        assert delete.status_code == 200

        gone = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
        assert gone.status_code == 404
