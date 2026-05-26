"""Driver work-schedule update notifications."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drivers.repository import DriverRepository
from app.modules.notifications.dispatch import notify
from app.modules.notifications.enums import NotificationEvent, NotificationType
from app.modules.user.repository import UserRepository

logger = structlog.get_logger()


async def notify_driver_work_schedule_updated(
    session: AsyncSession,
    *,
    driver_id: str,
    change_summary: str,
    effective_from: str | None = None,
    audit_user_id: str | None = None,
) -> bool:
    """Enqueue DRIVER_WORK_SCHEDULE_UPDATED for the driver's app user."""
    driver_repo = DriverRepository(session)
    user_repo = UserRepository(session)

    driver = await driver_repo.get_by_id(driver_id)
    if driver is None or not driver.user_id:
        return False

    if audit_user_id is not None and audit_user_id == driver.user_id:
        return False

    user = await user_repo.get_by_id(driver.user_id)
    if user is None:
        return False

    driver_name = ""
    if user.first_name or user.last_name:
        driver_name = f"{user.first_name or ''} {user.last_name or ''}".strip()

    context = {
        "driver_name": driver_name or "Driver",
        "change_summary": change_summary,
        "effective_from": effective_from or "",
        "updated_at": datetime.now(UTC).isoformat(),
    }

    sent = await notify(
        event=NotificationEvent.DRIVER_WORK_SCHEDULE_UPDATED,
        notification_type=NotificationType.DRIVER,
        organization_id=user.organization_id,
        user_id=driver.user_id,
        context=context,
    )
    if not sent:
        logger.warning(
            "driver_work_schedule_notify_skipped",
            driver_id=driver_id,
            user_id=driver.user_id,
        )
    return sent


async def notify_drivers_for_holiday_allow_list_change(
    session: AsyncSession,
    *,
    driver_ids: set[str],
    change_summary: str,
    audit_user_id: str | None = None,
) -> None:
    for driver_id in driver_ids:
        await notify_driver_work_schedule_updated(
            session,
            driver_id=driver_id,
            change_summary=change_summary,
            audit_user_id=audit_user_id,
        )
