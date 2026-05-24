"""Arq worker task for processing notifications.

Single task handles the full lifecycle in one session:
  1. Resolves preferences and renders templates (via NotificationService)
  2. If IN_APP is enabled → creates a Notification row (user inbox)
  3. If any external channel enabled → creates NotificationAuditLog row,
     sends per channel, and updates the channel status columns
"""

import structlog

from app.common.enums.logger import LogEvent
from app.modules.notifications.enums import NotificationChannel, NotificationStatus

logger = structlog.get_logger()


async def process_notification_task(
    ctx: dict,
    *,
    event: str,
    notification_type: str,
    organization_id: str | None = None,
    user_id: str | None = None,
    recipient_email: str | None = None,
    recipient_phone: str | None = None,
    context: dict | None = None,
) -> None:
    """Resolve preferences, render templates, send via all enabled channels, and log results."""
    from app.core.database import get_session_factory
    from app.modules.notifications.enums import NotificationEvent, NotificationType
    from app.modules.notifications.repository import (
        NotificationAuditLogRepository,
        NotificationRepository,
    )
    from app.modules.notifications.service import NotificationService

    factory = get_session_factory()
    async with factory() as session:
        if user_id and (recipient_email is None or recipient_phone is None):
            from sqlalchemy import select

            from app.modules.user.models import User

            ur = await session.execute(select(User.email, User.phone).where(User.id == user_id))
            urow = ur.one_or_none()
            if urow is not None:
                if recipient_email is None:
                    recipient_email = urow[0]
                if recipient_phone is None:
                    recipient_phone = urow[1]

        ntype = NotificationType(notification_type)
        svc = NotificationService(session)
        resolved = await svc.resolve_notification(
            event=NotificationEvent(event),
            notification_type=ntype,
            organization_id=organization_id,
            user_id=user_id,
            context=context,
        )

        if not resolved:
            return

        inapp_channels = [r for r in resolved if r.channel == NotificationChannel.IN_APP]
        external_channels = [r for r in resolved if r.channel != NotificationChannel.IN_APP]

        notification_id: str | None = None

        # Step 1: create inbox notification if in-app is enabled and user exists
        if inapp_channels and user_id:
            primary = next(
                (ch for ch in resolved if ch.channel == NotificationChannel.IN_APP),
                resolved[0],
            )
            notif_repo = NotificationRepository(session)
            notification = await notif_repo.create_notification(
                recipient_id=user_id,
                organization_id=organization_id,
                event=event,
                notification_type=ntype.value,
                subject=primary.subject,
                body=primary.body,
                context_json=context,
            )
            notification_id = notification.id
            await session.commit()

        # Step 2: send external channels (email, sms, push)
        if external_channels:
            email_ch = next((c for c in external_channels if c.channel == NotificationChannel.EMAIL), None)

            audit_repo = NotificationAuditLogRepository(session)
            audit_log = await audit_repo.create_entry(
                notification_id=notification_id,
                recipient_id=user_id,
                organization_id=organization_id,
                event=event,
                notification_type=ntype.value,
                recipient_email=recipient_email,
                recipient_phone=recipient_phone,
                subject=email_ch.subject if email_ch else None,
                context_json=context,
            )
            await session.commit()

            results: dict[str, bool] = {}

            for ch in external_channels:
                try:
                    if ch.channel == NotificationChannel.EMAIL:
                        ok = await _send_email(
                            audit_repo,
                            session,
                            audit_log_id=audit_log.id,
                            recipient_email=recipient_email,
                            subject=ch.subject,
                            body=ch.body,
                            template_name=ch.template_name,
                            context=context,
                        )
                    elif ch.channel == NotificationChannel.SMS:
                        ok = await _send_sms(
                            audit_repo,
                            session,
                            audit_log_id=audit_log.id,
                            recipient_phone=recipient_phone,
                            body=ch.body,
                            event=event,
                        )
                    elif ch.channel == NotificationChannel.PUSH:
                        ok = await _send_push(
                            audit_repo,
                            session,
                            audit_log_id=audit_log.id,
                            user_id=user_id,
                            subject=ch.subject,
                            body=ch.body,
                            context=context,
                        )
                    else:
                        ok = False
                    results[ch.channel.value] = ok
                except Exception:
                    logger.exception(LogEvent.NOTIFICATION_CHANNEL_ERROR, channel=ch.channel.value, notif_event=event)
                    results[ch.channel.value] = False

            logger.info(
                LogEvent.NOTIFICATION_PROCESSED,
                notif_event=event,
                notification_type=ntype.value,
                organization_id=organization_id,
                inbox_created=notification_id is not None,
                channels=results,
            )
        else:
            logger.info(
                LogEvent.NOTIFICATION_PROCESSED,
                notif_event=event,
                notification_type=ntype.value,
                organization_id=organization_id,
                inbox_created=notification_id is not None,
                channels={},
            )


# External channel senders (private)


async def _send_email(audit_repo, session, *, audit_log_id, recipient_email, subject, body, template_name, context) -> bool:
    if not recipient_email:
        await audit_repo.update_channel_status(
            audit_log_id,
            channel="email",
            status=NotificationStatus.FAILED.value,
            error="No recipient email provided",
        )
        await session.commit()
        return False

    from app.modules.notifications.senders.email import EmailSender

    sender = EmailSender()
    status, error_msg, external_id = await sender.send(
        to_address=recipient_email,
        subject=subject,
        body=body,
        template_name=template_name,
        context=context,
    )

    await audit_repo.update_channel_status(
        audit_log_id,
        channel="email",
        status=status.value,
        error=error_msg,
        external_id=external_id,
    )
    await session.commit()

    logger.info(
        LogEvent.NOTIFICATION_EMAIL_SENT,
        status=status.value,
        recipient=recipient_email[-10:] if recipient_email else None,
    )
    return status != NotificationStatus.FAILED


async def _send_sms(audit_repo, session, *, audit_log_id, recipient_phone, body, event: str | None = None) -> bool:
    if not recipient_phone:
        await audit_repo.update_channel_status(
            audit_log_id,
            channel="sms",
            status=NotificationStatus.FAILED.value,
            error="No recipient phone provided",
        )
        await session.commit()
        return False

    from app.modules.notifications.sanitizers import check_sms_length
    from app.modules.notifications.senders.sms import SmsSender

    check_sms_length(body, event=event)
    sender = SmsSender()
    status, error_msg, external_id = await sender.send(
        to_number=recipient_phone,
        body=body,
    )

    await audit_repo.update_channel_status(
        audit_log_id,
        channel="sms",
        status=status.value,
        error=error_msg,
        external_id=external_id,
    )
    await session.commit()

    logger.info(
        LogEvent.NOTIFICATION_SMS_SENT,
        status=status.value,
        recipient=recipient_phone[-4:] if recipient_phone else None,
    )
    return status != NotificationStatus.FAILED


async def _send_push(audit_repo, session, *, audit_log_id, user_id, subject, body, context) -> bool:
    if not user_id:
        await audit_repo.update_channel_status(
            audit_log_id,
            channel="push",
            status=NotificationStatus.FAILED.value,
            error="No user_id for push lookup",
        )
        await session.commit()
        return False

    from app.modules.notifications.repository import DeviceTokenRepository
    from app.modules.notifications.senders.push import PushSender

    device_repo = DeviceTokenRepository(session)
    tokens = await device_repo.find_by_user(user_id, active_only=True)
    if not tokens:
        await audit_repo.update_channel_status(
            audit_log_id,
            channel="push",
            status=NotificationStatus.FAILED.value,
            error="No active device tokens",
        )
        await session.commit()
        logger.info(LogEvent.NOTIFICATION_PUSH_NO_DEVICES, user_id=user_id)
        return False

    sender = PushSender()
    any_sent = False
    errors: list[str] = []

    for token_record in tokens:
        status, error_msg, external_id = await sender.send(
            device_token=token_record.device_token,
            title=subject,
            body=body,
            data=context,
        )

        if status == NotificationStatus.FAILED and error_msg and "not registered" in error_msg.lower():
            await device_repo.deactivate_by_token(token_record.device_token)
            logger.info(LogEvent.NOTIFICATION_PUSH_TOKEN_DEACTIVATED, token_id=token_record.id)

        if status != NotificationStatus.FAILED:
            any_sent = True
        elif error_msg:
            errors.append(error_msg)

    final_status = NotificationStatus.SENT if any_sent else NotificationStatus.FAILED
    error_summary = "; ".join(errors[:3]) if errors else None

    await audit_repo.update_channel_status(
        audit_log_id,
        channel="push",
        status=final_status.value,
        error=error_summary,
    )
    await session.commit()

    logger.info(LogEvent.NOTIFICATION_PUSH_SENT, user_id=user_id, devices=len(tokens), any_sent=any_sent)
    return any_sent
