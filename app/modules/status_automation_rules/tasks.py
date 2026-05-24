"""Status automation background tasks (Arq)."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from app.common.enums import LogEvent
from app.core.database import get_async_session
from app.modules.status_automation_rules.service import StatusAutomationRulesService

logger = structlog.get_logger()


async def evaluate_status_automation_rules_task(ctx: dict, event_ctx: dict[str, Any]) -> None:
    """Evaluate status automation rules for one transition event."""
    async with get_async_session() as session:
        service = StatusAutomationRulesService(session, request=None)
        await service.evaluate_for_event(event_ctx, commit=True)


async def run_daily_status_automation_reconciliation_task(ctx: dict, today: str | None = None) -> None:
    """Daily reconciliation run for status automation runtime."""
    try:
        run_date = date.fromisoformat(today) if today else date.today()
    except ValueError:
        logger.warning(LogEvent.SUSPENSION_RULES_CRON_INVALID_DATE, today=today)
        run_date = date.today()
    logger.info("STATUS_AUTOMATION_RECONCILIATION_STARTED", today=str(run_date))
    async with get_async_session() as session:
        service = StatusAutomationRulesService(session, request=None)
        await service.run_daily_reconciliation(run_date=run_date, commit=True)
    logger.info("STATUS_AUTOMATION_RECONCILIATION_COMPLETED", today=str(run_date))


tasks = [evaluate_status_automation_rules_task, run_daily_status_automation_reconciliation_task]

