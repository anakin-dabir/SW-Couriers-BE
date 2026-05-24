"""SMS notification sender — sends via Twilio REST API using httpx."""

import structlog

from app.common.enums.logger import LogEvent
from app.modules.notifications.enums import NotificationStatus

logger = structlog.get_logger()


class SmsSender:
    """Sends SMS notifications via the Twilio REST API."""

    async def send(
        self,
        *,
        to_number: str,
        body: str,
    ) -> tuple[NotificationStatus, str | None, str | None]:
        """Send an SMS via Twilio.

        Returns:
            (status, error_message, external_id)
        """
        import httpx

        from app.core.config import settings

        account_sid = (settings.TWILIO_ACCOUNT_SID or "").strip()
        auth_token = settings.TWILIO_AUTH_TOKEN.get_secret_value()
        from_number = (settings.TWILIO_FROM_NUMBER or "").strip()

        if not account_sid or not auth_token or not from_number:
            logger.warning(LogEvent.NOTIFICATION_SMS_NOT_CONFIGURED)
            return NotificationStatus.FAILED, "Twilio not configured", None

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url,
                    auth=(account_sid, auth_token),
                    data={
                        "From": from_number,
                        "To": to_number,
                        "Body": body,
                    },
                )

            if resp.status_code >= 400:
                error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_body.get("message", f"HTTP {resp.status_code}")
                logger.error(
                    LogEvent.NOTIFICATION_SMS_FAILED,
                    status_code=resp.status_code,
                    error=error_msg,
                )
                return NotificationStatus.FAILED, error_msg, None

            data = resp.json()
            sid = data.get("sid")
            logger.info(LogEvent.NOTIFICATION_SMS_SENT, to=to_number[-4:], sid=sid)
            return NotificationStatus.SENT, None, sid

        except httpx.TimeoutException:
            logger.error(LogEvent.NOTIFICATION_SMS_SEND_TIMEOUT, to=to_number[-4:])
            return NotificationStatus.FAILED, "Twilio request timeout", None
        except Exception as exc:
            logger.error(LogEvent.NOTIFICATION_SMS_SEND_ERROR, error=type(exc).__name__)
            return NotificationStatus.FAILED, type(exc).__name__, None
