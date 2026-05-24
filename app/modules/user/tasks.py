"""User-related background tasks (Arq). Owned by user module; registered in workers."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.common.enums import LogEvent
from app.common.utils import mask_email, validate_link
from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.mailer import EmailTemplateName, send_email
from app.modules.auth.repository import InviteRepository

logger = structlog.get_logger()


def _invite_context(first_name: str, invite_link: str, expires_days: int) -> dict:
    validate_link(invite_link)
    return {
        "name": (first_name or "").strip() or "there",
        "invite_link": invite_link,
        "expires_days": expires_days,
        "day_label": "day" if expires_days == 1 else "days",
    }


async def send_invite_email_task(
    ctx: dict,
    invite_id: str,
    to_email: str,
    first_name: str,
    invite_link: str,
    subject: str = "You're invited to SW Couriers",
    expires_days: int = 7,
) -> None:
    """Send invite email and track delivery status on the invite row."""
    async with get_async_session() as session:
        repo = InviteRepository(session)
        try:
            await send_email(
                to_email,
                subject,
                template_name=EmailTemplateName.INVITE_NEW_USER,
                context=_invite_context(first_name, invite_link, expires_days),
            )
            await repo.update_email_status(invite_id, "sent", email_sent_at=datetime.now(UTC))
            logger.info(LogEvent.INVITE_EMAIL_SENT, invite_id=invite_id, to=mask_email(to_email))
        except Exception as e:
            await session.rollback()
            error_msg = f"{type(e).__name__}: {e}"
            await repo.update_email_status(invite_id, "failed", email_last_error=error_msg)
            await session.commit()
            logger.warning(LogEvent.INVITE_EMAIL_FAILED, invite_id=invite_id, error=error_msg)
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e


tasks = [send_invite_email_task]
