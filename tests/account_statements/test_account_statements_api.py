"""API tests for account statements.

Requires DB migration 0138_account_statements. Run: poetry run alembic upgrade head
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.core.security import create_access_token
from app.modules.account_statements.enums import StatementPdfStatus
from app.modules.account_statements.models import AccountStatement
from app.modules.invoices.enums import InvoiceStatus, PaymentStatus
from app.modules.permission.service import PermissionService
from app.modules.invoices.models import Invoice
from app.modules.organizations.models import Organization

ORG_BASE = "/v1/organizations"
BILLING_B2B = "/v1/billing/b2b/account-statements"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _b2b_headers(user_id: str, organization_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


async def _create_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        reference="TSTMT0001",
        trading_name="Statement Test Org",
        legal_entity_name="Statement Test Org Ltd",
        companies_house_number="CHSTMT01",
        vat_number="GB111111111",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="1 Test Lane",
        reg_city="Cardiff",
        reg_postcode="CF10 1AA",
        status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()
    return org


async def _create_sent_invoice(db_session: AsyncSession, org: Organization, *, issue: date, total: str) -> Invoice:
    inv = Invoice(
        organization_id=org.id,
        issue_date=issue,
        due_date=issue + timedelta(days=30),
        subtotal=Decimal(total),
        vat_rate=Decimal("0"),
        vat_amount=Decimal("0"),
        total=Decimal(total),
        paid_amount=Decimal("0"),
        payment_status=PaymentStatus.UNPAID.value,
        status=InvoiceStatus.SENT.value,
    )
    db_session.add(inv)
    await db_session.flush()
    return inv


@pytest.mark.asyncio
async def test_admin_preview_and_summary(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    await _create_sent_invoice(db_session, org, issue=date(2026, 1, 15), total="100.00")

    period_start = date(2026, 1, 1)
    period_end = date(2026, 1, 31)
    params = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "include_credit_notes": "true",
        "include_payment_history": "true",
    }

    preview = await client.get(
        f"{ORG_BASE}/{org.id}/account-statements/preview",
        headers=_admin_headers(admin.id),
        params=params,
    )
    assert preview.status_code == 200
    ledger = preview.json()["data"]["ledger"]
    assert Decimal(ledger["total_invoice_amount"]) == Decimal("100.00")

    summary = await client.get(
        f"{ORG_BASE}/{org.id}/account-statements/summary",
        headers=_admin_headers(admin.id),
        params=params,
    )
    assert summary.status_code == 200
    assert "opening_balance" in summary.json()["data"]


@pytest.mark.asyncio
async def test_admin_create_statement_enqueues_pdf(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    await _create_sent_invoice(db_session, org, issue=date(2026, 2, 1), total="250.00")

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return type("Job", (), {"job_id": "job-test-1"})()

    monkeypatch.setattr("app.modules.account_statements.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements",
        headers=_admin_headers(admin.id),
        json={
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "include_line_item_detail": False,
            "include_credit_notes": True,
            "include_payment_history": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["statement_number"].startswith("ST-")
    assert body["pdf_status"] in {"GENERATING", "PENDING", "READY"}


@pytest.mark.asyncio
async def test_b2b_cannot_access_other_org_statement(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org(db_session)
    org_b = Organization(
        reference="TSTMT0002",
        trading_name="Other Org",
        legal_entity_name="Other Org Ltd",
        companies_house_number="CHSTMT02",
        vat_number="GB222222222",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="2 Test Lane",
        reg_city="Cardiff",
        reg_postcode="CF10 2AA",
        status="ACTIVE",
    )
    db_session.add(org_b)
    await db_session.flush()
    payer_a = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org_a.id,
    )
    payer_b = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org_b.id,
    )

    list_a = await client.get(BILLING_B2B, headers=_b2b_headers(payer_a.id, org_a.id))
    assert list_a.status_code == 200

    list_b_as_a = await client.get(
        BILLING_B2B,
        headers=_b2b_headers(payer_a.id, org_a.id),
        params={"search": "ST-"},
    )
    assert list_b_as_a.status_code == 200
    for item in list_b_as_a.json()["data"]["items"]:
        assert item["organization_id"] == org_a.id

    _ = payer_b  # ensure second user created for tenancy isolation setup


@pytest.mark.asyncio
async def test_validate_period_rejected_via_api(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements",
        headers=_admin_headers(admin.id),
        json={
            "period_start": "2026-03-01",
            "period_end": "2026-02-01",
            "include_credit_notes": True,
            "include_payment_history": True,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_account_statements_use_billing_acl(
    client_real_permissions: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    period_start = date(2026, 1, 1)
    period_end = date(2026, 1, 31)

    resp = await client_real_permissions.get(
        f"{ORG_BASE}/{org.id}/account-statements/preview",
        headers=_admin_headers(admin.id),
        params={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "include_credit_notes": "true",
            "include_payment_history": "true",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_account_statements_denied_when_billing_revoked(
    client_real_permissions: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    perm = PermissionService(db_session)
    await perm.set_permission(admin.id, Resource.BILLING, PermissionLevel.NONE, granted_by=admin.id)

    resp = await client_real_permissions.get(
        f"{ORG_BASE}/{org.id}/account-statements/summary",
        headers=_admin_headers(admin.id),
        params={"period_start": "2026-01-01", "period_end": "2026-01-31"},
    )
    assert resp.status_code == 403
    assert "BILLING" in resp.json()["message"]


async def _create_statement_via_api(
    client: AsyncClient,
    *,
    admin_id: str,
    org_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return type("Job", (), {"job_id": "job-test-send"})()

    monkeypatch.setattr("app.modules.account_statements.service.enqueue", _fake_enqueue)
    resp = await client.post(
        f"{ORG_BASE}/{org_id}/account-statements",
        headers=_admin_headers(admin_id),
        json={
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "include_line_item_detail": False,
            "include_credit_notes": True,
            "include_payment_history": True,
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


@pytest.mark.asyncio
async def test_send_email_accepts_email_field_alias(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    await _create_sent_invoice(db_session, org, issue=date(2026, 2, 1), total="100.00")
    statement_id = await _create_statement_via_api(
        client,
        admin_id=admin.id,
        org_id=org.id,
        monkeypatch=monkeypatch,
    )

    stmt = await db_session.get(AccountStatement, statement_id)
    assert stmt is not None
    stmt.pdf_status = StatementPdfStatus.READY.value
    stmt.pdf_r2_key = f"account-statements/{org.id}/{statement_id}.pdf"
    await db_session.flush()

    async def _fake_send_email(to_address: str, subject: str, *, html_body=None, template_name=None, context=None):
        return None

    monkeypatch.setattr("app.mailer.client.send_email", _fake_send_email)
    monkeypatch.setattr(
        "app.modules.account_statements.service.generate_presigned_url",
        lambda *args, **kwargs: "https://example.com/statement.pdf",
    )

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{statement_id}/send-email",
        headers=_admin_headers(admin.id),
        json={"email": "wilatun@mailinator.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["recipient_email"] == "wilatun@mailinator.com"
    assert resp.json()["data"]["status"] == "SENT"


@pytest.mark.asyncio
async def test_send_email_queues_when_pdf_generating(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    await _create_sent_invoice(db_session, org, issue=date(2026, 2, 1), total="100.00")
    statement_id = await _create_statement_via_api(
        client,
        admin_id=admin.id,
        org_id=org.id,
        monkeypatch=monkeypatch,
    )

    enqueued: list[dict] = []

    async def _capture_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        enqueued.append(kwargs)
        return type("Job", (), {"job_id": "job-deliver"})()

    monkeypatch.setattr("app.modules.account_statements.service.enqueue", _capture_enqueue)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{statement_id}/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "wilatun@mailinator.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "PENDING"
    assert len(enqueued) == 1
    assert enqueued[0]["recipient_email"] == "wilatun@mailinator.com"
    assert enqueued[0]["statement_id"] == statement_id
