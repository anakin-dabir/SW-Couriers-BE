"""Email notification sender — delegates to the centralised mailer client."""

import structlog

from app.common.enums.logger import LogEvent
from app.modules.notifications.enums import NotificationStatus
from app.modules.notifications.sanitizers import wrap_email_html

logger = structlog.get_logger()


class EmailSender:
    """Sends email notifications via the centralised mailer infrastructure."""

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        template_name: str | None = None,
        context: dict | None = None,
    ) -> tuple[NotificationStatus, str | None, str | None]:
        """Send an email notification.

        If ``template_name`` resolves to a file-based Jinja2 template in
        ``app/mailer/templates/``, it is rendered with ``context``.
        Otherwise ``body`` is wrapped in the branded email layout.

        Returns:
            (status, error_message, external_id)
        """
        from app.mailer.client import render as render_file_template
        from app.mailer.client import send_email

        html_body = wrap_email_html(body)
        if template_name and context is not None:
            try:
                html_body = render_file_template(template_name, context)
            except Exception:
                logger.debug(LogEvent.NOTIFICATION_EMAIL_TEMPLATE_RENDER_FAILED, template=template_name)

        try:
            await send_email(to_address, subject, html_body=html_body)
            return NotificationStatus.SENT, None, None
        except ValueError as exc:
            return NotificationStatus.FAILED, str(exc), None
        except RuntimeError as exc:
            logger.warning(LogEvent.NOTIFICATION_EMAIL_NOT_CONFIGURED)
            return NotificationStatus.FAILED, str(exc), None
        except Exception as exc:
            logger.error(LogEvent.NOTIFICATION_EMAIL_FAILED, error=type(exc).__name__)
            return NotificationStatus.FAILED, type(exc).__name__, None
