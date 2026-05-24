"""Suspension-rules background tasks (Arq). Owned by suspension_rules module."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from app.common.enums import LogEvent
from app.core.database import get_async_session
from app.mailer.client import EmailTemplateName, send_email
from app.modules.suspension_rules.service import SuspensionRulesService

logger = structlog.get_logger()


async def run_daily_suspension_rules_task(ctx: dict, today: str | None = None) -> None:
    """Entry point for the daily suspension-rules evaluation job.

    This is scheduled as an Arq cron job on the LOW queue. It:
    - Opens an isolated DB session
    - Delegates the actual work to SuspensionRulesService.run_daily_suspension_job
    - Logs start and completion for observability
    """
    try:
        run_date = date.fromisoformat(today) if today else date.today()
    except ValueError:
        logger.warning(LogEvent.SUSPENSION_RULES_CRON_INVALID_DATE, today=today)
        run_date = date.today()

    logger.info(LogEvent.SUSPENSION_RULES_CRON_STARTED, today=str(run_date))

    async with get_async_session() as session:
        service = SuspensionRulesService(session, request=None)
        await service.run_daily_suspension_job(today=run_date, commit=True)

    logger.info(LogEvent.SUSPENSION_RULES_CRON_COMPLETED, today=str(run_date))


async def send_email_task(
    ctx: dict,
    to_address: str,
    subject: str,
    *,
    template_name: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Generic async email task used by suspension notifications."""
    await send_email(
        to_address,
        subject,
        template_name=EmailTemplateName(template_name) if template_name else None,
        context=context or {},
    )


# Exported for worker registration
tasks = [run_daily_suspension_rules_task, send_email_task]
