"""Unit tests for QuickBooks payment sync early-exit paths."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.quickbooks.service import QuickBooksService


def _make_service_for_payment_sync(
    *,
    payment: SimpleNamespace,
    allocations: list,
) -> QuickBooksService:
    session = SimpleNamespace()
    session.get = AsyncMock(return_value=payment)

    svc = QuickBooksService.__new__(QuickBooksService)
    svc._session = session
    svc._assert_model_belongs_to_org = lambda **kwargs: None
    svc._require_organization_id = lambda *args: args[-1]
    svc.sync_customer_now = AsyncMock()
    async def _get_by_local(_org_id: str, entity_type: str, _local_id: str):
        if entity_type == "customer":
            return SimpleNamespace(qb_entity_id="qb-cust-1")
        return None

    svc._link_repo = SimpleNamespace(
        get_by_local=AsyncMock(side_effect=_get_by_local),
    )
    svc._billing_alloc_repo = SimpleNamespace(latest_for_payment=AsyncMock(return_value=allocations))
    return svc


@pytest.mark.asyncio
async def test_sync_payment_now_returns_without_error_when_no_allocations() -> None:
    payment = SimpleNamespace(
        id="pay-1",
        organization_id="org-1",
        customer_id="cust-1",
        allocated_amount=Decimal("0"),
        payment_date=date.today(),
        payment_number="PAY-000001",
        notes=None,
        qb_payload_fingerprint=None,
        qb_sync_status="NOT_SYNCED",
    )
    svc = _make_service_for_payment_sync(payment=payment, allocations=[])

    await svc.sync_payment_now(organization_id="org-1", payment_id="pay-1")

    svc.sync_customer_now.assert_awaited_once()
    svc._billing_alloc_repo.latest_for_payment.assert_awaited_once_with("pay-1")
    calls = svc._link_repo.get_by_local.await_args_list
    assert len(calls) == 2
    assert calls[0].args[1] == "customer"
    assert calls[1].args[1] == "payment"


@pytest.mark.asyncio
async def test_sync_payment_now_returns_when_allocations_all_non_positive() -> None:
    payment = SimpleNamespace(
        id="pay-2",
        organization_id="org-1",
        customer_id="cust-1",
        allocated_amount=Decimal("0"),
        payment_date=date.today(),
        payment_number="PAY-000002",
        notes=None,
        qb_payload_fingerprint=None,
        qb_sync_status="NOT_SYNCED",
    )
    zero_alloc = SimpleNamespace(invoice_id="inv-1", allocated_amount=Decimal("0"))
    svc = _make_service_for_payment_sync(payment=payment, allocations=[zero_alloc])

    await svc.sync_payment_now(organization_id="org-1", payment_id="pay-2")

    calls = svc._link_repo.get_by_local.await_args_list
    assert len(calls) == 2
    assert calls[0].args[1] == "customer"
    assert calls[1].args[1] == "payment"
