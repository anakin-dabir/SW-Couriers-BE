from __future__ import annotations

import pytest

from app.core.config import settings
from app.mailer import EmailTemplateName, send_email


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_all_email_templates_to_gmail_hardcoded_with_placeholders() -> None:
    """
    Hardcoded real SMTP send of all email templates to a fixed recipient.

    Safety: skips automatically if SMTP is not configured.
    Recipient and placeholders are hardcoded to avoid relying on env vars.
    """

    # Safety gate: don't run if SMTP isn't configured in this environment.
    smtp_configured = bool((settings.SMTP_HOST or "").strip() and (settings.SMTP_USERNAME or "").strip() and settings.SMTP_PASSWORD.get_secret_value())
    if not smtp_configured:
        pytest.skip("SMTP is not configured; skipping real email send.")

    to_email = "neftjester1@gmail.com"

    # Common placeholders — keep them deterministic.
    name = "Template Viewer"
    code = "123456"
    link = "https://example.com/verify?token=TESTTOKEN"
    invite_link = "https://example.com/invite?token=TESTINVITETOKEN"
    password = "SecureTestPass1!"

    # Each template needs its own subject and a context that satisfies the Jinja variables used by the template.
    cases: list[tuple[EmailTemplateName, str, dict[str, object]]] = [
        (
            EmailTemplateName.EMAIL_VERIFICATION,
            "Verify your SW Couriers account",
            {"name": name, "link": link, "code": code},
        ),
        (
            EmailTemplateName.INVITE_NEW_USER,
            "You're invited to SW Couriers",
            {
                "name": name,
                "invite_link": invite_link,
                "expires_days": 7,
                "day_label": "days",
            },
        ),
        (
            EmailTemplateName.PASSWORD_RESET,
            "Reset your SW Couriers password",
            {
                "name": name,
                "reset_otp": code,
                "expires_minutes": 30,
            },
        ),
        (
            EmailTemplateName.SUSPENSION_WARNING_B2B,
            "Important notice about your SW Couriers account",
            {
                "name": name,
                "rule_name": "Late payment rule",
                "condition_summary": "Overdue invoices > threshold",
                "conditions_met_human": "Invoice A, Invoice B are overdue",
                "support_email": "support@example.com",
            },
        ),
        (
            EmailTemplateName.SUSPENSION_RULE_FIRED_FINANCE,
            "Suspension rule triggered for B2B customer",
            {
                "account_name": "Acme Logistics",
                "account_email": "acme@example.com",
                "organization_name": None,
                "rule_name": "Credit limit rule",
                "condition_summary": "Credit utilization exceeds limit",
                "conditions_met_human": "Utilization=0.95, limit=0.8",
                "action_taken_human": "Warning sent to customer",
            },
        ),
        (
            EmailTemplateName.DRIVER_ACCOUNT_CREATED,
            "Your SW Couriers driver account",
            {"name": name, "email": to_email, "password": password},
        ),
        (
            EmailTemplateName.NOTIFICATION_BASE,
            "SW Couriers notification (template viewer)",
            {"content": "Hello from template viewer test."},
        ),
    ]

    for template_name, subject, context in cases:
        await send_email(
            to_email,
            subject,
            template_name=template_name,
            context=context,
        )

