"""Tests for QuickBooks ARQ task wrappers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Retry

from app.integrations.quickbooks.tasks import refresh_qb_connections_task, sync_qb_credit_note_task, sync_qb_invoice_task, sync_qb_payment_task


class _SessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_sync_qb_invoice_task_retries_on_failure() -> None:
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=_SessionCtx()),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.sync_invoice_now = AsyncMock(side_effect=RuntimeError("timeout from qbo"))

        with pytest.raises(Retry):
            await sync_qb_invoice_task(
                {"job_try": 1, "job_id": "job-1"},
                organization_id="org-1",
                invoice_id="inv-1",
            )

        instance.sync_invoice_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_qb_credit_note_task_retries_on_failure() -> None:
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=_SessionCtx()),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.sync_credit_note_now = AsyncMock(side_effect=RuntimeError("rate limit from qbo"))

        with pytest.raises(Retry):
            await sync_qb_credit_note_task(
                {"job_try": 1, "job_id": "job-1"},
                organization_id="org-1",
                credit_note_id="cn-1",
            )

        instance.sync_credit_note_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_qb_connections_task_calls_service() -> None:
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=_SessionCtx()),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.refresh_connections_due = AsyncMock(return_value={"checked": 2, "refreshed": 1, "skipped": 1, "failed": 0})

        await refresh_qb_connections_task({}, limit=100)

        instance.refresh_connections_due.assert_awaited_once_with(limit=100)


@pytest.mark.asyncio
async def test_sync_qb_payment_task_retries_on_failure() -> None:
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=_SessionCtx()),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.sync_payment_now = AsyncMock(side_effect=RuntimeError("connection reset by peer"))

        with pytest.raises(Retry):
            await sync_qb_payment_task(
                {"job_try": 1, "job_id": "job-1"},
                organization_id="org-1",
                payment_id="pay-1",
            )

        instance.sync_payment_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_qb_payment_task_does_not_retry_terminal_validation() -> None:
    with (
        patch("app.integrations.quickbooks.tasks.get_async_session", return_value=_SessionCtx()),
        patch("app.integrations.quickbooks.tasks.QuickBooksService") as svc_cls,
    ):
        instance = svc_cls.return_value
        instance.sync_payment_now = AsyncMock(side_effect=ValueError("invalid payload"))
        instance.should_retry_exception.return_value = False

        with pytest.raises(ValueError, match="invalid payload"):
            await sync_qb_payment_task(
                {"job_try": 1, "job_id": "job-1"},
                organization_id="org-1",
                payment_id="pay-1",
            )
