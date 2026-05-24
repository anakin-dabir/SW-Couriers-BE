from __future__ import annotations

from datetime import UTC, date, datetime

import structlog

from app.common.enums import LogEvent
from app.core.database import get_async_session
from app.modules.org_credit_settings.service import OrgCreditSettingsService

logger = structlog.get_logger()


async def apply_scheduled_credit_settings_task(ctx: dict, today: str | None = None) -> None:
    try:
        run_date = date.fromisoformat(today) if today else datetime.now(UTC).date()
    except ValueError:
        logger.warning(LogEvent.SUSPENSION_RULES_CRON_INVALID_DATE, today=today)
        run_date = datetime.now(UTC).date()

    async with get_async_session() as session:
        service = OrgCreditSettingsService(session, request=None)
        await service.apply_due_scheduled_credit_and_terms(run_date)


tasks = [apply_scheduled_credit_settings_task]
