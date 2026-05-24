from __future__ import annotations

# Billing payment tests require DB migration 0094 (remittance columns). Run: poetry run alembic upgrade head

from datetime import date, timedelta
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.core.security import create_access_token
from app.modules.billing.models import BillingPayment, BillingPaymentAllocation
from app.modules.invoices.models import Invoice, InvoiceEvent
from app.modules.organizations.models import Organization
from app.modules.permission.service import PermissionService

BILLING = "/v1/billing"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


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
        reference="TBILLING0001",
        trading_name="Billing Test Org",
        legal_entity_name="Billing Test Org Ltd",
        companies_house_number="CHBILL001",
        vat_number="GB123456789",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="1 Billing Street",
        reg_city="London",
        reg_postcode="EC1A 1BB",
        status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()
    return org


async def _create_b2b_payer(user_factory, db_session: AsyncSession, org: Organization):
    return await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )


def _record_payment_json_extra(_payer_id: str | None = None) -> dict:
    return {"client_type": "CUSTOMER_B2B"}


@pytest.mark.asyncio
async def test_admin_records_payment_and_lists_history(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "50.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
            "notes": "Seed payment",
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["organization_id"] == org.id
    assert created["amount"] == "50.00"
    assert created["allocation_status"] == "UNALLOCATED"

    history_resp = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert history_resp.status_code == 200
    payload = history_resp.json()["data"]
    assert payload["total"] >= 1
    assert any(item["id"] == created["id"] for item in payload["items"])


@pytest.mark.asyncio
async def test_admin_payment_history_global_omitting_organization_id(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADMIN with BILLING read may omit organization_id for global history/KPIs; list rows include organization_id."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org_a = await _create_org(db_session)
    org_b = Organization(
        reference="TBILLING_GLOBALB",
        trading_name="Billing Test Org B",
        legal_entity_name="Billing Test Org B Ltd",
        companies_house_number="CHBILL002",
        vat_number="GB987654321",
        date_of_incorporation=date(2021, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="2 Billing Street",
        reg_city="London",
        reg_postcode="EC1A 1CC",
        status="ACTIVE",
    )
    db_session.add(org_b)
    await db_session.flush()
    payer_a = await _create_b2b_payer(user_factory, db_session, org_a)
    payer_b = await _create_b2b_payer(user_factory, db_session, org_b)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    numbers: list[str] = []
    for org, payer, amt in ((org_a, payer_a, "10.00"), (org_b, payer_b, "20.00")):
        create_resp = await client.post(
            f"{BILLING}/payments",
            headers=_admin_headers(admin.id),
            params={"organization_id": org.id},
            json={
                **_record_payment_json_extra(payer.id),
                "amount": amt,
                "payment_date": date.today().isoformat(),
                "status": "NOT_DEPOSITED",
                "provider": "MANUAL",
            },
        )
        assert create_resp.status_code == 201
        numbers.append(create_resp.json()["data"]["payment_number"])

    rows_global: dict[str, dict] = {}
    for num in numbers:
        resp = await client.get(
            f"{BILLING}/payments/history",
            headers=_admin_headers(admin.id),
            params={"search": num, "size": 20},
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]["items"]:
            rows_global[item["payment_number"]] = item

    assert rows_global[numbers[0]]["organization_id"] == org_a.id
    assert rows_global[numbers[1]]["organization_id"] == org_b.id
    assert rows_global[numbers[0]]["organization_reference"] == org_a.reference
    assert rows_global[numbers[0]]["client_id"] == org_a.reference
    assert rows_global[numbers[0]]["organization_trading_name"] == org_a.trading_name
    assert rows_global[numbers[1]]["organization_reference"] == org_b.reference
    assert rows_global[numbers[1]]["client_id"] == org_b.reference
    assert rows_global[numbers[1]]["organization_trading_name"] == org_b.trading_name

    scoped = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params={"organization_id": org_a.id, "search": numbers[1], "size": 20},
    )
    assert scoped.status_code == 200
    assert not any(i["payment_number"] == numbers[1] for i in scoped.json()["data"]["items"])

    scoped_a = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params={"organization_id": org_a.id, "search": numbers[0], "size": 20},
    )
    assert scoped_a.status_code == 200
    assert any(i["payment_number"] == numbers[0] and i["organization_id"] == org_a.id for i in scoped_a.json()["data"]["items"])

    global_kpi = await client.get(f"{BILLING}/payments/kpis", headers=_admin_headers(admin.id))
    assert global_kpi.status_code == 200
    kpi_a = await client.get(
        f"{BILLING}/payments/kpis",
        headers=_admin_headers(admin.id),
        params={"organization_id": org_a.id},
    )
    kpi_b = await client.get(
        f"{BILLING}/payments/kpis",
        headers=_admin_headers(admin.id),
        params={"organization_id": org_b.id},
    )
    assert kpi_a.status_code == 200 and kpi_b.status_code == 200
    dg = Decimal(global_kpi.json()["data"]["total_received"])
    da = Decimal(kpi_a.json()["data"]["total_received"])
    db = Decimal(kpi_b.json()["data"]["total_received"])
    assert dg >= da + db


@pytest.mark.asyncio
async def test_admin_record_payment_requires_organization_scope(
    client: AsyncClient,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    payer = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        json={
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2C",
            "amount": "50.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert resp.status_code == 422
    assert "organization_id is required" in str(resp.json()).lower()


@pytest.mark.asyncio
async def test_allocate_payment_updates_projection_and_returns_allocations(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-899901",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "80.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id, "allocated_amount": "30.00", "notes": "Partial allocation"},
    )
    assert alloc_resp.status_code == 200
    data = alloc_resp.json()["data"]
    assert data["allocated_amount"] == "30.00"
    assert data["unallocated_amount"] == "50.00"
    assert data["allocation_status"] == "PARTIALLY_ALLOCATED"
    assert len(data["allocations"]) == 1
    assert data["allocations"][0]["invoice_id"] == invoice.id
    assert data["allocations"][0]["invoice_total_amount"] == "120.00"
    assert data["allocations"][0]["invoice_remaining_amount"] == "90.00"
    assert data["allocations"][0]["invoice_issue_date"] == invoice.issue_date.isoformat()
    await db_session.refresh(invoice)
    assert str(invoice.paid_amount) == "30.00"
    assert invoice.payment_status == "PARTIALLY_PAID"


@pytest.mark.asyncio
async def test_allocate_payment_accepts_bulk_payload_for_multiple_invoices(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice_a = Invoice(
        invoice_number="INV-899903",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    invoice_b = Invoice(
        invoice_number="INV-899904",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("40.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("8.00"),
        total=Decimal("48.00"),
        status="SENT",
    )
    db_session.add_all([invoice_a, invoice_b])
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "100.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            "allocations": [
                {"invoice_id": invoice_a.id, "allocated_amount": "30.00"},
                {"invoice_id": invoice_b.id, "allocated_amount": "20.00", "notes": "Bulk apply"},
            ]
        },
    )
    assert alloc_resp.status_code == 200
    payload = alloc_resp.json()["data"]
    assert payload["allocated_amount"] == "50.00"
    assert payload["unallocated_amount"] == "50.00"
    ids = {row["invoice_id"] for row in payload["allocations"]}
    assert {invoice_a.id, invoice_b.id}.issubset(ids)
    await db_session.refresh(invoice_a)
    await db_session.refresh(invoice_b)
    assert str(invoice_a.paid_amount) == "30.00"
    assert str(invoice_b.paid_amount) == "20.00"


@pytest.mark.asyncio
async def test_allocate_payment_bulk_validation_is_atomic_no_partial_writes(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice_a = Invoice(
        invoice_number="INV-899905",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    invoice_b = Invoice(
        invoice_number="INV-899906",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add_all([invoice_a, invoice_b])
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "60.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            "allocations": [
                {"invoice_id": invoice_a.id, "allocated_amount": "30.00"},
                {"invoice_id": invoice_b.id, "allocated_amount": "40.00"},
            ]
        },
    )
    assert alloc_resp.status_code == 422
    rows = (await db_session.execute(BillingPaymentAllocation.__table__.select().where(BillingPaymentAllocation.payment_id == payment_id))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_allocation_creates_new_revision_for_same_invoice(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-899902",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "100.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    first = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id, "allocated_amount": "20.00"},
    )
    assert first.status_code == 200
    second = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id, "allocated_amount": "60.00"},
    )
    assert second.status_code == 200
    payload = second.json()["data"]
    assert payload["allocated_amount"] == "80.00"
    assert payload["unallocated_amount"] == "20.00"

    rows = (await db_session.execute(BillingPaymentAllocation.__table__.select().where(BillingPaymentAllocation.payment_id == payment_id))).all()
    revision_nos = sorted(row.revision_no for row in rows)
    assert revision_nos == [1, 2]


@pytest.mark.asyncio
async def test_payment_history_supports_multivalue_filters_and_kpis(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "110.00",
            "payment_date": date.today().isoformat(),
            "status": "PENDING",
            "provider": "BANK_TRANSFER",
        },
    )
    await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "90.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )

    history_resp = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params=[
            ("organization_id", org.id),
            ("status", "PENDING"),
            ("status", "DEPOSITED"),
            ("provider", "BANK_TRANSFER"),
            ("provider", "MANUAL"),
        ],
    )
    assert history_resp.status_code == 200
    items = history_resp.json()["data"]["items"]
    assert len(items) >= 2
    assert {"PENDING", "DEPOSITED"}.issubset({item["status"] for item in items})

    kpi_resp = await client.get(
        f"{BILLING}/payments/kpis",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert kpi_resp.status_code == 200
    kpis = kpi_resp.json()["data"]
    assert Decimal(kpis["total_received"]) >= Decimal("200.00")
    assert Decimal(kpis["pending"]) >= Decimal("110.00")


@pytest.mark.asyncio
async def test_payment_history_options_returns_filter_enums(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(f"{BILLING}/payments/options", headers=_admin_headers(admin.id))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "PENDING" in data["statuses"]
    assert "UNALLOCATED" in data["allocation_statuses"]
    assert "MANUAL" in data["providers"]
    assert "VOIDED" not in data["statuses"]


@pytest.mark.asyncio
async def test_payment_history_rejects_invalid_date_range(client: AsyncClient, user_factory) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params={
            "payment_date_from": "2026-05-10",
            "payment_date_to": "2026-05-01",
        },
    )
    assert resp.status_code == 422


# Minimal PDF body so libmagic reports application/pdf
_MINIMAL_VALID_PDF = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n200\n%%EOF\n"
)


@pytest.mark.asyncio
async def test_record_payment_multipart_attaches_remittance_advice(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    captured: dict[str, object] = {}

    async def _fake_upload(key: str, content: bytes, content_type: str) -> str:
        captured["key"] = key
        captured["content_len"] = len(content)
        captured["content_type"] = content_type
        return key

    async def _fake_delete(key: str) -> None:
        captured["deleted"] = key

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)
    monkeypatch.setattr("app.storage.upload.upload_to_r2", _fake_upload)
    monkeypatch.setattr("app.storage.upload.delete_from_r2", _fake_delete)

    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "25.50",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "NOT_DEPOSITED",
            "provider": "BANK_TRANSFER",
        },
        files={"remittance_advice": ("bank-slip.pdf", _MINIMAL_VALID_PDF, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["amount"] == "25.50"
    assert data["remittance_advice"] is not None
    assert data["remittance_advice"]["content_type"] == "application/pdf"
    assert data["remittance_advice"]["original_filename"] == "bank-slip.pdf"
    assert int(data["remittance_advice"]["size_bytes"]) == len(_MINIMAL_VALID_PDF)
    assert str(captured["key"]).startswith(f"billing/remittance-advice/{org.id}/")


@pytest.mark.asyncio
async def test_record_payment_multipart_accepts_single_allocation_fields(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-MP-SINGLE-01",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "50.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "DEPOSITED",
            "provider": "MANUAL",
            "allocation_invoice_id": invoice.id,
            "allocation_allocated_amount": "30.00",
            "allocation_notes": "single in multipart",
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()["data"]
    assert payload["allocated_amount"] == "30.00"
    assert payload["unallocated_amount"] == "20.00"
    assert payload["allocation_status"] == "PARTIALLY_ALLOCATED"
    assert len(payload["allocations"]) == 1
    assert payload["allocations"][0]["invoice_id"] == invoice.id


@pytest.mark.asyncio
async def test_record_payment_multipart_accepts_bulk_allocations_json(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice_a = Invoice(
        invoice_number="INV-MP-BULK-01",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    invoice_b = Invoice(
        invoice_number="INV-MP-BULK-02",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        status="SENT",
    )
    db_session.add_all([invoice_a, invoice_b])
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "80.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "DEPOSITED",
            "provider": "MANUAL",
            "allocations_json": json.dumps(
                {
                    "allocations": [
                        {"invoice_id": invoice_a.id, "allocated_amount": "30.00"},
                        {"invoice_id": invoice_b.id, "allocated_amount": "20.00", "notes": "bulk in multipart"},
                    ]
                }
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()["data"]
    assert payload["allocated_amount"] == "50.00"
    assert payload["unallocated_amount"] == "30.00"
    ids = {row["invoice_id"] for row in payload["allocations"]}
    assert {invoice_a.id, invoice_b.id}.issubset(ids)


@pytest.mark.asyncio
async def test_record_payment_multipart_rejects_mixed_single_and_allocations_json(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "20.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "DEPOSITED",
            "provider": "MANUAL",
            "allocation_invoice_id": str(uuid4()),
            "allocation_allocated_amount": "10.00",
            "allocations_json": json.dumps({"allocations": [{"invoice_id": str(uuid4()), "allocated_amount": "5.00"}]}),
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert any(d.get("field") == "allocations_json" for d in (body["error"].get("details") or []))


@pytest.mark.asyncio
async def test_record_payment_multipart_allocation_failure_rolls_back_and_cleans_remittance(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-MP-ROLLBACK-01",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    uploaded: dict[str, str] = {}
    deleted: dict[str, str] = {}

    async def _fake_upload(key: str, content: bytes, content_type: str) -> str:
        uploaded["key"] = key
        return key

    async def _fake_delete(key: str) -> None:
        deleted["key"] = key

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)
    monkeypatch.setattr("app.storage.upload.upload_to_r2", _fake_upload)
    monkeypatch.setattr("app.storage.upload.delete_from_r2", _fake_delete)

    marker_note = f"rollback-{uuid4().hex[:8]}"
    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "50.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "DEPOSITED",
            "provider": "MANUAL",
            "notes": marker_note,
            "allocation_invoice_id": invoice.id,
            "allocation_allocated_amount": "90.00",
        },
        files={"remittance_advice": ("bank-slip.pdf", _MINIMAL_VALID_PDF, "application/pdf")},
    )
    assert resp.status_code == 422
    assert "OVER_ALLOCATED" in str(resp.json())
    assert uploaded.get("key")
    assert deleted.get("key") == uploaded.get("key")

    exists_stmt = select(BillingPayment.id).where(BillingPayment.notes == marker_note)
    exists = (await db_session.execute(exists_stmt)).scalar_one_or_none()
    assert exists is None


@pytest.mark.asyncio
async def test_remittance_advice_signed_url_and_delete(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def _fake_upload_simple(key: str, content: bytes, ct: str) -> str:
        return key

    async def _fake_delete_simple(key: str) -> None:
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)
    monkeypatch.setattr("app.storage.upload.upload_to_r2", _fake_upload_simple)
    monkeypatch.setattr("app.storage.upload.delete_from_r2", _fake_delete_simple)
    monkeypatch.setattr(
        "app.storage.r2_client.generate_presigned_url",
        lambda file_key, **kwargs: f"https://r2.test/{file_key}?sig=1",
    )

    create_resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
        files={"remittance_advice": ("x.pdf", _MINIMAL_VALID_PDF, "application/pdf")},
    )
    payment_id = create_resp.json()["data"]["id"]

    url_resp = await client.get(
        f"{BILLING}/payments/{payment_id}/remittance-advice/signed-url",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id, "disposition": "inline"},
    )
    assert url_resp.status_code == 200
    url_payload = url_resp.json()["data"]
    assert url_payload["url"].startswith("https://r2.test/")
    assert url_payload["disposition"] == "inline"

    del_resp = await client.delete(
        f"{BILLING}/payments/{payment_id}/remittance-advice",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["remittance_advice"] is None

    url_again = await client.get(
        f"{BILLING}/payments/{payment_id}/remittance-advice/signed-url",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert url_again.status_code == 404


@pytest.mark.asyncio
async def test_multipart_invalid_status_returns_422_with_field_details(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "INVALID_STATUS_X",
            "provider": "MANUAL",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    fields = {d["field"] for d in body["error"].get("details", [])}
    assert "status" in fields


@pytest.mark.asyncio
async def test_multipart_invalid_remittance_file_returns_422_with_remittance_field(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    gif_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x04\x01\x00;"
    resp = await client.post(
        f"{BILLING}/payments/multipart",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        data={
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
        files={"remittance_advice": ("fake.pdf", gif_bytes, "application/pdf")},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    details = body["error"].get("details") or []
    assert any(d.get("field") == "remittance_advice" for d in details)
    assert "image/gif" in body["message"] or any("image/gif" in (d.get("message") or "") for d in details)


@pytest.mark.asyncio
async def test_put_remittance_rejects_empty_file(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "15.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    put_resp = await client.put(
        f"{BILLING}/payments/{payment_id}/remittance-advice",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        files={"remittance_advice": ("empty.pdf", b"", "application/pdf")},
    )
    assert put_resp.status_code == 422
    body = put_resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert any(d.get("field") == "remittance_advice" for d in (body["error"].get("details") or []))


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_returns_balance_due(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-CAND-01",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) >= 1
    match = next((i for i in items if i["invoice_id"] == invoice.id), None)
    assert match is not None
    assert match["balance_due"] == "120.00"
    assert match["invoice_number"] == "INV-CAND-01"


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_accepts_b2b_shorthand_client_type(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "B2B",
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_admin_allows_org_wide_when_customer_omitted(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer_a = await _create_b2b_payer(user_factory, db_session, org)
    payer_b = await _create_b2b_payer(user_factory, db_session, org)
    inv_a = Invoice(
        invoice_number="INV-CAND-ORG-A",
        organization_id=org.id,
        customer_id=payer_a.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("80.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("16.00"),
        total=Decimal("96.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    inv_b = Invoice(
        invoice_number="INV-CAND-ORG-B",
        organization_id=org.id,
        customer_id=payer_b.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    db_session.add_all([inv_a, inv_b])
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["total"] == 2
    ids = {item["invoice_id"] for item in payload["items"]}
    assert ids == {inv_a.id, inv_b.id}


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_admin_b2c_requires_customer_id(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "client_type": "CUSTOMER_B2C",
        },
    )
    assert resp.status_code == 422
    assert "customer_id" in str(resp.json()).lower()


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_total_excludes_void_invoices(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    open_inv = Invoice(
        invoice_number="INV-CAND-OPEN",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    voided_inv = Invoice(
        invoice_number="INV-CAND-VOID",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    db_session.add_all([open_inv, voided_inv])
    await db_session.flush()
    db_session.add(InvoiceEvent(invoice_id=voided_inv.id, event_type="VOIDED", actor_id=admin.id, actor_role="ADMIN"))
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["invoice_id"] == open_inv.id


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_pagination_respects_eligible_total(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    created_ids: list[str] = []
    for idx in range(3):
        inv = Invoice(
            invoice_number=f"INV-CAND-PAGE-{idx}",
            organization_id=org.id,
            customer_id=payer.id,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=Decimal("10.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("2.00"),
            total=Decimal("12.00"),
            paid_amount=Decimal("0"),
            payment_status="UNPAID",
            status="SENT",
        )
        db_session.add(inv)
        await db_session.flush()
        created_ids.append(inv.id)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    first = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "page": 1,
            "size": 2,
            "sort_by": "issue_date",
            "sort_order": "desc",
        },
    )
    assert first.status_code == 200, first.text
    body1 = first.json()["data"]
    assert body1["total"] == 3
    assert len(body1["items"]) == 2
    assert body1["pages"] == 2

    second = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
            "page": 2,
            "size": 2,
            "sort_by": "issue_date",
            "sort_order": "desc",
        },
    )
    assert second.status_code == 200, second.text
    body2 = second.json()["data"]
    assert body2["total"] == 3
    assert len(body2["items"]) == 1
    page_ids = {body1["items"][0]["invoice_id"], body1["items"][1]["invoice_id"], body2["items"][0]["invoice_id"]}
    assert page_ids == set(created_ids)


@pytest.mark.asyncio
async def test_invoice_allocation_candidates_accepts_org_query_for_super_admin(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-CAND-SA-01",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=7),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        paid_amount=Decimal("0"),
        payment_status="UNPAID",
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(super_admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 200, resp.text
    assert any(i["invoice_id"] == invoice.id for i in resp.json()["data"]["items"])


@pytest.mark.asyncio
async def test_patch_payment_notes(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "20.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
            "notes": "Original",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    patch_resp = await client.patch(
        f"{BILLING}/payments/{payment_id}/notes",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"notes": "Revised note body"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["data"]["notes"] == "Revised note body"


@pytest.mark.asyncio
async def test_record_payment_422_when_notes_exceed_500_chars(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
            "notes": "n" * 501,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_record_payment_422_when_client_type_b2c_out_of_scope(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    b2c = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            "customer_id": b2c.id,
            "client_type": "CUSTOMER_B2C",
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert resp.status_code == 422
    fields = {d.get("field") for d in resp.json().get("error", {}).get("details", [])}
    assert "client_type" in fields


@pytest.mark.asyncio
async def test_record_payment_b2b_ignores_customer_id_when_present(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    other = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(other.id),
            "customer_id": other.id,
            "amount": "10.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["data"]["customer_id"] is None
    payment_id = create_resp.json()["data"]["id"]
    row = await db_session.get(BillingPayment, payment_id)
    assert row is not None
    assert row.customer_id is None
    assert row.metadata_json is not None
    assert row.metadata_json.get("deprecated_customer_id") == other.id


@pytest.mark.asyncio
async def test_record_payment_persists_recorded_client_type_in_metadata_json(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "11.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    payment_id = create_resp.json()["data"]["id"]
    row = await db_session.get(BillingPayment, payment_id)
    assert row is not None
    assert row.metadata_json is not None
    assert row.metadata_json.get("recorded_client_type") == "CUSTOMER_B2B"


@pytest.mark.asyncio
async def test_patch_payment_notes_422_when_notes_exceed_500_chars(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "12.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    patch_resp = await client.patch(
        f"{BILLING}/payments/{payment_id}/notes",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"notes": "z" * 501},
    )
    assert patch_resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_payment_notes_allows_empty_string(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "13.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
            "notes": "non-empty",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    patch_resp = await client.patch(
        f"{BILLING}/payments/{payment_id}/notes",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"notes": ""},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["notes"] == ""


@pytest.mark.asyncio
async def test_patch_payment_notes_409_when_version_is_stale(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "14.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]
    v1 = create_resp.json()["data"]["version"]

    first = await client.patch(
        f"{BILLING}/payments/{payment_id}/notes",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"notes": "first", "version": v1},
    )
    assert first.status_code == 200

    second = await client.patch(
        f"{BILLING}/payments/{payment_id}/notes",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"notes": "second", "version": v1},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_invoice_candidates_422_unknown_customer_id(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": str(uuid4()),
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invoice_candidates_admin_requires_organization_id_query(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "customer_id": payer.id,
            "client_type": "CUSTOMER_B2B",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "organization_id" in str(body).lower()


@pytest.mark.asyncio
async def test_invoice_candidates_422_invalid_client_type_query(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    resp = await client.get(
        f"{BILLING}/payments/invoice-candidates",
        headers=_admin_headers(admin.id),
        params={
            "organization_id": org.id,
            "customer_id": payer.id,
            "client_type": "RETAIL",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_void_payment_and_exclude_from_default_history_and_kpis(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "21.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    assert create_resp.status_code == 201
    payment_id = create_resp.json()["data"]["id"]
    payment_number = create_resp.json()["data"]["payment_number"]

    void_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"reason": "Entered in error"},
    )
    assert void_resp.status_code == 200, void_resp.text
    assert void_resp.json()["data"]["status"] == "VOIDED"

    history_resp = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert history_resp.status_code == 200
    numbers = {item["payment_number"] for item in history_resp.json()["data"]["items"]}
    assert payment_number not in numbers

    void_only = await client.get(
        f"{BILLING}/payments/history",
        headers=_admin_headers(admin.id),
        params=[("organization_id", org.id), ("status", "VOIDED")],
    )
    assert void_only.status_code == 200
    assert any(item["payment_number"] == payment_number for item in void_only.json()["data"]["items"])

    kpi_resp = await client.get(
        f"{BILLING}/payments/kpis",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert kpi_resp.status_code == 200
    kpis = kpi_resp.json()["data"]
    assert Decimal(kpis["total_received"]) == Decimal("0")


@pytest.mark.asyncio
async def test_void_payment_rejects_allocated_payment(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-VOID-001",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "30.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id, "allocated_amount": "10.00"},
    )
    assert alloc_resp.status_code == 200

    void_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={},
    )
    assert void_resp.status_code == 422


@pytest.mark.asyncio
async def test_void_payment_idempotent_when_already_voided(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "18.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    first = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"reason": "duplicate entry"},
    )
    assert first.status_code == 200
    assert first.json()["data"]["status"] == "VOIDED"

    second = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"reason": "already voided"},
    )
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "VOIDED"


@pytest.mark.asyncio
async def test_void_payment_409_when_version_is_stale(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "22.00",
            "payment_date": date.today().isoformat(),
            "status": "NOT_DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]
    v1 = create_resp.json()["data"]["version"]

    ok_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"version": v1},
    )
    assert ok_resp.status_code == 200

    stale = await client.post(
        f"{BILLING}/payments/{payment_id}/void",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"version": v1},
    )
    assert stale.status_code in {200, 409}


@pytest.mark.asyncio
async def test_admin_payment_kpis_uses_billing_acl(
    client_real_permissions: AsyncClient,
    user_factory,
) -> None:
    """ADMIN defaults grant BILLING WRITE; payment KPIs gate on Resource.BILLING."""
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client_real_permissions.get(f"{BILLING}/payments/kpis", headers=_admin_headers(admin.id))
    assert resp.status_code == 200
    assert "total_received" in resp.json()["data"]


@pytest.mark.asyncio
async def test_admin_payment_kpis_denied_when_billing_revoked(
    client_real_permissions: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    perm = PermissionService(db_session)
    await perm.set_permission(admin.id, Resource.BILLING, PermissionLevel.NONE, granted_by=admin.id)

    resp = await client_real_permissions.get(f"{BILLING}/payments/kpis", headers=_admin_headers(admin.id))
    assert resp.status_code == 403
    assert "BILLING" in resp.json()["message"]


@pytest.mark.asyncio
async def test_b2b_payment_history_uses_billing_acl(
    client_real_permissions: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)

    resp = await client_real_permissions.get(
        f"{BILLING}/payments/history",
        headers=_b2b_headers(payer.id, org.id),
        params={"page": 1, "size": 10},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()["data"]


@pytest.mark.asyncio
async def test_replace_payment_allocations_rebalances_and_unallocates_missing_rows(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice_a = Invoice(
        invoice_number="INV-899950",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    invoice_b = Invoice(
        invoice_number="INV-899951",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("60.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("12.00"),
        total=Decimal("72.00"),
        status="SENT",
    )
    db_session.add_all([invoice_a, invoice_b])
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "100.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            "allocations": [
                {"invoice_id": invoice_a.id, "allocated_amount": "40.00"},
                {"invoice_id": invoice_b.id, "allocated_amount": "20.00"},
            ]
        },
    )
    assert alloc_resp.status_code == 200
    assert alloc_resp.json()["data"]["allocated_amount"] == "60.00"

    replace_resp = await client.patch(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            "allocations": [
                {"invoice_id": invoice_b.id, "allocated_amount": "35.00"},
            ]
        },
    )
    assert replace_resp.status_code == 200
    data = replace_resp.json()["data"]
    assert data["allocated_amount"] == "35.00"
    assert data["unallocated_amount"] == "65.00"
    assert len(data["allocations"]) == 1
    assert data["allocations"][0]["invoice_id"] == invoice_b.id
    assert data["allocations"][0]["allocated_amount"] == "35.00"

    await db_session.refresh(invoice_a)
    await db_session.refresh(invoice_b)
    assert str(invoice_a.paid_amount) == "0.00"
    assert str(invoice_b.paid_amount) == "35.00"


@pytest.mark.asyncio
async def test_remove_payment_allocation_endpoint_unallocates_invoice(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    org = await _create_org(db_session)
    payer = await _create_b2b_payer(user_factory, db_session, org)
    invoice = Invoice(
        invoice_number="INV-899952",
        organization_id=org.id,
        customer_id=payer.id,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        status="SENT",
    )
    db_session.add(invoice)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.modules.billing.service.enqueue", _fake_enqueue)

    create_resp = await client.post(
        f"{BILLING}/payments",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={
            **_record_payment_json_extra(payer.id),
            "amount": "50.00",
            "payment_date": date.today().isoformat(),
            "status": "DEPOSITED",
            "provider": "MANUAL",
        },
    )
    payment_id = create_resp.json()["data"]["id"]

    alloc_resp = await client.post(
        f"{BILLING}/payments/{payment_id}/allocations",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
        json={"invoice_id": invoice.id, "allocated_amount": "25.00"},
    )
    assert alloc_resp.status_code == 200

    remove_resp = await client.delete(
        f"{BILLING}/payments/{payment_id}/allocations/{invoice.id}",
        headers=_admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert remove_resp.status_code == 200
    data = remove_resp.json()["data"]
    assert data["allocated_amount"] == "0.00"
    assert data["allocation_status"] == "UNALLOCATED"
    assert data["allocations"] == []
