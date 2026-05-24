"""Integration API tests for Invoices (v1) — list, create/update draft, finalize, void, write-off, PDF."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.billing.models import BillingPayment, BillingPaymentAllocation, Refund
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication, InvoiceEvent, InvoicePdfArtifact
from app.modules.user.models import User
from tests.invoices.conftest import purge_invoice_domain

INVOICES = "/v1/invoices"


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    """Admin auth headers with INVOICES READ/WRITE via permission_mock."""
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


def _customer_headers(user_id: str, client_type: str = "CUSTOMER_B2C") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type=client_type)
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": client_type,
    }


def _valid_create_payload(*, finalize: bool = False) -> dict[str, Any]:
    """Base payload used for both draft and create-and-finalise."""
    today = date.today()
    issue = today
    due = today + timedelta(days=14)
    return {
        "order_id": None,
        "issue_date": issue.isoformat(),
        "due_date": due.isoformat(),
        "subtotal": 100.0,
        "vat_rate": 20.0,
        "vat_amount": 20.0,
        "total": 120.0,
        "notes": "Test invoice",
        "finalize": finalize,
    }


class TestListInvoices:
    """GET /v1/invoices/ — list invoices (INVOICES READ)."""

    @pytest.mark.asyncio
    async def test_admin_lists_invoices_empty(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        await purge_invoice_domain(db_session)
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(INVOICES, headers=_admin_headers(admin.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_admin_filters_by_payment_status(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        today = date.today()
        unpaid = Invoice(
            invoice_number="INV-900001",
            issue_date=today,
            due_date=today + timedelta(days=10),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
        overdue = Invoice(
            invoice_number="INV-900002",
            issue_date=today - timedelta(days=30),
            due_date=today - timedelta(days=1),
            subtotal=Decimal("80.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("16.00"),
            total=Decimal("96.00"),
            status="SENT",
        )
        db_session.add(unpaid)
        db_session.add(overdue)
        await db_session.flush()

        resp = await client.get(
            INVOICES,
            headers=_admin_headers(admin.id),
            params=[("payment_status", "UNPAID")],
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        invoice_numbers = {item["invoice_number"] for item in items}
        assert "INV-900001" in invoice_numbers
        assert "INV-900002" not in invoice_numbers

    @pytest.mark.asyncio
    async def test_list_reflects_credit_applied_and_zero_balance(
        self,
        client: AsyncClient,
        user_factory,
        org_factory,
        db_session: AsyncSession,
    ) -> None:
        from tests.billing.credit_notes_helpers import (
            ADMIN_CN,
            admin_headers,
            ensure_credit_note_schema,
            seed_credit_note_fixture,
        )

        await ensure_credit_note_schema(db_session)
        org = await org_factory(reference="INV-LIST-CN")
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        customer = await user_factory(
            role="CUSTOMER_B2B",
            status="ACTIVE",
            email_verified=True,
            organization_id=org.id,
        )
        cn, invoice = await seed_credit_note_fixture(
            db_session,
            org_id=org.id,
            customer_id=customer.id,
            cn_amount=Decimal("120.00"),
            inv_total=Decimal("120.00"),
            inv_number="INV-LIST-CN-001",
            cn_number="CN-LIST-CN-001",
        )

        apply_resp = await client.post(
            f"{ADMIN_CN}/{cn.id}/apply",
            headers=admin_headers(admin.id),
            params={"organization_id": org.id},
            json={"invoice_id": invoice.id},
        )
        assert apply_resp.status_code == 200, apply_resp.text

        list_resp = await client.get(INVOICES, headers=_admin_headers(admin.id))
        assert list_resp.status_code == 200, list_resp.text
        row = next(i for i in list_resp.json()["data"]["items"] if i["invoice_number"] == "INV-LIST-CN-001")
        assert row["payment_status"] == "PAID"
        assert Decimal(row["credit_applied"]) == Decimal("120.00")
        assert Decimal(row["paid"]) == Decimal("0")
        assert Decimal(row["balance"]) == Decimal("0")

        detail_resp = await client.get(f"{INVOICES}/{invoice.id}", headers=_admin_headers(admin.id))
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()["data"]
        assert detail["payment_status"] == "PAID"
        assert Decimal(detail["outstanding_balance"]) == Decimal("0")
        assert len(detail["applied_credit_notes"]) == 1
        assert detail["applied_credit_notes"][0]["credit_note_number"] == "CN-LIST-CN-001"

    @pytest.mark.asyncio
    async def test_b2c_list_is_scoped_to_customer(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        owner = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
        foreign = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
        today = date.today()
        db_session.add(
            Invoice(
                invoice_number="INV-910001",
                customer_id=owner.id,
                issue_date=today,
                due_date=today + timedelta(days=10),
                subtotal=Decimal("100.00"),
                vat_rate=Decimal("20.00"),
                vat_amount=Decimal("20.00"),
                total=Decimal("120.00"),
                status="SENT",
            )
        )
        db_session.add(
            Invoice(
                invoice_number="INV-910002",
                customer_id=foreign.id,
                issue_date=today,
                due_date=today + timedelta(days=10),
                subtotal=Decimal("50.00"),
                vat_rate=Decimal("20.00"),
                vat_amount=Decimal("10.00"),
                total=Decimal("60.00"),
                status="SENT",
            )
        )
        await db_session.flush()

        resp = await client.get(INVOICES, headers=_customer_headers(owner.id, client_type="CUSTOMER_B2C"))
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        invoice_numbers = {item["invoice_number"] for item in items}
        assert "INV-910001" in invoice_numbers
        assert "INV-910002" not in invoice_numbers

    @pytest.mark.asyncio
    async def test_b2c_get_invoice_rejects_foreign_customer_invoice(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        owner = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
        foreign = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
        today = date.today()
        own_invoice = Invoice(
            invoice_number="INV-910003",
            customer_id=owner.id,
            issue_date=today,
            due_date=today + timedelta(days=10),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
        foreign_invoice = Invoice(
            invoice_number="INV-910004",
            customer_id=foreign.id,
            issue_date=today,
            due_date=today + timedelta(days=10),
            subtotal=Decimal("80.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("16.00"),
            total=Decimal("96.00"),
            status="SENT",
        )
        db_session.add(own_invoice)
        db_session.add(foreign_invoice)
        await db_session.flush()

        own_resp = await client.get(f"{INVOICES}/{own_invoice.id}", headers=_customer_headers(owner.id, client_type="CUSTOMER_B2C"))
        assert own_resp.status_code == 200

        foreign_resp = await client.get(f"{INVOICES}/{foreign_invoice.id}", headers=_customer_headers(owner.id, client_type="CUSTOMER_B2C"))
        assert foreign_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_can_sort_invoices_by_total_ascending(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        today = date.today()
        db_session.add(
            Invoice(
                invoice_number="INV-920001",
                issue_date=today,
                due_date=today + timedelta(days=10),
                subtotal=Decimal("10.00"),
                vat_rate=Decimal("20.00"),
                vat_amount=Decimal("2.00"),
                total=Decimal("12.00"),
                status="SENT",
            )
        )
        db_session.add(
            Invoice(
                invoice_number="INV-920002",
                issue_date=today,
                due_date=today + timedelta(days=10),
                subtotal=Decimal("100.00"),
                vat_rate=Decimal("20.00"),
                vat_amount=Decimal("20.00"),
                total=Decimal("120.00"),
                status="SENT",
            )
        )
        await db_session.flush()

        resp = await client.get(
            INVOICES,
            headers=_admin_headers(admin.id),
            params={"sort_by": "total", "sort_order": "asc"},
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        totals = [Decimal(item["total"]) for item in items[:2]]
        assert totals[0] <= totals[1]


class TestInvoiceListOrganizationFilter:
    """Optional organization_id on list/summary: admin narrows tenant; B2B cannot spoof."""

    @pytest.mark.asyncio
    async def test_admin_filters_list_by_organization_id(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        org_factory,
    ) -> None:
        await purge_invoice_domain(db_session)

        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org_a = await org_factory(reference="INV-FILTER-A")
        org_b = await org_factory(reference="INV-FILTER-B")
        today = date.today()
        inv_a = Invoice(
            invoice_number="INV-FILT-A1",
            organization_id=org_a.id,
            issue_date=today,
            due_date=today + timedelta(days=7),
            subtotal=Decimal("50.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("10.00"),
            total=Decimal("60.00"),
            status="SENT",
        )
        inv_b = Invoice(
            invoice_number="INV-FILT-B1",
            organization_id=org_b.id,
            issue_date=today,
            due_date=today + timedelta(days=7),
            subtotal=Decimal("50.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("10.00"),
            total=Decimal("60.00"),
            status="SENT",
        )
        db_session.add_all([inv_a, inv_b])
        await db_session.flush()

        resp_all = await client.get(INVOICES, headers=_admin_headers(admin.id))
        assert resp_all.status_code == 200
        all_numbers = {i["invoice_number"] for i in resp_all.json()["data"]["items"]}
        assert "INV-FILT-A1" in all_numbers
        assert "INV-FILT-B1" in all_numbers

        resp_a = await client.get(
            INVOICES,
            headers=_admin_headers(admin.id),
            params={"organization_id": org_a.id},
        )
        assert resp_a.status_code == 200
        nums_a = {i["invoice_number"] for i in resp_a.json()["data"]["items"]}
        assert nums_a == {"INV-FILT-A1"}

    @pytest.mark.asyncio
    async def test_b2b_rejects_foreign_organization_id_query(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        org_factory,
    ) -> None:
        org_own = await org_factory(reference="INV-B2B-OWN")
        org_other = await org_factory(reference="INV-B2B-OTHER")
        b2b = await user_factory(
            role="CUSTOMER_B2B",
            status="ACTIVE",
            email_verified=True,
            organization_id=org_own.id,
        )
        token, _ = create_access_token(
            user_id=b2b.id,
            role="CUSTOMER_B2B",
            client_type="CUSTOMER_B2B",
            organization_id=org_own.id,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "CUSTOMER_B2B",
        }
        resp = await client.get(
            INVOICES,
            headers=headers,
            params={"organization_id": org_other.id},
        )
        assert resp.status_code == 403


class TestCreateInvoice:
    """POST /v1/invoices/ — create invoice (draft or create & finalise)."""

    @pytest.mark.asyncio
    async def test_admin_creates_draft_invoice(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=False))
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["invoice_status"] == "DRAFT"
        assert data["payment_status"] == "UNPAID"
        assert data["subtotal"] == "100.00"
        assert data["vat_amount"] == "20.00"
        assert data["total"] == "120.00"

        # DB row exists and status matches
        invoice = await db_session.get(Invoice, data["id"])
        assert invoice is not None
        assert invoice.status == "DRAFT"

    @pytest.mark.asyncio
    async def test_admin_creates_and_finalises_invoice(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=True))
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["invoice_status"] == "SENT"
        assert data["payment_status"] == "UNPAID"

        invoice = await db_session.get(Invoice, data["id"])
        assert invoice is not None
        assert invoice.status == "SENT"

    @pytest.mark.asyncio
    async def test_create_due_date_before_issue_date_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload()
        payload["issue_date"] = "2025-12-10"
        payload["due_date"] = "2025-12-01"
        resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_admin_create_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.post(INVOICES, headers=_customer_headers(verified_user.id), json=_valid_create_payload())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_create_with_billing_contact_email_and_line_items(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(finalize=True)
        payload["billing_contact_email"] = "billing@example.com"
        payload["line_items"] = [
            {
                "description": "Consulting services",
                "quantity": 1,
                "unit_price": "100.00",
                "total_price": "100.00",
                "line_type": "service",
            }
        ]
        resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["billing_contact_email"] == "billing@example.com"
        assert len(data["line_items"]) == 1
        assert data["line_items"][0]["description"] == "Consulting services"

        invoice = await db_session.get(Invoice, data["id"])
        assert invoice is not None
        assert invoice.billing_contact_email == "billing@example.com"

    @pytest.mark.asyncio
    async def test_create_rejects_line_items_sum_mismatch(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(finalize=False)
        payload["line_items"] = [
            {
                "description": "Item A",
                "quantity": 1,
                "unit_price": "50.00",
                "total_price": "50.00",
                "line_type": "service",
            }
        ]
        resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_burst_create_draft_generates_unique_invoice_numbers(self, client: AsyncClient, user_factory) -> None:
        """Burst create requests should generate unique INV-prefixed numbers."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        responses = []
        for i in range(20):
            payload = _valid_create_payload(finalize=False)
            payload["notes"] = f"Burst invoice {i}"
            responses.append(await client.post(INVOICES, headers=headers, json=payload))
        assert all(r.status_code == 201 for r in responses)
        numbers = [r.json()["data"]["invoice_number"] for r in responses]
        assert len(numbers) == len(set(numbers))
        assert all(num.startswith("INV-") for num in numbers)


class TestUpdateAndFinalizeInvoice:
    """PATCH /v1/invoices/{id} and POST /{id}/finalize."""

    @pytest.mark.asyncio
    async def test_admin_updates_draft_invoice(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload())
        invoice = create_resp.json()["data"]

        patch_payload = {
            "subtotal": 200.0,
            "vat_amount": 40.0,
            "total": 240.0,
        }
        resp = await client.patch(
            INVOICES + f"/{invoice['id']}",
            headers=_admin_headers(admin.id),
            json=patch_payload | {"version": invoice["version"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_status"] == "DRAFT"
        assert data["subtotal"] == "200.00"
        assert data["vat_amount"] == "40.00"
        assert data["total"] == "240.00"

        row = await db_session.get(Invoice, invoice["id"])
        assert row is not None
        assert row.subtotal == Decimal("200.00")
        assert row.total == Decimal("240.00")

    @pytest.mark.asyncio
    async def test_non_admin_update_returns_403(self, client: AsyncClient, verified_user: User, db_session: AsyncSession) -> None:
        # Seed a draft invoice directly
        inv = Invoice(
            invoice_number="INV-000001",
            issue_date=date(2025, 12, 1),
            due_date=date(2025, 12, 15),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="DRAFT",
        )
        db_session.add(inv)
        await db_session.flush()

        resp = await client.patch(
            INVOICES + f"/{inv.id}",
            headers=_customer_headers(verified_user.id),
            json={"subtotal": 150.0, "version": inv.version},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_finalizes_draft_invoice_and_is_idempotent(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload())
        invoice = create_resp.json()["data"]

        # First finalize
        resp = await client.post(INVOICES + f"/{invoice['id']}/finalize", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_status"] == "SENT"

        # Second finalize should be idempotent
        resp2 = await client.post(INVOICES + f"/{invoice['id']}/finalize", headers=_admin_headers(admin.id))
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert data2["invoice_status"] == "SENT"

        row = await db_session.get(Invoice, invoice["id"])
        assert row is not None
        assert row.status == "SENT"


class TestDeleteDraftInvoice:
    """DELETE /v1/invoices/{id} — hard-delete draft only."""

    @pytest.mark.asyncio
    async def test_admin_deletes_draft_invoice(self, client: AsyncClient, user_factory, db_session: AsyncSession) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload())
        invoice = create_resp.json()["data"]
        invoice_id = invoice["id"]

        del_resp = await client.delete(INVOICES + f"/{invoice_id}", headers=_admin_headers(admin.id))
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json()["data"]["deleted"] is True

        row = await db_session.get(Invoice, invoice_id)
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_finalized_invoice_returns_409(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            INVOICES,
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(finalize=True),
        )
        invoice_id = create_resp.json()["data"]["id"]

        del_resp = await client.delete(INVOICES + f"/{invoice_id}", headers=_admin_headers(admin.id))
        assert del_resp.status_code == 409

    @pytest.mark.asyncio
    async def test_non_admin_delete_returns_403(self, client: AsyncClient, verified_user: User, db_session: AsyncSession) -> None:
        inv = Invoice(
            invoice_number="INV-000099",
            issue_date=date(2025, 12, 1),
            due_date=date(2025, 12, 15),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="DRAFT",
        )
        db_session.add(inv)
        await db_session.flush()

        resp = await client.delete(INVOICES + f"/{inv.id}", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_and_detail_include_notes(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        create_resp = await client.post(INVOICES, headers=headers, json=_valid_create_payload())
        assert create_resp.status_code == 201
        invoice_id = create_resp.json()["data"]["id"]
        assert create_resp.json()["data"]["notes"] == "Test invoice"

        detail_resp = await client.get(INVOICES + f"/{invoice_id}", headers=headers)
        assert detail_resp.status_code == 200
        assert detail_resp.json()["data"]["notes"] == "Test invoice"


class TestInvoiceInternalNoteCrud:
    """CRUD for single internal note at /v1/invoices/{id}/internal-note (invoices.notes)."""

    @pytest.mark.asyncio
    async def test_admin_crud_internal_note(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        payload = _valid_create_payload()
        payload.pop("notes", None)
        create_resp = await client.post(INVOICES, headers=headers, json=payload)
        invoice_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]
        note_path = f"{INVOICES}/{invoice_id}/internal-note"

        get_empty = await client.get(note_path, headers=headers)
        assert get_empty.status_code == 200
        body = get_empty.json()["data"]
        assert body["notes"] is None
        assert body["has_note"] is False
        assert body["invoice_status"] == "DRAFT"

        create_note = await client.post(
            note_path,
            headers=headers,
            json={"notes": "Customer requested manual invoice for this booking.", "version": version},
        )
        assert create_note.status_code == 201, create_note.text
        assert create_note.json()["data"]["notes"] == "Customer requested manual invoice for this booking."
        assert create_note.json()["data"]["has_note"] is True
        version = create_note.json()["data"]["version"]

        dup_create = await client.post(
            note_path,
            headers=headers,
            json={"notes": "Another note", "version": version},
        )
        assert dup_create.status_code == 409

        put_note = await client.put(
            note_path,
            headers=headers,
            json={"notes": "Updated internal note", "version": version},
        )
        assert put_note.status_code == 200
        assert put_note.json()["data"]["notes"] == "Updated internal note"
        version = put_note.json()["data"]["version"]

        idempotent_put = await client.put(
            note_path,
            headers=headers,
            json={"notes": "Updated internal note", "version": version},
        )
        assert idempotent_put.status_code == 200
        assert idempotent_put.json()["data"]["version"] == version

        del_note = await client.delete(note_path, headers=headers, params={"version": version})
        assert del_note.status_code == 200
        assert del_note.json()["data"]["notes"] is None
        assert del_note.json()["data"]["has_note"] is False

        idempotent_del = await client.delete(note_path, headers=headers, params={"version": version})
        assert idempotent_del.status_code == 200

    @pytest.mark.asyncio
    async def test_put_upserts_when_note_empty(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        payload = _valid_create_payload()
        payload.pop("notes", None)
        create_resp = await client.post(INVOICES, headers=headers, json=payload)
        invoice_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]
        note_path = f"{INVOICES}/{invoice_id}/internal-note"

        put_resp = await client.put(
            note_path,
            headers=headers,
            json={"notes": "First note via PUT", "version": version},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["data"]["has_note"] is True

    @pytest.mark.asyncio
    async def test_internal_note_on_finalized_invoice(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        payload = _valid_create_payload(finalize=True)
        payload.pop("notes", None)
        create_resp = await client.post(INVOICES, headers=headers, json=payload)
        invoice_id = create_resp.json()["data"]["id"]
        assert create_resp.json()["data"]["invoice_status"] == "SENT"
        version = create_resp.json()["data"]["version"]

        put_resp = await client.put(
            f"{INVOICES}/{invoice_id}/internal-note",
            headers=headers,
            json={"notes": "Note after finalize", "version": version},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["data"]["invoice_status"] == "SENT"
        assert put_resp.json()["data"]["notes"] == "Note after finalize"

    @pytest.mark.asyncio
    async def test_internal_note_stale_version_returns_409(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        payload = _valid_create_payload()
        payload.pop("notes", None)
        create_resp = await client.post(INVOICES, headers=headers, json=payload)
        invoice_id = create_resp.json()["data"]["id"]
        stale_version = create_resp.json()["data"]["version"]

        await client.put(
            f"{INVOICES}/{invoice_id}/internal-note",
            headers=headers,
            json={"notes": "First write", "version": stale_version},
        )

        conflict = await client.put(
            f"{INVOICES}/{invoice_id}/internal-note",
            headers=headers,
            json={"notes": "Second write", "version": stale_version},
        )
        assert conflict.status_code == 409

    @pytest.mark.asyncio
    async def test_super_admin_can_manage_internal_note(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        admin_headers = _admin_headers(admin.id)
        super_headers = _admin_headers(super_admin.id, role="SUPER_ADMIN")
        payload = _valid_create_payload()
        payload.pop("notes", None)
        create_resp = await client.post(INVOICES, headers=admin_headers, json=payload)
        invoice_id = create_resp.json()["data"]["id"]
        version = create_resp.json()["data"]["version"]

        get_resp = await client.get(f"{INVOICES}/{invoice_id}/internal-note", headers=super_headers)
        assert get_resp.status_code == 200

        put_resp = await client.put(
            f"{INVOICES}/{invoice_id}/internal-note",
            headers=super_headers,
            json={"notes": "Super admin note", "version": version},
        )
        assert put_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_non_admin_internal_note_returns_403(self, client: AsyncClient, verified_user: User, db_session: AsyncSession) -> None:
        inv = Invoice(
            invoice_number="INV-000200",
            issue_date=date(2025, 12, 1),
            due_date=date(2025, 12, 15),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="DRAFT",
        )
        db_session.add(inv)
        await db_session.flush()

        resp = await client.get(f"{INVOICES}/{inv.id}/internal-note", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


class TestVoidAndWriteOff:
    """POST /v1/invoices/{id}/void and /write-off."""

    @pytest.mark.asyncio
    async def test_admin_voids_invoice_via_event_outcome(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=True))
        invoice = create_resp.json()["data"]

        resp = await client.post(
            INVOICES + f"/{invoice['id']}/void",
            headers=_admin_headers(admin.id),
            json={"reason": "Customer dispute"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_status"] == "SENT"
        assert data["payment_status"] == "VOID"

        row = await db_session.get(Invoice, invoice["id"])
        assert row is not None
        assert row.status == "SENT"

    @pytest.mark.asyncio
    async def test_admin_write_off_invoice_via_event_outcome(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=True))
        invoice = create_resp.json()["data"]

        resp = await client.post(
            INVOICES + f"/{invoice['id']}/write-off",
            headers=_admin_headers(admin.id),
            json={"reason": "Bad debt"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_status"] == "SENT"
        assert data["payment_status"] == "WRITTEN_OFF"

        row = await db_session.get(Invoice, invoice["id"])
        assert row is not None
        assert row.status == "SENT"


class TestPdfFlow:
    """PDF request, status, and signed URL endpoints."""

    @pytest.mark.asyncio
    async def test_request_pdf_creates_artifact_and_returns_generating(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Patch invoice service enqueue so no real worker is started
        async def _fake_enqueue(*args, **kwargs):
            class _Job:
                job_id = "job-123"

            return _Job()

        monkeypatch.setattr("app.modules.invoices.service.enqueue", _fake_enqueue)

        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=True))
        invoice = create_resp.json()["data"]

        resp = await client.post(INVOICES + f"/{invoice['id']}/pdf", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["status"] == "GENERATING"
        assert payload["artifact_id"] is not None
        assert payload["job_id"] == "job-123"

        # Status endpoint should now report GENERATING
        status_resp = await client.get(INVOICES + f"/{invoice['id']}/pdf", headers=_admin_headers(admin.id))
        assert status_resp.status_code == 200
        status_payload = status_resp.json()["data"]
        assert status_payload["status"] in {"GENERATING", "READY"}

    @pytest.mark.asyncio
    async def test_signed_url_returns_404_when_no_ready_artifact(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(INVOICES, headers=_admin_headers(admin.id), json=_valid_create_payload(finalize=True))
        invoice = create_resp.json()["data"]

        resp = await client.post(
            INVOICES + f"/{invoice['id']}/pdf/signed-url",
            headers=_admin_headers(admin.id),
            json={"disposition": "attachment"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_signed_url_uses_generate_presigned_url_when_ready_artifact_exists(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed a READY artifact with r2_file_key
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        invoice = Invoice(
            invoice_number="INV-000010",
            issue_date=date(2025, 12, 1),
            due_date=date(2025, 12, 15),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
        db_session.add(invoice)
        await db_session.flush()

        artifact = InvoicePdfArtifact(
            invoice_id=invoice.id,
            template_version="v0-placeholder",
            signature_hash="hash",
            pdf_version=1,
            status="READY",
            r2_file_key=f"invoices/{invoice.id}/artifacts/test.pdf",
        )
        db_session.add(artifact)
        await db_session.flush()

        seen: dict[str, object] = {}

        def _fake_presign(
            key: str,
            expiry_seconds: int,
            content_type: str,
            response_content_disposition: str | None = None,
        ) -> str:
            seen["key"] = key
            seen["expiry_seconds"] = expiry_seconds
            seen["content_type"] = content_type
            seen["response_content_disposition"] = response_content_disposition
            return f"https://example.com/{key}"

        # Patch generate_presigned_url at its definition (used by route handler)
        monkeypatch.setattr("app.storage.r2_client.generate_presigned_url", _fake_presign)

        # attachment behavior
        resp = await client.post(
            INVOICES + f"/{invoice.id}/pdf/signed-url",
            headers=_admin_headers(admin.id),
            json={"disposition": "attachment"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["url"].startswith("https://example.com/invoices/")
        assert "expires_at" in data
        assert str(seen["response_content_disposition"]).startswith('attachment; filename="INV-000010.pdf"')

        # inline behavior
        resp_inline = await client.post(
            INVOICES + f"/{invoice.id}/pdf/signed-url",
            headers=_admin_headers(admin.id),
            json={"disposition": "inline"},
        )
        assert resp_inline.status_code == 200
        assert str(seen["response_content_disposition"]).startswith('inline; filename="INV-000010.pdf"')


class TestInvoiceSummaryAndPayments:
    @pytest.mark.asyncio
    async def test_summary_counts_paid_unpaid_overdue(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        today = date.today()
        db_session.add_all(
            [
                Invoice(
                    invoice_number="INV-930001",
                    issue_date=today,
                    due_date=today + timedelta(days=10),
                    subtotal=Decimal("100.00"),
                    vat_rate=Decimal("20.00"),
                    vat_amount=Decimal("20.00"),
                    total=Decimal("120.00"),
                    paid_amount=Decimal("120.00"),
                    payment_status="PAID",
                    status="SENT",
                ),
                Invoice(
                    invoice_number="INV-930002",
                    issue_date=today,
                    due_date=today + timedelta(days=10),
                    subtotal=Decimal("80.00"),
                    vat_rate=Decimal("20.00"),
                    vat_amount=Decimal("16.00"),
                    total=Decimal("96.00"),
                    payment_status="UNPAID",
                    status="SENT",
                ),
                Invoice(
                    invoice_number="INV-930003",
                    issue_date=today - timedelta(days=30),
                    due_date=today - timedelta(days=1),
                    subtotal=Decimal("50.00"),
                    vat_rate=Decimal("20.00"),
                    vat_amount=Decimal("10.00"),
                    total=Decimal("60.00"),
                    payment_status="UNPAID",
                    status="SENT",
                ),
            ]
        )
        await db_session.flush()

        resp = await client.get(f"{INVOICES}/summary", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_invoices"] >= 3
        assert data["total_paid"] >= 1
        assert data["total_unpaid"] >= 1
        assert data["overdue"] >= 1
        assert "with_completed_refunds" in data and data["with_completed_refunds"] >= 0
        assert "with_open_disputes" in data and data["with_open_disputes"] >= 0

    @pytest.mark.asyncio
    async def test_invoice_detail_includes_line_items_and_payment_method(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        org_factory,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        sample_org = await org_factory(reference="INV-ORG-930010")
        invoice = Invoice(
            invoice_number="INV-930010",
            organization_id=sample_org.id,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
        db_session.add(invoice)
        await db_session.flush()
        await db_session.execute(
            text(
                """
                INSERT INTO invoice_line_items (id, invoice_id, description, quantity, unit_price, total_price, line_type, created_at, updated_at)
                VALUES (:id, :invoice_id, :description, :quantity, :unit_price, :total_price, :line_type, now(), now())
                """
            ),
            {
                "id": "00000000-0000-0000-0000-000000000111",
                "invoice_id": invoice.id,
                "description": "Delivery charge",
                "quantity": 1,
                "unit_price": Decimal("100.00"),
                "total_price": Decimal("100.00"),
                "line_type": "service",
            },
        )
        payment = BillingPayment(
            payment_number="PAY-930010",
            organization_id=sample_org.id,
            amount=Decimal("50.00"),
            payment_date=date.today(),
            provider="BANK_TRANSFER",
            status="DEPOSITED",
            allocation_status="ALLOCATED",
            allocated_amount=Decimal("50.00"),
            unallocated_amount=Decimal("0.00"),
        )
        db_session.add(payment)
        await db_session.flush()
        db_session.add(
            BillingPaymentAllocation(
                payment_id=payment.id,
                invoice_id=invoice.id,
                revision_no=1,
                allocated_amount=Decimal("50.00"),
            )
        )
        await db_session.flush()

        resp = await client.get(f"{INVOICES}/{invoice.id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["payment_method"] == "Bank Transfer"
        assert len(data["line_items"]) == 1
        assert data["line_items"][0]["description"] == "Delivery charge"

    @pytest.mark.asyncio
    async def test_invoice_payments_endpoint_returns_allocations(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        org_factory,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        sample_org = await org_factory(reference="INV-ORG-930020")
        invoice = Invoice(
            invoice_number="INV-930020",
            organization_id=sample_org.id,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
        db_session.add(invoice)
        await db_session.flush()
        payment = BillingPayment(
            payment_number="PAY-930020",
            organization_id=sample_org.id,
            amount=Decimal("80.00"),
            payment_date=date.today(),
            provider="BRAINTREE",
            provider_txn_id="TXN-001",
            status="DEPOSITED",
            allocation_status="ALLOCATED",
            allocated_amount=Decimal("80.00"),
            unallocated_amount=Decimal("0.00"),
        )
        db_session.add(payment)
        await db_session.flush()
        db_session.add(
            BillingPaymentAllocation(
                payment_id=payment.id,
                invoice_id=invoice.id,
                revision_no=1,
                allocated_amount=Decimal("80.00"),
            )
        )
        await db_session.flush()

        resp = await client.get(f"{INVOICES}/{invoice.id}/payments", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["payment_number"] == "PAY-930020"
        assert items[0]["method"] == "Card Payment"
        assert items[0]["transaction_id"] == "TXN-001"


class TestInvoiceRefundDisputePortal:
    """Refund rollups, DISPUTED/REFUNDED filters, summary counts, event display_title."""

    @pytest.mark.asyncio
    async def test_refund_and_dispute_portal_fields(
        self,
        client: AsyncClient,
        user_factory,
        db_session: AsyncSession,
        org_factory,
    ) -> None:
        for model in (
            Refund,
            BillingPaymentAllocation,
            BillingPayment,
            InvoicePdfArtifact,
            InvoiceCreditApplication,
            CreditNote,
            InvoiceEvent,
            Invoice,
        ):
            await db_session.execute(delete(model))
        await db_session.flush()

        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org = await org_factory(reference="INV-ORG-REFUND")
        inv = Invoice(
            invoice_number="INV-REF-PORTAL-1",
            organization_id=org.id,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            payment_status="PARTIALLY_PAID",
            paid_amount=Decimal("50.00"),
            status="SENT",
        )
        db_session.add(inv)
        await db_session.flush()
        db_session.add(
            InvoiceEvent(
                invoice_id=inv.id,
                event_type="FINALIZED",
                actor_id=admin.id,
                actor_role="ADMIN",
            )
        )
        pay = BillingPayment(
            payment_number="PAY-REF-PORTAL-1",
            organization_id=org.id,
            amount=Decimal("50.00"),
            payment_date=date.today(),
            provider="BRAINTREE",
            status="DEPOSITED",
            allocation_status="ALLOCATED",
            allocated_amount=Decimal("50.00"),
            unallocated_amount=Decimal("0.00"),
        )
        db_session.add(pay)
        await db_session.flush()
        db_session.add(
            BillingPaymentAllocation(
                payment_id=pay.id,
                invoice_id=inv.id,
                revision_no=1,
                allocated_amount=Decimal("50.00"),
            )
        )
        db_session.add(
            Refund(
                refund_number="REF-PORTAL-TEST-000001",
                organization_id=org.id,
                billing_payment_id=pay.id,
                invoice_id=inv.id,
                refund_method="CARD_REFUND",
                refund_type="PARTIAL",
                status="COMPLETED",
                reason_category="CLIENT_REQUEST",
                reason_description="Partial refund test",
                requested_amount=Decimal("25.00"),
                processed_amount=Decimal("25.00"),
            )
        )
        await db_session.flush()

        resp = await client.get(INVOICES, headers=_admin_headers(admin.id), params={"search": "INV-REF-PORTAL"})
        assert resp.status_code == 200
        row = next(i for i in resp.json()["data"]["items"] if i["invoice_number"] == "INV-REF-PORTAL-1")
        assert Decimal(str(row["refunded_amount"])) == Decimal("25.00")
        assert row["has_pending_refunds"] is False
        assert row["has_open_dispute"] is False

        resp_f = await client.get(INVOICES, headers=_admin_headers(admin.id), params={"payment_status": "REFUNDED"})
        assert resp.status_code == 200
        assert "INV-REF-PORTAL-1" in {i["invoice_number"] for i in resp_f.json()["data"]["items"]}

        pay.braintree_status = "DISPUTE_OPEN"
        pay.dispute_status = "OPEN"
        await db_session.flush()

        resp_d = await client.get(INVOICES, headers=_admin_headers(admin.id), params={"search": "INV-REF-PORTAL"})
        row2 = next(i for i in resp_d.json()["data"]["items"] if i["invoice_number"] == "INV-REF-PORTAL-1")
        assert row2["has_open_dispute"] is True

        resp_dis = await client.get(INVOICES, headers=_admin_headers(admin.id), params={"payment_status": "DISPUTED"})
        assert resp_dis.status_code == 200
        assert "INV-REF-PORTAL-1" in {i["invoice_number"] for i in resp_dis.json()["data"]["items"]}

        sum_resp = await client.get(f"{INVOICES}/summary", headers=_admin_headers(admin.id))
        assert sum_resp.status_code == 200
        sd = sum_resp.json()["data"]
        assert sd["with_completed_refunds"] >= 1
        assert sd["with_open_disputes"] >= 1

        det = await client.get(f"{INVOICES}/{inv.id}", headers=_admin_headers(admin.id))
        assert det.status_code == 200
        dd = det.json()["data"]
        assert Decimal(str(dd["refund_summary"]["refunded_amount"])) == Decimal("25.00")
        assert dd["has_open_dispute"] is True
        titles = [e["display_title"] for e in dd["events"]]
        assert any("finalised" in t.lower() for t in titles)


@pytest.mark.asyncio
async def test_invoice_list_accepts_repeated_status_filters(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.get(
        INVOICES,
        headers=_admin_headers(admin.id),
        params=[
            ("status", "DRAFT"),
            ("status", "SENT"),
        ],
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_db_schema_defaults_use_sequences_for_driver_and_invoice_codes(
    db_session: AsyncSession,
) -> None:
    """Migration smoke: verify column defaults are backed by nextval sequences."""
    driver_default = (await db_session.execute(text("""
                SELECT column_default
                FROM information_schema.columns
                WHERE table_name = 'drivers' AND column_name = 'driver_code'
                """))).scalar_one_or_none()
    invoice_default = (await db_session.execute(text("""
                SELECT column_default
                FROM information_schema.columns
                WHERE table_name = 'invoices' AND column_name = 'invoice_number'
                """))).scalar_one_or_none()

    assert driver_default is not None
    assert invoice_default is not None
    assert "nextval" in driver_default
    assert "driver_code_seq" in driver_default
    assert "nextval" in invoice_default
    assert "invoice_number_seq" in invoice_default
