from __future__ import annotations

import argparse
import asyncio

from app.mailer import EmailTemplateName, send_email


def _cases() -> list[tuple[EmailTemplateName, str, dict[str, object]]]:
    return [
        (
            EmailTemplateName.EMAIL_VERIFICATION,
            "[Smoke] Verify your SW Couriers account",
            {"name": "Haider", "link": "https://example.com/verify", "code": "123456"},
        ),
        (
            EmailTemplateName.INVITE_NEW_USER,
            "[Smoke] You're invited to SW Couriers",
            {"name": "Haider", "invite_link": "https://example.com/invite", "expires_days": 7, "day_label": "days"},
        ),
        (
            EmailTemplateName.PASSWORD_RESET,
            "[Smoke] Reset your SW Couriers password",
            {"name": "Haider", "reset_otp": "123456", "expires_minutes": 15},
        ),
        (
            EmailTemplateName.SUPPORT_ISSUED_PASSWORD,
            "[Smoke] Your temporary password",
            {"name": "Haider", "temporary_password": "TempPass123!"},
        ),
        (
            EmailTemplateName.NOTIFICATION_BASE,
            "[Smoke] SW Couriers notification",
            {"content": "<p>Sample notification body.</p>"},
        ),
        (
            EmailTemplateName.SUSPENSION_WARNING_B2B,
            "[Smoke] Important account notice",
            {
                "name": "Haider",
                "rule_name": "Late payment rule",
                "condition_summary": "Invoices overdue",
                "conditions_met_human": "Invoice 001 overdue by 10 days",
                "support_email": "support@swcouriers.com",
            },
        ),
        (
            EmailTemplateName.SUSPENSION_RULE_FIRED_FINANCE,
            "[Smoke] Suspension rule triggered",
            {
                "account_name": "UrbanNest Home",
                "account_email": "owner@example.com",
                "organization_name": "UrbanNest Retail Group Ltd",
                "rule_name": "Credit threshold",
                "condition_summary": "Credit use > 90%",
                "conditions_met_human": "£9,100 of £10,000 used",
                "action_taken_human": "Warning email sent",
            },
        ),
        (
            EmailTemplateName.DRIVER_ACCOUNT_CREATED,
            "[Smoke] Your SW Couriers driver account",
            {"name": "Haider", "email": "driver@example.com", "password": "TempPass123!"},
        ),
        (
            EmailTemplateName.DRIVER_SET_PASSWORD_INVITE,
            "[Smoke] Set your driver password",
            {"name": "Haider", "activation_link": "swcouriers://activate?token=x", "expires_days": 7, "day_label": "days"},
        ),
        (
            EmailTemplateName.DOCUMENT_SHARE,
            "[Smoke] Document shared with you",
            {
                "shared_by_name": "Haider",
                "document_reference": "DOC-001",
                "document_title": "Contract.pdf",
                "message": "Please review",
                "expiry_date": "2026-05-25",
                "share_url": "https://example.com/share",
                "otp_required": True,
            },
        ),
        (
            EmailTemplateName.DOC_OTP,
            "[Smoke] Your document access code",
            {"user_name": "Haider", "access_scope": "DRIVER_DOCUMENTS", "otp_code": "123456", "expires_in_minutes": 10},
        ),
        (
            EmailTemplateName.SHARE_OTP,
            "[Smoke] Your shared-document access code",
            {"document_title": "Contract.pdf", "otp_code": "123456", "expires_in_minutes": 10},
        ),
        (
            EmailTemplateName.CREDIT_ALERT,
            "[Smoke] Credit limit warning",
            {
                "title": "Credit limit warning",
                "severity": "WARNING",
                "recipient_name": "Haider",
                "org_name": "UrbanNest",
                "summary": "90% of credit used",
                "triggered_at_display": "May 18, 2026 11:00",
                "action_url": "https://example.com/credit",
            },
        ),
    ]


async def _send_all(to_email: str) -> None:
    for template_name, subject, context in _cases():
        await send_email(to_email, subject, template_name=template_name, context=context)
        print(f"sent {template_name.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send smoke-test versions of every SW Couriers mailer template.")
    parser.add_argument("--to", required=True, help="Recipient email address")
    args = parser.parse_args()
    asyncio.run(_send_all(args.to))


if __name__ == "__main__":
    main()
