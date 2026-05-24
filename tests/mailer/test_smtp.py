"""SMTP / mailer client tests.

Unit tests mock aiosmtplib and config so no real SMTP is used in CI.
Optional live test runs only when SMTP credentials are set (e.g. .env.local).
"""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import pytest

from app.mailer import EmailTemplateName, send_email


def _get_body_text(message: EmailMessage) -> str:
    """Extract body as string from MIMEText message."""
    payload = message.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)


def _assert_message_shape(
    message: EmailMessage,
    *,
    subject: str,
    to: str,
    body_contains: str,
) -> None:
    assert message["Subject"] == subject
    assert message["To"] == to
    assert message["From"]
    body = _get_body_text(message)
    assert body_contains in body


# ── Unit tests (mocked, no network) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_email_skips_when_smtp_not_configured() -> None:
    """When SMTP is not configured, send_email raises and does not call SMTP."""
    with (
        patch("app.mailer.client._smtp_config", return_value=None),
        patch("app.mailer.client.aiosmtplib.send", new_callable=AsyncMock) as mock_send,
    ):
        with pytest.raises(RuntimeError, match="SMTP not configured"):
            await send_email(
                "user@example.com",
                "Test",
                template_name=EmailTemplateName.PASSWORD_RESET,
                context={
                    "name": "Test",
                    "reset_otp": "123456",
                    "expires_minutes": 15,
                },
            )
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_skips_invalid_recipient() -> None:
    """When recipient is invalid, send_email raises without sending."""
    smtp_conf = {
        "hostname": "smtp.example.com",
        "port": 587,
        "username": "noreply@example.com",
        "password": "secret",
        "use_tls": False,
        "start_tls": True,
        "from_address": "noreply@example.com",
        "from_name": "SW Couriers",
    }
    with (
        patch("app.mailer.client._smtp_config", return_value=smtp_conf),
        patch("app.mailer.client.aiosmtplib.send", new_callable=AsyncMock) as mock_send,
    ):
        with pytest.raises(ValueError, match="Invalid email address"):
            await send_email(
                "not-an-email",
                "Test",
                template_name=EmailTemplateName.PASSWORD_RESET,
                context={
                    "name": "Test",
                    "reset_otp": "123456",
                    "expires_minutes": 15,
                },
            )
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_calls_aiosmtplib_with_correct_message() -> None:
    """When SMTP is configured and recipient valid, send_email builds and sends the message."""
    smtp_conf = {
        "hostname": "smtp.example.com",
        "port": 587,
        "username": "noreply@example.com",
        "password": "secret",
        "use_tls": False,
        "start_tls": True,
        "from_address": "noreply@example.com",
        "from_name": "SW Couriers",
    }
    with (
        patch("app.mailer.client._smtp_config", return_value=smtp_conf),
        patch("app.mailer.client.aiosmtplib.send", new_callable=AsyncMock) as mock_send,
    ):
        await send_email(
            "recipient@example.com",
            "Reset your password",
            template_name=EmailTemplateName.PASSWORD_RESET,
            context={
                "name": "Jane",
                "reset_otp": "654321",
                "expires_minutes": 15,
            },
        )
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        message = call_args[0][0]
        _assert_message_shape(
            message,
            subject="Reset your password",
            to="recipient@example.com",
            body_contains="Jane",
        )
        assert "654321" in _get_body_text(message)
        assert "SW Couriers" in message["From"]


@pytest.mark.asyncio
async def test_send_email_passes_smtp_params_to_aiosmtplib() -> None:
    """send_email forwards host, port, credentials and TLS flags to aiosmtplib.send."""
    smtp_conf = {
        "hostname": "smtp.test.com",
        "port": 587,
        "username": "user@test.com",
        "password": "pass",
        "use_tls": False,
        "start_tls": True,
        "from_address": "user@test.com",
        "from_name": "Test",
    }
    with (
        patch("app.mailer.client._smtp_config", return_value=smtp_conf),
        patch("app.mailer.client.aiosmtplib.send", new_callable=AsyncMock) as mock_send,
    ):
        await send_email(
            "to@example.com",
            "Subject",
            template_name=EmailTemplateName.EMAIL_VERIFICATION,
            context={"name": "A", "link": "https://example.com"},
        )
        mock_send.assert_called_once()
        call_kw = mock_send.call_args[1]
        assert call_kw["hostname"] == "smtp.test.com"
        assert call_kw["port"] == 587
        assert call_kw["username"] == "user@test.com"
        assert call_kw["password"] == "pass"
        assert call_kw["start_tls"] is True
        assert call_kw["use_tls"] is False


# ── Optional live test (runs when SMTP is configured) ───────────────────────


def _smtp_configured() -> bool:
    """True if SMTP credentials are present (checks settings and os.environ at collection time)."""
    import os

    from app.core.config import settings

    host = (settings.SMTP_HOST or os.environ.get("SMTP_HOST") or "").strip()
    user = (settings.SMTP_USERNAME or os.environ.get("SMTP_USERNAME") or "").strip()
    password = settings.SMTP_PASSWORD.get_secret_value() or os.environ.get("SMTP_PASSWORD") or ""
    return bool(host and user and password)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _smtp_configured(),
    reason="SMTP not configured (set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD in .env.local to run)",
)
async def test_send_email_live_smoke() -> None:
    """Send one real email to self when SMTP credentials are set. Skipped in CI."""
    from app.core.config import settings

    to = (settings.SMTP_USERNAME or "").strip()
    assert to, "SMTP_USERNAME required for live test"
    await send_email(
        to,
        "SW Couriers SMTP test",
        template_name=EmailTemplateName.PASSWORD_RESET,
        context={
            "name": "Test",
            "reset_otp": "123456",
            "expires_minutes": 15,
        },
    )
