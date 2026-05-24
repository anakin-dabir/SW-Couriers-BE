"""Generic notification dispatch — the public API other modules should use.

Thin fire-and-forget wrappers that enqueue PROCESS_NOTIFICATION jobs.
The worker handles everything: preference resolution, template rendering,
per-channel sending, and log storage.

Usage::

    from app.modules.notifications.dispatch import notify, notify_many

    # Single recipient
    await notify(
        event=NotificationEvent.BOOKING_CONFIRMATION,
        notification_type=NotificationType.RECIPIENT,
        organization_id=org_id,
        recipient_email="person@example.com",
        recipient_phone="+441234567890",
        context={"tracking_id": "SW-12345"},
    )

    # Multiple recipients for one event
    await notify_many(
        event=NotificationEvent.BOOKING_CONFIRMATION,
        organization_id=org_id,
        recipients=[
            {"notification_type": "B2B_CUSTOMER", "user_id": admin_id, "recipient_email": admin_email},
            {"notification_type": "RECIPIENT", "recipient_email": customer_email, "recipient_phone": customer_phone},
            {"notification_type": "DRIVER", "user_id": driver_id},
        ],
        context={"tracking_id": "SW-12345"},
    )
"""

from __future__ import annotations

import structlog

from app.common.enums.jobs import Job
from app.common.enums.logger import LogEvent
from app.core.queue import QueuePriority, enqueue
from app.modules.notifications.enums import NotificationEvent, NotificationType

logger = structlog.get_logger()


async def notify(
    *,
    event: NotificationEvent,
    notification_type: NotificationType,
    organization_id: str | None = None,
    user_id: str | None = None,
    recipient_email: str | None = None,
    recipient_phone: str | None = None,
    context: dict | None = None,
) -> bool:
    """Enqueue a notification for async processing (single recipient).

    Args:
        event:              The notification event to trigger.
        notification_type:  ADMIN_INTERNAL, B2B_CUSTOMER, RECIPIENT, or DRIVER.
        organization_id:    Org context for preference/template cascade; required for non-DRIVER
                            streams. Optional for DRIVER (inbox rows may store NULL organization_id).
        user_id:            Target user for ADMIN_INTERNAL, B2B_CUSTOMER, or DRIVER (preference lookup).
        recipient_email:    Email address for EMAIL channel.
        recipient_phone:    Phone number for SMS channel.
        context:            Template variables (e.g. tracking_id, customer_name).

    Returns:
        True if the job was enqueued successfully, False otherwise.
    """
    job = await enqueue(
        Job.PROCESS_NOTIFICATION,
        event=event.value,
        notification_type=notification_type.value,
        organization_id=organization_id,
        user_id=user_id,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        context=context or {},
        priority=QueuePriority.NOTIFICATIONS,
    )
    return job is not None


async def notify_many(
    *,
    event: NotificationEvent,
    organization_id: str | None = None,
    recipients: list[dict],
    context: dict | None = None,
) -> int:
    """Enqueue notifications for multiple recipients of the same event.

    Each dict in ``recipients`` should contain::

        {
            "notification_type": "ADMIN_INTERNAL" | "B2B_CUSTOMER" | "RECIPIENT" | "DRIVER",
            "user_id": str | None,
            "recipient_email": str | None,
            "recipient_phone": str | None,
        }

    One independent Arq job is enqueued per recipient.

    Returns:
        Number of jobs successfully enqueued.
    """
    enqueued = 0
    for r in recipients:
        try:
            ntype = NotificationType(r["notification_type"])
        except (KeyError, ValueError):
            logger.warning(LogEvent.NOTIFY_MANY_INVALID_TYPE, recipient=r)
            continue

        ok = await notify(
            event=event,
            notification_type=ntype,
            organization_id=organization_id,
            user_id=r.get("user_id"),
            recipient_email=r.get("recipient_email"),
            recipient_phone=r.get("recipient_phone"),
            context=context,
        )
        if ok:
            enqueued += 1

    logger.info(
        LogEvent.NOTIFY_MANY_ENQUEUED,
        notif_event=event.value,
        organization_id=organization_id,
        total_recipients=len(recipients),
        enqueued=enqueued,
    )
    return enqueued
