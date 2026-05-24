from __future__ import annotations

import enum
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
import structlog
from email_validator import EmailNotValidError, validate_email
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.common.enums.logger import LogEvent
from app.common.utils import mask_email
from app.core.config import settings

logger = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class EmailTemplateName(enum.StrEnum):

    EMAIL_VERIFICATION = "email_verification.html"
    INVITE_NEW_USER = "invite_new_user.html"
    PASSWORD_RESET = "password_reset.html"
    SUPPORT_ISSUED_PASSWORD = "support_issued_password.html"
    NOTIFICATION_BASE = "notification_base.html"

    SUSPENSION_WARNING_B2B = "suspension_warning_b2b.html"
    SUSPENSION_RULE_FIRED_FINANCE = "suspension_rule_fired_finance.html"

    DRIVER_ACCOUNT_CREATED = "driver_account_created.html"

    DRIVER_SET_PASSWORD_INVITE = "driver_set_password_invite.html"

    DOCUMENT_SHARE = "document_share.html"

    DOC_OTP = "doc_otp.html"
    SHARE_OTP = "share_otp.html"

    CREDIT_ALERT = "credit_alert.html"


_env: Environment | None = None


def _get_env() -> Environment:
    global _env  # noqa: PLW0603
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(("html", "htm", "xml")),
        )
        _env.globals["email_logo_url"] = (settings.EMAIL_LOGO_URL or "").strip()
        _env.globals["support_email"] = (settings.SUPPORT_EMAIL or settings.EMAIL_FROM_ADDRESS or "").strip()
    return _env


def render(html_template: str, context: dict) -> str:
    return _get_env().get_template(str(html_template)).render(**context).strip()


def _validate_recipient(to_address: str) -> str | None:
    try:
        return validate_email(to_address, check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def _smtp_config() -> dict | None:
    host = (settings.SMTP_HOST or "").strip()
    username = (settings.SMTP_USERNAME or "").strip()
    password = settings.SMTP_PASSWORD.get_secret_value()

    if not host or not username or not password:
        logger.warning(
            LogEvent.MAIL_NOT_CONFIGURED,
            host=bool(host),
            username=bool(username),
            password_set=bool(password),
        )
        return None

    port = settings.SMTP_PORT or 587
    return {
        "hostname": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": port == 465,
        "start_tls": port == 587,
        "from_address": settings.EMAIL_FROM_ADDRESS,
        "from_name": settings.EMAIL_FROM_NAME,
    }


async def send_email(
    to_address: str,
    subject: str,
    *,
    html_body: str | None = None,
    template_name: EmailTemplateName | None = None,
    context: dict | None = None,
) -> None:
    """Send an HTML email.

    Provide either ``html_body`` (pre-rendered) or ``template_name`` + ``context``
    (Jinja2 file template). Raises ``ValueError`` for invalid recipients,
    ``RuntimeError`` if SMTP is not configured.
    """
    if html_body is None:
        if template_name is None or context is None:
            raise ValueError("Provide html_body or template_name + context")
        html_body = render(template_name, context)

    to_normalized = _validate_recipient(to_address)
    if to_normalized is None:
        logger.warning(LogEvent.MAIL_SKIPPED_INVALID_RECIPIENT, to=mask_email(to_address))
        raise ValueError("Invalid email address")

    conf = _smtp_config()
    if conf is None:
        raise RuntimeError("SMTP not configured")

    from_str = f"{conf['from_name']} <{conf['from_address']}>"
    message = MIMEText(html_body, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = from_str
    message["To"] = to_normalized

    await aiosmtplib.send(
        message,
        hostname=conf["hostname"],
        port=conf["port"],
        username=conf["username"],
        password=conf["password"],
        use_tls=conf["use_tls"],
        start_tls=conf["start_tls"],
    )
    logger.info(LogEvent.MAIL_SENT, to=mask_email(to_normalized), subject=subject)
