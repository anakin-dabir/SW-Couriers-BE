"""Arq tasks for vehicle maintenance alerts (preferred-driver notifications)."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from app.common.enums.logger import LogEvent
from app.core.database import get_async_session
from app.modules.vehicles.service import VehicleService

logger = structlog.get_logger()


async def evaluate_vehicle_service_due_alerts_task(ctx: dict[Any, Any], today: str | None = None) -> None:
    try:
        run_date = date.fromisoformat(today) if today else date.today()
    except ValueError:
        logger.warning("vehicle_service_due_cron_invalid_date", today=today)
        run_date = date.today()

    logger.info(LogEvent.VEHICLE_SERVICE_DUE_CRON_STARTED, today=str(run_date))

    async with get_async_session() as session:
        svc = VehicleService(session, request=None)
        candidates, notifications = await svc.run_daily_driver_service_due_evaluation(run_date)

    logger.info(
        LogEvent.VEHICLE_SERVICE_DUE_CRON_COMPLETED,
        today=str(run_date),
        candidates=candidates,
        notifications=notifications,
    )
