"""QuickBooks integration service for single-scope OAuth and async sync orchestration."""
from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Sequence
from typing import Any
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import structlog
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import Job
from app.common.exceptions import AppError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.core.redis import get_redis
from app.integrations.quickbooks.auth import (
    build_oauth_authorize_url,
    encrypt_token,
    exchange_code_for_tokens,
    generate_state,
    get_oauth_state_ttl_seconds,
)
from app.integrations.quickbooks.client import QuickBooksClient
from app.integrations.quickbooks.constants import QB_GLOBAL_NAMESPACE_ID
from app.integrations.quickbooks.models import QbLink, QbSyncLog
from app.integrations.quickbooks.sync_logging import (
    EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED,
    EVENT_CREDIT_NOTE_VOID_QUEUED,
    EVENT_PAYMENT_SYNC_SKIPPED,
    EVENT_VOID_CHAIN_STEP,
    LOG_STATUS_FAILED,
    LOG_STATUS_PENDING,
    LOG_STATUS_SYNCED,
    SyncLogContext,
    build_sync_payload,
    correlation_id_for_void_credit_note,
    reset_sync_log_context,
    set_sync_log_context,
)
from app.integrations.quickbooks.repository import (
    QbConnectionRepository,
    QbLinkRepository,
    QbReferenceMappingRepository,
    QbSyncLogRepository,
    QbSyncSettingsRepository,
)
from app.modules.addresses.models import Address
from app.modules.billing.models import BillingPayment
from app.modules.billing.repository import BillingPaymentAllocationRepository
from app.modules.invoices.enums import PaymentStatus
from app.modules.invoices.models import CreditNote, Invoice, InvoiceLineItem
from app.modules.invoices.repository import InvoiceCreditApplicationRepository
from app.modules.notifications.dispatch import notify
from app.modules.notifications.enums import NotificationEvent, NotificationType
from app.modules.orders.models import Order
from app.modules.organizations.models import Organization
from app.modules.user.models import User

QB_ENTITY_CUSTOMER = "customer"
QB_ENTITY_INVOICE = "invoice"
QB_ENTITY_CREDIT_NOTE = "credit_note"
QB_ENTITY_CREDIT_APPLICATION = "credit_application"
QB_ENTITY_PAYMENT = "payment"
QB_MAPPING_ITEM = "ITEM"
QB_MAPPING_TAX_CODE = "TAX_CODE"
QB_MAPPING_TERM = "TERM"
QB_MAPPING_CLASS = "CLASS"
QB_MAPPING_DEPARTMENT = "DEPARTMENT"
QB_MAPPING_LOCATION = "LOCATION"
SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_FAILED = "failed"
ACTION_CREATED = "Created"
ACTION_UPDATED = "Updated"
ACTION_QUEUED = "Queued"
ACTION_NO_CHANGE = "No Change"
ACTION_CREDIT_APPLIED = "Credit Applied"
_QB_WORKER_MAX_TRIES = 3
_CONNECTION_FAILURE_MARKERS = (
    "not connected",
    "token refresh failed",
    "invalid_grant",
    "revoked",
    "oauth",
    "authentication",
    "unauthorized",
    "401",
)
_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "rate limit",
    "too many requests",
    "connection reset",
    "network",
    "service unavailable",
    "502",
    "503",
    "504",
)

logger = structlog.get_logger()


class QuickBooksService(BaseService):
    @staticmethod
    def classify_sync_error(exc: Exception) -> tuple[str, str, bool]:
        """Return (error_code, human_message, should_retry)."""
        msg = str(exc or "").strip()
        lower = msg.lower()
        exc_name = type(exc).__name__
        if any(marker in lower for marker in _TRANSIENT_ERROR_MARKERS):
            return ("TRANSIENT_EXTERNAL", f"Transient upstream failure: {msg}", True)
        if any(marker in lower for marker in _CONNECTION_FAILURE_MARKERS):
            return ("TRANSIENT_EXTERNAL_CONNECTION", f"QuickBooks connection/auth issue: {msg}", True)
        if isinstance(exc, ValidationError):
            return ("TERMINAL_VALIDATION", f"Validation failed for QuickBooks sync: {msg}", False)
        if isinstance(exc, NotFoundError):
            return ("DEPENDENCY_BLOCKED", f"Sync dependency missing: {msg}", False)
        return (f"TERMINAL_EXTERNAL_{exc_name}".upper(), f"External integration error: {msg}", False)

    @staticmethod
    def should_retry_exception(exc: Exception) -> bool:
        return QuickBooksService.classify_sync_error(exc)[2]

    """Service layer for OAuth state, enqueue orchestration, sync execution, and operational APIs."""
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._conn_repo = QbConnectionRepository(session)
        self._link_repo = QbLinkRepository(session)
        self._mapping_repo = QbReferenceMappingRepository(session)
        self._settings_repo = QbSyncSettingsRepository(session)
        self._sync_log_repo = QbSyncLogRepository(session)
        self._credit_app_repo = InvoiceCreditApplicationRepository(session)
        self._billing_alloc_repo = BillingPaymentAllocationRepository(session)

    async def _log_sync(
        self,
        *,
        organization_id: str,
        entity_type: str,
        local_entity_id: str | None,
        event_type: str | None,
        action: str,
        status: str,
        job_id: str | None = None,
        attempt_no: int = 1,
        error_code: str | None = None,
        error_message: str | None = None,
        related_qb_id: str | None = None,
        payload: dict | None = None,
        trigger_source: str | None = None,
        correlation_id: str | None = None,
        step: str | None = None,
        business: dict | None = None,
    ) -> QbSyncLog | None:
        """Append qb_sync_logs row; never raises (observability must not break billing)."""
        organization_id = self._require_organization_id(organization_id)
        try:
            merged_payload = build_sync_payload(
                trigger_source=trigger_source,
                correlation_id=correlation_id,
                business=business,
                step=step,
                extra=payload,
            )
            return await self._sync_log_repo.log(
                organization_id=organization_id,
                entity_type=entity_type,
                local_entity_id=local_entity_id,
                event_type=event_type,
                action=action,
                status=status,
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=(error_message[:500] if error_message else None),
                related_qb_id=related_qb_id,
                payload=merged_payload,
            )
        except Exception as exc:
            logger.warning(
                "quickbooks.sync_log_write_failed",
                entity_type=entity_type,
                local_entity_id=local_entity_id,
                event_type=event_type,
                error=type(exc).__name__,
            )
            return None

    async def _queue_sync_job(
        self,
        job: Job,
        *,
        organization_id: str,
        entity_type: str,
        local_entity_id: str,
        event_type: str,
        job_id: str,
        trigger_source: str,
        queue_args: tuple[Any, ...] = (),
        queue_kwargs: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        business: dict | None = None,
    ) -> Any:
        """Enqueue ARQ job and record PENDING sync log (single gateway for domain modules)."""
        organization_id = self._require_organization_id(organization_id)
        kwargs = dict(queue_kwargs or {})
        kwargs.setdefault("_job_id", job_id)
        kwargs.setdefault("priority", QueuePriority.DEFAULT)
        job_result = await enqueue(job, *queue_args, **kwargs)
        arq_job_id = getattr(job_result, "job_id", None) if job_result is not None else None
        await self._log_sync(
            organization_id=organization_id,
            entity_type=entity_type,
            local_entity_id=local_entity_id,
            event_type=event_type,
            action=ACTION_QUEUED,
            status=LOG_STATUS_PENDING,
            job_id=arq_job_id or job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            business=business,
            payload={
                "enqueue": {
                    "job_name": job.value if isinstance(job, Job) else str(job),
                    "queued": job_result is not None,
                }
            },
        )
        return job_result

    @staticmethod
    def _require_organization_id(organization_id: str) -> str:
        _ = organization_id
        # Global singleton QuickBooks model: one namespace for all operations.
        return QB_GLOBAL_NAMESPACE_ID

    @staticmethod
    def resolve_swc_scope_id() -> str:
        """Return global singleton namespace id for all QuickBooks operations."""
        return QB_GLOBAL_NAMESPACE_ID

    async def get_connect_url(self, *, organization_id: str, actor_user_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        logger.info(
            "quickbooks.connect_url_generated",
            actor_user_id=actor_user_id,
            effective_scope_id=organization_id,
            action="connect_url_generated",
        )
        state = generate_state()
        redis = get_redis()
        expires_at = datetime.now(UTC) + timedelta(seconds=get_oauth_state_ttl_seconds())
        payload = json.dumps(
            {
                "scope_id": organization_id,
                "user_id": actor_user_id,
                "exp": int(expires_at.timestamp()),
            }
        )
        await redis.setex(f"qb:oauth_state:{state}", get_oauth_state_ttl_seconds(), payload)
        return {"authorization_url": build_oauth_authorize_url(state), "state": state}

    async def handle_callback(self, *, state: str, code: str, realm_id: str) -> dict:
        redis = get_redis()
        state_key = f"qb:oauth_state:{state}"
        lock_key = f"qb:oauth_state_lock:{state}"
        raw = await redis.get(state_key)
        if raw is None:
            await self._record_oauth_callback_anomaly(redis=redis, reason="missing_or_expired_state", state=state)
            raise ValidationError("OAuth state is missing or expired")
        try:
            payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except (TypeError, ValueError):
            logger.warning("quickbooks.oauth_state_invalid", reason="malformed_payload")
            await self._record_oauth_callback_anomaly(redis=redis, reason="malformed_payload", state=state)
            raise ValidationError("OAuth state payload is invalid") from None
        if not isinstance(payload, dict):
            logger.warning("quickbooks.oauth_state_invalid", reason="invalid_payload_type")
            await self._record_oauth_callback_anomaly(redis=redis, reason="invalid_payload_type", state=state)
            raise ValidationError("OAuth state payload is invalid")

        organization_id = self.resolve_swc_scope_id()
        actor_user_id = payload.get("user_id")
        exp = payload.get("exp")
        if not actor_user_id or exp is None:
            logger.warning("quickbooks.oauth_state_invalid", reason="missing_payload_fields")
            await self._record_oauth_callback_anomaly(redis=redis, reason="missing_payload_fields", state=state)
            raise ValidationError("OAuth state payload is invalid")
        try:
            exp_ts = int(exp)
        except (TypeError, ValueError):
            logger.warning("quickbooks.oauth_state_invalid", reason="invalid_expiry")
            await self._record_oauth_callback_anomaly(redis=redis, reason="invalid_expiry", state=state)
            raise ValidationError("OAuth state payload is invalid") from None
        if exp_ts < int(datetime.now(UTC).timestamp()):
            logger.warning("quickbooks.oauth_state_invalid", reason="expired")
            await self._record_oauth_callback_anomaly(redis=redis, reason="expired", state=state)
            raise ValidationError("OAuth state is missing or expired")

        acquired = await redis.set(lock_key, "1", ex=60, nx=True)
        if not acquired:
            await self._record_oauth_callback_anomaly(redis=redis, reason="state_replayed_or_in_progress", state=state)
            raise ValidationError("OAuth callback is already being processed")
        try:
            token_payload = await exchange_code_for_tokens(code, realm_id)
            await self._conn_repo.upsert_for_org(
                str(organization_id),
                {
                    "realm_id": token_payload["realm_id"],
                    "access_token_enc": encrypt_token(token_payload["access_token"]),
                    "refresh_token_enc": encrypt_token(token_payload["refresh_token"]),
                    "access_token_expires_at": token_payload["access_token_expires_at"],
                    "refresh_token_expires_at": token_payload["refresh_token_expires_at"],
                    "connected_by_id": str(actor_user_id),
                    "is_active": True,
                    "last_error": None,
                },
            )
            await redis.delete(state_key)
            logger.info(
                "quickbooks.callback_connected",
                actor_user_id=str(actor_user_id),
                effective_scope_id=str(organization_id),
                action="oauth_callback_connected",
                realm_id=realm_id,
            )
            return {
                "connected": True,
                "realm_id": realm_id,
            }
        except Exception:
            logger.warning(
                "quickbooks.oauth_callback_exchange_failed",
                effective_scope_id=str(organization_id),
                actor_user_id=str(actor_user_id),
            )
            await self._record_oauth_callback_anomaly(redis=redis, reason="token_exchange_failed", state=state)
            raise
        finally:
            await redis.delete(lock_key)

    async def _record_oauth_callback_anomaly(self, *, redis, reason: str, state: str | None = None) -> None:
        """Best-effort Redis counters for suspicious/invalid callback traffic."""
        bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
        counter_key = f"qb:oauth_callback_anomaly:{reason}:{bucket}"
        state_fragment = hashlib.sha256((state or "").encode("utf-8")).hexdigest()[:12] if state else "none"
        state_counter_key = f"qb:oauth_callback_anomaly_state:{state_fragment}:{bucket}"
        try:
            await redis.incr(counter_key)
            await redis.expire(counter_key, 3600)
            await redis.incr(state_counter_key)
            await redis.expire(state_counter_key, 3600)
        except Exception:
            logger.warning("quickbooks.oauth_callback_anomaly_counter_failed", reason=reason)

    async def get_status(self, *, organization_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        conn = await self._conn_repo.find_one(organization_id=organization_id)
        last_synced_at, failed_syncs = await self._status_sync_metrics(organization_id)
        if conn is None:
            return {
                "connected": False,
                "expires_at": None,
                "connection_status": "revoked",
                "status_created_at": None,
                "last_refreshed_at": None,
                "last_synced_at": last_synced_at,
                "failed_syncs": failed_syncs,
                "last_error_at": None,
                "last_error": None,
            }
        if not conn.is_active:
            return {
                "connected": False,
                "realm_id": conn.realm_id,
                "expires_at": conn.access_token_expires_at,
                "connection_status": "revoked",
                "status_created_at": conn.created_at,
                "last_refreshed_at": conn.last_refreshed_at,
                "last_synced_at": last_synced_at,
                "failed_syncs": failed_syncs,
                "last_error_at": conn.last_error_at,
                "last_error": conn.last_error,
            }

        connection_status = "active"
        if conn.access_token_expires_at <= datetime.now(UTC):
            connection_status = "expired"
        if conn.last_error and any(marker in conn.last_error.lower() for marker in ("invalid_grant", "revoked", "token")):
            connection_status = "revoked"
        return {
            "connected": True,
            "realm_id": conn.realm_id,
            "expires_at": conn.access_token_expires_at,
            "connection_status": connection_status,
            "status_created_at": conn.created_at,
            "last_refreshed_at": conn.last_refreshed_at,
            "last_synced_at": last_synced_at,
            "failed_syncs": failed_syncs,
            "last_error_at": conn.last_error_at,
            "last_error": conn.last_error,
        }

    async def refresh_connections_due(self, *, limit: int = 200) -> dict:
        rows = await self._conn_repo.list_active(limit=limit)
        refreshed = 0
        skipped = 0
        failed = 0
        now = datetime.now(UTC)
        lead_window = timedelta(seconds=settings.QUICKBOOKS_REFRESH_LEAD_SECONDS)
        max_refresh_age = timedelta(seconds=settings.QUICKBOOKS_REFRESH_SAFETY_MAX_AGE_SECONDS)
        for conn in rows:
            expiry_due = conn.access_token_expires_at <= (now + lead_window)
            refresh_anchor = conn.last_refreshed_at or conn.created_at
            stale_due = refresh_anchor <= (now - max_refresh_age)
            refresh_due = expiry_due or stale_due
            if not refresh_due:
                skipped += 1
                continue
            try:
                client = QuickBooksClient(self._conn_repo, conn.organization_id)
                updated = await client.ensure_connection()
                if updated.access_token_expires_at > conn.access_token_expires_at:
                    refreshed += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "quickbooks.refresh_due_connection_failed",
                    organization_id=conn.organization_id,
                    error=str(exc)[:500],
                )
        return {
            "checked": len(rows),
            "refreshed": refreshed,
            "skipped": skipped,
            "failed": failed,
        }

    async def disconnect(self, *, organization_id: str) -> None:
        organization_id = self._require_organization_id(organization_id)
        conn = await self._conn_repo.find_one(organization_id=organization_id)
        if conn is None:
            return
        await self._conn_repo.update_by_id(
            conn.id,
            {"is_active": False, "last_error": None},
            expected_version=conn.version,
        )

    async def list_mappings(
        self,
        *,
        organization_id: str,
        mapping_type: str | None = None,
        is_active: bool | None = None,
        limit: int = 200,
    ) -> list[dict]:
        organization_id = self._require_organization_id(organization_id)
        rows = await self._mapping_repo.list_for_org(
            organization_id,
            mapping_type=mapping_type,
            is_active=is_active,
            limit=limit,
        )
        return [
            {
                "id": row.id,
                "mapping_type": row.mapping_type,
                "local_key": row.local_key,
                "qb_ref_id": row.qb_ref_id,
                "qb_ref_name": row.qb_ref_name,
                "is_active": row.is_active,
                "metadata": row.metadata_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    async def upsert_mapping(
        self,
        *,
        organization_id: str,
        mapping_type: str,
        local_key: str,
        qb_ref_id: str,
        qb_ref_name: str | None,
        is_active: bool,
        metadata: dict | None,
    ) -> dict:
        organization_id = self._require_organization_id(organization_id)
        row = await self._mapping_repo.upsert_mapping(
            organization_id=organization_id,
            mapping_type=mapping_type.upper(),
            local_key=local_key.strip(),
            qb_ref_id=qb_ref_id.strip(),
            qb_ref_name=qb_ref_name.strip() if isinstance(qb_ref_name, str) else qb_ref_name,
            is_active=is_active,
            metadata=metadata,
        )
        return {
            "id": row.id,
            "mapping_type": row.mapping_type,
            "local_key": row.local_key,
            "qb_ref_id": row.qb_ref_id,
            "qb_ref_name": row.qb_ref_name,
            "is_active": row.is_active,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def delete_mapping(self, *, organization_id: str, mapping_type: str, local_key: str) -> bool:
        organization_id = self._require_organization_id(organization_id)
        row = await self._mapping_repo.deactivate_mapping(
            organization_id=organization_id,
            mapping_type=mapping_type.upper(),
            local_key=local_key.strip(),
        )
        return row is not None

    async def get_sync_settings(self, *, organization_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        row = await self._settings_repo.get_or_create_default(organization_id)
        return {
            "strict_mapping_mode": row.strict_mapping_mode,
            "sync_attachments": row.sync_attachments,
            "auto_retry_enabled": row.auto_retry_enabled,
            "max_retry_attempts": row.max_retry_attempts,
            "retry_backoff_seconds": row.retry_backoff_seconds,
            "allow_force_reapply_credit": row.allow_force_reapply_credit,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def update_sync_settings(self, *, organization_id: str, updates: dict) -> dict:
        organization_id = self._require_organization_id(organization_id)
        clean_updates = {k: v for k, v in updates.items() if v is not None}
        row = await self._settings_repo.upsert_for_org(organization_id, clean_updates)
        return {
            "strict_mapping_mode": row.strict_mapping_mode,
            "sync_attachments": row.sync_attachments,
            "auto_retry_enabled": row.auto_retry_enabled,
            "max_retry_attempts": row.max_retry_attempts,
            "retry_backoff_seconds": row.retry_backoff_seconds,
            "allow_force_reapply_credit": row.allow_force_reapply_credit,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def enqueue_customer_sync(
        self,
        *,
        organization_id: str,
        customer_id: str,
        force: bool = False,
        trigger_source: str = "quickbooks.manual_sync",
        correlation_id: str | None = None,
    ) -> dict:
        """Queue customer sync for one scope-bound local customer.

        Returns a queue-result dict with `queued`, `job_id`, entity identifiers, and normalized sync status.
        """
        organization_id = self._require_organization_id(organization_id)
        await self._assert_user_belongs_to_org(user_id=customer_id, organization_id=organization_id)
        existing_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CUSTOMER, customer_id)
        if existing_link is not None and existing_link.sync_status == "QUEUED" and not force:
            return {
                "queued": False,
                "job_id": None,
                "entity_type": QB_ENTITY_CUSTOMER,
                "local_entity_id": customer_id,
                "sync_status": SYNC_STATUS_PENDING,
            }
        await self._enforce_org_queue_quota(organization_id=organization_id, additional_jobs=1)

        if existing_link is None:
            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CUSTOMER,
                local_entity_id=customer_id,
                qb_entity_id=f"pending:{customer_id}",
                sync_status="QUEUED",
            )
        else:
            await self._link_repo.update_by_id(
                existing_link.id,
                {"sync_status": "QUEUED", "last_error": None},
                expected_version=existing_link.version,
            )
        job_id = self._job_id("customer", organization_id, customer_id, force=force)
        job = await self._queue_sync_job(
            Job.SYNC_QB_CUSTOMER,
            organization_id=organization_id,
            entity_type=QB_ENTITY_CUSTOMER,
            local_entity_id=customer_id,
            event_type="CUSTOMER_QUEUED",
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            queue_kwargs={
                "organization_id": organization_id,
                "customer_id": customer_id,
                "force": force,
            },
        )
        return {
            "queued": True,
            "job_id": job.job_id if job else None,
            "entity_type": QB_ENTITY_CUSTOMER,
            "local_entity_id": customer_id,
            "sync_status": SYNC_STATUS_PENDING,
        }

    async def enqueue_invoice_sync(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        force: bool = False,
        trigger_source: str = "quickbooks.manual_sync",
        correlation_id: str | None = None,
        business: dict | None = None,
    ) -> dict:
        """Queue invoice sync for one scope-bound local invoice.

        Returns queue metadata and transitions invoice/link status to `QUEUED` when enqueue is accepted.
        """
        organization_id = self._require_organization_id(organization_id)
        invoice = await self._session.get(Invoice, invoice_id)
        if invoice is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        self._assert_model_belongs_to_org(
            model_name="invoice",
            model_id=invoice_id,
            model_org_id=getattr(invoice, "organization_id", None),
            organization_id=organization_id,
        )

        await self._session.flush()
        if invoice.qb_sync_status == "QUEUED" and not force:
            return {
                "queued": False,
                "job_id": None,
                "entity_type": QB_ENTITY_INVOICE,
                "local_entity_id": invoice_id,
                "sync_status": SYNC_STATUS_PENDING,
            }
        await self._enforce_org_queue_quota(organization_id=organization_id, additional_jobs=1)
        if invoice.qb_sync_status != "QUEUED":
            invoice.qb_sync_status = "QUEUED"
        job_id = self._job_id("invoice", organization_id, invoice_id, version=invoice.version, force=force)
        job = await self._queue_sync_job(
            Job.SYNC_QB_INVOICE,
            organization_id=organization_id,
            entity_type=QB_ENTITY_INVOICE,
            local_entity_id=invoice_id,
            event_type="INVOICE_QUEUED",
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            business=business,
            queue_kwargs={
                "organization_id": organization_id,
                "invoice_id": invoice_id,
                "force": force,
            },
        )
        return {
            "queued": True,
            "job_id": job.job_id if job else None,
            "entity_type": QB_ENTITY_INVOICE,
            "local_entity_id": invoice_id,
            "sync_status": SYNC_STATUS_PENDING,
        }

    async def enqueue_credit_note_sync(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        force: bool = False,
        trigger_source: str = "quickbooks.manual_sync",
        correlation_id: str | None = None,
    ) -> dict:
        """Queue credit-note sync for one scope-bound local credit note.

        Returns queue metadata and transitions credit-note/link status to `QUEUED` when enqueue is accepted.
        """
        organization_id = self._require_organization_id(organization_id)
        credit_note = await self._session.get(CreditNote, credit_note_id)
        if credit_note is None:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        self._assert_model_belongs_to_org(
            model_name="credit_note",
            model_id=credit_note_id,
            model_org_id=getattr(credit_note, "organization_id", None),
            organization_id=organization_id,
        )

        await self._session.flush()
        if getattr(credit_note, "qb_sync_status", "NOT_SYNCED") == "QUEUED" and not force:
            return {
                "queued": False,
                "job_id": None,
                "entity_type": QB_ENTITY_CREDIT_NOTE,
                "local_entity_id": credit_note_id,
                "sync_status": SYNC_STATUS_PENDING,
            }
        await self._enforce_org_queue_quota(organization_id=organization_id, additional_jobs=1)
        if getattr(credit_note, "qb_sync_status", "NOT_SYNCED") != "QUEUED":
            credit_note.qb_sync_status = "QUEUED"
        job_id = self._job_id("credit-note", organization_id, credit_note_id, version=credit_note.version, force=force)
        job = await self._queue_sync_job(
            Job.SYNC_QB_CREDIT_NOTE,
            organization_id=organization_id,
            entity_type=QB_ENTITY_CREDIT_NOTE,
            local_entity_id=credit_note_id,
            event_type="CREDIT_NOTE_QUEUED",
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            queue_kwargs={
                "organization_id": organization_id,
                "credit_note_id": credit_note_id,
                "force": force,
            },
        )
        return {
            "queued": True,
            "job_id": job.job_id if job else None,
            "entity_type": QB_ENTITY_CREDIT_NOTE,
            "local_entity_id": credit_note_id,
            "sync_status": SYNC_STATUS_PENDING,
        }

    async def enqueue_payment_sync(
        self,
        *,
        organization_id: str,
        payment_id: str,
        force: bool = False,
        trigger_source: str = "quickbooks.manual_sync",
        correlation_id: str | None = None,
    ) -> dict:
        """Queue payment sync for one scope-bound local billing payment."""
        organization_id = self._require_organization_id(organization_id)
        payment = await self._session.get(BillingPayment, payment_id)
        if payment is None:
            raise NotFoundError(resource="billing_payment", id=payment_id)
        self._assert_model_belongs_to_org(
            model_name="billing_payment",
            model_id=payment_id,
            model_org_id=getattr(payment, "organization_id", None),
            organization_id=organization_id,
        )

        if payment.qb_sync_status == "QUEUED" and not force:
            return {
                "queued": False,
                "job_id": None,
                "entity_type": QB_ENTITY_PAYMENT,
                "local_entity_id": payment_id,
                "sync_status": SYNC_STATUS_PENDING,
            }
        await self._enforce_org_queue_quota(organization_id=organization_id, additional_jobs=1)
        if payment.qb_sync_status != "QUEUED":
            payment.qb_sync_status = "QUEUED"

        job_id = self._job_id("payment", organization_id, payment_id, version=payment.version, force=force)
        job = await self._queue_sync_job(
            Job.SYNC_QB_PAYMENT,
            organization_id=organization_id,
            entity_type=QB_ENTITY_PAYMENT,
            local_entity_id=payment_id,
            event_type="PAYMENT_QUEUED",
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            queue_kwargs={
                "organization_id": organization_id,
                "payment_id": payment_id,
                "force": force,
            },
        )
        return {
            "queued": True,
            "job_id": job.job_id if job else None,
            "entity_type": QB_ENTITY_PAYMENT,
            "local_entity_id": payment_id,
            "sync_status": SYNC_STATUS_PENDING,
        }

    async def sync_customer_now(self, *, organization_id: str, customer_id: str, force: bool = False, job_id: str | None = None, attempt_no: int = 1) -> None:
        """Create/update a QBO Customer for a local user and persist linkage.

        Inputs include org/customer ids plus optional retry metadata (`job_id`, `attempt_no`).
        Side effects: writes/updates `qb_links` and `qb_sync_logs`; raises on validation/API failures.
        """
        organization_id = self._require_organization_id(organization_id)
        user = await self._session.get(User, customer_id)
        if user is None:
            raise NotFoundError(resource="user", id=customer_id)
        self._assert_model_belongs_to_org(
            model_name="user",
            model_id=customer_id,
            model_org_id=getattr(user, "organization_id", None),
            organization_id=organization_id,
        )

        client = QuickBooksClient(self._conn_repo, organization_id)
        link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CUSTOMER, customer_id)
        if link is not None and str(link.qb_entity_id).startswith("pending:"):
            link = None
        # QBO requires unique DisplayName; using email avoids duplicate-name conflicts across repeated sandbox runs.
        display_name = user.email
        organization = await self._get_organization(user.organization_id)
        customer_operation = "update" if link and not force else "create"
        customer_action = ACTION_UPDATED if customer_operation == "update" else ACTION_CREATED
        customer_event = "CUSTOMER_UPDATED" if customer_operation == "update" else "CUSTOMER_CREATED"

        try:
            payload = self._build_qb_customer_payload(
                user=user,
                display_name=display_name,
                organization=organization,
                existing_link=link,
                sparse_update=bool(link and not force),
            )
            if link and not force:
                response = await client.update_customer(payload)
                customer = response.get("Customer", {})
            else:
                existing_customer = None
                lookup_existing = getattr(client, "find_customer_by_email", None)
                if callable(lookup_existing):
                    maybe_result = lookup_existing(user.email)
                    existing_customer = await maybe_result if inspect.isawaitable(maybe_result) else None
                if existing_customer is not None:
                    payload["Id"] = str(existing_customer.get("Id") or "")
                    payload["SyncToken"] = str(existing_customer.get("SyncToken") or "0")
                    payload["sparse"] = True
                    response = await client.update_customer(payload)
                    customer = response.get("Customer", {})
                else:
                    response = await client.create_customer(payload)
                    customer = response.get("Customer", {})

            qb_customer_id = str(customer.get("Id") or "")
            if not qb_customer_id:
                raise AppError("QuickBooks customer response did not include an Id")
            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CUSTOMER,
                local_entity_id=customer_id,
                qb_entity_id=qb_customer_id,
                sync_token=str(customer.get("SyncToken") or "0"),
                sync_status="SYNCED",
                last_error=None,
            )
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CUSTOMER,
                local_entity_id=customer_id,
                event_type=customer_event,
                action=customer_action,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=qb_customer_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            await self._link_repo.mark_failed(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CUSTOMER,
                local_entity_id=customer_id,
                error_message=str(exc),
            )
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CUSTOMER,
                local_entity_id=customer_id,
                event_type=customer_event,
                action=customer_action,
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=link.qb_entity_id if link is not None else None,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def sync_invoice_now(self, *, organization_id: str, invoice_id: str, force: bool = False, job_id: str | None = None, attempt_no: int = 1) -> None:
        """Create/update a QBO Invoice for one local invoice and apply linked credits.

        Builds payload from local invoice/line/mapping data, uses fingerprint no-op detection, then records
        sync outcomes in links/logs. Raises if prerequisites or QBO operations fail.
        """
        organization_id = self._require_organization_id(organization_id)
        invoice = await self._session.get(Invoice, invoice_id)
        if invoice is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        self._assert_model_belongs_to_org(
            model_name="invoice",
            model_id=invoice_id,
            model_org_id=getattr(invoice, "organization_id", None),
            organization_id=organization_id,
        )
        if invoice.status != "SENT":
            raise ValidationError("Only SENT invoices can be synced to QuickBooks")
        if not invoice.customer_id:
            raise ValidationError("Invoice must have customer_id before QuickBooks sync")

        if getattr(invoice, "payment_status", None) in {PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value}:
            await self._void_invoice_in_quickbooks(
                organization_id=organization_id,
                invoice_id=invoice_id,
                invoice=invoice,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            return

        await self.sync_customer_now(
            organization_id=organization_id,
            customer_id=invoice.customer_id,
            force=force,
            job_id=job_id,
            attempt_no=attempt_no,
        )
        customer_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CUSTOMER, invoice.customer_id)
        if customer_link is None:
            raise AppError("Customer mapping was not available after sync")

        client = QuickBooksClient(self._conn_repo, organization_id)
        invoice_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_INVOICE, invoice_id)
        settings = await self._settings_repo.get_or_create_default(organization_id)
        invoice_line_items = await self._get_invoice_line_items(invoice_id)
        order = await self._get_order(invoice.order_id)
        organization = await self._get_organization(invoice.organization_id)
        customer = await self._session.get(User, invoice.customer_id) if invoice.customer_id else None
        pickup_address = await self._get_address(getattr(order, "pickup_address_id", None))

        payload = {
            "DocNumber": invoice.invoice_number,
            "TxnDate": invoice.issue_date.isoformat(),
            "DueDate": invoice.due_date.isoformat(),
            "CustomerRef": {"value": customer_link.qb_entity_id},
            "PrivateNote": self._compose_invoice_private_note(invoice.notes, order.order_id if order else None),
            "Line": await self._build_qb_invoice_lines(
                organization_id=organization_id,
                invoice_total=invoice.total,
                line_items=invoice_line_items,
                vat_rate=invoice.vat_rate,
                strict=settings.strict_mapping_mode,
            ),
        }
        if getattr(invoice, "currency", None):
            payload["CurrencyRef"] = {"value": str(invoice.currency)}
        term_ref = await self._resolve_mapping_ref(
            organization_id=organization_id,
            mapping_type=QB_MAPPING_TERM,
            local_key="default",
            strict=False,
        )
        if term_ref is not None:
            payload["SalesTermRef"] = {"value": term_ref}
        billing_email = (getattr(invoice, "billing_contact_email", None) or "").strip() or None
        bill_email = {"Address": billing_email} if billing_email else self._build_qb_customer_bill_email(customer)
        if bill_email is not None:
            payload["BillEmail"] = bill_email
        bill_addr = self._build_qb_bill_addr(organization)
        if bill_addr is not None:
            payload["BillAddr"] = bill_addr
        ship_addr = self._build_qb_ship_addr(pickup_address)
        if ship_addr is not None:
            payload["ShipAddr"] = ship_addr
        customer_memo = self._build_qb_customer_memo(order)
        if customer_memo is not None:
            payload["CustomerMemo"] = customer_memo
        payload_fingerprint = self._payload_fingerprint(payload)
        if invoice_link and not force and invoice.qb_payload_fingerprint == payload_fingerprint:
            await self._sync_invoice_credit_applications(
                organization_id=organization_id,
                invoice=invoice,
                qb_invoice_id=invoice_link.qb_entity_id,
                qb_customer_id=customer_link.qb_entity_id,
                force=force,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            invoice.qb_sync_status = "SYNCED"
            invoice.qb_last_sync_at = datetime.now(UTC)
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type="INVOICE_NO_CHANGE",
                action=ACTION_NO_CHANGE,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=invoice_link.qb_entity_id,
                payload={"reason": "payload_unchanged"},
            )
            return
        invoice_operation = "update" if invoice_link and not force else "create"
        invoice_action = ACTION_UPDATED if invoice_operation == "update" else ACTION_CREATED
        invoice_event = "INVOICE_UPDATED" if invoice_operation == "update" else "INVOICE_CREATED"
        try:
            if invoice_link and not force:
                payload["Id"] = invoice_link.qb_entity_id
                payload["SyncToken"] = invoice_link.sync_token or "0"
                payload["sparse"] = True
                response = await client.update_invoice(payload)
                remote_invoice = response.get("Invoice", {})
            else:
                response = await client.create_invoice(payload)
                remote_invoice = response.get("Invoice", {})

            qb_invoice_id = str(remote_invoice.get("Id") or "")
            if not qb_invoice_id:
                raise AppError("QuickBooks invoice response did not include an Id")

            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                qb_entity_id=qb_invoice_id,
                sync_token=str(remote_invoice.get("SyncToken") or "0"),
                sync_status="SYNCED",
                last_error=None,
            )
            await self._sync_invoice_credit_applications(
                organization_id=organization_id,
                invoice=invoice,
                qb_invoice_id=qb_invoice_id,
                qb_customer_id=customer_link.qb_entity_id,
                force=force,
                job_id=job_id,
                attempt_no=attempt_no,
            )
            invoice.qb_sync_status = "SYNCED"
            invoice.qb_last_sync_at = datetime.now(UTC)
            invoice.qb_payload_fingerprint = payload_fingerprint
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type=invoice_event,
                action=invoice_action,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=qb_invoice_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            invoice.qb_sync_status = "FAILED"
            await self._link_repo.mark_failed(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                error_message=str(exc),
            )
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type=invoice_event,
                action=invoice_action,
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=invoice_link.qb_entity_id if invoice_link is not None else None,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def _get_invoice_line_items(self, invoice_id: str) -> list[InvoiceLineItem]:
        execute = getattr(self._session, "execute", None)
        if execute is None:
            return []
        result = await execute(select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice_id).order_by(InvoiceLineItem.created_at.asc()))
        return list(result.scalars().all())

    async def _get_order(self, order_id: str | None) -> Order | None:
        if not order_id:
            return None
        return await self._session.get(Order, order_id)

    async def _get_address(self, address_id: str | None) -> Address | None:
        if not address_id:
            return None
        return await self._session.get(Address, address_id)

    async def _get_organization(self, organization_id: str | None) -> Organization | None:
        if not organization_id:
            return None
        return await self._session.get(Organization, organization_id)

    def _assert_model_belongs_to_org(
        self,
        *,
        model_name: str,
        model_id: str,
        model_org_id: str | None,
        organization_id: str,
    ) -> None:
        _ = (model_name, model_id, model_org_id, organization_id)
        # Global singleton mode intentionally allows cross-organization sync entities.
        return None

    async def _assert_user_belongs_to_org(self, *, user_id: str, organization_id: str) -> None:
        _ = organization_id
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError(resource="user", id=user_id)

    @staticmethod
    def _compose_invoice_private_note(notes: str | None, order_reference: str | None) -> str:
        parts: list[str] = []
        if order_reference:
            parts.append(f"Order ID: #{order_reference}")
        if notes:
            parts.append(notes)
        return " | ".join(parts)

    async def _build_qb_invoice_lines(
        self,
        *,
        organization_id: str,
        invoice_total: Decimal,
        line_items: list[InvoiceLineItem],
        vat_rate: Decimal,
        strict: bool,
    ) -> list[dict]:
        tax_ref = await self._resolve_mapping_ref(
            organization_id=organization_id,
            mapping_type=QB_MAPPING_TAX_CODE,
            local_key=f"vat:{str(vat_rate)}",
            strict=strict,
        )
        if line_items:
            lines: list[dict] = []
            for item in line_items:
                qty = float(item.quantity or 1)
                unit_price = float(item.unit_price or 0)
                amount = float(item.total_price or 0)
                line_payload = {
                    "Description": item.description,
                    "Amount": amount,
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": {
                        "Qty": qty,
                        "UnitPrice": unit_price,
                    },
                }
                item_ref = await self._resolve_mapping_ref(
                    organization_id=organization_id,
                    mapping_type=QB_MAPPING_ITEM,
                    local_key=getattr(item, "line_type", "service"),
                    strict=strict,
                )
                if item_ref is not None:
                    line_payload["SalesItemLineDetail"]["ItemRef"] = {"value": item_ref}
                if tax_ref is not None:
                    line_payload["SalesItemLineDetail"]["TaxCodeRef"] = {"value": tax_ref}
                lines.append(
                    line_payload
                )
            return lines
        fallback_line = {
            "Amount": float(invoice_total),
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "Qty": 1,
                "UnitPrice": float(invoice_total),
            },
        }
        if tax_ref is not None:
            fallback_line["SalesItemLineDetail"]["TaxCodeRef"] = {"value": tax_ref}
        return [fallback_line]

    @staticmethod
    def _build_qb_customer_bill_email(customer: User | None) -> dict | None:
        if customer is None or not customer.email:
            return None
        return {"Address": customer.email}

    @staticmethod
    def _build_qb_bill_addr(organization: Organization | None) -> dict | None:
        if organization is None:
            return None
        line1 = (organization.reg_address_line_1 or "").strip()
        city = (organization.reg_city or "").strip()
        postcode = (organization.reg_postcode or "").strip()
        if not line1:
            return None
        payload: dict[str, str] = {"Line1": line1}
        legal_name = (organization.legal_entity_name or "").strip()
        if legal_name:
            payload["Line2"] = legal_name
        line2 = (organization.reg_address_line_2 or "").strip()
        if line2:
            payload["Line3"] = line2
        if city:
            payload["City"] = city
        state = (organization.reg_state or "").strip()
        if state:
            payload["CountrySubDivisionCode"] = state
        if postcode:
            payload["PostalCode"] = postcode
        country = (organization.reg_country or "").strip()
        if country:
            payload["Country"] = country
        return payload

    @staticmethod
    def _build_qb_ship_addr(address: Address | None) -> dict | None:
        if address is None:
            return None
        line1 = (address.line_1 or "").strip()
        if not line1:
            return None
        payload: dict[str, str] = {"Line1": line1}
        line2 = (address.line_2 or "").strip()
        if line2:
            payload["Line2"] = line2
        city = (address.city or "").strip()
        if city:
            payload["City"] = city
        subdivision = (getattr(address, "state", None) or getattr(address, "county", None) or "").strip()
        if subdivision:
            payload["CountrySubDivisionCode"] = subdivision
        postcode = (address.postcode or "").strip()
        if postcode:
            payload["PostalCode"] = postcode
        country = (address.country or "").strip()
        if country:
            payload["Country"] = country
        return payload

    @staticmethod
    def _build_qb_customer_memo(order: Order | None) -> dict | None:
        if order is None or not order.order_id:
            return None
        return {"value": f"Order ID: #{order.order_id}"[:1000]}

    @staticmethod
    def _build_qb_credit_note_customer_memo(reason: str | None) -> dict | None:
        cleaned = (reason or "").strip()
        if not cleaned:
            return None
        return {"value": cleaned[:1000]}

    def _build_qb_customer_payload(
        self,
        *,
        user: User,
        display_name: str,
        organization: Organization | None,
        existing_link: QbLink | None,
        sparse_update: bool,
    ) -> dict:
        payload: dict[str, object] = {
            "DisplayName": display_name,
            "PrimaryEmailAddr": {"Address": user.email},
            "GivenName": user.first_name,
            "FamilyName": user.last_name,
        }
        title_value = str(getattr(user, "title", "") or "").strip()
        if title_value:
            payload["Title"] = title_value[:15]
        position_role = str(getattr(user, "position_role", "") or "").strip()
        if position_role:
            payload["Job"] = position_role[:100]
        phone = str(getattr(user, "phone", "") or "").strip()
        if phone:
            payload["PrimaryPhone"] = {"FreeFormNumber": phone[:30]}
        notes = str(getattr(user, "notes", "") or "").strip()
        if notes:
            payload["Notes"] = notes[:4000]
        company_name = ""
        if organization is not None:
            company_name = (organization.trading_name or organization.legal_entity_name or "").strip()
        if company_name:
            payload["CompanyName"] = company_name[:100]
            bill_addr = self._build_qb_bill_addr(organization)
            if bill_addr is not None:
                payload["BillAddr"] = bill_addr
        if sparse_update and existing_link is not None:
            payload["Id"] = existing_link.qb_entity_id
            payload["SyncToken"] = existing_link.sync_token or "0"
            payload["sparse"] = True
        return payload

    async def _sync_invoice_credit_applications(
        self,
        *,
        organization_id: str,
        invoice: Invoice,
        qb_invoice_id: str,
        qb_customer_id: str,
        force: bool,
        job_id: str | None,
        attempt_no: int,
    ) -> None:
        """Sync local invoice-credit applications as QBO Payment link transactions.

        Inputs: synced invoice context plus org/job metadata. Side effects: upserts credit-application links
        and emits per-application success/failure logs with related QBO ids when available.
        """
        applications = await self._credit_app_repo.list_for_invoice(invoice.id)
        if not applications:
            return

        client = QuickBooksClient(self._conn_repo, organization_id)
        for app in applications:
            local_application_id = str(app.id)
            existing_link = None
            try:
                existing_link = await self._link_repo.get_by_local(
                    organization_id,
                    QB_ENTITY_CREDIT_APPLICATION,
                    local_application_id,
                )
                if existing_link and not force:
                    continue

                applied_amount = float(app.applied_amount or 0)
                if applied_amount <= 0:
                    continue

                credit_note_link = await self._link_repo.get_by_local(
                    organization_id,
                    QB_ENTITY_CREDIT_NOTE,
                    app.credit_note_id,
                )
                if credit_note_link is None:
                    await self.sync_credit_note_now(
                        organization_id=organization_id,
                        credit_note_id=app.credit_note_id,
                        force=force,
                        job_id=job_id,
                        attempt_no=attempt_no,
                    )
                    credit_note_link = await self._link_repo.get_by_local(
                        organization_id,
                        QB_ENTITY_CREDIT_NOTE,
                        app.credit_note_id,
                    )
                    if credit_note_link is None:
                        raise AppError("Credit note mapping was not available after sync")

                payment_payload = {
                    "CustomerRef": {"value": qb_customer_id},
                    "TotalAmt": applied_amount,
                    "PrivateNote": f"Apply credit note {app.credit_note_id} to invoice {invoice.id}",
                    "Line": [
                        {
                            "Amount": applied_amount,
                            "LinkedTxn": [{"TxnId": qb_invoice_id, "TxnType": "Invoice"}],
                        },
                        {
                            "Amount": applied_amount,
                            "LinkedTxn": [{"TxnId": credit_note_link.qb_entity_id, "TxnType": "CreditMemo"}],
                        },
                    ],
                }
                response = await client.create_payment(payment_payload)
                payment = response.get("Payment", {})
                qb_payment_id = str(payment.get("Id") or "")
                if not qb_payment_id:
                    raise AppError("QuickBooks payment response did not include an Id")

                await self._link_repo.upsert_mapping(
                    organization_id=organization_id,
                    entity_type=QB_ENTITY_CREDIT_APPLICATION,
                    local_entity_id=local_application_id,
                    qb_entity_id=qb_payment_id,
                    sync_token=str(payment.get("SyncToken") or "0"),
                    sync_status="SYNCED",
                    last_error=None,
                )
                await self._log_sync(
                    organization_id=organization_id,
                    entity_type=QB_ENTITY_CREDIT_APPLICATION,
                    local_entity_id=local_application_id,
                    event_type="CREDIT_APPLICATION_APPLIED",
                    action=ACTION_CREDIT_APPLIED,
                    status="SYNCED",
                    job_id=job_id,
                    attempt_no=attempt_no,
                    related_qb_id=qb_payment_id,
                    payload={
                        "invoice_id": invoice.id,
                        "credit_note_id": app.credit_note_id,
                        "amount": applied_amount,
                        "qb_invoice_id": qb_invoice_id,
                        "qb_credit_memo_id": credit_note_link.qb_entity_id,
                        "qb_payment_id": qb_payment_id,
                    },
                )
            except Exception as exc:
                error_code, human_message, _retry = self.classify_sync_error(exc)
                await self._link_repo.mark_failed(
                    organization_id=organization_id,
                    entity_type=QB_ENTITY_CREDIT_APPLICATION,
                    local_entity_id=local_application_id,
                    error_message=str(exc),
                )
                log_row = await self._log_sync(
                    organization_id=organization_id,
                    entity_type=QB_ENTITY_CREDIT_APPLICATION,
                    local_entity_id=local_application_id,
                    event_type="CREDIT_APPLICATION_APPLIED",
                    action=ACTION_CREDIT_APPLIED,
                    status="FAILED",
                    job_id=job_id,
                    attempt_no=attempt_no,
                    error_code=error_code,
                    error_message=human_message[:500],
                    related_qb_id=existing_link.qb_entity_id if existing_link is not None else None,
                    payload={
                        "invoice_id": invoice.id,
                        "credit_note_id": app.credit_note_id,
                        "amount": float(app.applied_amount or 0),
                        "qb_invoice_id": qb_invoice_id,
                    },
                )
                await self._maybe_notify_qb_connection_broken(log_row)
                raise

    async def sync_credit_note_now(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        force: bool = False,
        job_id: str | None = None,
        attempt_no: int = 1,
    ) -> None:
        """Create/update a QBO CreditMemo for one local credit note.

        Ensures customer mapping exists, builds memo payload (amount/tax/item refs), and persists link/log state.
        Raises on ownership, validation, or QBO API errors.
        """
        organization_id = self._require_organization_id(organization_id)
        credit_note = await self._session.get(CreditNote, credit_note_id)
        if credit_note is None:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        self._assert_model_belongs_to_org(
            model_name="credit_note",
            model_id=credit_note_id,
            model_org_id=getattr(credit_note, "organization_id", None),
            organization_id=organization_id,
        )
        if credit_note.status != "ISSUED":
            raise ValidationError("Only ISSUED credit notes can be synced to QuickBooks")
        if not credit_note.customer_id:
            raise ValidationError("Credit note must have customer_id before QuickBooks sync")

        await self.sync_customer_now(
            organization_id=organization_id,
            customer_id=credit_note.customer_id,
            force=force,
            job_id=job_id,
            attempt_no=attempt_no,
        )
        customer_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CUSTOMER, credit_note.customer_id)
        if customer_link is None:
            raise AppError("Customer mapping was not available after sync")
        customer = await self._session.get(User, credit_note.customer_id)
        organization = await self._get_organization(credit_note.organization_id)

        client = QuickBooksClient(self._conn_repo, organization_id)
        settings = await self._settings_repo.get_or_create_default(organization_id)
        credit_note_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CREDIT_NOTE, credit_note_id)
        tax_ref = await self._resolve_mapping_ref(
            organization_id=organization_id,
            mapping_type=QB_MAPPING_TAX_CODE,
            local_key="vat:default",
            strict=False,
        )
        payload = {
            "DocNumber": credit_note.credit_note_number,
            "TxnDate": credit_note.issue_date.isoformat(),
            "CustomerRef": {"value": customer_link.qb_entity_id},
            "PrivateNote": credit_note.reason or "",
            "CurrencyRef": {"value": credit_note.currency},
            "Line": [
                {
                    "Description": (credit_note.reason or f"Credit note {credit_note.credit_note_number}")[:4000],
                    "Amount": float(credit_note.total_credit_amount),
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": {"Qty": 1},
                }
            ],
        }
        bill_email = self._build_qb_customer_bill_email(customer)
        if bill_email is not None:
            payload["BillEmail"] = bill_email
        bill_addr = self._build_qb_bill_addr(organization)
        if bill_addr is not None:
            payload["BillAddr"] = bill_addr
        customer_memo = self._build_qb_credit_note_customer_memo(credit_note.reason)
        if customer_memo is not None:
            payload["CustomerMemo"] = customer_memo
        default_item_ref = await self._resolve_mapping_ref(
            organization_id=organization_id,
            mapping_type=QB_MAPPING_ITEM,
            local_key="credit_note",
            strict=settings.strict_mapping_mode,
        )
        if default_item_ref is not None:
            payload["Line"][0]["SalesItemLineDetail"]["ItemRef"] = {"value": default_item_ref}
        if tax_ref is not None:
            payload["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"] = {"value": tax_ref}
        payload_fingerprint = self._payload_fingerprint(payload)
        if credit_note_link and not force and credit_note.qb_payload_fingerprint == payload_fingerprint:
            credit_note.qb_sync_status = "SYNCED"
            credit_note.qb_last_sync_at = datetime.now(UTC)
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type="CREDIT_NOTE_NO_CHANGE",
                action=ACTION_NO_CHANGE,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=credit_note_link.qb_entity_id,
                payload={"reason": "payload_unchanged"},
            )
            return
        credit_note_operation = "update" if credit_note_link and not force else "create"
        credit_note_action = ACTION_UPDATED if credit_note_operation == "update" else ACTION_CREATED
        credit_note_event = "CREDIT_NOTE_UPDATED" if credit_note_operation == "update" else "CREDIT_NOTE_CREATED"
        try:
            if credit_note_link and not force:
                payload["Id"] = credit_note_link.qb_entity_id
                payload["SyncToken"] = credit_note_link.sync_token or "0"
                payload["sparse"] = True
                response = await client.update_credit_memo(payload)
                remote_credit_note = response.get("CreditMemo", {})
            else:
                response = await client.create_credit_memo(payload)
                remote_credit_note = response.get("CreditMemo", {})

            qb_credit_note_id = str(remote_credit_note.get("Id") or "")
            if not qb_credit_note_id:
                raise AppError("QuickBooks credit memo response did not include an Id")

            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                qb_entity_id=qb_credit_note_id,
                sync_token=str(remote_credit_note.get("SyncToken") or "0"),
                sync_status="SYNCED",
                last_error=None,
            )
            credit_note.qb_sync_status = "SYNCED"
            credit_note.qb_last_sync_at = datetime.now(UTC)
            credit_note.qb_payload_fingerprint = payload_fingerprint
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type=credit_note_event,
                action=credit_note_action,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=qb_credit_note_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            credit_note.qb_sync_status = "FAILED"
            await self._link_repo.mark_failed(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                error_message=str(exc),
            )
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type=credit_note_event,
                action=credit_note_action,
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=credit_note_link.qb_entity_id if credit_note_link is not None else None,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def _void_invoice_in_quickbooks(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        invoice: Invoice,
        job_id: str | None,
        attempt_no: int,
    ) -> None:
        client = QuickBooksClient(self._conn_repo, organization_id)
        invoice_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_INVOICE, invoice_id)
        if invoice_link is None:
            invoice.qb_sync_status = "NOT_SYNCED"
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type="INVOICE_VOID_SKIPPED",
                action=ACTION_NO_CHANGE,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                payload={"reason": "no_qb_mapping"},
            )
            return
        try:
            await client.void_invoice(
                qb_invoice_id=invoice_link.qb_entity_id,
                sync_token=invoice_link.sync_token or "0",
            )
            invoice.qb_sync_status = "SYNCED"
            invoice.qb_last_sync_at = datetime.now(UTC)
            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                qb_entity_id=invoice_link.qb_entity_id,
                sync_token=str(int(invoice_link.sync_token or "0") + 1),
                sync_status="SYNCED",
                last_error=None,
            )
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type="INVOICE_VOIDED",
                action="VOIDED",
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=invoice_link.qb_entity_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            invoice.qb_sync_status = "FAILED"
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_INVOICE,
                local_entity_id=invoice_id,
                event_type="INVOICE_VOIDED",
                action="VOIDED",
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=invoice_link.qb_entity_id,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def void_credit_note_now(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        job_id: str | None = None,
        attempt_no: int = 1,
    ) -> None:
        """Void/delete QBO CreditMemo for a locally voided credit note."""
        organization_id = self._require_organization_id(organization_id)
        credit_note = await self._session.get(CreditNote, credit_note_id)
        if credit_note is None:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        self._assert_model_belongs_to_org(
            model_name="credit_note",
            model_id=credit_note_id,
            model_org_id=getattr(credit_note, "organization_id", None),
            organization_id=organization_id,
        )
        client = QuickBooksClient(self._conn_repo, organization_id)
        credit_note_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CREDIT_NOTE, credit_note_id)
        if credit_note_link is None:
            credit_note.qb_sync_status = "NOT_SYNCED"
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type="CREDIT_NOTE_VOID_SKIPPED",
                action=ACTION_NO_CHANGE,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                payload={"reason": "no_qb_mapping"},
            )
            return
        try:
            await client.void_credit_memo(
                qb_credit_memo_id=credit_note_link.qb_entity_id,
                sync_token=credit_note_link.sync_token or "0",
            )
            credit_note.qb_sync_status = "SYNCED"
            credit_note.qb_last_sync_at = datetime.now(UTC)
            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                qb_entity_id=credit_note_link.qb_entity_id,
                sync_token=str(int(credit_note_link.sync_token or "0") + 1),
                sync_status="SYNCED",
                last_error=None,
            )
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type="CREDIT_NOTE_VOIDED",
                action="VOIDED",
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=credit_note_link.qb_entity_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            credit_note.qb_sync_status = "FAILED"
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_CREDIT_NOTE,
                local_entity_id=credit_note_id,
                event_type="CREDIT_NOTE_VOIDED",
                action="VOIDED",
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=credit_note_link.qb_entity_id,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def enqueue_void_credit_note(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        version: int,
        trigger_source: str = "billing.void_credit_note",
        void_reason: str | None = None,
        credit_note_number: str | None = None,
    ) -> None:
        organization_id = self._require_organization_id(organization_id)
        credit_note = await self._session.get(CreditNote, credit_note_id)
        if credit_note is None:
            return
        credit_note.qb_sync_status = "QUEUED"
        correlation_id = correlation_id_for_void_credit_note(
            organization_id=organization_id,
            credit_note_id=credit_note_id,
            version=version,
        )
        job_id = f"qb:void-cn:{organization_id}:{credit_note_id}:{version}"
        await self._queue_sync_job(
            Job.VOID_QB_CREDIT_NOTE,
            organization_id=organization_id,
            entity_type=QB_ENTITY_CREDIT_NOTE,
            local_entity_id=credit_note_id,
            event_type=EVENT_CREDIT_NOTE_VOID_QUEUED,
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            business={
                "credit_note_id": credit_note_id,
                "credit_note_number": credit_note_number or getattr(credit_note, "credit_note_number", None),
                "void_reason": (void_reason or "")[:500] or None,
            },
            queue_kwargs={
                "organization_id": organization_id,
                "credit_note_id": credit_note_id,
            },
        )

    async def enqueue_void_credit_note_chain(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        reversal_invoice_id: str,
        affected_invoice_ids: list[str],
        version: int,
        trigger_source: str = "billing.void_credit_note",
        void_reason: str | None = None,
        credit_note_number: str | None = None,
        applied_total: str | None = None,
    ) -> str:
        """Queue void saga; returns correlation_id for support tooling."""
        organization_id = self._require_organization_id(organization_id)
        correlation_id = correlation_id_for_void_credit_note(
            organization_id=organization_id,
            credit_note_id=credit_note_id,
            version=version,
        )
        job_id = f"qb:void-cn-chain:{organization_id}:{credit_note_id}:{version}"
        await self._queue_sync_job(
            Job.VOID_QB_CREDIT_NOTE_CHAIN,
            organization_id=organization_id,
            entity_type=QB_ENTITY_CREDIT_NOTE,
            local_entity_id=credit_note_id,
            event_type=EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED,
            job_id=job_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            business={
                "credit_note_id": credit_note_id,
                "credit_note_number": credit_note_number,
                "reversal_invoice_id": reversal_invoice_id,
                "affected_invoice_ids": affected_invoice_ids,
                "void_reason": (void_reason or "")[:500] or None,
                "applied_total": applied_total,
            },
            queue_kwargs={
                "organization_id": organization_id,
                "credit_note_id": credit_note_id,
                "reversal_invoice_id": reversal_invoice_id,
                "affected_invoice_ids": affected_invoice_ids,
            },
        )
        return correlation_id

    async def log_void_chain_step(
        self,
        *,
        organization_id: str,
        credit_note_id: str,
        step: str,
        status: str,
        job_id: str | None = None,
        attempt_no: int = 1,
        error_code: str | None = None,
        error_message: str | None = None,
        business: dict | None = None,
    ) -> None:
        await self._log_sync(
            organization_id=organization_id,
            entity_type=QB_ENTITY_CREDIT_NOTE,
            local_entity_id=credit_note_id,
            event_type=EVENT_VOID_CHAIN_STEP,
            action=ACTION_QUEUED,
            status=status,
            job_id=job_id,
            attempt_no=attempt_no,
            step=step,
            error_code=error_code,
            error_message=error_message,
            trigger_source="billing.void_credit_note",
            business=business,
        )

    async def sync_payment_now(self, *, organization_id: str, payment_id: str, force: bool = False, job_id: str | None = None, attempt_no: int = 1) -> None:
        """Create/update a QBO Payment for one local billing payment."""
        organization_id = self._require_organization_id(organization_id)
        payment = await self._session.get(BillingPayment, payment_id)
        if payment is None:
            raise NotFoundError(resource="billing_payment", id=payment_id)
        self._assert_model_belongs_to_org(
            model_name="billing_payment",
            model_id=payment_id,
            model_org_id=getattr(payment, "organization_id", None),
            organization_id=organization_id,
        )

        if not payment.customer_id:
            raise ValidationError("Billing payment must have customer_id before QuickBooks sync")

        await self.sync_customer_now(
            organization_id=organization_id,
            customer_id=payment.customer_id,
            force=force,
            job_id=job_id,
            attempt_no=attempt_no,
        )
        customer_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_CUSTOMER, payment.customer_id)
        if customer_link is None:
            raise AppError("Customer mapping was not available after sync")

        allocations = await self._billing_alloc_repo.latest_for_payment(payment.id)
        linked_txns: list[dict[str, str]] = []
        for alloc in allocations:
            if alloc.allocated_amount <= 0:
                continue
            invoice_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_INVOICE, alloc.invoice_id)
            if invoice_link is None:
                await self.sync_invoice_now(
                    organization_id=organization_id,
                    invoice_id=alloc.invoice_id,
                    force=force,
                    job_id=job_id,
                    attempt_no=attempt_no,
                )
                invoice_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_INVOICE, alloc.invoice_id)
                if invoice_link is None:
                    raise AppError(f"Invoice mapping missing after sync for invoice_id={alloc.invoice_id}")
            linked_txns.append({"TxnId": invoice_link.qb_entity_id, "TxnType": "Invoice"})

        payment_link = await self._link_repo.get_by_local(organization_id, QB_ENTITY_PAYMENT, payment.id)
        if not linked_txns and payment_link is None:
            logger.info(
                "quickbooks.sync_payment_skipped",
                reason="no_positive_invoice_allocations_and_no_existing_qbo_payment",
                payment_id=payment.id,
                organization_id=organization_id,
            )
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                event_type=EVENT_PAYMENT_SYNC_SKIPPED,
                action=ACTION_NO_CHANGE,
                status=LOG_STATUS_SYNCED,
                job_id=job_id,
                attempt_no=attempt_no,
                trigger_source="quickbooks.sync_payment_now",
                payload={"reason": "no_positive_invoice_allocations_and_no_existing_qbo_payment"},
            )
            return

        allocated_total = sum((Decimal(str(alloc.allocated_amount)) for alloc in allocations if alloc.allocated_amount > 0), Decimal("0"))
        payload = {
            "CustomerRef": {"value": customer_link.qb_entity_id},
            "TotalAmt": float(allocated_total),
            "TxnDate": payment.payment_date.isoformat(),
            "PrivateNote": payment.notes or f"Payment {payment.payment_number}",
            "Line": [{"Amount": float(allocated_total), "LinkedTxn": linked_txns}] if linked_txns else [],
        }
        payload_fingerprint = self._payload_fingerprint(payload)

        if payment_link and not force and payment.qb_payload_fingerprint == payload_fingerprint:
            payment.qb_sync_status = "SYNCED"
            payment.qb_last_sync_at = datetime.now(UTC)
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                event_type="PAYMENT_NO_CHANGE",
                action=ACTION_NO_CHANGE,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=payment_link.qb_entity_id,
                payload={"reason": "payload_unchanged"},
            )
            return

        client = QuickBooksClient(self._conn_repo, organization_id)
        event_type = "PAYMENT_UPDATED" if payment_link else "PAYMENT_CREATED"
        action = ACTION_UPDATED if payment_link else ACTION_CREATED
        try:
            if payment_link is not None:
                update_payload = {
                    **payload,
                    "Id": payment_link.qb_entity_id,
                    "SyncToken": str(payment_link.sync_token or "0"),
                    "sparse": True,
                }
                response = await client.update_payment(update_payload)
            else:
                response = await client.create_payment(payload)
            remote_payment = response.get("Payment", {})

            qb_payment_id = str(remote_payment.get("Id") or "")
            if not qb_payment_id:
                raise AppError("QuickBooks payment response did not include an Id")

            await self._link_repo.upsert_mapping(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                qb_entity_id=qb_payment_id,
                sync_token=str(remote_payment.get("SyncToken") or "0"),
                sync_status="SYNCED",
                last_error=None,
            )
            payment.qb_sync_status = "SYNCED"
            payment.qb_last_sync_at = datetime.now(UTC)
            payment.qb_payload_fingerprint = payload_fingerprint
            await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                event_type=event_type,
                action=action,
                status="SYNCED",
                job_id=job_id,
                attempt_no=attempt_no,
                related_qb_id=qb_payment_id,
            )
        except Exception as exc:
            error_code, human_message, _retry = self.classify_sync_error(exc)
            payment.qb_sync_status = "FAILED"
            await self._link_repo.mark_failed(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                error_message=str(exc),
            )
            log_row = await self._log_sync(
                organization_id=organization_id,
                entity_type=QB_ENTITY_PAYMENT,
                local_entity_id=payment.id,
                event_type=event_type,
                action=action,
                status="FAILED",
                job_id=job_id,
                attempt_no=attempt_no,
                error_code=error_code,
                error_message=human_message[:500],
                related_qb_id=payment_link.qb_entity_id if payment_link is not None else None,
            )
            await self._maybe_notify_qb_connection_broken(log_row)
            raise

    async def preflight_invoice_sync(self, *, organization_id: str, invoice_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        invoice = await self._session.get(Invoice, invoice_id)
        if invoice is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        self._assert_model_belongs_to_org(
            model_name="invoice",
            model_id=invoice_id,
            model_org_id=getattr(invoice, "organization_id", None),
            organization_id=organization_id,
        )
        if invoice.status != "SENT":
            raise ValidationError("Only SENT invoices can be synced to QuickBooks")
        if not invoice.customer_id:
            raise ValidationError("Invoice must have customer_id before QuickBooks sync")
        failures: list[str] = []
        warnings: list[str] = []
        settings = await self._settings_repo.get_or_create_default(organization_id)
        items = await self._get_invoice_line_items(invoice.id)
        if settings.strict_mapping_mode:
            if not items:
                warnings.append("Invoice has no line items; aggregate fallback line will be used")
            for item in items:
                ref = await self._mapping_repo.get_mapping(organization_id, QB_MAPPING_ITEM, item.line_type)
                if ref is None or not ref.is_active:
                    failures.append(f"Missing ITEM mapping for line_type='{item.line_type}'")
                tax_ref = await self._mapping_repo.get_mapping(organization_id, QB_MAPPING_TAX_CODE, f"vat:{str(invoice.vat_rate)}")
                if tax_ref is None or not tax_ref.is_active:
                    failures.append(f"Missing TAX_CODE mapping for vat:{str(invoice.vat_rate)}")
        return {
            "invoice_id": invoice_id,
            "valid": len(failures) == 0,
            "failures": failures,
            "warnings": warnings,
        }

    async def enqueue_resync(
        self,
        *,
        organization_id: str,
        entity_type: str,
        local_entity_id: str,
        force: bool = False,
    ) -> dict:
        """Dispatch resync enqueue for a supported entity type.

        Input is generic (`entity_type`, `local_entity_id`) and output is the same queue-result dict shape as
        direct enqueue methods.
        """
        normalized = entity_type.lower().strip()
        if normalized == QB_ENTITY_CUSTOMER:
            return await self.enqueue_customer_sync(organization_id=organization_id, customer_id=local_entity_id, force=force)
        if normalized == QB_ENTITY_INVOICE:
            return await self.enqueue_invoice_sync(organization_id=organization_id, invoice_id=local_entity_id, force=force)
        if normalized == QB_ENTITY_CREDIT_NOTE:
            return await self.enqueue_credit_note_sync(organization_id=organization_id, credit_note_id=local_entity_id, force=force)
        if normalized == QB_ENTITY_PAYMENT:
            return await self.enqueue_payment_sync(organization_id=organization_id, payment_id=local_entity_id, force=force)
        raise ValidationError("Unsupported entity_type for resync")

    async def get_sync_health(self, *, organization_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        now = datetime.now(UTC)
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        failed_24h_stmt = select(func.count(QbSyncLog.id)).where(
            QbSyncLog.organization_id == organization_id,
            QbSyncLog.status == "FAILED",
            QbSyncLog.created_at >= day_ago,
        )
        failed_7d_stmt = select(func.count(QbSyncLog.id)).where(
            QbSyncLog.organization_id == organization_id,
            QbSyncLog.status == "FAILED",
            QbSyncLog.created_at >= week_ago,
        )
        pending_stmt = select(func.count(QbLink.id)).where(
            QbLink.organization_id == organization_id,
            QbLink.qb_entity_id.like("pending:%"),
        )
        last_failure_stmt = (
            select(QbSyncLog.created_at)
            .where(QbSyncLog.organization_id == organization_id, QbSyncLog.status == "FAILED")
            .order_by(QbSyncLog.created_at.desc())
            .limit(1)
        )
        failed_24h = int((await self._session.execute(failed_24h_stmt)).scalar_one() or 0)
        failed_7d = int((await self._session.execute(failed_7d_stmt)).scalar_one() or 0)
        pending_links = int((await self._session.execute(pending_stmt)).scalar_one() or 0)
        last_failure_at = (await self._session.execute(last_failure_stmt)).scalar_one_or_none()
        return {
            "failed_last_24h": failed_24h,
            "failed_last_7d": failed_7d,
            "pending_links": pending_links,
            "last_failure_at": last_failure_at,
        }

    async def reconcile(self, *, organization_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        missing_invoice_links_stmt = select(func.count(Invoice.id)).where(
            Invoice.status == "SENT",
            ~Invoice.id.in_(
                select(QbLink.local_entity_id).where(
                    QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
                    QbLink.entity_type == QB_ENTITY_INVOICE,
                )
            ),
        )
        missing_credit_links_stmt = select(func.count(CreditNote.id)).where(
            CreditNote.status == "ISSUED",
            ~CreditNote.id.in_(
                select(QbLink.local_entity_id).where(
                    QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
                    QbLink.entity_type == QB_ENTITY_CREDIT_NOTE,
                )
            ),
        )
        failed_invoice_links_stmt = select(func.count(QbLink.id)).where(
            QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
            QbLink.entity_type == QB_ENTITY_INVOICE,
            QbLink.sync_status == "FAILED",
        )
        failed_credit_links_stmt = select(func.count(QbLink.id)).where(
            QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
            QbLink.entity_type == QB_ENTITY_CREDIT_NOTE,
            QbLink.sync_status == "FAILED",
        )
        failed_credit_apply_links_stmt = select(func.count(QbLink.id)).where(
            QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
            QbLink.entity_type == QB_ENTITY_CREDIT_APPLICATION,
            QbLink.sync_status == "FAILED",
        )
        return {
            "missing_invoice_links": int((await self._session.execute(missing_invoice_links_stmt)).scalar_one() or 0),
            "missing_credit_note_links": int((await self._session.execute(missing_credit_links_stmt)).scalar_one() or 0),
            "failed_invoice_links": int((await self._session.execute(failed_invoice_links_stmt)).scalar_one() or 0),
            "failed_credit_note_links": int((await self._session.execute(failed_credit_links_stmt)).scalar_one() or 0),
            "failed_credit_application_links": int((await self._session.execute(failed_credit_apply_links_stmt)).scalar_one() or 0),
        }

    @staticmethod
    def _payload_fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _resolve_mapping_ref(
        self,
        *,
        organization_id: str,
        mapping_type: str,
        local_key: str,
        strict: bool,
    ) -> str | None:
        ref = await self._mapping_repo.get_mapping(organization_id, mapping_type, local_key)
        if ref and ref.is_active:
            return ref.qb_ref_id
        if strict:
            raise ValidationError(f"Missing active {mapping_type} mapping for '{local_key}'")
        return None

    async def list_failed_syncs(self, *, organization_id: str, limit: int = 50) -> list[dict]:
        organization_id = self._require_organization_id(organization_id)
        rows = await self._sync_log_repo.list_recent_failures(organization_id, limit=limit)
        return [
            {
                "id": row.id,
                "entity_type": row.entity_type,
                "event_type": row.event_type,
                "local_entity_id": row.local_entity_id,
                "action": row.action,
                "status": row.status,
                "attempt_no": row.attempt_no,
                "job_id": row.job_id,
                "error_code": row.error_code,
                "error_message": row.error_message,
                "related_qb_id": row.related_qb_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def list_logs(
        self,
        *,
        organization_id: str,
        statuses: list[str] | None = None,
        entity_type: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        error_code: str | None = None,
        job_id: str | None = None,
        local_entity_id: str | None = None,
        search: str | None = None,
        period: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
    ) -> list[dict]:
        from app.integrations.quickbooks.log_date_range import resolve_qb_log_created_at_bounds
        from app.modules.orders.enums import SummaryPeriodPreset

        organization_id = self._require_organization_id(organization_id)
        effective_statuses = [s.upper().strip() for s in (statuses or []) if str(s).strip()]
        period_enum = SummaryPeriodPreset(period) if period else None
        created_from, created_to_exclusive = resolve_qb_log_created_at_bounds(
            period=period_enum,
            date_from=date_from,
            date_to=date_to,
            today=date.today(),
        )
        rows = await self._sync_log_repo.list_logs(
            organization_id=organization_id,
            statuses=effective_statuses or None,
            entity_type=entity_type,
            event_type=event_type,
            action=action,
            error_code=error_code,
            job_id=job_id,
            local_entity_id=local_entity_id,
            search=search,
            created_from=created_from,
            created_to_exclusive=created_to_exclusive,
            limit=limit,
        )
        return [
            {
                "id": row.id,
                "entity_type": row.entity_type,
                "event_type": row.event_type,
                "local_entity_id": row.local_entity_id,
                "action": row.action,
                "status": row.status,
                "attempt_no": row.attempt_no,
                "job_id": row.job_id,
                "error_code": row.error_code,
                "error_message": row.error_message,
                "related_qb_id": row.related_qb_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def get_log_detail(self, *, organization_id: str, log_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        row = await self._sync_log_repo.get_log(organization_id=organization_id, log_id=log_id)
        if row is None:
            raise NotFoundError(resource="qb_sync_log", id=log_id)
        return {
            "id": row.id,
            "entity_type": row.entity_type,
            "event_type": row.event_type,
            "local_entity_id": row.local_entity_id,
            "action": row.action,
            "status": row.status,
            "attempt_no": row.attempt_no,
            "job_id": row.job_id,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "related_qb_id": row.related_qb_id,
            "payload": row.payload,
            "created_at": row.created_at,
        }

    async def bulk_resync(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        statuses: Sequence[str] | None = None,
        entity_type: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        error_code: str | None = None,
        include_non_connection_failures: bool = False,
        force: bool = False,
        batch_size: int = 200,
        limit: int = 2000,
    ) -> dict:
        """Replay failed/pending sync logs into queue in scalable batches.

        Applies log filters (`status`/`statuses`, entity/action/event/error) and by default replays only
        `PENDING` + connection-related `FAILED` entries unless overridden. Returns replay summary with counts/items.
        """
        organization_id = self._require_organization_id(organization_id)
        effective_statuses = [s.upper().strip() for s in (statuses or []) if str(s).strip()]
        if not effective_statuses:
            effective_statuses = [status.upper().strip()] if status and status.strip() else ["FAILED", "PENDING"]
        rows = await self._sync_log_repo.list_logs(
            organization_id=organization_id,
            statuses=effective_statuses,
            entity_type=entity_type,
            event_type=event_type,
            action=action,
            error_code=error_code,
            limit=limit,
        )
        if not include_non_connection_failures:
            rows = [row for row in rows if row.status == "PENDING" or self._is_retryable_failure_log(row)]
        return await self._replay_resync_rows(
            rows=rows,
            organization_id=organization_id,
            force=force,
            batch_size=batch_size,
        )

    async def bulk_resync_final_failures(
        self,
        *,
        organization_id: str,
        entity_type: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        error_code: str | None = None,
        force: bool = False,
        batch_size: int = 200,
        limit: int = 2000,
    ) -> dict:
        """Replay only retry-exhausted FAILED logs into queue in scalable batches."""
        organization_id = self._require_organization_id(organization_id)
        rows = await self._sync_log_repo.list_logs(
            organization_id=organization_id,
            statuses=["FAILED"],
            entity_type=entity_type,
            event_type=event_type,
            action=action,
            error_code=error_code,
            limit=limit,
        )
        rows = [row for row in rows if int(getattr(row, "attempt_no", 0) or 0) >= _QB_WORKER_MAX_TRIES]
        return await self._replay_resync_rows(
            rows=rows,
            organization_id=organization_id,
            force=force,
            batch_size=batch_size,
        )

    async def _replay_resync_rows(
        self,
        *,
        rows: Sequence[QbSyncLog],
        organization_id: str,
        force: bool,
        batch_size: int,
    ) -> dict:
        requested = len(rows)
        queued = 0
        skipped = 0
        items: list[dict] = []
        for idx in range(0, len(rows), max(batch_size, 1)):
            batch = rows[idx : idx + batch_size]
            for row in batch:
                if not row.local_entity_id:
                    skipped += 1
                    continue
                try:
                    result = await self.enqueue_resync(
                        organization_id=organization_id,
                        entity_type=row.entity_type,
                        local_entity_id=row.local_entity_id,
                        force=force,
                    )
                    items.append(result)
                    if result.get("queued"):
                        queued += 1
                    else:
                        skipped += 1
                except (ValidationError, NotFoundError):
                    skipped += 1
        return {
            "requested": requested,
            "queued": queued,
            "skipped": skipped,
            "items": items,
        }

    @staticmethod
    def _is_connection_failure_log(row: QbSyncLog) -> bool:
        code = str(getattr(row, "error_code", "") or "").lower()
        if code == "authenticationerror":
            return True
        message = str(getattr(row, "error_message", "") or "").lower()
        return any(marker in message for marker in _CONNECTION_FAILURE_MARKERS)

    @staticmethod
    def _is_retryable_failure_log(row: QbSyncLog) -> bool:
        code = str(getattr(row, "error_code", "") or "").upper()
        if code.startswith("TRANSIENT_EXTERNAL"):
            return True
        return QuickBooksService._is_connection_failure_log(row)

    async def _should_send_connection_broken_alert(self, organization_id: str, ttl_seconds: int = 1800) -> bool:
        redis = get_redis()
        key = f"qb:connection_broken_alert:{organization_id}"
        existing = await redis.get(key)
        if existing:
            return False
        await redis.setex(key, ttl_seconds, "1")
        return True

    async def _build_qb_connection_broken_context(self, log_row: QbSyncLog, organization_id: str) -> dict:
        organization_id = self._require_organization_id(organization_id)
        conn = await self._conn_repo.find_one(organization_id=organization_id)
        connection_status = "revoked"
        if conn is not None and conn.is_active:
            connection_status = "active"
            if conn.access_token_expires_at <= datetime.now(UTC):
                connection_status = "expired"
            if conn.last_error and any(marker in str(conn.last_error).lower() for marker in ("invalid_grant", "revoked", "token")):
                connection_status = "revoked"
        last_synced_at, failed_24h = await self._status_sync_metrics(organization_id)
        return {
            "connection_status": connection_status,
            "realm_id": getattr(conn, "realm_id", None) if conn is not None else None,
            "connected_at": getattr(conn, "created_at", None) if conn is not None else None,
            "last_refreshed_at": getattr(conn, "last_refreshed_at", None) if conn is not None else None,
            "last_error_at": getattr(conn, "last_error_at", None) if conn is not None else None,
            "last_error": getattr(conn, "last_error", None) if conn is not None else None,
            "recent_failed_count": failed_24h,
            "last_failure_at": last_synced_at,
            "entity_type": log_row.entity_type,
            "local_entity_id": log_row.local_entity_id,
            "event_type": log_row.event_type,
            "action": log_row.action,
            "job_id": log_row.job_id,
            "attempt_no": log_row.attempt_no,
            "error_code": log_row.error_code,
            "error_message": log_row.error_message,
            "related_qb_id": log_row.related_qb_id,
            "created_at": log_row.created_at,
            "admin_qb_settings_url": "/admin/settings/integrations/quickbooks",
            "resync_hint": "Use Bulk Resync with status=FAILED and include_non_connection_failures=false.",
            "raw_error_detail": log_row.error_message,
        }

    async def _maybe_notify_qb_connection_broken(self, log_row: QbSyncLog) -> None:
        if getattr(log_row, "status", "").upper() != "FAILED":
            return
        if not self._is_connection_failure_log(log_row):
            return
        organization_id = str(getattr(log_row, "organization_id", "") or "").strip()
        if not organization_id:
            try:
                organization_id = self.resolve_swc_scope_id()
            except ValidationError:
                return
        if not await self._should_send_connection_broken_alert(organization_id):
            return
        conn = await self._conn_repo.find_one(organization_id=organization_id)
        user_id = getattr(conn, "connected_by_id", None) if conn is not None else None
        if not user_id:
            return
        context = await self._build_qb_connection_broken_context(log_row, organization_id)
        await notify(
            event=NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE,
            notification_type=NotificationType.ADMIN_INTERNAL,
            organization_id=organization_id,
            user_id=user_id,
            context=context,
        )

    @staticmethod
    def _job_id(entity: str, organization_id: str, local_entity_id: str, *, version: int | None = None, force: bool = False) -> str:
        parts = ["qb", entity, organization_id, local_entity_id]
        if version is not None:
            parts.append(str(version))
        if force:
            parts.extend(["force", uuid4().hex])
        return ":".join(parts)

    async def _status_sync_metrics(self, organization_id: str) -> tuple[datetime | None, int]:
        organization_id = self._require_organization_id(organization_id)
        execute = getattr(self._session, "execute", None)
        if execute is None:
            return None, 0
        day_ago = datetime.now(UTC) - timedelta(days=1)
        last_synced_stmt = (
            select(QbSyncLog.created_at)
            .where(QbSyncLog.organization_id == organization_id, QbSyncLog.status == "SYNCED")
            .order_by(QbSyncLog.created_at.desc())
            .limit(1)
        )
        failed_24h_stmt = select(func.count(QbSyncLog.id)).where(
            QbSyncLog.organization_id == organization_id,
            QbSyncLog.status == "FAILED",
            QbSyncLog.created_at >= day_ago,
        )
        last_synced_at = (await execute(last_synced_stmt)).scalar_one_or_none()
        failed_24h = int((await execute(failed_24h_stmt)).scalar_one() or 0)
        return last_synced_at, failed_24h

    async def _enforce_org_queue_quota(self, *, organization_id: str, additional_jobs: int = 1) -> None:
        """Guardrail against enqueue floods on expensive QuickBooks sync jobs."""
        max_pending = int(getattr(settings, "QUICKBOOKS_ORG_QUEUE_MAX_PENDING", 500) or 500)
        if max_pending <= 0:
            return
        current_pending = await self._count_org_pending_jobs(organization_id=organization_id)
        if current_pending + max(additional_jobs, 0) <= max_pending:
            return
        raise ValidationError(
            "QuickBooks sync queue is at capacity. "
            "Please wait for queued jobs to complete before enqueuing more."
        )

    async def _count_org_pending_jobs(self, *, organization_id: str) -> int:
        _ = organization_id
        pending_customer_links_stmt = select(func.count(QbLink.id)).where(
            QbLink.organization_id == QB_GLOBAL_NAMESPACE_ID,
            QbLink.entity_type == QB_ENTITY_CUSTOMER,
            QbLink.sync_status == "QUEUED",
        )
        pending_invoices_stmt = select(func.count(Invoice.id)).where(Invoice.qb_sync_status == "QUEUED")
        pending_credit_notes_stmt = select(func.count(CreditNote.id)).where(CreditNote.qb_sync_status == "QUEUED")
        pending_payments_stmt = select(func.count(BillingPayment.id)).where(BillingPayment.qb_sync_status == "QUEUED")
        pending_customer_links = int((await self._session.execute(pending_customer_links_stmt)).scalar_one() or 0)
        pending_invoices = int((await self._session.execute(pending_invoices_stmt)).scalar_one() or 0)
        pending_credit_notes = int((await self._session.execute(pending_credit_notes_stmt)).scalar_one() or 0)
        pending_payments = int((await self._session.execute(pending_payments_stmt)).scalar_one() or 0)
        return pending_customer_links + pending_invoices + pending_credit_notes + pending_payments
