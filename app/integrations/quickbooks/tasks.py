"""ARQ worker tasks for QuickBooks sync."""

from __future__ import annotations

from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.integrations.quickbooks.service import QuickBooksService
from app.integrations.quickbooks.sync_logging import (
    LOG_STATUS_FAILED,
    LOG_STATUS_PENDING,
    SyncLogContext,
    correlation_id_for_void_credit_note,
    reset_sync_log_context,
    set_sync_log_context,
)


async def sync_qb_customer_task(
    ctx: dict,
    organization_id: str,
    customer_id: str,
    force: bool = False,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        try:
            await service.sync_customer_now(
                organization_id=organization_id,
                customer_id=customer_id,
                force=force,
                job_id=ctx.get("job_id"),
                attempt_no=int(ctx.get("job_try", 1)),
            )
        except Exception as exc:
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise


async def sync_qb_invoice_task(
    ctx: dict,
    organization_id: str,
    invoice_id: str,
    force: bool = False,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        try:
            await service.sync_invoice_now(
                organization_id=organization_id,
                invoice_id=invoice_id,
                force=force,
                job_id=ctx.get("job_id"),
                attempt_no=int(ctx.get("job_try", 1)),
            )
        except Exception as exc:
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise


async def sync_qb_credit_note_task(
    ctx: dict,
    organization_id: str,
    credit_note_id: str,
    force: bool = False,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        try:
            await service.sync_credit_note_now(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                force=force,
                job_id=ctx.get("job_id"),
                attempt_no=int(ctx.get("job_try", 1)),
            )
        except Exception as exc:
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise


async def void_qb_credit_note_task(
    ctx: dict,
    organization_id: str,
    credit_note_id: str,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        job_id = ctx.get("job_id")
        token = set_sync_log_context(
            SyncLogContext(
                correlation_id=correlation_id_for_void_credit_note(
                    organization_id=organization_id,
                    credit_note_id=credit_note_id,
                ),
                trigger_source="billing.void_credit_note",
                trigger_entity_id=credit_note_id,
            )
        )
        try:
            await service.void_credit_note_now(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                job_id=job_id,
                attempt_no=int(ctx.get("job_try", 1)),
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise
        finally:
            reset_sync_log_context(token)


async def void_qb_credit_note_chain_task(
    ctx: dict,
    organization_id: str,
    credit_note_id: str,
    reversal_invoice_id: str,
    affected_invoice_ids: list[str] | None = None,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        job_id = ctx.get("job_id")
        attempt_no = int(ctx.get("job_try", 1))
        token = set_sync_log_context(
            SyncLogContext(
                correlation_id=correlation_id_for_void_credit_note(
                    organization_id=organization_id,
                    credit_note_id=credit_note_id,
                ),
                trigger_source="billing.void_credit_note",
                trigger_entity_id=credit_note_id,
            )
        )
        try:
            await service.log_void_chain_step(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                step="reversal_invoice_sync",
                status=LOG_STATUS_PENDING,
                job_id=job_id,
                attempt_no=attempt_no,
                business={"reversal_invoice_id": reversal_invoice_id},
            )
            await service.sync_invoice_now(
                organization_id=organization_id,
                invoice_id=reversal_invoice_id,
                force=True,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            for inv_id in affected_invoice_ids or []:
                await service.log_void_chain_step(
                    organization_id=organization_id,
                    credit_note_id=credit_note_id,
                    step="affected_invoice_resync",
                    status=LOG_STATUS_PENDING,
                    job_id=job_id,
                    attempt_no=attempt_no,
                    business={"invoice_id": inv_id},
                )
                await service.sync_invoice_now(
                    organization_id=organization_id,
                    invoice_id=inv_id,
                    force=True,
                    job_id=job_id,
                    attempt_no=attempt_no,
                )
            await service.log_void_chain_step(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                step="void_credit_memo",
                status=LOG_STATUS_PENDING,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            await service.void_credit_note_now(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            error_code, human_message, _retry = service.classify_sync_error(exc)
            await service.log_void_chain_step(
                organization_id=organization_id,
                credit_note_id=credit_note_id,
                step="chain_failed",
                status=LOG_STATUS_FAILED,
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message,
            )
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise
        finally:
            reset_sync_log_context(token)


async def sync_qb_payment_task(
    ctx: dict,
    organization_id: str,
    payment_id: str,
    force: bool = False,
) -> None:
    async with get_async_session() as session:
        service = QuickBooksService(session)
        token = set_sync_log_context(
            SyncLogContext(
                correlation_id=ctx.get("job_id"),
                trigger_source="billing.payment_sync",
                trigger_entity_id=payment_id,
            )
        )
        try:
            await service.sync_payment_now(
                organization_id=organization_id,
                payment_id=payment_id,
                force=force,
                job_id=ctx.get("job_id"),
                attempt_no=int(ctx.get("job_try", 1)),
            )
        except Exception as exc:
            if service.should_retry_exception(exc):
                raise retry_backoff(ctx.get("job_try", 1), base=45) from exc
            raise
        finally:
            reset_sync_log_context(token)


async def refresh_qb_connections_task(ctx: dict, limit: int = 200) -> None:  # noqa: ARG001
    async with get_async_session() as session:
        service = QuickBooksService(session)
        await service.refresh_connections_due(limit=limit)
