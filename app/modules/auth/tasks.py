"""Auth-related background tasks (Arq). Owned by auth module; registered in workers."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.common.enums import LogEvent
from app.common.utils import mask_email, validate_link
from app.core.config import settings
from app.core.queue import retry_backoff
from app.mailer import EmailTemplateName, send_email

logger = structlog.get_logger()


async def send_support_issued_password_email_task(
    ctx: dict,
    to_email: str,
    first_name: str | None,
    temporary_password: str,
) -> None:
    """Email the temporary password set by support; user must change password after login."""
    try:
        await send_email(
            to_email,
            "Your SW Couriers temporary password",
            template_name=EmailTemplateName.SUPPORT_ISSUED_PASSWORD,
            context={
                "name": (first_name or "").strip() or "there",
                "temporary_password": temporary_password,
            },
        )
        logger.info(LogEvent.SUPPORT_ISSUED_PASSWORD_EMAIL_SENT, to=mask_email(to_email))
    except Exception as e:
        logger.warning(
            LogEvent.SUPPORT_ISSUED_PASSWORD_EMAIL_FAILED,
            to=mask_email(to_email),
            error=f"{type(e).__name__}: {e}",
        )
        raise retry_backoff(ctx.get("job_try", 1), base=30) from e


async def send_password_reset_email_task(
    ctx: dict,
    to_email: str,
    first_name: str | None,
    reset_otp: str,
    expires_minutes: int,
) -> None:
    try:
        await send_email(
            to_email,
            "Reset your SW Couriers password",
            template_name=EmailTemplateName.PASSWORD_RESET,
            context={
                "name": (first_name or "").strip() or "there",
                "reset_otp": reset_otp,
                "expires_minutes": expires_minutes,
            },
        )
        logger.info(LogEvent.PASSWORD_RESET_EMAIL_SENT, to=mask_email(to_email))
    except Exception as e:
        logger.warning(
            LogEvent.PASSWORD_RESET_EMAIL_FAILED,
            to=mask_email(to_email),
        )
        raise retry_backoff(ctx.get("job_try", 1), base=30) from e


async def send_verification_email_task(
    ctx: dict,
    to_email: str,
    first_name: str | None,
    verification_link: str | None = None,
    verification_code: str | None = None,
) -> None:
    """Send an email verification link or code. Retries on failure with exponential backoff."""
    try:
        validated_link = validate_link(verification_link) if verification_link and verification_link != "[NOT_LINK_JUST_CODE]" else None
        await send_email(
            to_email,
            "Verify your SW Couriers account",
            template_name=EmailTemplateName.EMAIL_VERIFICATION,
            context={
                "name": (first_name or "").strip() or "there",
                "link": validated_link,
                "code": verification_code,
            },
        )
        logger.info(LogEvent.VERIFICATION_EMAIL_SENT, to=mask_email(to_email))
    except Exception as e:
        logger.warning(LogEvent.VERIFICATION_EMAIL_FAILED, to=mask_email(to_email))
        raise retry_backoff(ctx.get("job_try", 1), base=30) from e


async def send_driver_activation_email_task(
    ctx: dict,
    invite_id: str,
    to_email: str,
    first_name: str,
    activation_link: str,
    expires_days: int = 7,
) -> None:
    """Send driver set-password deep link; updates invite email delivery status."""
    from app.core.database import get_async_session
    from app.modules.auth.repository import InviteRepository

    async with get_async_session() as session:
        repo = InviteRepository(session)
        try:
            validated = validate_link(activation_link)
            await send_email(
                to_email,
                "Set your SW Couriers driver password",
                template_name=EmailTemplateName.DRIVER_SET_PASSWORD_INVITE,
                context={
                    "name": (first_name or "").strip() or "there",
                    "activation_link": validated,
                    "expires_days": expires_days,
                    "day_label": "day" if expires_days == 1 else "days",
                    "driver_play_store_url": (settings.DRIVER_APP_PLAY_STORE_URL or "").strip(),
                    "driver_app_store_url": (settings.DRIVER_APP_APP_STORE_URL or "").strip(),
                },
            )
            await repo.update_email_status(invite_id, "sent", email_sent_at=datetime.now(UTC))
            logger.info(LogEvent.DRIVER_ACTIVATION_EMAIL_SENT, invite_id=invite_id, to=mask_email(to_email))
        except Exception as e:
            await session.rollback()
            error_msg = f"{type(e).__name__}: {e}"
            await repo.update_email_status(invite_id, "failed", email_last_error=error_msg)
            await session.commit()
            logger.warning(LogEvent.DRIVER_ACTIVATION_EMAIL_FAILED, invite_id=invite_id, error=error_msg)
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e


async def cleanup_expired_tokens_task(ctx: dict) -> None:
    """Purge expired refresh tokens from the database.

    Registered as an Arq cron job (runs daily). Prevents unbounded table growth.
    """
    from app.core.database import get_async_session
    from app.modules.auth.repository import RefreshTokenRepository

    async with get_async_session() as session:
        repo = RefreshTokenRepository(session)
        deleted = await repo.delete_expired()
        logger.info(LogEvent.EXPIRED_TOKENS_CLEANED, deleted=deleted)


tasks = [
    send_support_issued_password_email_task,
    send_password_reset_email_task,
    send_verification_email_task,
    send_driver_activation_email_task,
]
