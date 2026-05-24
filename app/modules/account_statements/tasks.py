"""Account statement background jobs (PDF generation and recurring schedules)."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.common.enums import LogEvent
from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.modules.account_statements.enums import StatementPdfStatus
from app.modules.account_statements.ledger import StatementLedgerBuilder
from app.modules.account_statements.pdf_builder import build_statement_html, html_to_pdf
from app.modules.account_statements.repository import AccountStatementRepository
from app.modules.account_statements.service import AccountStatementService
from app.modules.account_statements.validation import validate_email
from app.modules.organizations.repository import OrganizationRepository
from app.storage.upload import delete_from_r2, upload_to_r2

logger = structlog.get_logger()


async def generate_account_statement_pdf_task(ctx: dict, statement_id: str) -> None:
    """Render statement HTML, convert with WeasyPrint, upload to R2."""
    async with get_async_session() as session:
        stmt_repo = AccountStatementRepository(session)
        org_repo = OrganizationRepository(session)

        statement = await stmt_repo.get_by_id(statement_id)
        if statement is None:
            logger.warning(LogEvent.ARQ_JOB_FAILED, job="generate_account_statement_pdf_task", statement_id=statement_id)
            return

        org = await org_repo.get_by_id(statement.organization_id)
        if org is None:
            await _mark_failed(stmt_repo, statement_id, "ORG_NOT_FOUND", "Organization not found")
            await session.commit()
            return

        try:
            builder = StatementLedgerBuilder(session)
            ledger = await builder.build(
                organization_id=statement.organization_id,
                period_start=statement.period_start,
                period_end=statement.period_end,
                include_line_item_detail=statement.include_line_item_detail,
                include_credit_notes=statement.include_credit_notes,
                include_payment_history=statement.include_payment_history,
                aging_as_of=statement.period_end,
            )
            parts = [
                getattr(org, "reg_address_line_1", "") or "",
                getattr(org, "reg_city", "") or "",
                getattr(org, "reg_postcode", "") or "",
            ]
            client_address = ", ".join(p for p in parts if p)
            html_content = build_statement_html(
                ledger=ledger,
                period_start=statement.period_start,
                period_end=statement.period_end,
                client_name=getattr(org, "trading_name", "") or getattr(org, "legal_entity_name", ""),
                client_address=client_address,
                client_email=getattr(org, "billing_email", "") or "",
                statement_number=statement.statement_number,
            )
            pdf_bytes = html_to_pdf(html_content)
        except Exception as exc:
            await _mark_failed(stmt_repo, statement_id, type(exc).__name__, str(exc))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from exc

        r2_key = f"account-statements/{statement.organization_id}/{statement_id}.pdf"
        old_key = statement.pdf_r2_key
        try:
            await upload_to_r2(r2_key, pdf_bytes, "application/pdf")
        except Exception as exc:
            await _mark_failed(stmt_repo, statement_id, "UPLOAD_FAILED", str(exc))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from exc

        await stmt_repo.update_by_id(
            statement_id,
            {
                "pdf_status": StatementPdfStatus.READY.value,
                "pdf_r2_key": r2_key,
                "generated_at": datetime.now(UTC),
                "failure_reason": None,
            },
        )
        await session.commit()

        if old_key and old_key != r2_key:
            try:
                await delete_from_r2(old_key)
            except Exception:
                logger.warning("account_statement.r2_delete_old_failed", key=old_key)

        logger.info(
            LogEvent.ARQ_JOB_ENQUEUED,
            job="generate_account_statement_pdf_task",
            statement_id=statement_id,
            r2_key=r2_key,
        )


async def deliver_scheduled_account_statement_task(
    ctx: dict,
    statement_id: str,
    organization_id: str,
    recipient_email: str,
    sent_by_user_id: str | None = None,
) -> None:
    """Email a statement once its PDF is ready (scheduled or manual queue)."""
    async with get_async_session() as session:
        service = AccountStatementService(session)
        stmt = await service.get_statement(statement_id, organization_id=organization_id)
        if stmt.pdf_status != StatementPdfStatus.READY.value:
            raise retry_backoff(ctx.get("job_try", 1), base=45)

        await service.send_email(
            statement_id,
            organization_id=organization_id,
            recipient_email=validate_email(recipient_email),
            sent_by_user_id=sent_by_user_id,
        )
        await session.commit()


async def run_account_statement_schedules_task(ctx: dict) -> None:
    """Process due recurring statement schedules (LOW worker cron)."""
    now = datetime.now(UTC)
    async with get_async_session() as session:
        service = AccountStatementService(session)
        processed = await service.process_due_schedules(now_utc=now)
    logger.info(
        "account_statement.schedules_cron_completed",
        processed=processed,
        run_at=now.isoformat(),
    )


async def _mark_failed(repo: AccountStatementRepository, statement_id: str, code: str, message: str) -> None:
    await repo.update_by_id(
        statement_id,
        {
            "pdf_status": StatementPdfStatus.FAILED.value,
            "failure_reason": f"{code}: {message}"[:500],
        },
    )
