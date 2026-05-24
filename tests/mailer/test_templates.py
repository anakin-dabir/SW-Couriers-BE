"""Test email template rendering.

Verifies that all mailer templates render without error and produce
expected HTML structure and content. Does not send real email.
"""

from __future__ import annotations

import pytest

from app.mailer.client import EmailTemplateName, render

# ── Context fixtures matching production usage ─────────────────────────────


@pytest.fixture
def password_reset_context() -> dict:
    """Context used by send_password_reset_email_task."""
    return {
        "name": "Jane",
        "reset_otp": "847291",
        "expires_minutes": 15,
    }


@pytest.fixture
def email_verification_context() -> dict:
    """Context used by AuthService._send_verification_email."""
    return {
        "name": "Alice",
        "link": "https://portal.example.com/verify-email?token=xyz789",
    }


@pytest.fixture
def invite_new_user_context() -> dict:
    """Context used by send_invite_email_task (_invite_context)."""
    return {
        "name": "Bob",
        "invite_link": "https://portal.example.com/set-password?token=invite456",
        "expires_days": 7,
        "day_label": "days",
    }


@pytest.fixture
def support_issued_password_context() -> dict:
    """Context used by send_support_issued_password_email_task."""
    return {
        "name": "Chris",
        "temporary_password": "TempP@ssw0rd9",
    }


# ── Per-template render tests ──────────────────────────────────────────────


def test_render_password_reset(password_reset_context: dict) -> None:
    """Password reset template renders with expected content."""
    html = render(EmailTemplateName.PASSWORD_RESET, password_reset_context)
    assert html
    assert "Reset your password" in html
    assert "Jane" in html
    assert "15 minutes" in html
    assert "847291" in html
    assert "one-time" in html.lower()
    assert "None" not in html


def test_render_password_reset_with_empty_name() -> None:
    """Password reset with empty name still renders (caller applies 'there' fallback in production)."""
    context = {
        "name": "",
        "reset_otp": "000000",
        "expires_minutes": 15,
    }
    html = render(EmailTemplateName.PASSWORD_RESET, context)
    assert "Hello" in html
    assert "15 minutes" in html


def test_render_email_verification(email_verification_context: dict) -> None:
    """Email verification template renders with expected content."""
    html = render(EmailTemplateName.EMAIL_VERIFICATION, email_verification_context)
    assert html
    assert "Verify your account" in html
    assert "Alice" in html
    assert "https://portal.example.com/verify-email?token=xyz789" in html
    assert "Verify email" in html
    assert "None" not in html


def test_render_invite_new_user(invite_new_user_context: dict) -> None:
    """Invite new user template renders with expected content."""
    html = render(EmailTemplateName.INVITE_NEW_USER, invite_new_user_context)
    assert html
    assert "Set up your account" in html
    assert "Bob" in html
    assert "https://portal.example.com/set-password?token=invite456" in html
    assert "7 days" in html
    assert "Set up your account" in html
    assert "None" not in html


def test_render_support_issued_password(support_issued_password_context: dict) -> None:
    """Support-issued temporary password template renders with expected content."""
    html = render(EmailTemplateName.SUPPORT_ISSUED_PASSWORD, support_issued_password_context)
    assert html
    assert "Chris" in html
    assert "TempP@ssw0rd9" in html
    assert "temporary password" in html.lower()
    assert "None" not in html


# ── Shared structure and safety ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("template_name", "context"),
    [
        (
            EmailTemplateName.PASSWORD_RESET,
            {
                "name": "T",
                "reset_otp": "111111",
                "expires_minutes": 15,
            },
        ),
        (
            EmailTemplateName.EMAIL_VERIFICATION,
            {"name": "T", "link": "https://example.com/v"},
        ),
        (
            EmailTemplateName.INVITE_NEW_USER,
            {
                "name": "T",
                "invite_link": "https://example.com/i",
                "expires_days": 1,
                "day_label": "day",
            },
        ),
        (
            EmailTemplateName.SUPPORT_ISSUED_PASSWORD,
            {"name": "T", "temporary_password": "Ab3!xYz9Qw"},
        ),
    ],
)
def test_all_templates_render_without_error(
    template_name: EmailTemplateName,
    context: dict,
) -> None:
    """Every template renders without raising."""
    html = render(template_name, context)
    assert isinstance(html, str)
    assert len(html) > 100


def test_rendered_html_has_doctype_and_title() -> None:
    """Rendered output is valid HTML with doctype and title."""
    html = render(
        EmailTemplateName.PASSWORD_RESET,
        {
            "name": "X",
            "reset_otp": "222222",
            "expires_minutes": 15,
        },
    )
    assert html.strip().lower().startswith("<!doctype html>")
    assert "<title>" in html
    assert "</title>" in html


def test_rendered_html_includes_sw_couriers_branding() -> None:
    """All templates include SW Couriers branding."""
    contexts = [
        (
            EmailTemplateName.PASSWORD_RESET,
            {"name": "X", "reset_otp": "333333", "expires_minutes": 15},
        ),
        (EmailTemplateName.EMAIL_VERIFICATION, {"name": "X", "link": "https://x.com"}),
        (
            EmailTemplateName.INVITE_NEW_USER,
            {"name": "X", "invite_link": "https://x.com", "expires_days": 1, "day_label": "day"},
        ),
        (
            EmailTemplateName.SUPPORT_ISSUED_PASSWORD,
            {"name": "X", "temporary_password": "Secr3t!Tmp"},
        ),
    ]
    for template_name, context in contexts:
        html = render(template_name, context)
        assert "SW Couriers" in html, f"{template_name} should contain SW Couriers"


def test_rendered_html_escapes_context_values() -> None:
    """User-provided context is HTML-escaped to prevent XSS."""
    context = {
        "name": "<script>alert(1)</script>",
        "reset_otp": "444444",
        "expires_minutes": 15,
    }
    html = render(EmailTemplateName.PASSWORD_RESET, context)
    # Script tag must not appear as raw HTML (Jinja2 autoescape turns it into &lt;script&gt;)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_rendered_html_contains_logo_branding() -> None:
    """Base template includes logo branding even when no hosted logo URL is configured."""
    html = render(
        EmailTemplateName.PASSWORD_RESET,
        {
            "name": "X",
            "reset_otp": "555555",
            "expires_minutes": 15,
        },
    )
    assert "SW" in html
    assert "COURIERS" in html
