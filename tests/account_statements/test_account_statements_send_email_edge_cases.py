"""Edge-case tests for account statement send-email."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.account_statements.enums import StatementCreatedByType, StatementPdfStatus
from app.modules.account_statements.models import AccountStatement
from app.modules.organizations.models import Organization
from tests.account_statements.test_account_statements_api import (
    ORG_BASE,
    _admin_headers,
    _create_org,
)


async def _seed_statement(
    db_session: AsyncSession,
    *,
    org_id: str,
    pdf_status: str = StatementPdfStatus.READY.value,
    failure_reason: str | None = None,
) -> AccountStatement:
    stmt = AccountStatement(
        organization_id=org_id,
        statement_number=f"ST-EDGE-{org_id[:8]}",
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("50.00"),
        total_invoice_amount=Decimal("50.00"),
        total_paid=Decimal("0"),
        total_unpaid=Decimal("50.00"),
        total_overdue=Decimal("0"),
        aging_json={},
        pdf_status=pdf_status,
        pdf_r2_key=f"account-statements/{org_id}/edge.pdf" if pdf_status == StatementPdfStatus.READY.value else None,
        failure_reason=failure_reason,
        content_signature=f"sig-edge-{org_id[:8]}",
        created_by_user_type=StatementCreatedByType.ADMIN.value,
    )
    db_session.add(stmt)
    await db_session.flush()
    return stmt


@pytest.mark.asyncio
async def test_send_email_rejects_invalid_recipient(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    stmt = await _seed_statement(db_session, org_id=org.id)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{stmt.id}/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "not-valid"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_send_email_rejects_missing_recipient_field(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    stmt = await _seed_statement(db_session, org_id=org.id)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{stmt.id}/send-email",
        headers=_admin_headers(admin.id),
        json={},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_send_email_statement_not_found(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/00000000-0000-0000-0000-000000000099/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "billing@example.com"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_email_rejects_failed_pdf_status(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    stmt = await _seed_statement(
        db_session,
        org_id=org.id,
        pdf_status=StatementPdfStatus.FAILED.value,
        failure_reason="R2 upload timeout",
    )

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{stmt.id}/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "billing@example.com"},
    )
    assert resp.status_code == 422
    assert "PDF generation failed" in resp.json()["message"]


@pytest.mark.asyncio
async def test_send_email_maps_smtp_runtime_error_to_validation(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    stmt = await _seed_statement(db_session, org_id=org.id)

    async def _raise_smtp(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("SMTP not configured")

    monkeypatch.setattr("app.mailer.client.send_email", _raise_smtp)
    monkeypatch.setattr(
        "app.modules.account_statements.service.generate_presigned_url",
        lambda *args, **kwargs: ("https://example.com/statement.pdf", datetime.now(UTC)),
    )

    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statements/{stmt.id}/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "billing@example.com"},
    )
    assert resp.status_code == 422
    assert "Email service is not configured" in resp.json()["message"]


@pytest.mark.asyncio
async def test_send_email_wrong_org_returns_not_found(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    import uuid

    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org_a = await _create_org(db_session)
    org_b = Organization(
        reference=f"TSTMT{uuid.uuid4().hex[:8].upper()}",
        trading_name="Other Statement Org",
        legal_entity_name="Other Statement Org Ltd",
        companies_house_number="CHSTMT99",
        vat_number="GB999999999",
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
    stmt = await _seed_statement(db_session, org_id=org_a.id)

    resp = await client.post(
        f"{ORG_BASE}/{org_b.id}/account-statements/{stmt.id}/send-email",
        headers=_admin_headers(admin.id),
        json={"recipient_email": "billing@example.com"},
    )
    assert resp.status_code == 404
