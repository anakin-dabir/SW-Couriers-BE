"""Content sanitization for notification templates.

- sanitize_email_html: cleans dangerous HTML, keeps safe formatting tags
- strip_html_to_text: removes all HTML tags for SMS storage
- plain_text_to_html: converts \\n line breaks to <br> for email display
- wrap_email_html: wraps body content in the branded email layout via Jinja2
"""

import re

import nh3
import structlog

from app.common.enums.logger import LogEvent

logger = structlog.get_logger()

_SAFE_TAGS = {
    "p",
    "br",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "s",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "span",
    "div",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "blockquote",
    "hr",
}

_SAFE_ATTRS: dict[str, set[str]] = {
    "*": {"style", "class"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
SMS_SEGMENT_LIMIT = 160


def sanitize_email_html(html: str) -> str:
    """Strip dangerous tags/attributes from HTML, keep safe formatting."""
    return nh3.clean(html, tags=_SAFE_TAGS, attributes=_SAFE_ATTRS)


def strip_html_to_text(html: str) -> str:
    """Remove all HTML tags, returning plain text for SMS."""
    text = nh3.clean(html, tags=set())
    return text.strip()


def plain_text_to_html(text: str) -> str:
    """Convert plain text with \\n to HTML with <br> tags.

    Only converts if the text has no existing HTML tags (i.e. hardcoded defaults).
    Already-HTML content (admin overrides) passes through unchanged.
    """
    if _HTML_TAG_RE.search(text):
        return text
    return text.replace("\n", "<br>")


def check_sms_length(rendered_body: str, event: str | None = None) -> None:
    """Log a warning if a rendered SMS exceeds the single-segment limit."""
    length = len(rendered_body)
    if length > SMS_SEGMENT_LIMIT:
        segments = -(-length // SMS_SEGMENT_LIMIT)
        logger.warning(
            LogEvent.SMS_EXCEEDS_SEGMENT_LIMIT,
            length=length,
            segments=segments,
            event=event,
        )


def wrap_email_html(body: str) -> str:
    """Wrap rendered email body in the branded layout (notification_base.html).

    Uses the Jinja2 template engine from app.mailer.client so the notification
    emails share the same base.html header/footer as auth emails.
    """
    from app.mailer.client import EmailTemplateName
    from app.mailer.client import render as render_template

    html_body = plain_text_to_html(body)
    return render_template(EmailTemplateName.NOTIFICATION_BASE, {"content": html_body})
