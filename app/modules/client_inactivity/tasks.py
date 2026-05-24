"""Client inactivity background tasks (Arq)."""

from __future__ import annotations

from datetime import date

import structlog

from app.common.enums.logger import LogEvent
from app.core.database import get_async_session
from app.modules.client_inactivity.service import ClientInactivityService

logger = structlog.get_logger()


async def run_daily_client_inactivity_task(ctx: dict, today: str | None = None) -> None:
    try:
        run_date = date.fromisoformat(today) if today else date.today()
    except ValueError:
        logger.warning(LogEvent.CLIENT_INACTIVITY_CRON_INVALID_DATE, today=today)
        run_date = date.today()

    logger.info(LogEvent.CLIENT_INACTIVITY_CRON_STARTED, today=str(run_date))

    async with get_async_session() as session:
        service = ClientInactivityService(session, request=None)
        await service.run_daily_inactivity_job(today=run_date, commit=True)

    logger.info(LogEvent.CLIENT_INACTIVITY_CRON_FINISHED, today=str(run_date))


tasks = [run_daily_client_inactivity_task]
