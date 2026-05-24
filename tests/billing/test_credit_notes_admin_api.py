"""Admin credit note API tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invoices.models import CreditNote, InvoiceCreditApplication
from tests.billing.credit_notes_helpers import (
    ADMIN_CN,
    admin_headers,
    ensure_credit_note_schema,
    make_credit_note,
    make_sent_invoice,
    seed_credit_note_fixture,
)


@pytest.mark.asyncio
async def test_admin_create_credit_note_with_customer(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNADMINCREATE01")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)

    resp = await client.post(
        ADMIN_CN,
        headers=admin_headers(admin.id),
        json={
            "organization_id": org.id,
            "customer_id": customer.id,
            "issue_date": date.today().isoformat(),
            "amount": "40.00",
            "reason_category": "OTHER",
            "reason": "Manual adjustment",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["total_credit_amount"] == "40.00"
    assert body["status"] == "OPEN"


@pytest.mark.asyncio
async def test_admin_create_credit_note_from_source_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-SRC-INV")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    invoice = make_sent_invoice(organization_id=org.id, customer_id=customer.id, invoice_number="INV-SRC")
    db_session.add(invoice)
    await db_session.flush()

    resp = await client.post(
        ADMIN_CN,
        headers=admin_headers(admin.id),
        json={
            "organization_id": org.id,
            "source_invoice_id": invoice.id,
            "issue_date": date.today().isoformat(),
            "amount": "15.00",
            "reason_category": "OTHER",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["source_invoice_id"] == invoice.id


@pytest.mark.asyncio
async def test_admin_create_rejects_non_positive_amount(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-BAD-AMT")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.post(
        ADMIN_CN,
        headers=admin_headers(admin.id),
        json={
            "organization_id": org.id,
            "issue_date": date.today().isoformat(),
            "amount": "0",
            "reason_category": "OTHER",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_list_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    resp = await client.get(ADMIN_CN, headers=admin_headers(admin.id))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_list_credit_notes_by_organization(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org_a = await org_factory(reference="CN-ADMIN-LIST-A")
    org_b = await org_factory(reference="CN-ADMIN-LIST-B")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    db_session.add(make_credit_note(organization_id=org_a.id, customer_id=None, credit_note_number="CN-LIST-A"))
    db_session.add(make_credit_note(organization_id=org_b.id, customer_id=None, credit_note_number="CN-LIST-B"))
    await db_session.flush()

    resp = await client.get(ADMIN_CN, headers=admin_headers(admin.id), params={"organization_id": org_a.id})
    assert resp.status_code == 200, resp.text
    numbers = {item["credit_note_number"] for item in resp.json()["data"]["items"]}
    assert "CN-LIST-A" in numbers
    assert "CN-LIST-B" not in numbers


@pytest.mark.asyncio
async def test_admin_get_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-GET-NO-ORG")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-GET-NO-ORG")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(f"{ADMIN_CN}/{cn.id}", headers=admin_headers(admin.id))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_create_rejects_customer_from_other_org(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org_a = await org_factory(reference="CN-ADMIN-CUST-A")
    org_b = await org_factory(reference="CN-ADMIN-CUST-B")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org_b.id)

    resp = await client.post(
        ADMIN_CN,
        headers=admin_headers(admin.id),
        json={
            "organization_id": org_a.id,
            "customer_id": customer_b.id,
            "issue_date": date.today().isoformat(),
            "amount": "10.00",
            "reason_category": "OTHER",
        },
    )
    assert resp.status_code == 422
    assert "organisation" in resp.json()["message"].lower() or "organization" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_get_credit_note_detail(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-GET")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, _ = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=customer.id, cn_number="CN-ADMIN-DETAIL"
    )

    resp = await client.get(
        f"{ADMIN_CN}/{cn.id}",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["credit_note_number"] == "CN-ADMIN-DETAIL"


@pytest.mark.asyncio
async def test_admin_apply_credit_note_to_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-APPLY")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=customer.id, cn_amount=Decimal("35.00")
    )

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["data"]["applied_amount"]) == Decimal("35.00")


@pytest.mark.asyncio
async def test_admin_apply_rejects_wrong_org(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org_a = await org_factory(reference="CN-ADMIN-APPLY-A")
    org_b = await org_factory(reference="CN-ADMIN-APPLY-B")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org_a.id)
    cn, invoice = await seed_credit_note_fixture(db_session, org_id=org_a.id, customer_id=customer.id)

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org_b.id},
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_invoice_candidates_for_credit_note(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-CAND")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=customer.id)

    resp = await client.get(
        f"{ADMIN_CN}/{cn.id}/invoice-candidates",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    ids = {row["invoice_id"] for row in resp.json()["data"]["items"]}
    assert invoice.id in ids


@pytest.mark.asyncio
async def test_admin_create_sets_customer_from_source_invoice_when_omitted(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-CUST-SRC")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    invoice = make_sent_invoice(organization_id=org.id, customer_id=customer.id, invoice_number="INV-CUST-SRC")
    db_session.add(invoice)
    await db_session.flush()

    resp = await client.post(
        ADMIN_CN,
        headers=admin_headers(admin.id),
        json={
            "organization_id": org.id,
            "source_invoice_id": invoice.id,
            "issue_date": date.today().isoformat(),
            "amount": "22.00",
            "reason_category": "OTHER",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["customer_id"] == customer.id


@pytest.mark.asyncio
async def test_admin_void_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-VOID-NO-ORG")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-VOID-NO-ORG")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.post(f"{ADMIN_CN}/{cn.id}/void", headers=admin_headers(admin.id))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_client_email_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNADMEMAILNOORG01")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-EMAIL-NO-ORG")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(f"{ADMIN_CN}/{cn.id}/client-email", headers=admin_headers(admin.id))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_send_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-SEND-NO-ORG")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-SEND-NO-ORG")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/send-to-client",
        headers=admin_headers(admin.id),
        json={"email": "x@example.com"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_candidates_rejects_unassigned_credit_note(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-CAND-NOCUST")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-ADM-NOCUST")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(
        f"{ADMIN_CN}/{cn.id}/invoice-candidates",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 422
    assert "customer" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_apply_rejects_unassigned_credit_note(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNADMINAPPLYNOCUST")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-ADM-APPLY-NC")
    inv = make_sent_invoice(organization_id=org.id, customer_id=customer.id, invoice_number="INV-ADM-NC")
    db_session.add_all([cn, inv])
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": inv.id},
    )
    assert resp.status_code == 422
    assert "applying credit" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_apply_rejects_customer_mismatch(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-MISMATCH")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, _ = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=user_a.id)
    inv_b = make_sent_invoice(organization_id=org.id, customer_id=user_b.id, invoice_number="INV-ADM-MISMATCH")
    db_session.add(inv_b)
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": inv_b.id},
    )
    assert resp.status_code == 422
    assert "customer" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_apply_rejects_draft_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    from datetime import timedelta

    from app.modules.invoices.models import Invoice

    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-DRAFT")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, _ = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=customer.id)
    draft = Invoice(
        invoice_number="INV-ADM-DRAFT",
        organization_id=org.id,
        customer_id=customer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="DRAFT",
        payment_status="UNPAID",
    )
    db_session.add(draft)
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": draft.id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_apply_rejects_voided_credit_note(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-VOID-CN")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=customer.id)
    cn.status = "VOIDED"
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_apply_returns_422_when_nothing_left(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-NOTHING")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=customer.id, cn_amount=Decimal("10.00")
    )
    db_session.add(
        InvoiceCreditApplication(
            invoice_id=invoice.id,
            credit_note_id=cn.id,
            applied_amount=Decimal("10.00"),
            applied_at=date.today(),
        )
    )
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/apply",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 422
    assert "nothing" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_void_credit_note_without_applications(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNADMINVOID001")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-VOID-OK")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/void",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"reason": "Issued in error"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "VOID"


@pytest.mark.asyncio
async def test_admin_void_with_applications_creates_reversal(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-VOID-BLOCK")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=customer.id)
    db_session.add(
        InvoiceCreditApplication(
            invoice_id=invoice.id,
            credit_note_id=cn.id,
            applied_amount=Decimal("10.00"),
            applied_at=date.today(),
        )
    )
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/void",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"reason": "Reverse applied credit"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "VOID"
    assert data.get("reversal_invoice_id")


@pytest.mark.asyncio
async def test_admin_request_credit_note_pdf(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-PDF")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-ADMIN-PDF-1")
    db_session.add(cn)
    await db_session.flush()

    async def _fake_enqueue(*_args, **_kwargs):  # noqa: ANN002
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/pdf",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] in {"GENERATING", "READY"}


@pytest.mark.asyncio
async def test_admin_credit_note_pdf_status_requires_organization_id(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-PDF-STATUS")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-PDF-STATUS")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(
        f"{ADMIN_CN}/{cn.id}/pdf",
        headers=admin_headers(admin.id),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_send_credit_note_to_client(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-SEND")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        email="cn-client@example.com",
    )
    cn = make_credit_note(organization_id=org.id, customer_id=customer.id, credit_note_number="CN-SEND")
    db_session.add(cn)
    await db_session.flush()
    sent: dict[str, str] = {}

    async def _fake_send_email(to_address: str, subject: str, *, html_body=None, template_name=None, context=None):
        sent["to"] = to_address

    monkeypatch.setattr("app.modules.billing.service.send_email", _fake_send_email, raising=False)
    monkeypatch.setattr("app.mailer.client.send_email", _fake_send_email)

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/send-to-client",
        headers=admin_headers(admin.id),
        json={"email": "override@example.com"},
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    assert sent["to"] == "override@example.com"
    refreshed = (
        await db_session.execute(select(CreditNote).where(CreditNote.id == cn.id))
    ).scalar_one()
    assert refreshed.sent_to_email == "override@example.com"


@pytest.mark.asyncio
async def test_admin_client_email_prefill(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-ADMIN-EMAIL")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    customer = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        email="billing@client.example",
    )
    cn = make_credit_note(organization_id=org.id, customer_id=customer.id, credit_note_number="CN-EMAIL")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(
        f"{ADMIN_CN}/{cn.id}/client-email",
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["email"] == "billing@client.example"


@pytest.mark.asyncio
async def test_admin_send_requires_email_when_none_available(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNADMINSENDNE01")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-NO-EMAIL")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.post(
        f"{ADMIN_CN}/{cn.id}/send-to-client",
        headers=admin_headers(admin.id),
        json={},
        params={"organization_id": org.id},
    )
    assert resp.status_code == 422
