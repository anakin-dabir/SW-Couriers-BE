"""Account statement business logic."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import Job
from app.common.enums.user import UserRole
from app.common.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.core.queue import QueuePriority, enqueue
from app.modules.account_statements.constants import (
    COMPANY_ADDRESS,
    COMPANY_EMAIL,
    COMPANY_NAME,
    PDF_TEMPLATE_VERSION,
    SIGNED_URL_EXPIRY_SECONDS,
)
from app.modules.account_statements.v1.schemas import (
    StatementDetailResponse,
    StatementProviderInfo,
    ledger_snapshot_from_ledger,
    statement_to_detail,
)
from app.modules.account_statements.enums import (
    StatementCreatedByType,
    StatementDeliveryStatus,
    StatementPdfStatus,
    StatementScheduleFrequency,
    StatementScheduleStatus,
)
from app.modules.account_statements.ledger import (
    StatementLedgerBuilder,
    compute_content_signature,
)
from app.modules.account_statements.models import AccountStatement, AccountStatementSchedule
from app.modules.account_statements.repository import (
    AccountStatementDeliveryRepository,
    AccountStatementRepository,
    AccountStatementScheduleRepository,
)
from app.modules.account_statements.scheduling import (
    initial_next_run_at_utc,
    is_once_custom_schedule,
    local_date_now,
    next_run_at_utc,
    normalize_frequency,
    parse_interval_days,
    resolve_timezone,
    statement_period_for_custom_run,
    statement_period_for_run,
    validate_schedule_inputs,
)
from app.modules.account_statements.validation import validate_email, validate_period
from app.modules.organizations.repository import OrganizationRepository
from app.storage.r2_client import generate_presigned_url


class AccountStatementService(BaseService):
    def __init__(self, session: AsyncSession, request=None) -> None:
        super().__init__(session, request)
        self._stmt_repo = AccountStatementRepository(session)
        self._schedule_repo = AccountStatementScheduleRepository(session)
        self._delivery_repo = AccountStatementDeliveryRepository(session)
        self._org_repo = OrganizationRepository(session)

    async def _ensure_org(self, org_id: str) -> Any:
        org = await self._org_repo.get_by_id(org_id)
        if org is None:
            raise NotFoundError(resource="organization", id=org_id)
        return org

    def _org_address(self, org: Any) -> str:
        parts = [
            getattr(org, "reg_address_line_1", "") or "",
            getattr(org, "reg_city", "") or "",
            getattr(org, "reg_postcode", "") or "",
            getattr(org, "reg_country", None) or "United Kingdom",
        ]
        return ", ".join(p for p in parts if p)

    def _ledger_to_snapshot(self, ledger: Any) -> dict[str, Any]:
        """Serialize ledger for snapshot_json storage (includes running balance per row)."""
        return ledger_snapshot_from_ledger(ledger).model_dump(mode="json")

    def _provider_info(self) -> StatementProviderInfo:
        return StatementProviderInfo(name=COMPANY_NAME, address=COMPANY_ADDRESS, email=COMPANY_EMAIL)

    def _client_email(self, org: Any) -> str | None:
        for attr in ("billing_email", "accounts_email", "contact_email", "email"):
            value = getattr(org, attr, None)
            if value and str(value).strip():
                return str(value).strip()
        return None

    async def build_ledger(
        self,
        *,
        organization_id: str,
        period_start: date,
        period_end: date,
        include_line_item_detail: bool,
        include_credit_notes: bool,
        include_payment_history: bool,
        aging_as_of: date | None = None,
    ) -> Any:
        await self._ensure_org(organization_id)
        validate_period(period_start, period_end, today=date.today())
        builder = StatementLedgerBuilder(self._session)
        return await builder.build(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            include_line_item_detail=include_line_item_detail,
            include_credit_notes=include_credit_notes,
            include_payment_history=include_payment_history,
            aging_as_of=aging_as_of or period_end,
        )

    async def get_preview(
        self,
        *,
        organization_id: str,
        period_start: date,
        period_end: date,
        include_line_item_detail: bool,
        include_credit_notes: bool,
        include_payment_history: bool,
        preview_aging_as_of_today: bool = True,
    ) -> dict[str, Any]:
        aging_as_of = date.today() if preview_aging_as_of_today else period_end
        ledger = await self.build_ledger(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            include_line_item_detail=include_line_item_detail,
            include_credit_notes=include_credit_notes,
            include_payment_history=include_payment_history,
            aging_as_of=aging_as_of,
        )
        org = await self._ensure_org(organization_id)
        return {
            "organization_id": organization_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "provider": self._provider_info(),
            "client_name": getattr(org, "trading_name", "") or getattr(org, "legal_entity_name", ""),
            "client_address": self._org_address(org),
            "client_email": self._client_email(org),
            "ledger": ledger_snapshot_from_ledger(ledger),
        }

    async def create_statement(
        self,
        *,
        organization_id: str,
        period_start: date,
        period_end: date,
        include_line_item_detail: bool,
        include_credit_notes: bool,
        include_payment_history: bool,
        created_by_user_id: str | None,
        created_by_user_type: StatementCreatedByType,
        idempotency_key: str | None = None,
    ) -> AccountStatement:
        validate_period(period_start, period_end, today=date.today())
        signature = compute_content_signature(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            include_line_item_detail=include_line_item_detail,
            include_credit_notes=include_credit_notes,
            include_payment_history=include_payment_history,
            template_version=PDF_TEMPLATE_VERSION,
        )

        existing = await self._stmt_repo.get_active_by_signature(organization_id, signature)
        if existing is not None:
            if existing.pdf_status == StatementPdfStatus.READY.value:
                return existing
            if existing.pdf_status in {StatementPdfStatus.PENDING.value, StatementPdfStatus.GENERATING.value}:
                return existing

        ledger = await self.build_ledger(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            include_line_item_detail=include_line_item_detail,
            include_credit_notes=include_credit_notes,
            include_payment_history=include_payment_history,
            aging_as_of=period_end,
        )
        snapshot = self._ledger_to_snapshot(ledger)

        statement = await self._stmt_repo.create(
            {
                "organization_id": organization_id,
                "period_start": period_start,
                "period_end": period_end,
                "opening_balance": ledger.opening_balance,
                "closing_balance": ledger.closing_balance,
                "total_invoice_amount": ledger.total_invoice_amount,
                "total_paid": ledger.total_paid,
                "total_unpaid": ledger.total_unpaid,
                "total_overdue": ledger.total_overdue,
                "aging_json": ledger.aging,
                "include_line_item_detail": include_line_item_detail,
                "include_credit_notes": include_credit_notes,
                "include_payment_history": include_payment_history,
                "pdf_status": StatementPdfStatus.GENERATING.value,
                "pdf_template_version": PDF_TEMPLATE_VERSION,
                "content_signature": signature,
                "created_by_user_id": created_by_user_id,
                "created_by_user_type": created_by_user_type.value,
                "snapshot_json": snapshot,
            }
        )
        await self._session.flush()

        job = await enqueue(
            Job.GENERATE_ACCOUNT_STATEMENT_PDF,
            statement_id=statement.id,
            _job_id=self._pdf_job_id(statement_id=statement.id, signature=signature, idempotency_key=idempotency_key),
            priority=QueuePriority.LOW,
        )
        if job and job.job_id:
            statement.job_id = job.job_id
            await self._session.flush()
        return statement

    @staticmethod
    def _pdf_job_id(*, statement_id: str, signature: str, idempotency_key: str | None) -> str:
        sig_part = signature[:12]
        idem_raw = (idempotency_key or "").strip()
        idem_part = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()[:12] if idem_raw else "noidem"
        return f"stmtpdf:{statement_id}:{sig_part}:{idem_part}"

    async def get_statement(self, statement_id: str, *, organization_id: str) -> AccountStatement:
        stmt = await self._stmt_repo.get_by_id(statement_id, organization_id=organization_id)
        if stmt is None or stmt.deleted_at is not None:
            raise NotFoundError(resource="account_statement", id=statement_id)
        return stmt

    async def get_statement_detail(self, statement_id: str, *, organization_id: str) -> StatementDetailResponse:
        stmt = await self.get_statement(statement_id, organization_id=organization_id)
        org = await self._ensure_org(organization_id)
        return statement_to_detail(stmt, org=org)

    async def get_pdf_status(self, statement_id: str, *, organization_id: str) -> dict[str, Any]:
        stmt = await self.get_statement(statement_id, organization_id=organization_id)
        return {
            "statement_id": stmt.id,
            "status": stmt.pdf_status,
            "job_id": stmt.job_id,
            "failure_reason": stmt.failure_reason,
            "generated_at": stmt.generated_at.isoformat() if stmt.generated_at else None,
        }

    async def get_signed_url(
        self,
        statement_id: str,
        *,
        organization_id: str,
        disposition: str = "attachment",
    ) -> tuple[str, datetime]:
        stmt = await self.get_statement(statement_id, organization_id=organization_id)
        if stmt.pdf_status != StatementPdfStatus.READY.value or not stmt.pdf_r2_key:
            raise NotFoundError(resource="account_statement_pdf", id=statement_id)
        safe_name = stmt.statement_number.replace('"', "")
        disp = disposition if disposition in {"inline", "attachment"} else "attachment"
        content_disposition = f'{disp}; filename="{safe_name}.pdf"'
        url = generate_presigned_url(
            stmt.pdf_r2_key,
            expiry_seconds=SIGNED_URL_EXPIRY_SECONDS,
            content_type="application/pdf",
            response_content_disposition=content_disposition,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=SIGNED_URL_EXPIRY_SECONDS)
        return url, expires_at

    async def delete_statement(self, statement_id: str, *, organization_id: str) -> None:
        stmt = await self.get_statement(statement_id, organization_id=organization_id)
        if await self._stmt_repo.has_successful_delivery(statement_id):
            raise ConflictError("Cannot delete a statement that has been emailed to a client")
        await self._stmt_repo.update_by_id(
            statement_id,
            {"deleted_at": datetime.now(UTC)},
            expected_version=stmt.version,
        )

    async def send_email(
        self,
        statement_id: str,
        *,
        organization_id: str,
        recipient_email: str,
        sent_by_user_id: str | None,
    ) -> dict[str, Any]:
        stmt = await self.get_statement(statement_id, organization_id=organization_id)
        email_clean = validate_email(recipient_email)

        if stmt.pdf_status != StatementPdfStatus.READY.value:
            if stmt.pdf_status in {
                StatementPdfStatus.PENDING.value,
                StatementPdfStatus.GENERATING.value,
            }:
                return await self._queue_statement_email(
                    statement_id=statement_id,
                    organization_id=organization_id,
                    recipient_email=email_clean,
                    sent_by_user_id=sent_by_user_id,
                )
            if stmt.pdf_status == StatementPdfStatus.FAILED.value:
                reason = (stmt.failure_reason or "PDF generation failed").strip()
                raise ValidationError(
                    "Statement PDF generation failed; create a new statement before sending email",
                    details=[{"field": "pdf_status", "message": reason}],
                )
            raise ValidationError(
                "Statement PDF is not ready to send",
                details=[{"field": "pdf_status", "message": stmt.pdf_status}],
            )

        url, expires_at = await self.get_signed_url(statement_id, organization_id=organization_id, disposition="attachment")

        from app.mailer.client import send_email

        html_body = (
            f"<p>Your account statement <strong>{stmt.statement_number}</strong> "
            f"for period {stmt.period_start} to {stmt.period_end} is ready.</p>"
            f"<p><a href='{url}'>Download statement (expires {expires_at.isoformat()})</a></p>"
        )
        event = await self._delivery_repo.create(
            {
                "statement_id": statement_id,
                "recipient_email": email_clean,
                "status": StatementDeliveryStatus.PENDING.value,
                "sent_by_user_id": sent_by_user_id,
            }
        )
        try:
            await send_email(email_clean, f"Account Statement {stmt.statement_number}", html_body=html_body)
            await self._delivery_repo.update_by_id(
                event.id,
                {
                    "status": StatementDeliveryStatus.SENT.value,
                    "sent_at": datetime.now(UTC),
                },
            )
        except ValueError as exc:
            await self._delivery_repo.update_by_id(
                event.id,
                {
                    "status": StatementDeliveryStatus.FAILED.value,
                    "error_message": str(exc)[:500],
                },
            )
            raise ValidationError(str(exc)) from exc
        except RuntimeError as exc:
            await self._delivery_repo.update_by_id(
                event.id,
                {
                    "status": StatementDeliveryStatus.FAILED.value,
                    "error_message": str(exc)[:500],
                },
            )
            message = "Email service is not configured" if "SMTP not configured" in str(exc) else str(exc)
            raise ValidationError(message) from exc
        except Exception as exc:
            await self._delivery_repo.update_by_id(
                event.id,
                {
                    "status": StatementDeliveryStatus.FAILED.value,
                    "error_message": str(exc)[:500],
                },
            )
            raise
        return {"recipient_email": email_clean, "status": StatementDeliveryStatus.SENT.value}

    async def _queue_statement_email(
        self,
        *,
        statement_id: str,
        organization_id: str,
        recipient_email: str,
        sent_by_user_id: str | None,
    ) -> dict[str, Any]:
        job_suffix = hashlib.sha256(recipient_email.encode("utf-8")).hexdigest()[:16]
        await enqueue(
            Job.DELIVER_SCHEDULED_ACCOUNT_STATEMENT,
            statement_id=statement_id,
            organization_id=organization_id,
            recipient_email=recipient_email,
            sent_by_user_id=sent_by_user_id,
            _job_id=f"stmtdeliver:manual:{statement_id}:{job_suffix}",
            priority=QueuePriority.LOW,
        )
        return {"recipient_email": recipient_email, "status": StatementDeliveryStatus.PENDING.value}

    async def list_statements(
        self,
        organization_id: str,
        *,
        page: int,
        size: int,
        search: str | None,
        period_start_from: date | None,
        period_start_to: date | None,
        generated_from: datetime | None,
        generated_to: datetime | None,
    ) -> tuple[list[AccountStatement], int]:
        await self._ensure_org(organization_id)
        return await self._stmt_repo.list_for_org(
            organization_id,
            page=page,
            size=size,
            search=search,
            period_start_from=period_start_from,
            period_start_to=period_start_to,
            generated_from=generated_from,
            generated_to=generated_to,
        )

    # ── Schedules ─────────────────────────────────────────────

    async def create_schedule(
        self,
        *,
        organization_id: str,
        frequency: str,
        valid_from: date | None,
        valid_to: date | None,
        recipient_email: str,
        timezone: str,
        include_line_item_detail: bool,
        include_credit_notes: bool,
        include_payment_history: bool,
        interval_days: int | None = None,
    ) -> AccountStatementSchedule:
        await self._ensure_org(organization_id)
        now = datetime.now(UTC)
        freq, tz, interval_storage, resolved_from, resolved_to = validate_schedule_inputs(
            frequency=frequency,
            valid_from=valid_from,
            valid_to=valid_to,
            timezone=timezone,
            interval_days=interval_days,
            now_utc=now,
        )
        email_clean = validate_email(recipient_email)
        next_run = initial_next_run_at_utc(
            frequency=freq,
            tz=tz,
            valid_from=resolved_from,
            valid_to=resolved_to,
            interval_storage=interval_storage,
            now_utc=now,
        )
        status = StatementScheduleStatus.ACTIVE.value if next_run is not None else StatementScheduleStatus.COMPLETED.value
        return await self._schedule_repo.create(
            {
                "organization_id": organization_id,
                "frequency": freq.value,
                "valid_from": resolved_from,
                "valid_to": resolved_to,
                "recipient_email": email_clean,
                "timezone": str(tz.key),
                "custom_cron": interval_storage,
                "include_line_item_detail": include_line_item_detail,
                "include_credit_notes": include_credit_notes,
                "include_payment_history": include_payment_history,
                "status": status,
                "next_run_at": next_run,
            }
        )

    async def list_schedules(self, organization_id: str) -> list[AccountStatementSchedule]:
        await self._ensure_org(organization_id)
        return await self._schedule_repo.list_for_org(organization_id)

    async def process_due_schedules(self, *, now_utc: datetime | None = None) -> int:
        """Run all schedules whose ``next_run_at`` has passed. Returns count processed."""
        ref = now_utc or datetime.now(UTC)
        processed = 0
        while True:
            batch = await self._schedule_repo.list_due(before=ref, limit=1)
            if not batch:
                break
            await self._execute_schedule_run(batch[0], now_utc=ref)
            await self._session.flush()
            processed += 1
        return processed

    async def _execute_schedule_run(
        self,
        schedule: AccountStatementSchedule,
        *,
        now_utc: datetime,
    ) -> None:
        freq = normalize_frequency(schedule.frequency)
        tz = resolve_timezone(schedule.timezone)
        run_local = local_date_now(tz=tz, now_utc=now_utc)

        once_custom = freq == StatementScheduleFrequency.CUSTOM and is_once_custom_schedule(
            schedule.custom_cron
        )

        if not once_custom and (run_local < schedule.valid_from or run_local > schedule.valid_to):
            await self._schedule_repo.update_by_id(
                schedule.id,
                {
                    "status": StatementScheduleStatus.COMPLETED.value,
                    "next_run_at": None,
                },
                expected_version=schedule.version,
            )
            await self._session.flush()
            return

        if freq == StatementScheduleFrequency.CUSTOM:
            period_start, period_end = statement_period_for_custom_run(
                run_local_date=run_local,
                valid_from=schedule.valid_from,
                valid_to=schedule.valid_to,
                last_run_at=schedule.last_run_at,
                tz=tz,
                interval_storage=schedule.custom_cron,
            )
        else:
            period_start, period_end = statement_period_for_run(freq, run_local_date=run_local)

        validate_period(period_start, period_end, today=run_local)

        statement = await self.create_statement(
            organization_id=schedule.organization_id,
            period_start=period_start,
            period_end=period_end,
            include_line_item_detail=schedule.include_line_item_detail,
            include_credit_notes=schedule.include_credit_notes,
            include_payment_history=schedule.include_payment_history,
            created_by_user_id=None,
            created_by_user_type=StatementCreatedByType.SYSTEM,
            idempotency_key=f"schedule:{schedule.id}:{period_start}:{period_end}",
        )

        following = next_run_at_utc(
            frequency=freq,
            tz=tz,
            valid_from=schedule.valid_from,
            valid_to=schedule.valid_to,
            interval_storage=schedule.custom_cron,
            after_utc=now_utc,
        )
        status = (
            StatementScheduleStatus.ACTIVE.value
            if following is not None
            else StatementScheduleStatus.COMPLETED.value
        )
        await self._schedule_repo.update_by_id(
            schedule.id,
            {
                "last_run_at": now_utc,
                "next_run_at": following,
                "status": status,
            },
            expected_version=schedule.version,
        )
        await self._session.flush()

        await enqueue(
            Job.DELIVER_SCHEDULED_ACCOUNT_STATEMENT,
            statement_id=statement.id,
            organization_id=schedule.organization_id,
            recipient_email=schedule.recipient_email,
            _job_id=f"stmtdeliver:{schedule.id}:{statement.id}",
            priority=QueuePriority.LOW,
        )


def resolve_admin_org_id(user: Any, org_id: str | None) -> str:
    """Admin must pass org_id; B2B uses JWT org."""
    from app.common.enums import ClientType

    if user.client_type == ClientType.CUSTOMER_B2B:
        if not user.organization_id:
            raise ForbiddenError("Tenant context missing")
        if org_id and org_id != user.organization_id:
            raise ForbiddenError("Cannot access another organization's statements")
        return user.organization_id

    role_val = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    if role_val in (UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value):
        oid = (org_id or "").strip()
        if not oid:
            raise ValidationError("org_id path parameter is required")
        try:
            UUID(oid)
        except ValueError:
            raise ValidationError("org_id must be a valid UUID") from None
        return oid
    raise ForbiddenError("Not allowed to access account statements")
