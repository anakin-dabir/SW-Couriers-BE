"""B2B self-serve credit note API tests."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.billing.service import (
    B2bCreditNoteCustomerFilterMode,
    parse_b2b_credit_note_customer_filter,
)
from tests.billing.credit_notes_helpers import (
    B2B_CN,
    b2b_headers,
    ensure_credit_note_schema,
    make_credit_note,
    make_sent_invoice,
    seed_credit_note_fixture,
)


def test_parse_b2b_credit_note_customer_filter() -> None:
    assert parse_b2b_credit_note_customer_filter(None).mode == B2bCreditNoteCustomerFilterMode.ALL_IN_ORG
    assert parse_b2b_credit_note_customer_filter("").mode == B2bCreditNoteCustomerFilterMode.UNASSIGNED_ONLY
    parsed = parse_b2b_credit_note_customer_filter("  uuid-here  ")
    assert parsed.mode == B2bCreditNoteCustomerFilterMode.SPECIFIC_CUSTOMER
    assert parsed.customer_id == "uuid-here"


@pytest.mark.asyncio
async def test_b2b_list_returns_all_org_credit_notes_by_default(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-LIST-ORG")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)

    cn_a, _ = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=user_a.id, cn_number="CN-OWN-A", inv_number="INV-OWN-A"
    )
    db_session.add(
        make_credit_note(
            organization_id=org.id,
            customer_id=user_b.id,
            credit_note_number="CN-OWN-B",
        )
    )
    db_session.add(
        make_credit_note(
            organization_id=org.id,
            customer_id=None,
            credit_note_number="CN-ORG-WIDE",
        )
    )
    await db_session.flush()

    resp = await client.get(B2B_CN, headers=b2b_headers(user_a.id, org.id))
    assert resp.status_code == 200, resp.text
    numbers = {item["credit_note_number"] for item in resp.json()["data"]["items"]}
    assert numbers == {"CN-OWN-A", "CN-OWN-B", "CN-ORG-WIDE"}
    assert cn_a.id in {item["id"] for item in resp.json()["data"]["items"]}


@pytest.mark.asyncio
async def test_b2b_list_customer_id_filter_scopes_to_one_contact(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-FILTER-CUST")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=user_a.id, cn_number="CN-F-A", inv_number="INV-F-A")
    db_session.add(make_credit_note(organization_id=org.id, customer_id=user_b.id, credit_note_number="CN-F-B"))
    await db_session.flush()

    resp = await client.get(B2B_CN, headers=b2b_headers(user_a.id, org.id), params={"customer_id": user_b.id})
    assert resp.status_code == 200, resp.text
    numbers = {item["credit_note_number"] for item in resp.json()["data"]["items"]}
    assert numbers == {"CN-F-B"}


@pytest.mark.asyncio
async def test_b2b_list_empty_customer_id_returns_unassigned_only(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-FILTER-NULL")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    db_session.add(make_credit_note(organization_id=org.id, customer_id=user.id, credit_note_number="CN-ASSIGNED"))
    db_session.add(make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-UNASSIGNED"))
    await db_session.flush()

    resp = await client.get(B2B_CN, headers=b2b_headers(user.id, org.id), params={"customer_id": ""})
    assert resp.status_code == 200, resp.text
    numbers = {item["credit_note_number"] for item in resp.json()["data"]["items"]}
    assert numbers == {"CN-UNASSIGNED"}


@pytest.mark.asyncio
async def test_b2b_list_rejects_customer_id_from_other_org(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org_a = await org_factory(reference="CN-B2B-FILTER-ORG-A")
    org_b = await org_factory(reference="CN-B2B-FILTER-ORG-B")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org_a.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org_b.id)

    resp = await client.get(B2B_CN, headers=b2b_headers(user_a.id, org_a.id), params={"customer_id": user_b.id})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_b2b_can_get_other_customer_credit_note_same_org(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-GET-ORG")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn_b = make_credit_note(organization_id=org.id, customer_id=user_b.id, credit_note_number="CN-OTHER")
    db_session.add(cn_b)
    await db_session.flush()

    resp = await client.get(f"{B2B_CN}/{cn_b.id}", headers=b2b_headers(user_a.id, org.id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["credit_note_number"] == "CN-OTHER"


@pytest.mark.asyncio
async def test_b2b_can_get_org_wide_credit_note_without_customer(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-NULL-CUST")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-NO-CUST")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(f"{B2B_CN}/{cn.id}", headers=b2b_headers(user.id, org.id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["credit_note_number"] == "CN-NO-CUST"


@pytest.mark.asyncio
async def test_b2b_apply_credit_note_full_amount(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-FULL")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=user.id, cn_amount=Decimal("50.00")
    )

    resp = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["data"]["applied_amount"]) == Decimal("50.00")

    apps = (
        await db_session.execute(
            select(InvoiceCreditApplication).where(InvoiceCreditApplication.credit_note_id == cn.id)
        )
    ).scalars().all()
    assert len(apps) == 1
    assert apps[0].applied_amount == Decimal("50.00")


@pytest.mark.asyncio
async def test_b2b_apply_partial_then_remaining_to_second_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-PARTIAL")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, inv1 = await seed_credit_note_fixture(
        db_session,
        org_id=org.id,
        customer_id=user.id,
        cn_amount=Decimal("80.00"),
        inv_total=Decimal("50.00"),
        inv_number="INV-PART-1",
        cn_number="CN-PART",
    )
    inv2 = make_sent_invoice(
        organization_id=org.id,
        customer_id=user.id,
        invoice_number="INV-PART-2",
        total=Decimal("60.00"),
    )
    db_session.add(inv2)
    await db_session.flush()

    first = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": inv1.id},
    )
    assert first.status_code == 200, first.text
    assert Decimal(first.json()["data"]["applied_amount"]) == Decimal("50.00")

    second = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": inv2.id},
    )
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["data"]["applied_amount"]) == Decimal("30.00")


@pytest.mark.asyncio
async def test_b2b_apply_rejects_other_customer_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CNB2BMISMATCH01")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, _ = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=user_a.id)
    inv_b = make_sent_invoice(organization_id=org.id, customer_id=user_b.id, invoice_number="INV-OTHER-CUST")
    db_session.add(inv_b)
    await db_session.flush()

    resp = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user_a.id, org.id),
        json={"invoice_id": inv_b.id},
    )
    assert resp.status_code == 422
    assert "customer" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_apply_rejects_draft_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-DRAFT")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, _ = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=user.id)
    draft = Invoice(
        invoice_number="INV-DRAFT",
        organization_id=org.id,
        customer_id=user.id,
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
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": draft.id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_b2b_apply_rejects_voided_credit_note(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-VOID-CN")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(db_session, org_id=org.id, customer_id=user.id)
    cn.status = "VOIDED"
    await db_session.flush()

    resp = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 422
    assert "not applicable" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_apply_returns_422_when_nothing_left(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-NOTHING")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, invoice = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=user.id, cn_amount=Decimal("10.00")
    )
    db_session.add(
        InvoiceCreditApplication(
            invoice_id=invoice.id,
            credit_note_id=cn.id,
            applied_amount=Decimal("10.00"),
            applied_at=date.today(),
            applied_by=user.id,
        )
    )
    await db_session.flush()

    resp = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": invoice.id},
    )
    assert resp.status_code == 422
    assert "nothing to apply" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_invoice_candidates_only_own_eligible_invoices(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-CANDIDATES")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn, inv_a = await seed_credit_note_fixture(
        db_session, org_id=org.id, customer_id=user_a.id, inv_number="INV-CAND-A"
    )
    inv_b = make_sent_invoice(organization_id=org.id, customer_id=user_b.id, invoice_number="INV-CAND-B")
    db_session.add(inv_b)
    await db_session.flush()

    resp = await client.get(
        f"{B2B_CN}/{cn.id}/invoice-candidates",
        headers=b2b_headers(user_a.id, org.id),
    )
    assert resp.status_code == 200, resp.text
    ids = {row["invoice_id"] for row in resp.json()["data"]["items"]}
    assert inv_a.id in ids
    assert inv_b.id not in ids


@pytest.mark.asyncio
async def test_b2b_invoice_candidates_pagination_total_matches_eligible(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-CAND-PAGE")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn = make_credit_note(organization_id=org.id, customer_id=user.id, credit_note_number="CN-PAGE")
    db_session.add(cn)
    for i in range(3):
        db_session.add(
            make_sent_invoice(
                organization_id=org.id,
                customer_id=user.id,
                invoice_number=f"INV-PAGE-{i}",
            )
        )
    paid_off = make_sent_invoice(
        organization_id=org.id,
        customer_id=user.id,
        invoice_number="INV-PAID-OFF",
        payment_status="PAID",
    )
    paid_off.total = Decimal("0.01")
    db_session.add(paid_off)
    await db_session.flush()

    resp = await client.get(
        f"{B2B_CN}/{cn.id}/invoice-candidates",
        headers=b2b_headers(user.id, org.id),
        params={"page": 1, "size": 2},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["items"]) == 2
    assert data["total"] >= 3


@pytest.mark.asyncio
async def test_b2b_credit_note_is_tenant_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org_a = await org_factory(reference="CN-B2B-TENANT-A")
    org_b = await org_factory(reference="CN-B2B-TENANT-B")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org_a.id)
    cn_b = make_credit_note(organization_id=org_b.id, customer_id=None, credit_note_number="CN-OTHER-ORG")
    db_session.add(cn_b)
    await db_session.flush()

    resp = await client.get(f"{B2B_CN}/{cn_b.id}", headers=b2b_headers(user_a.id, org_a.id))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_b2b_request_pdf_org_scoped_same_org_contact(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-PDF")
    user_a = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    user_b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn_b = make_credit_note(organization_id=org.id, customer_id=user_b.id, credit_note_number="CN-PDF-B")
    db_session.add(cn_b)
    await db_session.flush()

    async def _fake_enqueue(*_args, **_kwargs):  # noqa: ANN002
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    other_contact = await client.post(f"{B2B_CN}/{cn_b.id}/pdf", headers=b2b_headers(user_a.id, org.id))
    assert other_contact.status_code == 200, other_contact.text

    cn_a = make_credit_note(organization_id=org.id, customer_id=user_a.id, credit_note_number="CN-PDF-A")
    db_session.add(cn_a)
    await db_session.flush()
    ok_resp = await client.post(f"{B2B_CN}/{cn_a.id}/pdf", headers=b2b_headers(user_a.id, org.id))
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["data"]["status"] in {"GENERATING", "READY"}


@pytest.mark.asyncio
async def test_b2b_list_invalid_customer_id_uuid_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-BAD-UUID")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)

    resp = await client.get(B2B_CN, headers=b2b_headers(user.id, org.id), params={"customer_id": "not-a-uuid"})
    assert resp.status_code == 422
    assert "customer_id" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_list_unknown_customer_id_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-UNKNOWN-CUST")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    unknown_id = "00000000-0000-4000-8000-000000000099"

    resp = await client.get(B2B_CN, headers=b2b_headers(user.id, org.id), params={"customer_id": unknown_id})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_b2b_candidates_rejects_unassigned_credit_note_without_customer(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-CAND-NOCUST")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-NO-CUST-CAND")
    db_session.add(cn)
    await db_session.flush()

    resp = await client.get(
        f"{B2B_CN}/{cn.id}/invoice-candidates",
        headers=b2b_headers(user.id, org.id),
    )
    assert resp.status_code == 422
    assert "customer" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_apply_rejects_unassigned_credit_note_without_customer(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    org = await org_factory(reference="CN-B2B-APPLY-NOCUST")
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)
    cn = make_credit_note(organization_id=org.id, customer_id=None, credit_note_number="CN-NO-CUST-APPLY")
    inv = make_sent_invoice(organization_id=org.id, customer_id=user.id, invoice_number="INV-NOCUST")
    db_session.add_all([cn, inv])
    await db_session.flush()

    resp = await client.post(
        f"{B2B_CN}/{cn.id}/apply",
        headers=b2b_headers(user.id, org.id),
        json={"invoice_id": inv.id},
    )
    assert resp.status_code == 422
    assert "applying credit" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_b2b_list_invalid_org_id_in_token_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
) -> None:
    await ensure_credit_note_schema(db_session)
    user = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True)
    resp = await client.get(B2B_CN, headers=b2b_headers(user.id, "not-a-uuid"))
    assert resp.status_code == 422
    assert "organization_id must be a valid UUID" in resp.json()["message"]
