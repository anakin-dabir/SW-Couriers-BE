from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import ValidationError
from app.modules.invoices.enums import InvoiceStatus
from app.modules.orders.service import OrderService


@pytest.mark.asyncio
async def test_record_card_billing_for_order_marks_deposited_and_allocates_when_invoice_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(flush=AsyncMock())
    service = OrderService(session=session, request=None)

    mark_mock = AsyncMock(return_value=SimpleNamespace(id="pay-1"))
    alloc_mock = AsyncMock(return_value=SimpleNamespace())

    class _FakeBilling:
        def __init__(self, *_a, **_k) -> None:
            self.mark_payment_status = mark_mock
            self.add_or_revise_allocation = alloc_mock

    monkeypatch.setattr("app.modules.orders.service.BillingService", _FakeBilling)

    order = SimpleNamespace(
        id="order-1",
        order_id="SWC-ORD-000001",
        total_amount=Decimal("50.00"),
    )
    invoice = SimpleNamespace(id="inv-1", status=InvoiceStatus.SENT.value)

    await service._record_card_billing_for_order(
        order=order,
        invoice=invoice,
        organization_id="org-1",
        created_by_id="user-1",
        braintree_transaction_id="txn-1",
        braintree_status="settled",
        transaction_fee=Decimal("0.30"),
        payment_id="pay-pending-1",
    )

    mark_mock.assert_awaited_once()
    mk = mark_mock.await_args
    assert mk is not None
    assert mk.kwargs["payment_id"] == "pay-pending-1"
    assert mk.kwargs["organization_id"] == "org-1"

    alloc_mock.assert_awaited_once()
    ak = alloc_mock.await_args
    assert ak is not None
    assert ak.kwargs["payment_id"] == "pay-1"
    assert ak.kwargs["invoice_id"] == "inv-1"
    assert ak.kwargs["allocated_amount"] == Decimal("50.00")


@pytest.mark.asyncio
async def test_record_card_billing_for_order_finalizes_draft_invoice_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(flush=AsyncMock())
    service = OrderService(session=session, request=None)

    mark_mock = AsyncMock(return_value=SimpleNamespace(id="pay-draft"))
    alloc_mock = AsyncMock(return_value=SimpleNamespace())
    finalize_mock = AsyncMock(
        return_value=SimpleNamespace(id="inv-draft", status=InvoiceStatus.SENT.value),
    )

    class _FakeBilling:
        def __init__(self, *_a, **_k) -> None:
            self.mark_payment_status = mark_mock
            self.add_or_revise_allocation = alloc_mock

    monkeypatch.setattr("app.modules.orders.service.BillingService", _FakeBilling)
    monkeypatch.setattr("app.modules.orders.service.InvoiceService.finalize", finalize_mock)

    order = SimpleNamespace(
        id="order-draft-pay",
        order_id="SWC-ORD-DRAFT",
        total_amount=Decimal("10.00"),
    )
    draft_inv = SimpleNamespace(id="inv-draft", status=InvoiceStatus.DRAFT.value)

    await service._record_card_billing_for_order(
        order=order,
        invoice=draft_inv,
        organization_id="org-1",
        created_by_id="user-1",
        braintree_transaction_id="txn-draft",
        payment_id="pay-pending-draft",
    )

    finalize_mock.assert_awaited_once()
    fa = finalize_mock.await_args
    assert fa is not None
    assert fa.args[0] == "inv-draft" and fa.args[1] == "org-1"
    assert fa.kwargs.get("audit_user_id") == "user-1"
    assert fa.kwargs.get("audit_user_role") == "CUSTOMER_B2B"
    alloc_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_precharge_saved_card_for_order_raises_when_charge_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SimpleNamespace(flush=AsyncMock())
    service = OrderService(session=session, request=None)

    charge_mock = AsyncMock(
        return_value=SimpleNamespace(
            success=False,
            braintree_transaction_id=None,
            processor_message="Card declined",
        )
    )

    class _FakePaymentService:
        def __init__(self, *_a, **_k) -> None:
            pass

        charge_saved_card_for_booking = charge_mock

    monkeypatch.setattr("app.modules.orders.service.PaymentService", _FakePaymentService)

    with pytest.raises(ValidationError, match="Card declined"):
        await service._precharge_saved_card_for_order(
            organization_id="org-1",
            credit_card_id="cc-1",
            charge_amount=Decimal("25.00"),
            verified_payment_method_nonce="nonce-abc",
            order_id="pay-pending-1",
        )
