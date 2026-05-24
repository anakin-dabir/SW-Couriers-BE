"""Tests for void_qb_credit_note_chain_task saga orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Retry

from app.integrations.quickbooks.tasks import void_qb_credit_note_chain_task


class _SessionCtx:
    def __init__(self) -> None:
        self.session = AsyncMock()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_void_chain_task_runs_reversal_affected_invoices_and_void() -> None:
    session_ctx = _SessionCtx()
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=session_ctx),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.log_void_chain_step = AsyncMock()
        instance.sync_invoice_now = AsyncMock()
        instance.void_credit_note_now = AsyncMock()

        await void_qb_credit_note_chain_task(
            {"job_try": 1, "job_id": "qb:void-cn-chain:org:cn:2"},
            organization_id="00000000-0000-4000-8000-000000000901",
            credit_note_id="cn-1",
            reversal_invoice_id="inv-rev",
            affected_invoice_ids=["inv-a", "inv-b"],
        )

        steps = [c.kwargs.get("step") for c in instance.log_void_chain_step.await_args_list]
        assert steps[0] == "reversal_invoice_sync"
        assert "affected_invoice_resync" in steps
        assert steps[-1] == "void_credit_memo"

        sync_calls = [c.kwargs["invoice_id"] for c in instance.sync_invoice_now.await_args_list]
        assert sync_calls == ["inv-rev", "inv-a", "inv-b"]
        instance.void_credit_note_now.assert_awaited_once()
        session_ctx.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_void_chain_task_logs_chain_failed_and_retries_on_transient_error() -> None:
    session_ctx = _SessionCtx()
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=session_ctx),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.log_void_chain_step = AsyncMock()
        instance.sync_invoice_now = AsyncMock(side_effect=RuntimeError("connection reset by peer"))
        instance.void_credit_note_now = AsyncMock()
        instance.classify_sync_error = lambda exc: ("CONNECTION", str(exc), True)  # noqa: ARG005
        instance.should_retry_exception = lambda exc: True  # noqa: ARG005

        with pytest.raises(Retry):
            await void_qb_credit_note_chain_task(
                {"job_try": 1, "job_id": "job-chain-fail"},
                organization_id="00000000-0000-4000-8000-000000000901",
                credit_note_id="cn-1",
                reversal_invoice_id="inv-rev",
                affected_invoice_ids=[],
            )

        failed_steps = [c.kwargs.get("step") for c in instance.log_void_chain_step.await_args_list]
        assert "chain_failed" in failed_steps
        session_ctx.session.rollback.assert_awaited()
