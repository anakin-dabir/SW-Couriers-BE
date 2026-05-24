"""Organization-related background tasks (Arq). Registered in workers/master.py."""

from __future__ import annotations

import structlog

from app.common.enums import LogEvent
from app.common.utils import mask_email
from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.mailer import EmailTemplateName, send_email

logger = structlog.get_logger()


async def send_document_share_email_task(
    ctx: dict,
    share_id: str,
    to_email: str,
    document_title: str,
    document_reference: str | None,
    shared_by_name: str,
    share_url: str,
    expiry_date: str | None = None,
    message: str | None = None,
    otp_required: bool = False,
) -> None:
    """Send a document share email to a single recipient."""
    async with get_async_session() as session:  # noqa: F841  (session unused — email-only task)
        try:
            await send_email(
                to_email,
                f"Document shared with you: {document_title}",
                template_name=EmailTemplateName.DOCUMENT_SHARE,
                context={
                    "shared_by_name": shared_by_name,
                    "document_title": document_title,
                    "document_reference": document_reference,
                    "share_url": share_url,
                    "expiry_date": expiry_date,
                    "message": message,
                    "otp_required": otp_required,
                },
            )
            logger.info(
                LogEvent.MAIL_SENT,
                task="send_document_share_email_task",
                share_id=share_id,
                to=mask_email(to_email),
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.warning(
                "document_share_email.failed",
                share_id=share_id,
                to=mask_email(to_email),
                error=error_msg,
            )
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e


async def send_doc_otp_email_task(
    ctx: dict,
    to_email: str,
    otp_code: str,
    user_name: str,
    expires_in_minutes: int = 10,
    access_scope: str = "ORG_DOCUMENTS",
) -> None:
    """Send a document access OTP email to the requesting user."""
    async with get_async_session() as session:  # noqa: F841
        try:
            subject = (
                "Your SW Couriers Driver Document Access Code"
                if access_scope == "DRIVER_DOCUMENTS"
                else "Your SW Couriers Document Access Code"
            )
            await send_email(
                to_email,
                subject,
                template_name=EmailTemplateName.DOC_OTP,
                context={
                    "user_name": user_name,
                    "otp_code": otp_code,
                    "expires_in_minutes": expires_in_minutes,
                    "access_scope": access_scope,
                },
            )
            logger.info(
                LogEvent.MAIL_SENT,
                task="send_doc_otp_email_task",
                to=mask_email(to_email),
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.warning(
                "doc_otp_email.failed",
                to=mask_email(to_email),
                error=error_msg,
            )
            raise retry_backoff(ctx.get("job_try", 1), base=30) from e


async def send_share_otp_email_task(
    ctx: dict,
    to_email: str,
    otp_code: str,
    document_title: str | None = None,
    expires_in_minutes: int = 10,
) -> None:
    """Send a document share OTP email to an unauthenticated external recipient."""
    async with get_async_session() as session:  # noqa: F841
        try:
            await send_email(
                to_email,
                "Your SW Couriers Document Access Code",
                template_name=EmailTemplateName.SHARE_OTP,
                context={
                    "otp_code": otp_code,
                    "document_title": document_title,
                    "expires_in_minutes": expires_in_minutes,
                },
            )
            logger.info(
                LogEvent.MAIL_SENT,
                task="send_share_otp_email_task",
                to=mask_email(to_email),
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.warning(
                "share_otp_email.failed",
                to=mask_email(to_email),
                error=error_msg,
            )
            raise retry_backoff(ctx.get("job_try", 1), base=30) from e


tasks = [send_document_share_email_task, send_doc_otp_email_task, send_share_otp_email_task]
