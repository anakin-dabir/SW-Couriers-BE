"""Invoice business logic: CRUD, finalize, void, write-off, PDF request and signed URL.

- create_draft / create_and_finalize: single-step create; finalize uses one INSERT with status=SENT.
- update_draft: only DRAFT; strips lifecycle status from payload; quantizes amounts to 2 dp.
- finalize: DRAFT -> SENT; idempotent if already SENT.
- void / write_off: append immutable outcome events; reason required; recorded in events and audit.
- request_pdf: dedupe by signature; enqueue worker; r2_file_key set by worker after upload.
- Signed URLs: generated on demand from r2_file_key; not stored.
All currency amounts are quantized to 2 decimal places before persist.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from fastapi import Request
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.core.queue import QueuePriority, enqueue
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.billing.models import BillingPaymentAllocation
from app.modules.billing.repository import (
    BillingPaymentAllocationRepository,
    BillingPaymentRepository,
    RefundRepository,
)
from app.modules.invoices.enums import InvoiceEventType, InvoiceStatus, PaymentStatus
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication, InvoiceLineItem, InvoicePdfArtifact
from app.modules.invoices.repository import (
    InvoiceCreditApplicationRepository,
    InvoiceEventRepository,
    InvoicePdfArtifactRepository,
    InvoiceRepository,
)
from app.modules.invoices.v1.schemas import PaymentStatusLiteral
from app.storage.r2_client import generate_presigned_url

# Currency: persist with 2 decimal places (ROUND_HALF_UP)
_CURRENCY_TWO_PLACES = Decimal("0.01")


def _quantize(d: Decimal) -> Decimal:
    """Round amount to 2 decimal places for DB consistency."""
    return d.quantize(_CURRENCY_TWO_PLACES, rounding=ROUND_HALF_UP)


# PDF: template version used for rendering; signed URL expiry for download links
PDF_TEMPLATE_VERSION = "v0-placeholder"
PDF_GENERATING_STALE_MINUTES = 45
SIGNED_URL_EXPIRY_SECONDS = 300  # 5 minutes


def _compute_pdf_signature(invoice: Invoice, line_items: list, applications: list[InvoiceCreditApplication], template_version: str) -> str:
    """Canonical hash of all PDF-visible data for dedupe. Must match worker context."""
    payload: dict[str, Any] = {
        "template_version": template_version,
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "subtotal": str(invoice.subtotal),
        "vat_rate": str(invoice.vat_rate),
        "vat_amount": str(invoice.vat_amount),
        "total": str(invoice.total),
        "status": invoice.status,
        "order_id": invoice.order_id,
    }
    payload["line_items"] = sorted(
        [
            {
                "description": getattr(li, "description", ""),
                "quantity": getattr(li, "quantity", 0),
                "unit_price": str(getattr(li, "unit_price", 0)),
                "total_price": str(getattr(li, "total_price", 0)),
            }
            for li in line_items
        ],
        key=lambda x: (x["description"], x["quantity"]),
    )
    payload["credit_applications"] = sorted(
        [
            {
                "credit_note_id": a.credit_note_id,
                "applied_amount": str(a.applied_amount),
                "applied_at": a.applied_at.isoformat() if getattr(a.applied_at, "isoformat", None) else str(a.applied_at),
            }
            for a in applications
        ],
        key=lambda x: (x["credit_note_id"], x["applied_at"]),
    )
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pdf_generating_is_stale(artifact: InvoicePdfArtifact) -> bool:
    """True when a GENERATING artifact is old enough that the worker likely never ran."""
    created = getattr(artifact, "created_at", None)
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return datetime.now(UTC) - created > timedelta(minutes=PDF_GENERATING_STALE_MINUTES)


def _scope(organization_id: str | None) -> dict[str, str]:
    """RBAC scope for repo calls: org filter when B2B, empty when admin."""
    return {"organization_id": organization_id} if organization_id else {}


def _is_close_money(a: Decimal, b: Decimal) -> bool:
    return abs(_quantize(a) - _quantize(b)) <= _CURRENCY_TWO_PLACES


def compute_payment_status(
    invoice: Invoice,
    *,
    paid_amount: Decimal,
    credit_total: Decimal = Decimal("0"),
    outcome_event_type: str | None = None,
) -> PaymentStatusLiteral:
    """Resolve payment status from event overrides + billing allocations + due date."""
    if outcome_event_type == InvoiceEventType.VOIDED.value:
        return PaymentStatus.VOID.value
    if outcome_event_type == InvoiceEventType.WRITTEN_OFF.value:
        return PaymentStatus.WRITTEN_OFF.value
    total_after_credit = invoice.total - credit_total
    balance = total_after_credit - paid_amount
    today = date.today()
    due = getattr(invoice, "due_date", None)
    if balance <= 0:
        return PaymentStatus.PAID.value
    if paid_amount > 0:
        return PaymentStatus.PARTIALLY_PAID.value
    if due and due < today:
        return PaymentStatus.OVERDUE.value
    return PaymentStatus.UNPAID.value


class InvoiceService(BaseService):
    """Invoice CRUD, lifecycle, and PDF. Enforces org scope for B2B."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._invoice_repo = InvoiceRepository(session)
        self._event_repo = InvoiceEventRepository(session)
        self._artifact_repo = InvoicePdfArtifactRepository(session)
        self._credit_app_repo = InvoiceCreditApplicationRepository(session)
        self._billing_alloc_repo = BillingPaymentAllocationRepository(session)
        self._billing_payment_repo = BillingPaymentRepository(session)
        self._refund_repo = RefundRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._user_agent = request.headers.get("user-agent") if request else None

    @staticmethod
    def _provider_label(provider: str | None) -> str:
        p = str(provider or "").upper()
        labels = {
            "BRAINTREE": "Card Payment",
            "BANK_TRANSFER": "Bank Transfer",
            "CHEQUE": "Cheque",
            "MANUAL": "Manual",
            "OTHER": "Other",
        }
        return labels.get(p, "Manual")

    async def _enqueue_qb_invoice_sync(
        self,
        *,
        organization_id: str | None,
        invoice_id: str,
        version: int,
        trigger_source: str = "invoice.sync",
        correlation_id: str | None = None,
        business: dict | None = None,
    ) -> None:
        """Queue QuickBooks invoice sync for organization-scoped invoices."""
        if not organization_id:
            return
        from app.integrations.quickbooks.service import QuickBooksService

        await QuickBooksService(self._session).enqueue_invoice_sync(
            organization_id=organization_id,
            invoice_id=invoice_id,
            trigger_source=trigger_source,
            correlation_id=correlation_id,
            business=business,
        )

    async def _log_audit(
        self,
        action: str,
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.BILLING,
        event_type: AuditEventType | str = AuditEventType.INVOICE_GENERATED,
    ) -> None:
        await self._audit.log(
            action=action,
            entity_type="invoice",
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=old_value,
            new_value=new_value,
            ip_address=self._ip,
            user_agent=self._user_agent,
            reason=reason,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    @staticmethod
    def _derive_vat_rate(subtotal: Decimal, vat_amount: Decimal) -> Decimal:
        if subtotal <= Decimal("0"):
            if vat_amount > Decimal("0"):
                raise ValidationError("VAT amount cannot be positive when subtotal is zero")
            return Decimal("0")
        return _quantize((vat_amount / subtotal) * Decimal("100"))

    def _validate_order_amounts(self, *, subtotal: Decimal, vat_amount: Decimal, total: Decimal) -> None:
        if subtotal < 0 or vat_amount < 0 or total < 0:
            raise ValidationError("Invoice amounts must be non-negative")
        expected_total = _quantize(subtotal + vat_amount)
        if not _is_close_money(expected_total, total):
            raise ValidationError("Order totals mismatch: subtotal + vat_amount must equal total")

    @staticmethod
    def _build_order_line_items(order: Any, stops: list[Any], packages_by_stop: dict[str, list[Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        subtotal = _quantize(Decimal(getattr(order, "subtotal", 0) or 0))
        breakdown = getattr(order, "price_breakdown", None) or {}
        package_lines = breakdown.get("packages") if isinstance(breakdown, dict) else None

        if isinstance(package_lines, list) and package_lines:
            for idx, pkg in enumerate(package_lines, start=1):
                qty = int(pkg.get("quantity") or 1)
                unit = _quantize(Decimal(str(pkg.get("unit_price") or pkg.get("amount") or 0)))
                total_price = _quantize(Decimal(str(pkg.get("amount") or (unit * qty))))
                items.append(
                    {
                        "package_id": pkg.get("package_id"),
                        "description": str(pkg.get("description") or f"Package {idx}"),
                        "quantity": qty,
                        "unit_price": unit,
                        "total_price": total_price,
                        "line_type": str(pkg.get("line_type") or "service"),
                    }
                )
        else:
            all_packages: list[Any] = []
            for stop in stops:
                all_packages.extend(packages_by_stop.get(stop.id, []))
            if all_packages and subtotal == Decimal("0"):
                for pkg in all_packages:
                    label = getattr(pkg, "package_id", None) or getattr(pkg, "id", None) or "package"
                    items.append(
                        {
                            "package_id": getattr(pkg, "id", None),
                            "description": f"Package {label}",
                            "quantity": 1,
                            "unit_price": Decimal("0"),
                            "total_price": Decimal("0"),
                            "line_type": "service",
                        }
                    )
            else:
                ref = getattr(order, "order_id", None) or getattr(order, "id", "")
                items.append(
                    {
                        "package_id": None,
                        "description": f"Order service {ref}",
                        "quantity": 1,
                        "unit_price": subtotal,
                        "total_price": subtotal,
                        "line_type": "service",
                    }
                )
        return items

    async def _replace_line_items(self, invoice_id: str, items: list[dict[str, Any]]) -> None:
        await self._session.execute(delete(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice_id))
        for item in items:
            self._session.add(
                InvoiceLineItem(
                    invoice_id=invoice_id,
                    package_id=item.get("package_id"),
                    description=item["description"],
                    quantity=int(item["quantity"]),
                    unit_price=_quantize(Decimal(item["unit_price"])),
                    total_price=_quantize(Decimal(item["total_price"])),
                    line_type=item.get("line_type") or "service",
                )
            )
        await self._session.flush()

    async def has_pdf_history(self, invoice_id: str) -> bool:
        """Return True when at least one PDF artifact exists for invoice."""
        latest = await self._artifact_repo.get_latest_for_invoice(invoice_id)
        return latest is not None

    async def request_pdf_if_previously_generated(self, invoice_id: str, organization_id: str | None = None) -> None:
        """Regenerate PDF only for invoices that already have artifact history."""
        if not await self.has_pdf_history(invoice_id):
            return
        await self.request_pdf(invoice_id, organization_id=organization_id)

    async def sync_from_order(
        self,
        *,
        order: Any,
        stops: list[Any],
        packages_by_stop: dict[str, list[Any]],
        issue_date: date | None = None,
        due_date: date | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """Create/update order-linked invoice and line items with strong validation."""
        invoice_issue_date = issue_date or date.today()
        invoice_due_date = due_date or invoice_issue_date
        if invoice_due_date < invoice_issue_date:
            raise ValidationError("due_date must be on or after issue_date")
        subtotal = _quantize(Decimal(getattr(order, "subtotal", 0) or 0))
        vat_amount = _quantize(Decimal(getattr(order, "vat_amount", 0) or 0))
        total = _quantize(Decimal(getattr(order, "total_amount", 0) or 0))
        self._validate_order_amounts(subtotal=subtotal, vat_amount=vat_amount, total=total)
        vat_rate = self._derive_vat_rate(subtotal, vat_amount)
        items = self._build_order_line_items(order, stops, packages_by_stop)
        line_subtotal = _quantize(sum((Decimal(i["total_price"]) for i in items), Decimal("0")))
        if not _is_close_money(line_subtotal, subtotal):
            raise ValidationError("Invoice line items subtotal does not match order subtotal")

        existing = await self._invoice_repo.get_by_order_id(order.id, organization_id=order.organization_id)
        if existing is None:
            invoice = await self.create_draft(
                order_id=order.id,
                organization_id=order.organization_id,
                customer_id=order.customer_id,
                issue_date=invoice_issue_date,
                due_date=invoice_due_date,
                subtotal=subtotal,
                vat_rate=vat_rate,
                vat_amount=vat_amount,
                total=total,
                notes="Auto-generated from order",
                audit_user_id=audit_user_id,
                audit_user_role=audit_user_role,
            )
            await self._replace_line_items(invoice.id, items)
            return invoice

        if existing.payment_status in {PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value}:
            raise ConflictError("Cannot update an invoice that is voided or written off")

        update_data = {
            "issue_date": invoice_issue_date,
            "due_date": invoice_due_date,
            "subtotal": subtotal,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "total": total,
            "notes": "Auto-synced from order",
        }
        updated = await self._invoice_repo.update_by_id(existing.id, update_data, expected_version=existing.version)
        await self._replace_line_items(existing.id, items)
        await self._event_repo.append(existing.id, InvoiceEventType.DRAFT_SAVED, actor_id=audit_user_id, actor_role=audit_user_role)
        await self.recompute_payment_projection(existing.id)
        try:
            await self.request_pdf_if_previously_generated(existing.id, organization_id=existing.organization_id)
        except Exception:
            # PDF failures are handled by artifact statuses; do not block order flow.
            pass
        if updated.organization_id and updated.status == InvoiceStatus.SENT:
            await self._enqueue_qb_invoice_sync(
                organization_id=updated.organization_id,
                invoice_id=updated.id,
                version=updated.version,
            )
        return updated

    async def create_draft(
        self,
        *,
        order_id: str | None,
        organization_id: str | None,
        customer_id: str | None,
        issue_date: date,
        due_date: date,
        subtotal: Decimal,
        vat_rate: Decimal,
        vat_amount: Decimal,
        total: Decimal,
        notes: str | None = None,
        billing_contact_email: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """Create a new draft invoice with next INV-NNNNNN. One invoice per order enforced at finalize."""
        if due_date < issue_date:
            raise ValidationError("Due date must be on or after issue date")
        if total < 0:
            raise ValidationError("Total must be non-negative")
        if subtotal < 0 or vat_amount < 0:
            raise ValidationError("Subtotal and VAT amount must be non-negative")
        invoice_number = await self._invoice_repo.next_invoice_number()
        data = {
            "invoice_number": invoice_number,
            "order_id": order_id,
            "organization_id": organization_id,
            "customer_id": customer_id,
            "issue_date": issue_date,
            "due_date": due_date,
            "subtotal": _quantize(subtotal),
            "vat_rate": _quantize(vat_rate),
            "vat_amount": _quantize(vat_amount),
            "total": _quantize(total),
            "status": InvoiceStatus.DRAFT,
            "paid_amount": Decimal("0"),
            "payment_status": PaymentStatus.UNPAID,
            "braintree_transaction_id": None,
            "notes": notes,
            "billing_contact_email": (billing_contact_email or "").strip() or None,
        }
        invoice = await self._invoice_repo.create(data)
        await self._event_repo.append(invoice.id, InvoiceEventType.CREATED, actor_id=audit_user_id, actor_role=audit_user_role)
        await self._log_audit(
            "invoice.created",
            entity_id=invoice.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"invoice_number": invoice.invoice_number},
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_GENERATED,
        )
        return invoice

    async def create_and_finalize(
        self,
        *,
        order_id: str | None,
        organization_id: str | None,
        customer_id: str | None,
        issue_date: date,
        due_date: date,
        subtotal: Decimal,
        vat_rate: Decimal,
        vat_amount: Decimal,
        total: Decimal,
        notes: str | None = None,
        billing_contact_email: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
        queue_qb_sync: bool = True,
    ) -> Invoice:
        """Create invoice with status SENT in a single INSERT (faster than create_draft + update). Appends CREATED and FINALIZED events and audit."""
        if due_date < issue_date:
            raise ValidationError("Due date must be on or after issue date")
        if total < 0:
            raise ValidationError("Total must be non-negative")
        if subtotal < 0 or vat_amount < 0:
            raise ValidationError("Subtotal and VAT amount must be non-negative")
        invoice_number = await self._invoice_repo.next_invoice_number()
        data = {
            "invoice_number": invoice_number,
            "order_id": order_id,
            "organization_id": organization_id,
            "customer_id": customer_id,
            "issue_date": issue_date,
            "due_date": due_date,
            "subtotal": _quantize(subtotal),
            "vat_rate": _quantize(vat_rate),
            "vat_amount": _quantize(vat_amount),
            "total": _quantize(total),
            "status": InvoiceStatus.SENT,
            "paid_amount": Decimal("0"),
            "payment_status": PaymentStatus.UNPAID,
            "braintree_transaction_id": None,
            "notes": notes,
            "billing_contact_email": (billing_contact_email or "").strip() or None,
            "qb_sync_status": "QUEUED" if organization_id else "NOT_SYNCED",
        }
        invoice = await self._invoice_repo.create(data)
        await self._event_repo.append(invoice.id, InvoiceEventType.CREATED, actor_id=audit_user_id, actor_role=audit_user_role)
        await self._event_repo.append(invoice.id, InvoiceEventType.FINALIZED, actor_id=audit_user_id, actor_role=audit_user_role)
        await self._log_audit(
            "invoice.created",
            entity_id=invoice.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"invoice_number": invoice.invoice_number},
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_GENERATED,
        )
        await self._log_audit(
            "invoice.finalized",
            entity_id=invoice.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"status": InvoiceStatus.DRAFT},
            new_value={"status": InvoiceStatus.SENT},
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_SENT,
        )
        if queue_qb_sync:
            await self._enqueue_qb_invoice_sync(
                organization_id=organization_id,
                invoice_id=invoice.id,
                version=invoice.version,
            )
        return invoice

    @staticmethod
    def _reversal_line_item_description(
        *,
        credit_note_number: str,
        applied_gross: Decimal,
        void_reason: str,
    ) -> str:
        """Single-line description for credit-note void reversal invoices (max 255 chars)."""
        text = (
            f"Reversal of credit note {credit_note_number} "
            f"(applied GBP {applied_gross:.2f}). Void reason: {void_reason.strip()}"
        )
        return text[:255]

    async def create_reversal_for_credit_note_void(
        self,
        *,
        credit_note: CreditNote,
        applied_total: Decimal,
        void_reason: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """Create a finalized reversal invoice when voiding an applied credit note."""
        from app.modules.billing.metrics import DEFAULT_VAT_RATE, split_vat_from_gross

        if not credit_note.organization_id:
            raise ValidationError("Credit note must belong to an organization")
        if not credit_note.customer_id:
            raise ValidationError("Credit note must have customer_id before void with applications")
        gross = _quantize(applied_total)
        subtotal, vat_amount, total = split_vat_from_gross(gross, DEFAULT_VAT_RATE)
        reason_clean = void_reason.strip()
        today = date.today()
        note = (
            f"Reversal of credit note {credit_note.credit_note_number}. "
            f"Void reason: {reason_clean}"
        )[:2000]
        line_description = self._reversal_line_item_description(
            credit_note_number=credit_note.credit_note_number,
            applied_gross=gross,
            void_reason=reason_clean,
        )
        invoice = await self.create_and_finalize(
            order_id=None,
            organization_id=credit_note.organization_id,
            customer_id=credit_note.customer_id,
            issue_date=today,
            due_date=today,
            subtotal=subtotal,
            vat_rate=DEFAULT_VAT_RATE,
            vat_amount=vat_amount,
            total=total,
            notes=note,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
            queue_qb_sync=False,
        )
        await self._replace_line_items(
            invoice.id,
            [
                {
                    "description": line_description,
                    "quantity": 1,
                    "unit_price": str(subtotal),
                    "total_price": str(subtotal),
                    "line_type": "service",
                }
            ],
        )
        from app.integrations.quickbooks.sync_logging import correlation_id_for_void_credit_note

        saga_id = correlation_id_for_void_credit_note(
            organization_id=credit_note.organization_id,
            credit_note_id=credit_note.id,
        )
        await self._enqueue_qb_invoice_sync(
            organization_id=credit_note.organization_id,
            invoice_id=invoice.id,
            version=invoice.version,
            trigger_source="billing.void_credit_note_reversal",
            correlation_id=saga_id,
            business={
                "credit_note_id": credit_note.id,
                "credit_note_number": credit_note.credit_note_number,
                "reversal_invoice_id": invoice.id,
            },
        )
        return await self._invoice_repo.get_by_id_or_404(invoice.id, organization_id=credit_note.organization_id)

    async def update_draft(
        self,
        invoice_id: str,
        data: dict[str, Any],
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """Update draft-only fields. 409 if not DRAFT. Cannot change status via update."""
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        if invoice.status != InvoiceStatus.DRAFT:
            raise ConflictError("Only draft invoices can be updated")
        # PATCH must not change status (lifecycle only via dedicated actions)
        data = {k: v for k, v in data.items() if k != "status"}
        # Quantize currency fields so DB always has 2 decimal places
        for key in ("subtotal", "vat_rate", "vat_amount", "total"):
            if key in data and isinstance(data[key], Decimal):
                data[key] = _quantize(data[key])
        issue_date = data.get("issue_date", invoice.issue_date)
        due_date = data.get("due_date", invoice.due_date)
        if due_date < issue_date:
            raise ValidationError("Due date must be on or after issue date")
        updated = await self._invoice_repo.update_by_id(invoice_id, data, expected_version=invoice.version, **scope)
        await self._event_repo.append(invoice_id, InvoiceEventType.DRAFT_SAVED, actor_id=audit_user_id, actor_role=audit_user_role)
        if {"subtotal", "vat_rate", "vat_amount", "total", "due_date"} & set(data.keys()):
            return await self.recompute_payment_projection(invoice_id)
        return updated

    async def _assert_draft_invoice_deletable(self, invoice_id: str) -> None:
        """Block hard-delete when FK RESTRICT or business rules would fail."""
        if await self._session.scalar(
            select(exists().where(BillingPaymentAllocation.invoice_id == invoice_id))
        ):
            raise ConflictError("Cannot delete invoice with payment allocations")
        if await self._session.scalar(
            select(exists().where(InvoiceCreditApplication.invoice_id == invoice_id))
        ):
            raise ConflictError("Cannot delete invoice with applied credit notes")
        if await self._session.scalar(
            select(exists().where(CreditNote.reversal_invoice_id == invoice_id))
        ):
            raise ConflictError("Cannot delete invoice referenced as a credit-note reversal")

    async def delete_draft(
        self,
        invoice_id: str,
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Hard-delete a DRAFT invoice and cascaded events, line items, and PDF artifacts."""
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        if invoice.status != InvoiceStatus.DRAFT:
            raise ConflictError("Only draft invoices can be deleted")
        await self._assert_draft_invoice_deletable(invoice_id)
        snapshot = {
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "organization_id": invoice.organization_id,
            "total": str(invoice.total),
        }
        await self._invoice_repo.hard_delete(invoice_id, **scope)
        await self._log_audit(
            "invoice.draft_deleted",
            entity_id=invoice_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value=snapshot,
            severity="WARNING",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_STATUS_CHANGED,
        )

    async def get_invoice_internal_note(self, invoice_id: str, organization_id: str | None) -> Invoice:
        scope = _scope(organization_id)
        return await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)

    @staticmethod
    def display_internal_note(notes: str | None) -> str | None:
        """Normalize stored notes for API responses (None when unset or whitespace-only)."""
        if notes is None:
            return None
        cleaned = notes.strip()
        return cleaned or None

    @staticmethod
    def _normalize_internal_note(notes: str) -> str:
        cleaned = (notes or "").strip()
        if not cleaned:
            raise ValidationError("Internal note must not be blank")
        if len(cleaned) > 2000:
            raise ValidationError("Internal note must be at most 2000 characters")
        return cleaned

    @classmethod
    def has_internal_note(cls, invoice: Invoice) -> bool:
        return cls.display_internal_note(invoice.notes) is not None

    async def _persist_internal_note(
        self,
        invoice_id: str,
        *,
        notes_value: str | None,
        version: int,
        organization_id: str | None,
        audit_action: str,
        audit_user_id: str | None,
        audit_user_role: str | None,
        old_notes: str | None,
    ) -> Invoice:
        scope = _scope(organization_id)
        updated = await self._invoice_repo.update_by_id(
            invoice_id,
            {"notes": notes_value},
            expected_version=version,
            **scope,
        )
        await self._log_audit(
            audit_action,
            entity_id=invoice_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"notes": old_notes} if old_notes is not None else None,
            new_value={"notes": updated.notes},
            severity="WARNING" if audit_action.endswith("_deleted") else "INFO",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_STATUS_CHANGED,
        )
        if updated.organization_id and updated.status == InvoiceStatus.SENT.value:
            await self._enqueue_qb_invoice_sync(
                organization_id=updated.organization_id,
                invoice_id=updated.id,
                version=updated.version,
                trigger_source="invoice.internal_note_changed",
                business={"notes_changed": True},
            )
        return updated

    async def upsert_invoice_internal_note(
        self,
        invoice_id: str,
        *,
        notes: str,
        version: int,
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """Set or replace the internal note (idempotent when content unchanged)."""
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        normalized = self._normalize_internal_note(notes)
        current = self.display_internal_note(invoice.notes)
        if current == normalized:
            return invoice
        action = "invoice.internal_note_created" if current is None else "invoice.internal_note_updated"
        return await self._persist_internal_note(
            invoice_id,
            notes_value=normalized,
            version=version,
            organization_id=organization_id,
            audit_action=action,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
            old_notes=invoice.notes,
        )

    async def create_invoice_internal_note(
        self,
        invoice_id: str,
        *,
        notes: str,
        version: int,
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        if self.has_internal_note(invoice):
            raise ConflictError("Internal note already exists; use PUT to update")
        normalized = self._normalize_internal_note(notes)
        return await self._persist_internal_note(
            invoice_id,
            notes_value=normalized,
            version=version,
            organization_id=organization_id,
            audit_action="invoice.internal_note_created",
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
            old_notes=invoice.notes,
        )

    async def update_invoice_internal_note(
        self,
        invoice_id: str,
        *,
        notes: str,
        version: int,
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        """PUT upsert — create or replace; preferred for Edit Note UI."""
        return await self.upsert_invoice_internal_note(
            invoice_id,
            notes=notes,
            version=version,
            organization_id=organization_id,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def delete_invoice_internal_note(
        self,
        invoice_id: str,
        *,
        version: int,
        organization_id: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Invoice:
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        if not self.has_internal_note(invoice):
            return invoice
        return await self._persist_internal_note(
            invoice_id,
            notes_value=None,
            version=version,
            organization_id=organization_id,
            audit_action="invoice.internal_note_deleted",
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
            old_notes=invoice.notes,
        )

    async def finalize(self, invoice_id: str, organization_id: str | None, audit_user_id: str | None = None, audit_user_role: str | None = None) -> Invoice:
        """Set status to SENT. Record event. Idempotent if already SENT."""
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        if invoice.status != InvoiceStatus.DRAFT:
            if invoice.status == InvoiceStatus.SENT:
                return invoice
            raise ConflictError("Only draft invoices can be finalized")
        update_data: dict[str, Any] = {"status": InvoiceStatus.SENT}
        if invoice.organization_id:
            update_data["qb_sync_status"] = "QUEUED"
        await self._invoice_repo.update_by_id(invoice_id, update_data, expected_version=invoice.version, **scope)
        await self._event_repo.append(invoice_id, InvoiceEventType.FINALIZED, actor_id=audit_user_id, actor_role=audit_user_role)
        await self._log_audit(
            "invoice.finalized",
            entity_id=invoice_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"status": InvoiceStatus.DRAFT},
            new_value={"status": InvoiceStatus.SENT},
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_SENT,
        )
        updated = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        await self._enqueue_qb_invoice_sync(
            organization_id=updated.organization_id,
            invoice_id=updated.id,
            version=updated.version,
        )
        return updated

    async def void(self, invoice_id: str, reason: str, organization_id: str | None, audit_user_id: str | None = None, audit_user_role: str | None = None) -> Invoice:
        """Mark invoice as VOIDED via event. Reason required."""
        reason_clean = (reason or "").strip()
        if not reason_clean:
            raise ValidationError("Reason is required for void")
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        latest_outcome = await self._event_repo.latest_outcome_event_type(invoice_id)
        if latest_outcome in (InvoiceEventType.VOIDED.value, InvoiceEventType.WRITTEN_OFF.value):
            raise ConflictError("Invoice is already voided or written off")
        await self._invoice_repo.update_by_id(invoice_id, {}, expected_version=invoice.version, **scope)
        await self._event_repo.append(invoice_id, InvoiceEventType.VOIDED, reason=reason_clean, actor_id=audit_user_id, actor_role=audit_user_role)
        if hasattr(self._session, "execute"):
            projected = await self.recompute_payment_projection(invoice_id)
        else:
            # Unit tests may inject lightweight session/repo stubs.
            projected = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        await self._log_audit(
            "invoice.voided",
            entity_id=invoice_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            reason=reason_clean,
            old_value={"outcome_event_type": latest_outcome},
            new_value={"payment_status": PaymentStatus.VOID},
            severity="WARNING",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_VOIDED,
        )
        await self._enqueue_qb_invoice_sync(
            organization_id=projected.organization_id,
            invoice_id=projected.id,
            version=projected.version,
        )
        return projected

    async def write_off(self, invoice_id: str, reason: str, organization_id: str | None, audit_user_id: str | None = None, audit_user_role: str | None = None) -> Invoice:
        """Mark invoice as WRITTEN_OFF via event. Reason required."""
        reason_clean = (reason or "").strip()
        if not reason_clean:
            raise ValidationError("Reason is required for write-off")
        scope = _scope(organization_id)
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        latest_outcome = await self._event_repo.latest_outcome_event_type(invoice_id)
        if latest_outcome in (InvoiceEventType.VOIDED.value, InvoiceEventType.WRITTEN_OFF.value):
            raise ConflictError("Invoice is already voided or written off")
        await self._invoice_repo.update_by_id(invoice_id, {}, expected_version=invoice.version, **scope)
        await self._event_repo.append(invoice_id, InvoiceEventType.WRITTEN_OFF, reason=reason_clean, actor_id=audit_user_id, actor_role=audit_user_role)
        if hasattr(self._session, "execute"):
            projected = await self.recompute_payment_projection(invoice_id)
        else:
            # Unit tests may inject lightweight session/repo stubs.
            projected = await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        await self._log_audit(
            "invoice.written_off",
            entity_id=invoice_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            reason=reason_clean,
            old_value={"outcome_event_type": latest_outcome},
            new_value={"payment_status": PaymentStatus.WRITTEN_OFF},
            severity="WARNING",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.INVOICE_VOIDED,
        )
        await self._enqueue_qb_invoice_sync(
            organization_id=projected.organization_id,
            invoice_id=projected.id,
            version=projected.version,
        )
        return projected

    async def list_invoices(
        self,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[str] | None = None,
        payment_status: list[str] | None = None,
        show_draft: bool = False,
        invoiced_from: date | None = None,
        invoiced_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        period: str | None = None,
        organization_id: str | None = None,
        customer_id: str | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[Invoice], int]:
        """Paginated list with filters. Pass organization_id/customer_id for tenant scoping."""
        return await self._invoice_repo.list_invoices(
            page=page,
            size=size,
            search=search,
            status=cast(Any, status),
            payment_status=cast(Any, payment_status),
            show_draft=show_draft,
            invoiced_from=invoiced_from,
            invoiced_to=invoiced_to,
            due_from=due_from,
            due_to=due_to,
            period=period,
            organization_id=organization_id,
            customer_id=customer_id,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def refund_summaries_for_invoice_ids(self, invoice_ids: list[str]) -> dict[str, dict[str, object]]:
        return await self._refund_repo.summarize_refunds_by_invoice_ids(invoice_ids)

    async def invoice_ids_with_open_dispute(self, invoice_ids: list[str]) -> set[str]:
        return await self._billing_payment_repo.invoice_ids_with_open_payment_dispute(invoice_ids)

    async def summary_invoices(
        self,
        *,
        search: str | None = None,
        status: list[str] | None = None,
        payment_status: list[str] | None = None,
        show_draft: bool = False,
        invoiced_from: date | None = None,
        invoiced_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        period: str | None = None,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict[str, int]:
        return await self._invoice_repo.summary_invoices(
            search=search,
            status=cast(Any, status),
            payment_status=cast(Any, payment_status),
            show_draft=show_draft,
            invoiced_from=invoiced_from,
            invoiced_to=invoiced_to,
            due_from=due_from,
            due_to=due_to,
            period=period,
            organization_id=organization_id,
            customer_id=customer_id,
        )

    async def list_invoice_payments(
        self,
        *,
        invoice_id: str,
        page: int = 1,
        size: int = 20,
        organization_id: str | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        rows, total = await self._billing_payment_repo.list_for_invoice(
            invoice_id=invoice_id,
            page=page,
            size=size,
            organization_id=organization_id,
        )
        for row in rows:
            row["method"] = self._provider_label(cast(str | None, row.get("provider")))
        return rows, total

    async def latest_invoice_payment_method(self, *, invoice_id: str, organization_id: str | None = None) -> str | None:
        provider = await self._billing_payment_repo.latest_method_for_invoice(invoice_id=invoice_id, organization_id=organization_id)
        return self._provider_label(provider) if provider else None

    async def get_invoice(
        self,
        invoice_id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> Invoice:
        """Get invoice by id. Optional org/customer scope for tenant boundary."""
        invoice = await self._invoice_repo.get_by_id_with_order(
            invoice_id,
            organization_id=organization_id,
            customer_id=customer_id,
        )
        if invoice is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        return invoice

    async def get_invoice_detail(
        self,
        invoice_id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> Invoice | None:
        """Get invoice detail with relations under optional org/customer scope."""
        return await self._invoice_repo.get_with_relations(
            invoice_id,
            organization_id=organization_id,
            customer_id=customer_id,
        )

    async def get_credit_applied_total(self, invoice_id: str) -> Decimal:
        """Sum of applied credit for this invoice."""
        return await self._credit_app_repo.get_applied_total_for_invoice(invoice_id)

    async def credit_totals_for_invoice_ids(self, invoice_ids: list[str]) -> dict[str, Decimal]:
        """Batch sum of credit-note applications per invoice (for list balance)."""
        return await self._credit_app_repo.totals_applied_for_invoices(invoice_ids)

    async def get_allocated_paid_total(self, invoice_id: str) -> Decimal:
        """Stored paid_amount projection for this invoice."""
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id)
        return invoice.paid_amount

    async def get_outcome_event_type(self, invoice_id: str) -> str | None:
        return await self._event_repo.latest_outcome_event_type(invoice_id)

    async def recompute_payment_projection(self, invoice_id: str) -> Invoice:
        """Recompute and persist paid_amount/payment_status projections on invoice."""
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id)
        paid_total = _quantize(await self._billing_alloc_repo.total_allocated_for_invoice(invoice_id))
        credit_total = _quantize(await self._credit_app_repo.get_applied_total_for_invoice(invoice_id))
        outcome_event_type = await self._event_repo.latest_outcome_event_type(invoice_id)
        payment_status = compute_payment_status(
            invoice,
            paid_amount=paid_total,
            credit_total=credit_total,
            outcome_event_type=outcome_event_type,
        )
        return await self._invoice_repo.update_by_id(
            invoice_id,
            {
                "paid_amount": paid_total,
                "payment_status": payment_status,
            },
            expected_version=invoice.version,
        )

    async def request_pdf(
        self,
        invoice_id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], InvoicePdfArtifact | None]:
        """Request PDF generation. Returns (response_payload, artifact). If READY artifact exists for current signature, return it; else create artifact and enqueue job."""
        from app.common.enums import Job

        invoice = await self._invoice_repo.get_with_relations(
            invoice_id,
            organization_id=organization_id,
            customer_id=customer_id,
        )
        if invoice is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        applications = await self._credit_app_repo.list_for_invoice(invoice_id)
        line_items = list(invoice.line_items) if invoice.line_items else []
        signature_hash = _compute_pdf_signature(invoice, line_items, applications, PDF_TEMPLATE_VERSION)

        # Dedupe: if we already have a READY PDF for this exact data, return it (no new job)
        ready = await self._artifact_repo.get_ready_by_signature(invoice_id, PDF_TEMPLATE_VERSION, signature_hash)
        if ready is not None:
            return (
                {
                    "status": "READY",
                    "job_id": None,
                    "error_code": None,
                    "error_message": None,
                    "artifact_id": ready.id,
                },
                ready,
            )

        # Dedupe in-flight work: when a matching artifact is already GENERATING, reuse it.
        generating = await self._artifact_repo.get_generating_by_signature(invoice_id, PDF_TEMPLATE_VERSION, signature_hash)
        if generating is not None:
            if _pdf_generating_is_stale(generating):
                await self._expire_stale_pdf_artifact(generating)
                await self._session.flush()
            else:
                return (
                    {
                        "status": "GENERATING",
                        "job_id": generating.job_id,
                        "error_code": None,
                        "error_message": None,
                        "artifact_id": generating.id,
                    },
                    generating,
                )

        # New artifact + enqueue worker; r2_file_key will be set by worker after upload
        pdf_version = await self._artifact_repo.get_next_pdf_version(invoice_id)
        artifact = await self._artifact_repo.create(
            {
                "invoice_id": invoice_id,
                "template_version": PDF_TEMPLATE_VERSION,
                "signature_hash": signature_hash,
                "pdf_version": pdf_version,
                "status": "GENERATING",
            }
        )
        job = await enqueue(
            Job.GENERATE_INVOICE_PDF,
            invoice_id=invoice_id,
            artifact_id=artifact.id,
            template_version=PDF_TEMPLATE_VERSION,
            _job_id=self._pdf_job_id(invoice_id=invoice_id, signature_hash=signature_hash, idempotency_key=idempotency_key),
            priority=QueuePriority.LOW,
        )
        job_id = job.job_id if job else None
        if job_id:
            artifact.job_id = job_id
            await self._session.flush()

        return (
            {
                "status": "GENERATING",
                "job_id": job_id,
                "error_code": None,
                "error_message": None,
                "artifact_id": artifact.id,
            },
            artifact,
        )

    @staticmethod
    def _pdf_job_id(*, invoice_id: str, signature_hash: str, idempotency_key: str | None) -> str:
        """Stable queue id for duplicate-safe PDF generation retries."""
        sig_part = signature_hash[:12]
        idem_raw = (idempotency_key or "").strip()
        idem_part = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()[:12] if idem_raw else "noidem"
        return f"invpdf:{invoice_id}:{sig_part}:{idem_part}"

    async def get_pdf_status(
        self,
        invoice_id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """Current PDF status for polling."""
        scope = _scope(organization_id)
        if customer_id is None:
            await self._invoice_repo.get_by_id_or_404(invoice_id, **scope)
        else:
            invoice = await self._invoice_repo.get_by_id_with_order(
                invoice_id,
                organization_id=organization_id,
                customer_id=customer_id,
            )
            if invoice is None:
                raise NotFoundError(resource="invoice", id=invoice_id)
        latest = await self._artifact_repo.get_latest_for_invoice(invoice_id)
        if latest is None:
            return {"status": "NOT_REQUESTED", "job_id": None, "error_code": None, "error_message": None, "artifact_id": None}
        if latest.status == "GENERATING" and _pdf_generating_is_stale(latest):
            await self._expire_stale_pdf_artifact(latest)
            await self._session.flush()
        return {
            "status": latest.status,
            "job_id": latest.job_id,
            "error_code": latest.error_code,
            "error_message": latest.error_message,
            "artifact_id": latest.id,
        }

    async def _expire_stale_pdf_artifact(self, artifact: InvoicePdfArtifact) -> None:
        await self._artifact_repo.update_by_id(
            artifact.id,
            {
                "status": "FAILED",
                "error_code": "GENERATION_TIMEOUT",
                "error_message": "PDF generation timed out. Request the PDF again to retry.",
            },
        )
        artifact.status = "FAILED"
        artifact.error_code = "GENERATION_TIMEOUT"
        artifact.error_message = "PDF generation timed out. Request the PDF again to retry."

    def get_signed_url(self, r2_file_key: str, disposition: str = "attachment", expiry_seconds: int = SIGNED_URL_EXPIRY_SECONDS) -> str:
        """Generate short-lived signed URL for PDF. disposition: inline | attachment."""
        return generate_presigned_url(r2_file_key, expiry_seconds=expiry_seconds, content_type="application/pdf")
