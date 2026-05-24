"""Service-layer tests for QuickBooks sync logging gateway (_log_sync, _queue_sync_job, void enqueue)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.common.enums import Job
from app.integrations.quickbooks.constants import QB_GLOBAL_NAMESPACE_ID
from app.integrations.quickbooks.sync_logging import (
    EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED,
    EVENT_CREDIT_NOTE_VOID_QUEUED,
    LOG_STATUS_PENDING,
)
from app.integrations.quickbooks.service import (
    ACTION_QUEUED,
    QB_ENTITY_CUSTOMER,
    QB_ENTITY_INVOICE,
    QB_ENTITY_PAYMENT,
    QuickBooksService,
)


@pytest.mark.asyncio
async def test_log_sync_never_raises_when_repo_fails() -> None:
    service = QuickBooksService(session=AsyncMock())  # type: ignore[arg-type]
    service._sync_log_repo = SimpleNamespace(
        log=AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await service._log_sync(
        organization_id="org-1",
        entity_type="invoice",
        local_entity_id="inv-1",
        event_type="INVOICE_QUEUED",
        action=ACTION_QUEUED,
        status=LOG_STATUS_PENDING,
    )
    assert result is None


@pytest.mark.asyncio
async def test_log_sync_uses_repo_not_recursive() -> None:
    service = QuickBooksService(session=AsyncMock())  # type: ignore[arg-type]
    log_mock = AsyncMock(return_value=SimpleNamespace(id="log-1"))
    service._sync_log_repo = SimpleNamespace(log=log_mock)

    await service._log_sync(
        organization_id="org-1",
        entity_type="payment",
        local_entity_id="pay-1",
        event_type="PAYMENT_QUEUED",
        action=ACTION_QUEUED,
        status=LOG_STATUS_PENDING,
        trigger_source="billing.payment_sync",
        correlation_id="corr-pay",
    )

    log_mock.assert_awaited_once()
    call_kwargs = log_mock.await_args.kwargs
    assert call_kwargs["organization_id"] == QB_GLOBAL_NAMESPACE_ID
    assert call_kwargs["payload"]["correlation_id"] == "corr-pay"
    assert call_kwargs["payload"]["trigger_source"] == "billing.payment_sync"


@pytest.mark.asyncio
async def test_queue_sync_job_logs_pending_when_enqueue_returns_none() -> None:
    service = QuickBooksService(session=AsyncMock())  # type: ignore[arg-type]
    log_mock = AsyncMock(return_value=SimpleNamespace(id="log-1"))
    service._sync_log_repo = SimpleNamespace(log=log_mock)

    with patch("app.integrations.quickbooks.service.enqueue", new_callable=AsyncMock, return_value=None):
        result = await service._queue_sync_job(
            Job.SYNC_QB_PAYMENT,
            organization_id="org-1",
            entity_type="payment",
            local_entity_id="pay-1",
            event_type="PAYMENT_QUEUED",
            job_id="qb:payment:org-1:pay-1:1",
            trigger_source="billing.payment_sync",
            queue_kwargs={"organization_id": "org-1", "payment_id": "pay-1"},
        )

    assert result is None
    log_mock.assert_awaited_once()
    payload = log_mock.await_args.kwargs["payload"]
    assert payload["enqueue"]["queued"] is False
    assert log_mock.await_args.kwargs["job_id"] == "qb:payment:org-1:pay-1:1"


@pytest.mark.asyncio
async def test_enqueue_void_credit_note_writes_pending_log() -> None:
    cn = SimpleNamespace(
        id="cn-1",
        credit_note_number="CN-2026-00001",
        qb_sync_status="NOT_SYNCED",
        version=1,
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=cn)

    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    log_mock = AsyncMock(return_value=SimpleNamespace(id="log-1"))
    service._sync_log_repo = SimpleNamespace(log=log_mock)

    with patch("app.integrations.quickbooks.service.enqueue", new_callable=AsyncMock, return_value=SimpleNamespace(job_id="j-void")):
        await service.enqueue_void_credit_note(
            organization_id="org-1",
            credit_note_id="cn-1",
            version=2,
            void_reason="Customer request",
            credit_note_number="CN-2026-00001",
        )

    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["event_type"] == EVENT_CREDIT_NOTE_VOID_QUEUED
    assert log_mock.await_args.kwargs["status"] == LOG_STATUS_PENDING
    business = log_mock.await_args.kwargs["payload"]["business"]
    assert business["void_reason"] == "Customer request"
    assert "correlation_id" in log_mock.await_args.kwargs["payload"]


@pytest.mark.asyncio
async def test_enqueue_void_credit_note_chain_returns_correlation_id() -> None:
    session = AsyncMock()
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    log_mock = AsyncMock(return_value=SimpleNamespace(id="log-1"))
    service._sync_log_repo = SimpleNamespace(log=log_mock)

    with patch("app.integrations.quickbooks.service.enqueue", new_callable=AsyncMock, return_value=SimpleNamespace(job_id="j-chain")):
        corr = await service.enqueue_void_credit_note_chain(
            organization_id="org-1",
            credit_note_id="cn-1",
            reversal_invoice_id="inv-rev",
            affected_invoice_ids=["inv-a", "inv-b"],
            version=2,
            void_reason="Applied reversal",
            credit_note_number="CN-2026-00004",
            applied_total="340.00",
        )

    assert corr == f"qb:void-cn:{QB_GLOBAL_NAMESPACE_ID}:cn-1:v2"
    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["event_type"] == EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED
    business = log_mock.await_args.kwargs["payload"]["business"]
    assert business["reversal_invoice_id"] == "inv-rev"
    assert business["affected_invoice_ids"] == ["inv-a", "inv-b"]
    assert business["applied_total"] == "340.00"


@pytest.mark.asyncio
async def test_enqueue_void_credit_note_no_op_when_cn_missing() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo = SimpleNamespace(log=AsyncMock())

    with patch("app.integrations.quickbooks.service.enqueue", new_callable=AsyncMock) as enqueue_mock:
        await service.enqueue_void_credit_note(
            organization_id="org-1",
            credit_note_id="missing",
            version=1,
        )
        enqueue_mock.assert_not_awaited()
    service._sync_log_repo.log.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_payment_skip_writes_skipped_log() -> None:
    payment = SimpleNamespace(
        id="pay-1",
        organization_id="org-1",
        customer_id="cust-1",
        amount=Decimal("100.00"),
        payment_date=date.today(),
        payment_number="PAY-1",
        notes=None,
        qb_sync_status="NOT_SYNCED",
        qb_payload_fingerprint=None,
        version=1,
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=payment)

    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    log_mock = AsyncMock(return_value=SimpleNamespace(id="log-1"))
    service._sync_log_repo = SimpleNamespace(log=log_mock)
    async def get_by_local(org_id: str, entity_type: str, local_id: str):  # noqa: ANN001
        if entity_type == QB_ENTITY_CUSTOMER:
            return SimpleNamespace(qb_entity_id="qb-cust-1")
        if entity_type in (QB_ENTITY_INVOICE, QB_ENTITY_PAYMENT):
            return None
        return None

    service._link_repo = SimpleNamespace(get_by_local=AsyncMock(side_effect=get_by_local))
    service._billing_alloc_repo = SimpleNamespace(latest_for_payment=AsyncMock(return_value=[]))
    service._conn_repo = SimpleNamespace()

    with patch.object(service, "sync_customer_now", new_callable=AsyncMock):
        await service.sync_payment_now(
            organization_id="org-1",
            payment_id="pay-1",
            job_id="job-pay-1",
        )

    assert any(
        call.kwargs.get("event_type") == "PAYMENT_SYNC_SKIPPED"
        for call in log_mock.await_args_list
    )


@pytest.mark.asyncio
async def test_invoice_service_enqueue_delegates_to_quickbooks_with_trigger() -> None:
    from app.modules.invoices.service import InvoiceService

    session = AsyncMock()
    inv_service = InvoiceService(session=session)  # type: ignore[arg-type]
    captured: list[dict] = []

    async def capture_enqueue_invoice_sync(self, **kwargs):  # noqa: ANN001, ARG001
        captured.append(kwargs)
        return {"queued": True}

    with patch(
        "app.integrations.quickbooks.service.QuickBooksService.enqueue_invoice_sync",
        capture_enqueue_invoice_sync,
    ):
        await inv_service._enqueue_qb_invoice_sync(
            organization_id="org-1",
            invoice_id="inv-1",
            version=1,
            trigger_source="invoice.create_and_finalize",
            correlation_id="corr-inv",
        )

    assert len(captured) == 1
    assert captured[0]["trigger_source"] == "invoice.create_and_finalize"
    assert captured[0]["correlation_id"] == "corr-inv"


@pytest.mark.asyncio
async def test_billing_payment_enqueue_skips_zero_allocation() -> None:
    from app.modules.billing.service import BillingService

    session = AsyncMock()
    service = BillingService(session=session, request=None)  # type: ignore[arg-type]
    service._allocation_repo = SimpleNamespace(
        total_latest_allocated_for_payment=AsyncMock(return_value=Decimal("0")),
    )

    with patch(
        "app.integrations.quickbooks.service.QuickBooksService.enqueue_payment_sync",
        new_callable=AsyncMock,
    ) as qb_enqueue:
        await service._enqueue_qb_payment_sync(
            payment_id="pay-1",
            organization_id="org-1",
            version=1,
        )
        qb_enqueue.assert_not_awaited()
