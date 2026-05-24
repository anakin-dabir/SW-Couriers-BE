from __future__ import annotations

import asyncio

import app.models  # noqa: F401 — register all ORM models for relationship resolution
from app.common.enums import LogEvent

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


import structlog
from arq.cron import cron

from app.core.queue import QueuePriority
from app.core.redis import get_redis_settings
from app.integrations.quickbooks.tasks import (
    refresh_qb_connections_task,
    sync_qb_credit_note_task,
    sync_qb_customer_task,
    sync_qb_invoice_task,
    sync_qb_payment_task,
    void_qb_credit_note_chain_task,
    void_qb_credit_note_task,
)
from app.modules.auth.tasks import (
    cleanup_expired_tokens_task,
    send_driver_activation_email_task,
    send_password_reset_email_task,
    send_support_issued_password_email_task,
    send_verification_email_task,
)
from app.modules.account_statements.tasks import (
    deliver_scheduled_account_statement_task,
    generate_account_statement_pdf_task,
    run_account_statement_schedules_task,
)
from app.modules.invoices.tasks import generate_credit_note_pdf_task, generate_invoice_pdf_task
from app.modules.notifications.tasks import process_notification_task
from app.modules.org_credit_alerts.tasks import (
    auto_unsnooze_credit_alerts_task,
    evaluate_org_credit_alerts_task,
    send_credit_alert_email_task,
)
from app.modules.org_credit_settings.tasks import apply_scheduled_credit_settings_task
from app.modules.organizations.tasks import send_doc_otp_email_task, send_document_share_email_task, send_share_otp_email_task
from app.modules.status_automation_rules.tasks import (
    evaluate_status_automation_rules_task,
    run_daily_status_automation_reconciliation_task,
)
from app.modules.client_inactivity.tasks import run_daily_client_inactivity_task
from app.modules.suspension_rules.tasks import run_daily_suspension_rules_task, send_email_task
from app.modules.user.tasks import send_invite_email_task
from app.modules.vehicles.tasks import evaluate_vehicle_service_due_alerts_task

logger = structlog.get_logger()

_REDIS_SETTINGS = get_redis_settings()

HIGH_FUNCTIONS: list = [
    send_support_issued_password_email_task,
    send_password_reset_email_task,
    send_verification_email_task,
    send_driver_activation_email_task,
    send_doc_otp_email_task,
    send_share_otp_email_task,
]
DEFAULT_FUNCTIONS: list = [
    send_invite_email_task,
    send_document_share_email_task,
    sync_qb_customer_task,
    sync_qb_invoice_task,
    sync_qb_credit_note_task,
    sync_qb_payment_task,
    void_qb_credit_note_task,
    void_qb_credit_note_chain_task,
    evaluate_status_automation_rules_task,
]
NOTIFICATION_FUNCTIONS: list = [process_notification_task, send_credit_alert_email_task]
LOW_FUNCTIONS: list = [
    cleanup_expired_tokens_task,
    generate_invoice_pdf_task,
    generate_credit_note_pdf_task,
    generate_account_statement_pdf_task,
    deliver_scheduled_account_statement_task,
    run_account_statement_schedules_task,
    run_daily_suspension_rules_task,
    evaluate_vehicle_service_due_alerts_task,
    evaluate_org_credit_alerts_task,
    auto_unsnooze_credit_alerts_task,
    send_email_task,
    refresh_qb_connections_task,
    run_daily_status_automation_reconciliation_task,
    run_daily_client_inactivity_task,
]


async def _noop(ctx: dict) -> None:
    pass


def _with_fallback(funcs: list) -> list:
    return funcs if funcs else [_noop]


def _make_startup(queue_name: str):
    async def startup(ctx: dict) -> None:
        logger.info(LogEvent.ARQ_WORKER_STARTED, queue=queue_name)

    return startup


def _make_shutdown(queue_name: str):
    async def shutdown(ctx: dict) -> None:
        logger.info(LogEvent.ARQ_WORKER_STOPPED, queue=queue_name)

    return shutdown


class HighWorkerSettings:
    queue_name = QueuePriority.HIGH
    functions = _with_fallback(HIGH_FUNCTIONS)
    redis_settings = _REDIS_SETTINGS
    max_tries = 3
    job_timeout = 30

    on_startup = _make_startup(QueuePriority.HIGH)
    on_shutdown = _make_shutdown(QueuePriority.HIGH)


class DefaultWorkerSettings:
    queue_name = QueuePriority.DEFAULT
    functions = _with_fallback(DEFAULT_FUNCTIONS)
    redis_settings = _REDIS_SETTINGS
    max_tries = 3
    job_timeout = 120

    on_startup = _make_startup(QueuePriority.DEFAULT)
    on_shutdown = _make_shutdown(QueuePriority.DEFAULT)


class LowWorkerSettings:
    queue_name = QueuePriority.LOW
    functions = _with_fallback(LOW_FUNCTIONS)
    cron_jobs = [
        cron(cleanup_expired_tokens_task, hour=3, minute=0),
        cron(run_daily_suspension_rules_task, hour=2, minute=0),
        cron(run_daily_client_inactivity_task, hour=2, minute=15),
        cron(run_daily_status_automation_reconciliation_task, hour=1, minute=30),
        cron(apply_scheduled_credit_settings_task, hour=0, minute=5),
        cron(evaluate_vehicle_service_due_alerts_task, hour=6, minute=30),
        cron(evaluate_org_credit_alerts_task, minute={0, 15, 30, 45}),
        cron(auto_unsnooze_credit_alerts_task, minute={5, 20, 35, 50}),
        cron(refresh_qb_connections_task, minute={0, 15, 30, 45}),
        cron(run_account_statement_schedules_task, minute={10, 40}),
    ]
    redis_settings = _REDIS_SETTINGS
    max_tries = 3
    job_timeout = 300

    on_startup = _make_startup(QueuePriority.LOW)
    on_shutdown = _make_shutdown(QueuePriority.LOW)


class NotificationsWorkerSettings:
    queue_name = QueuePriority.NOTIFICATIONS
    functions = _with_fallback(NOTIFICATION_FUNCTIONS)
    redis_settings = _REDIS_SETTINGS
    max_tries = 1
    job_timeout = 120

    on_startup = _make_startup(QueuePriority.NOTIFICATIONS)
    on_shutdown = _make_shutdown(QueuePriority.NOTIFICATIONS)


WorkerSettings = DefaultWorkerSettings
