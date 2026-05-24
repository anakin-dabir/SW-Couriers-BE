"""Invoice PDF generation (ARQ task). Renders template, generates PDF, uploads to R2, updates artifact.

Flow: load invoice + relations -> build context (must match signature in service) -> render Jinja2 HTML ->
WeasyPrint to PDF -> upload to R2 -> set artifact status READY and r2_file_key. On failure, mark FAILED
and optionally retry. r2_file_key is set here in the worker; signed URLs are generated on demand by the API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import structlog

from app.common.enums import LogEvent
from app.common.schemas import quantize_currency
from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.modules.invoices.repository import (
    CreditNotePdfArtifactRepository,
    CreditNoteRepository,
    InvoiceCreditApplicationRepository,
    InvoicePdfArtifactRepository,
    InvoiceRepository,
)
from app.storage.upload import upload_to_r2

logger = structlog.get_logger()

# Jinja2 template dir: app/modules/invoices/templates/{template_version}/invoice.html
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _format_date(d) -> str:
    """Format date as 'December 07, 2025'."""
    if d is None:
        return ""
    if hasattr(d, "strftime"):
        return d.strftime("%B %d, %Y")
    return str(d)


def _format_currency(amount: Decimal | int | float | str) -> str:
    """Format as £X,XXX.XX. Uses Decimal (quantized to 2dp) so no float rounding; display-only."""
    try:
        d = quantize_currency(amount)
        return f"£{d:,.2f}"
    except (TypeError, ValueError, ArithmeticError):
        return str(amount)


def _build_pdf_context(invoice, line_items: list, applications: list) -> dict:
    """Build Jinja2 context from invoice + line_items + applications. Field set must match _compute_pdf_signature in service for dedupe."""
    order = getattr(invoice, "order", None)
    org = getattr(invoice, "organization", None)

    order_id = ""
    if order:
        order_id = getattr(order, "order_id", "") or ""
    if order_id and not order_id.startswith("#"):
        order_id = "#" + order_id

    company_name = ""
    email = ""
    address = ""
    if org:
        company_name = getattr(org, "trading_name", "") or getattr(org, "legal_entity_name", "") or ""
        parts = [
            getattr(org, "reg_address_line_1", "") or "",
            getattr(org, "reg_address_line_2", None) or "",
            getattr(org, "reg_city", "") or "",
            getattr(org, "reg_state", None) or "",
            getattr(org, "reg_postcode", "") or "",
            getattr(org, "reg_country", None) or "United Kingdom",
        ]
        address = ", ".join(p for p in parts if p).strip()
    if order:
        email = getattr(order, "contact_email", "") or email

    line_data = [
        {
            "description": getattr(li, "description", ""),
            "quantity": getattr(li, "quantity", 0),
            "unit_price": str(getattr(li, "unit_price", 0)),
            "total_price": str(getattr(li, "total_price", 0)),
        }
        for li in line_items
    ]
    credit_data = [
        {
            "credit_note_number": getattr(getattr(a, "credit_note", None), "credit_note_number", ""),
            "applied_amount": str(a.applied_amount),
            "applied_at": a.applied_at.isoformat() if getattr(a.applied_at, "isoformat", None) else str(a.applied_at),
        }
        for a in applications
    ]

    credit_total = sum(a.applied_amount for a in applications)
    total_after_credit = invoice.total - credit_total  # Display: total minus applied credits

    packages_for_table = [
        {
            "tracking_id": f"#{i+1:02d}" if not getattr(li, "description", "").strip() else getattr(li, "description", ""),
            "weight": "—",
            "dimensions": "—",
            "amount": _format_currency(getattr(li, "total_price", 0)),
        }
        for i, li in enumerate(line_items)
    ]
    delivery_rows = [
        {
            "customer_name": getattr(order, "contact_name", "") if order else "—",
            "postcode": getattr(org, "reg_postcode", "—") if org else "—",
            "delivery_id": order_id or "—",
            "total_packages": f"{len(line_items):02d}" if line_items else "00",
            "total_weight": "—",
            "total_amount": _format_currency(invoice.total),
            "packages": packages_for_table,
        }
    ]
    if not delivery_rows and line_items:
        delivery_rows = [
            {
                "customer_name": "—",
                "postcode": "—",
                "delivery_id": order_id or "—",
                "total_packages": f"{len(line_items):02d}",
                "total_weight": "—",
                "total_amount": _format_currency(invoice.total),
                "packages": packages_for_table,
            }
        ]

    paid_on = ""
    payment_details = None

    return {
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "issued_on": _format_date(invoice.issue_date),
        "paid_on": paid_on,
        "subtotal": _format_currency(invoice.subtotal),
        "vat_rate": str(invoice.vat_rate),
        "vat_amount": _format_currency(invoice.vat_amount),
        "total": _format_currency(invoice.total),
        "status": invoice.status,
        "line_items": line_data,
        "credit_applications": credit_data,
        "total_after_credit": _format_currency(total_after_credit),
        "order_id": order_id or "—",
        "bill_to": {
            "company_name": company_name or "—",
            "email": email or "—",
            "address": address or "—",
        },
        "payment_details": payment_details,
        "delivery_rows": delivery_rows,
        "company_footer": {
            "email": "shiftopus@gmail.com",
            "contact": "+44 7700 900123",
            "address": "55 Bridge End, Cardiff, CF10 2BN, United Kingdom",
        },
    }


def _render_html(template_version: str, context: dict) -> str:
    """Render invoice HTML with Jinja2. Template path: templates/{template_version}/invoice.html."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_path = _TEMPLATES_DIR / template_version
    if not templates_path.is_dir():
        raise FileNotFoundError(f"Invoice template dir not found: {templates_path}")
    env = Environment(
        loader=FileSystemLoader(str(templates_path)),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    template = env.get_template("invoice.html")
    return template.render(**context)


def _html_to_pdf(html_content: str) -> bytes:
    """Convert HTML to PDF using weasyprint."""
    from weasyprint import HTML

    pdf_bytes = HTML(string=html_content).write_pdf()
    if pdf_bytes is None:
        raise RuntimeError("WeasyPrint returned no PDF bytes")
    return pdf_bytes


async def generate_invoice_pdf_task(
    ctx: dict,
    invoice_id: str,
    artifact_id: str,
    template_version: str,
) -> None:
    """Load invoice, render HTML, generate PDF, upload to R2, mark artifact READY or FAILED."""
    async with get_async_session() as session:
        invoice_repo = InvoiceRepository(session)
        artifact_repo = InvoicePdfArtifactRepository(session)
        credit_app_repo = InvoiceCreditApplicationRepository(session)

        invoice = await invoice_repo.get_with_relations(invoice_id, organization_id=None)
        if invoice is None:
            await _mark_artifact_failed(artifact_repo, artifact_id, "INVOICE_NOT_FOUND", f"Invoice {invoice_id} not found")
            return

        artifact = await artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            logger.warning(LogEvent.ARQ_JOB_FAILED, job="generate_invoice_pdf_task", artifact_id=artifact_id, error="Artifact not found")
            return

        try:
            applications = await credit_app_repo.list_for_invoice(invoice_id)
            line_items = list(invoice.line_items) if invoice.line_items else []
            context = _build_pdf_context(invoice, line_items, applications)
            html_content = _render_html(template_version, context)
            pdf_bytes = _html_to_pdf(html_content)
        except FileNotFoundError as e:
            await _mark_artifact_failed(artifact_repo, artifact_id, "TEMPLATE_NOT_FOUND", str(e))
            await session.commit()
            return
        except Exception as e:
            await _mark_artifact_failed(artifact_repo, artifact_id, type(e).__name__, str(e))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e

        r2_key = f"invoices/{invoice_id}/artifacts/{artifact_id}.pdf"
        try:
            await upload_to_r2(r2_key, pdf_bytes, "application/pdf")
        except Exception as e:
            # Transient: retry; persist FAILED so client can poll
            await _mark_artifact_failed(artifact_repo, artifact_id, "UPLOAD_FAILED", str(e))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e

        # Persist R2 key in DB here (worker). Signed URLs are generated on demand from this key; the URL itself is not stored.
        await artifact_repo.update_by_id(
            artifact_id,
            {
                "status": "READY",
                "r2_file_key": r2_key,
                "generated_at": datetime.now(UTC),
            },
        )
        await session.commit()
        logger.info(
            LogEvent.ARQ_JOB_ENQUEUED,
            job="generate_invoice_pdf_task",
            invoice_id=invoice_id,
            artifact_id=artifact_id,
            r2_key=r2_key,
        )


async def _mark_artifact_failed(artifact_repo: InvoicePdfArtifactRepository, artifact_id: str, error_code: str, error_message: str) -> None:
    """Set artifact status to FAILED and store error details."""
    await artifact_repo.update_by_id(
        artifact_id,
        {
            "status": "FAILED",
            "error_code": error_code[:50] if error_code else None,
            "error_message": (error_message[:500]) if error_message else None,
        },
    )


def _build_credit_note_html(credit_note, applications: list) -> str:
    applied_total = sum((a.applied_amount for a in applications), Decimal("0"))
    remaining = quantize_currency(Decimal(credit_note.total_credit_amount) - Decimal(applied_total))
    app_rows = ""
    for app in applications:
        inv = getattr(app, "invoice", None)
        inv_num = getattr(inv, "invoice_number", "") if inv else ""
        app_rows += (
            f"<tr><td>{inv_num}</td><td>{_format_currency(app.applied_amount)}</td>"
            f"<td>{_format_date(app.applied_at)}</td></tr>"
        )
    if not app_rows:
        app_rows = "<tr><td colspan='3'>No applications yet</td></tr>"
    reason = getattr(credit_note, "reason", None) or "-"
    return f"""
    <html>
      <body style='font-family: Arial, sans-serif; padding: 24px;'>
        <h1>Credit Note {credit_note.credit_note_number}</h1>
        <p>Issue Date: {_format_date(credit_note.issue_date)}</p>
        <p>Status: {credit_note.status}</p>
        <p>Reason Category: {getattr(credit_note, "reason_category", "OTHER")}</p>
        <p>Reason: {reason}</p>
        <hr/>
        <p>Total Credit: {_format_currency(credit_note.total_credit_amount)}</p>
        <p>Applied: {_format_currency(applied_total)}</p>
        <p>Remaining: {_format_currency(remaining)}</p>
        <h3>Applications</h3>
        <table border='1' cellspacing='0' cellpadding='6'>
          <tr><th>Invoice</th><th>Amount</th><th>Applied At</th></tr>
          {app_rows}
        </table>
      </body>
    </html>
    """.strip()


async def generate_credit_note_pdf_task(
    ctx: dict,
    credit_note_id: str,
    artifact_id: str,
    template_version: str,
) -> None:
    """Generate credit-note PDF, upload to R2, mark artifact status."""
    _ = template_version
    async with get_async_session() as session:
        credit_repo = CreditNoteRepository(session)
        artifact_repo = CreditNotePdfArtifactRepository(session)
        app_repo = InvoiceCreditApplicationRepository(session)

        credit_note = await credit_repo.get_with_relations(credit_note_id, organization_id=None)
        if credit_note is None:
            await _mark_credit_artifact_failed(artifact_repo, artifact_id, "CREDIT_NOTE_NOT_FOUND", f"Credit note {credit_note_id} not found")
            await session.commit()
            return
        artifact = await artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            logger.warning(LogEvent.ARQ_JOB_FAILED, job="generate_credit_note_pdf_task", artifact_id=artifact_id, error="Artifact not found")
            return
        try:
            applications = await app_repo.list_for_credit_note(credit_note_id)
            html_content = _build_credit_note_html(credit_note, applications)
            pdf_bytes = _html_to_pdf(html_content)
        except Exception as e:
            await _mark_credit_artifact_failed(artifact_repo, artifact_id, type(e).__name__, str(e))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e
        r2_key = f"credit-notes/{credit_note_id}/artifacts/{artifact_id}.pdf"
        try:
            await upload_to_r2(r2_key, pdf_bytes, "application/pdf")
        except Exception as e:
            await _mark_credit_artifact_failed(artifact_repo, artifact_id, "UPLOAD_FAILED", str(e))
            await session.commit()
            raise retry_backoff(ctx.get("job_try", 1), base=60) from e
        await artifact_repo.update_by_id(
            artifact_id,
            {
                "status": "READY",
                "r2_file_key": r2_key,
                "generated_at": datetime.now(UTC),
            },
        )
        await session.commit()


async def _mark_credit_artifact_failed(
    artifact_repo: CreditNotePdfArtifactRepository,
    artifact_id: str,
    error_code: str,
    error_message: str,
) -> None:
    await artifact_repo.update_by_id(
        artifact_id,
        {
            "status": "FAILED",
            "error_code": error_code[:50] if error_code else None,
            "error_message": (error_message[:500]) if error_message else None,
        },
    )
