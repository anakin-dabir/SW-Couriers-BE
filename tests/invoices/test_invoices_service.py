"""Unit tests for invoice service helpers (compute_payment_status, PDF signature)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.invoices.enums import InvoiceEventType, PaymentStatus
from app.modules.invoices.service import InvoiceService, compute_payment_status
from app.common.exceptions import ValidationError


class _DummyInvoice:
    """Minimal stand-in for Invoice needed by compute_payment_status."""

    def __init__(
        self,
        *,
        total: Decimal,
        due_date: date | None = None,
    ) -> None:
        self.total = total
        self.due_date = due_date


def test_compute_payment_status_respects_stored_void_or_written_off() -> None:
    inv_void = _DummyInvoice(total=Decimal("100.00"))
    inv_written_off = _DummyInvoice(total=Decimal("100.00"))

    assert compute_payment_status(inv_void, paid_amount=Decimal("0"), outcome_event_type=InvoiceEventType.VOIDED) == PaymentStatus.VOID
    assert compute_payment_status(inv_written_off, paid_amount=Decimal("0"), outcome_event_type=InvoiceEventType.WRITTEN_OFF) == PaymentStatus.WRITTEN_OFF


def test_compute_payment_status_unpaid_and_overdue() -> None:
    today = date.today()
    inv_overdue = _DummyInvoice(
        total=Decimal("100.00"),
        due_date=today.replace(year=today.year - 1),
    )
    inv_unpaid_not_due = _DummyInvoice(
        total=Decimal("100.00"),
        due_date=today.replace(year=today.year + 1),
    )

    assert compute_payment_status(inv_overdue, paid_amount=Decimal("0")) == PaymentStatus.OVERDUE
    assert compute_payment_status(inv_unpaid_not_due, paid_amount=Decimal("0")) == PaymentStatus.UNPAID


def test_compute_payment_status_partially_paid_and_paid() -> None:
    today = date.today()
    inv_partial = _DummyInvoice(
        total=Decimal("100.00"),
        due_date=today.replace(year=today.year - 1),
    )
    inv_paid = _DummyInvoice(
        total=Decimal("100.00"),
        due_date=today.replace(year=today.year - 1),
    )

    assert compute_payment_status(inv_partial, paid_amount=Decimal("40.00")) == PaymentStatus.PARTIALLY_PAID
    assert compute_payment_status(inv_paid, paid_amount=Decimal("100.00")) == PaymentStatus.PAID


def test_compute_payment_status_paid_when_credit_covers_balance() -> None:
    inv = _DummyInvoice(total=Decimal("120.00"), due_date=date.today())
    assert (
        compute_payment_status(
            inv,
            paid_amount=Decimal("0"),
            credit_total=Decimal("120.00"),
        )
        == PaymentStatus.PAID
    )


def test_invoice_list_item_balance_subtracts_credit_and_paid() -> None:
    from app.modules.invoices.v1.routes import _invoice_to_list_item

    invoice = SimpleNamespace(
        id="inv-1",
        invoice_number="INV-000001",
        order=None,
        issue_date=date.today(),
        due_date=date.today(),
        total=Decimal("120.00"),
        paid_amount=Decimal("20.00"),
        status="SENT",
        payment_status="PARTIALLY_PAID",
    )
    item = _invoice_to_list_item(invoice, credit_total=Decimal("50.00"))
    assert item.credit_applied == Decimal("50.00")
    assert item.paid == Decimal("20.00")
    assert item.balance == Decimal("50.00")


def test_invoice_list_item_balance_never_negative() -> None:
    from app.modules.invoices.v1.routes import _invoice_to_list_item

    invoice = SimpleNamespace(
        id="inv-2",
        invoice_number="INV-000002",
        order=None,
        issue_date=date.today(),
        due_date=date.today(),
        total=Decimal("100.00"),
        paid_amount=Decimal("0"),
        status="SENT",
        payment_status="PAID",
    )
    item = _invoice_to_list_item(invoice, credit_total=Decimal("100.00"))
    assert item.balance == Decimal("0")


@pytest.mark.asyncio
async def test_enqueue_qb_invoice_sync_skips_when_org_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    enqueue_mock = AsyncMock()
    monkeypatch.setattr("app.modules.invoices.service.enqueue", enqueue_mock)

    await service._enqueue_qb_invoice_sync(
        organization_id=None,
        invoice_id="inv-1",
        version=1,
    )

    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_qb_invoice_sync_delegates_to_quickbooks_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    enqueue_mock = AsyncMock(return_value={"queued": True})
    monkeypatch.setattr(
        "app.integrations.quickbooks.service.QuickBooksService.enqueue_invoice_sync",
        enqueue_mock,
    )

    await service._enqueue_qb_invoice_sync(
        organization_id="org-1",
        invoice_id="inv-1",
        version=7,
        trigger_source="invoice.sync",
        correlation_id="corr-1",
    )

    enqueue_mock.assert_awaited_once()
    call = enqueue_mock.await_args.kwargs
    assert call["organization_id"] == "org-1"
    assert call["invoice_id"] == "inv-1"
    assert call["trigger_source"] == "invoice.sync"
    assert call["correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_void_enqueues_qb_sync_for_org_invoice() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    existing = SimpleNamespace(
        id="inv-1",
        version=2,
        organization_id="org-1",
    )
    updated = SimpleNamespace(id="inv-1", version=3, organization_id="org-1")
    refreshed = SimpleNamespace(id="inv-1", version=3, organization_id="org-1")

    service._invoice_repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(side_effect=[existing, refreshed]),
        update_by_id=AsyncMock(return_value=updated),
    )
    service._event_repo = SimpleNamespace(append=AsyncMock(), latest_outcome_event_type=AsyncMock(return_value=None))
    service._log_audit = AsyncMock()  # type: ignore[method-assign]
    service._enqueue_qb_invoice_sync = AsyncMock()  # type: ignore[method-assign]

    result = await service.void(
        invoice_id="inv-1",
        reason="Customer dispute",
        organization_id="org-1",
    )

    assert result is refreshed
    service._invoice_repo.update_by_id.assert_awaited_once_with(
        "inv-1",
        {},
        expected_version=2,
        organization_id="org-1",
    )
    service._event_repo.append.assert_awaited_once()
    assert service._event_repo.append.await_args.args[1] == InvoiceEventType.VOIDED
    service._enqueue_qb_invoice_sync.assert_awaited_once_with(
        organization_id="org-1",
        invoice_id="inv-1",
        version=3,
    )


@pytest.mark.asyncio
async def test_upsert_internal_note_enqueues_qb_sync_for_sent_org_invoice() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    existing = SimpleNamespace(
        id="inv-1",
        version=2,
        organization_id="org-1",
        status="SENT",
        notes=None,
    )
    updated = SimpleNamespace(
        id="inv-1",
        version=3,
        organization_id="org-1",
        status="SENT",
        notes="Customer requested manual invoice.",
    )

    service._invoice_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=existing))
    service._persist_internal_note = AsyncMock(return_value=updated)  # type: ignore[method-assign]

    result = await service.upsert_invoice_internal_note(
        "inv-1",
        notes="Customer requested manual invoice.",
        version=2,
        organization_id=None,
    )

    assert result is updated
    service._persist_internal_note.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_internal_note_skips_persist_when_unchanged() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    existing = SimpleNamespace(
        id="inv-1",
        version=2,
        organization_id="org-1",
        status="SENT",
        notes="Same text",
    )
    service._invoice_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=existing))
    service._persist_internal_note = AsyncMock()  # type: ignore[method-assign]

    result = await service.upsert_invoice_internal_note(
        "inv-1",
        notes="Same text",
        version=2,
        organization_id=None,
    )

    assert result is existing
    service._persist_internal_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_internal_note_idempotent_when_empty() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    existing = SimpleNamespace(id="inv-1", version=1, notes=None)
    service._invoice_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=existing))
    service._persist_internal_note = AsyncMock()  # type: ignore[method-assign]

    result = await service.delete_invoice_internal_note("inv-1", version=1, organization_id=None)

    assert result is existing
    service._persist_internal_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_internal_note_enqueues_qb_for_sent_invoice() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    updated = SimpleNamespace(id="inv-1", version=3, organization_id="org-1", status="SENT", notes="Note")
    service._invoice_repo = SimpleNamespace(update_by_id=AsyncMock(return_value=updated))
    service._log_audit = AsyncMock()  # type: ignore[method-assign]
    service._enqueue_qb_invoice_sync = AsyncMock()  # type: ignore[method-assign]

    result = await service._persist_internal_note(
        "inv-1",
        notes_value="Note",
        version=2,
        organization_id=None,
        audit_action="invoice.internal_note_updated",
        audit_user_id="admin-1",
        audit_user_role="ADMIN",
        old_notes=None,
    )

    assert result is updated
    service._enqueue_qb_invoice_sync.assert_awaited_once_with(
        organization_id="org-1",
        invoice_id="inv-1",
        version=3,
        trigger_source="invoice.internal_note_changed",
        business={"notes_changed": True},
    )


@pytest.mark.asyncio
async def test_write_off_enqueues_qb_sync_for_org_invoice() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    existing = SimpleNamespace(
        id="inv-1",
        version=4,
        organization_id="org-1",
    )
    updated = SimpleNamespace(id="inv-1", version=5, organization_id="org-1")
    refreshed = SimpleNamespace(id="inv-1", version=5, organization_id="org-1")

    service._invoice_repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(side_effect=[existing, refreshed]),
        update_by_id=AsyncMock(return_value=updated),
    )
    service._event_repo = SimpleNamespace(append=AsyncMock(), latest_outcome_event_type=AsyncMock(return_value=None))
    service._log_audit = AsyncMock()  # type: ignore[method-assign]
    service._enqueue_qb_invoice_sync = AsyncMock()  # type: ignore[method-assign]

    result = await service.write_off(
        invoice_id="inv-1",
        reason="Bad debt",
        organization_id="org-1",
    )

    assert result is refreshed
    service._invoice_repo.update_by_id.assert_awaited_once_with(
        "inv-1",
        {},
        expected_version=4,
        organization_id="org-1",
    )
    service._event_repo.append.assert_awaited_once()
    assert service._event_repo.append.await_args.args[1] == InvoiceEventType.WRITTEN_OFF
    service._enqueue_qb_invoice_sync.assert_awaited_once_with(
        organization_id="org-1",
        invoice_id="inv-1",
        version=5,
    )


def test_validate_order_amounts_rejects_mismatch() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    with pytest.raises(ValidationError):
        service._validate_order_amounts(
            subtotal=Decimal("100.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("119.98"),
        )


@pytest.mark.asyncio
async def test_sync_from_order_creates_draft_and_line_items() -> None:
    service = InvoiceService(session=SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=AsyncMock()), request=None)
    service._invoice_repo = SimpleNamespace(get_by_order_id=AsyncMock(return_value=None))
    service.create_draft = AsyncMock(return_value=SimpleNamespace(id="inv-1"))  # type: ignore[method-assign]
    service._replace_line_items = AsyncMock()  # type: ignore[method-assign]
    order = SimpleNamespace(
        id="order-1",
        order_id="SWC-ORD-000001",
        organization_id="org-1",
        customer_id="user-1",
        subtotal=Decimal("0"),
        vat_amount=Decimal("0"),
        total_amount=Decimal("0"),
        price_breakdown=None,
    )

    invoice = await service.sync_from_order(order=order, stops=[], packages_by_stop={})

    assert invoice.id == "inv-1"
    service.create_draft.assert_awaited_once()
    service._replace_line_items.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_pdf_if_previously_generated_skips_when_no_history() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    service._artifact_repo = SimpleNamespace(get_latest_for_invoice=AsyncMock(return_value=None))
    service.request_pdf = AsyncMock()  # type: ignore[method-assign]

    await service.request_pdf_if_previously_generated("inv-1", organization_id="org-1")

    service.request_pdf.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_pdf_if_previously_generated_requests_when_history_exists() -> None:
    service = InvoiceService(session=SimpleNamespace(), request=None)
    service._artifact_repo = SimpleNamespace(get_latest_for_invoice=AsyncMock(return_value=SimpleNamespace(id="art-1")))
    service.request_pdf = AsyncMock()  # type: ignore[method-assign]

    await service.request_pdf_if_previously_generated("inv-1", organization_id="org-1")

    service.request_pdf.assert_awaited_once_with("inv-1", organization_id="org-1")


@pytest.mark.asyncio
async def test_request_pdf_reuses_generating_artifact_instead_of_enqueuing(monkeypatch: pytest.MonkeyPatch) -> None:
    service = InvoiceService(session=SimpleNamespace(flush=AsyncMock()), request=None)
    invoice = SimpleNamespace(
        id="inv-1",
        invoice_number="INV-999001",
        issue_date=date.today(),
        due_date=date.today(),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
        order_id=None,
        line_items=[],
    )
    generating = SimpleNamespace(id="art-1", job_id="job-1")
    service._invoice_repo = SimpleNamespace(get_with_relations=AsyncMock(return_value=invoice))
    service._credit_app_repo = SimpleNamespace(list_for_invoice=AsyncMock(return_value=[]))
    service._artifact_repo = SimpleNamespace(
        get_ready_by_signature=AsyncMock(return_value=None),
        get_generating_by_signature=AsyncMock(return_value=generating),
        get_next_pdf_version=AsyncMock(),
        create=AsyncMock(),
    )
    enqueue_mock = AsyncMock()
    monkeypatch.setattr("app.modules.invoices.service.enqueue", enqueue_mock)

    payload, artifact = await service.request_pdf("inv-1", idempotency_key="idem-1")

    assert payload["status"] == "GENERATING"
    assert payload["artifact_id"] == "art-1"
    assert payload["job_id"] == "job-1"
    assert artifact is generating
    enqueue_mock.assert_not_awaited()
    service._artifact_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_pdf_uses_stable_job_id_with_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    service = InvoiceService(session=SimpleNamespace(flush=AsyncMock()), request=None)
    invoice = SimpleNamespace(
        id="inv-1",
        invoice_number="INV-999002",
        issue_date=date.today(),
        due_date=date.today(),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
        order_id=None,
        line_items=[],
    )
    created = SimpleNamespace(id="art-new", job_id=None)
    service._invoice_repo = SimpleNamespace(get_with_relations=AsyncMock(return_value=invoice))
    service._credit_app_repo = SimpleNamespace(list_for_invoice=AsyncMock(return_value=[]))
    service._artifact_repo = SimpleNamespace(
        get_ready_by_signature=AsyncMock(return_value=None),
        get_generating_by_signature=AsyncMock(return_value=None),
        get_next_pdf_version=AsyncMock(return_value=2),
        create=AsyncMock(return_value=created),
    )
    enqueue_mock = AsyncMock(return_value=SimpleNamespace(job_id="job-queued"))
    monkeypatch.setattr("app.modules.invoices.service.enqueue", enqueue_mock)

    payload, artifact = await service.request_pdf("inv-1", idempotency_key="idem-123")

    assert payload["status"] == "GENERATING"
    assert payload["artifact_id"] == "art-new"
    assert payload["job_id"] == "job-queued"
    assert artifact is created
    enqueue_kwargs = enqueue_mock.await_args.kwargs
    assert enqueue_kwargs["_job_id"].startswith("invpdf:inv-1:")
    from app.core.queue import QueuePriority

    assert enqueue_kwargs["priority"] == QueuePriority.LOW


@pytest.mark.asyncio
async def test_create_reversal_for_credit_note_void_builds_invoice_and_enqueues_qb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimal import Decimal

    credit_note = SimpleNamespace(
        id="cn-rev-1",
        organization_id="org-1",
        customer_id="cust-1",
        credit_note_number="CN-2026-00099",
    )
    finalized = SimpleNamespace(id="inv-rev-1", version=1)
    refreshed = SimpleNamespace(id="inv-rev-1", version=1, organization_id="org-1")

    service = InvoiceService(session=SimpleNamespace(), request=None)  # type: ignore[arg-type]
    service.create_and_finalize = AsyncMock(return_value=finalized)  # type: ignore[method-assign]
    service._replace_line_items = AsyncMock()  # type: ignore[method-assign]
    service._enqueue_qb_invoice_sync = AsyncMock()  # type: ignore[method-assign]
    service._invoice_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=refreshed))

    result = await service.create_reversal_for_credit_note_void(
        credit_note=credit_note,
        applied_total=Decimal("120.00"),
        void_reason="Reverse applied credit",
        audit_user_id="admin-1",
        audit_user_role="ADMIN",
    )

    assert result is refreshed
    service.create_and_finalize.assert_awaited_once()
    create_kwargs = service.create_and_finalize.await_args.kwargs
    assert create_kwargs["queue_qb_sync"] is False
    assert "CN-2026-00099" in create_kwargs["notes"]
    service._replace_line_items.assert_awaited_once()
    line_items = service._replace_line_items.await_args.args[1]
    assert line_items[0]["description"]
    assert "CN-2026-00099" in line_items[0]["description"]
    service._enqueue_qb_invoice_sync.assert_awaited_once()
    qb_kwargs = service._enqueue_qb_invoice_sync.await_args.kwargs
    assert qb_kwargs["trigger_source"] == "billing.void_credit_note_reversal"
    assert qb_kwargs["correlation_id"] == "qb:void-cn:org-1:cn-rev-1"


@pytest.mark.asyncio
async def test_create_reversal_requires_customer_when_applied() -> None:
    from decimal import Decimal

    service = InvoiceService(session=SimpleNamespace(), request=None)  # type: ignore[arg-type]
    cn = SimpleNamespace(
        id="cn-no-cust",
        organization_id="org-1",
        customer_id=None,
        credit_note_number="CN-X",
    )
    with pytest.raises(ValidationError, match="customer_id"):
        await service.create_reversal_for_credit_note_void(
            credit_note=cn,
            applied_total=Decimal("10"),
            void_reason="test",
        )


def test_reversal_line_item_description_includes_cn_amount_and_reason() -> None:
    desc = InvoiceService._reversal_line_item_description(
        credit_note_number="CN-2026-00004",
        applied_gross=Decimal("340.00"),
        void_reason="Reverse applied credit",
    )
    assert "CN-2026-00004" in desc
    assert "340.00" in desc
    assert "Reverse applied credit" in desc
    assert len(desc) <= 255
