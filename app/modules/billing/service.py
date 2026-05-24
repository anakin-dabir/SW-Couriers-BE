"""Billing service layer (foundation)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import ClientType, Job, UserRole
from app.common.enums.logger import LogEvent
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.core.queue import QueuePriority, enqueue
from app.integrations.braintree import (
    BraintreeDisputeStatus,
    BraintreeTransactionStatus,
    normalize_braintree_dispute_status,
    get_braintree_gateway,
    normalize_braintree_status,
    refund_or_void_transaction,
)
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.billing.enums import (
    AllocationStatus,
    PaymentEventType,
    PaymentProvider,
    PaymentRecordStatus,
    RefundEventType,
    RefundMethod,
    RefundReasonCategory,
    RefundStatus,
    RefundType,
)
from app.modules.billing.models import BillingPayment, BillingPaymentAllocation, Refund, RefundEvent
from app.modules.billing.repository import (
    BillingPaymentAllocationRepository,
    BillingPaymentEventRepository,
    BillingPaymentRepository,
    RefundEventRepository,
    RefundRepository,
)
from app.modules.invoices.enums import InvoiceStatus
from app.modules.invoices.models import CreditNote, Invoice, InvoiceCreditApplication
from app.modules.organizations.models import Organization
from app.modules.invoices.repository import (
    CreditNotePdfArtifactRepository,
    CreditNoteRepository,
    InvoiceCreditApplicationRepository,
    InvoiceEventRepository,
    InvoiceRepository,
)
from app.modules.invoices.service import PDF_TEMPLATE_VERSION, SIGNED_URL_EXPIRY_SECONDS, compute_payment_status
from app.modules.user.models import User

_MONEY_QUANT = Decimal("0.01")

logger = structlog.get_logger()

REMITTANCE_SIGNED_URL_EXPIRY_SECONDS = 300
CREDIT_NOTE_PDF_TEMPLATE_VERSION = PDF_TEMPLATE_VERSION

_CONTENT_TYPE_TO_SUFFIX = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}


class B2bCreditNoteCustomerFilterMode(Enum):
    ALL_IN_ORG = "all_in_org"
    SPECIFIC_CUSTOMER = "specific_customer"
    UNASSIGNED_ONLY = "unassigned_only"


@dataclass(frozen=True)
class B2bCreditNoteCustomerFilter:
    mode: B2bCreditNoteCustomerFilterMode
    customer_id: str | None = None


def parse_b2b_credit_note_customer_filter(customer_id: str | None) -> B2bCreditNoteCustomerFilter:
    """Parse B2B list `customer_id` query: omitted=all org, empty=unassigned only, UUID=one customer."""
    if customer_id is None:
        return B2bCreditNoteCustomerFilter(mode=B2bCreditNoteCustomerFilterMode.ALL_IN_ORG)
    if customer_id == "":
        return B2bCreditNoteCustomerFilter(mode=B2bCreditNoteCustomerFilterMode.UNASSIGNED_ONLY)
    return B2bCreditNoteCustomerFilter(
        mode=B2bCreditNoteCustomerFilterMode.SPECIFIC_CUSTOMER,
        customer_id=customer_id.strip(),
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _safe_remittance_filename(name: str, *, max_len: int = 200) -> str:
    base = (name or "remittance").strip()
    base = base.replace("\\", "_").replace("/", "_")
    base = "".join(c for c in base if c.isprintable() and c not in '<>:"|?*\x00')
    base = base[:max_len].strip(".") or "remittance"
    base = re.sub(r"\s+", " ", base).strip()
    return base or "remittance"


def _remittance_object_suffix(content_type: str) -> str:
    try:
        return _CONTENT_TYPE_TO_SUFFIX[content_type]
    except KeyError as exc:
        raise ValidationError(f"Unsupported remittance content type: {content_type}") from exc


# How many invoices to pull from `list_invoices` per DB round-trip when scanning allocation candidates.
_INVOICE_ALLOCATION_CANDIDATE_LIST_BATCH = 200


class BillingService(BaseService):
    """Core billing payment operations.

    This is intentionally introduced as a foundation service first; API routes
    and QuickBooks payment-sync orchestration are added in subsequent steps.
    """

    def __init__(self, session: AsyncSession, request=None) -> None:
        super().__init__(session, request)
        self._payment_repo = BillingPaymentRepository(session)
        self._allocation_repo = BillingPaymentAllocationRepository(session)
        self._event_repo = BillingPaymentEventRepository(session)
        self._refund_repo = RefundRepository(session)
        self._refund_event_repo = RefundEventRepository(session)
        self._invoice_repo = InvoiceRepository(session)
        self._invoice_event_repo = InvoiceEventRepository(session)
        self._credit_app_repo = InvoiceCreditApplicationRepository(session)
        self._credit_note_repo = CreditNoteRepository(session)
        self._credit_note_pdf_repo = CreditNotePdfArtifactRepository(session)
        self._audit = AuditService(session)

    async def list_payment_history(
        self,
        *,
        organization_id: str | None,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[str] | None = None,
        allocation_status: list[str] | None = None,
        provider: list[str] | None = None,
        payment_date_from: date | None = None,
        payment_date_to: date | None = None,
    ) -> tuple[list[BillingPayment], int]:
        items, total = await self._payment_repo.list_history(
            organization_id=organization_id,
            page=page,
            size=size,
            search=search,
            status=status,
            allocation_status=allocation_status,
            provider=provider,
            payment_date_from=payment_date_from,
            payment_date_to=payment_date_to,
        )
        await self._attach_organization_labels_to_payments(items)
        return items, total

    async def _attach_organization_labels_to_payments(self, payments: list[BillingPayment]) -> None:
        """Populate ``_organization_reference`` / ``_organization_trading_name`` for list responses (global admin view)."""
        if not payments:
            return
        org_ids = list({str(p.organization_id) for p in payments})
        stmt = select(Organization.id, Organization.reference, Organization.trading_name).where(Organization.id.in_(org_ids))
        rows = (await self._session.execute(stmt)).all()
        labels = {str(oid): (ref, tname) for oid, ref, tname in rows}
        for p in payments:
            ref, tname = labels.get(str(p.organization_id), (None, None))
            setattr(p, "_organization_reference", ref)
            setattr(p, "_organization_trading_name", tname)

    async def _attach_allocation_invoice_snapshots(self, *, organization_id: str, allocations: list) -> None:
        """Augment allocation ORM rows with invoice totals/outstanding for payment detail (same balance math as allocation candidates)."""
        if not allocations:
            return
        invoice_ids = [a.invoice_id for a in allocations]
        stmt = select(Invoice).where(Invoice.id.in_(invoice_ids), Invoice.organization_id == organization_id)
        invoices = list((await self._session.execute(stmt)).scalars().all())
        inv_by_id = {str(i.id): i for i in invoices}
        paid_by_inv = await self._allocation_repo.totals_allocated_for_invoices(invoice_ids)
        credit_by_inv = await self._credit_app_repo.totals_applied_for_invoices(invoice_ids)
        for a in allocations:
            inv = inv_by_id.get(str(a.invoice_id))
            iid = str(a.invoice_id)
            if inv is None:
                setattr(a, "_detail_invoice_number", None)
                setattr(a, "_detail_invoice_total", Decimal("0"))
                setattr(a, "_detail_invoice_remaining", Decimal("0"))
                setattr(a, "_detail_invoice_issue_date", None)
                continue
            paid_total = _money(Decimal(str(paid_by_inv.get(iid, 0))))
            credit_total = _money(Decimal(str(credit_by_inv.get(iid, 0))))
            remaining_raw = _money(inv.total - credit_total - paid_total)
            remaining = remaining_raw if remaining_raw > Decimal("0") else Decimal("0")
            setattr(a, "_detail_invoice_number", inv.invoice_number)
            setattr(a, "_detail_invoice_total", inv.total)
            setattr(a, "_detail_invoice_remaining", remaining)
            setattr(a, "_detail_invoice_issue_date", inv.issue_date)

    async def payment_kpis(
        self,
        *,
        organization_id: str | None,
        search: str | None = None,
        status: list[str] | None = None,
        allocation_status: list[str] | None = None,
        provider: list[str] | None = None,
        payment_date_from: date | None = None,
        payment_date_to: date | None = None,
    ) -> dict[str, Decimal]:
        return await self._payment_repo.payment_kpis(
            organization_id=organization_id,
            search=search,
            status=status,
            allocation_status=allocation_status,
            provider=provider,
            payment_date_from=payment_date_from,
            payment_date_to=payment_date_to,
        )

    async def apply_braintree_payment_status(
        self,
        *,
        billing_payment_id: str,
        braintree_transaction_id: str,
        braintree_status: str,
        metadata_json: dict | None = None,
    ) -> BillingPayment | None:
        payment_id = str(billing_payment_id).strip()
        tx_id = str(braintree_transaction_id).strip()
        status = normalize_braintree_status(braintree_status)
        if not payment_id or not status:
            return None
        payment = await self._payment_repo.find_one(id=payment_id)
        if payment is None:
            return None
        updated = await self._payment_repo.update_by_id(
            payment.id,
            {
                "braintree_status": status,
                "braintree_status_updated_at": datetime.now(UTC),
            }
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.BRAINTREE_STATUS_CHANGED.value,
                "actor_id": None,
                "payload_json": {
                    "provider": PaymentProvider.BRAINTREE.value,
                    "billing_payment_id": payment.id,
                    "provider_txn_id": tx_id,
                    "braintree_status": status,
                    "metadata": metadata_json or {},
                },
            }
        )
        return updated

    async def apply_braintree_dispute_status(
        self,
        *,
        billing_payment_id: str,
        braintree_transaction_id: str,
        dispute_id: str,
        dispute_status: str,
        dispute_amount: Decimal | None = None,
        dispute_fee: Decimal | None = None,
        webhook_kind: str | None = None,
        metadata_json: dict | None = None,
    ) -> BillingPayment | None:
        payment_id = str(billing_payment_id).strip()
        tx_id = str(braintree_transaction_id).strip()
        local_dispute_id = str(dispute_id).strip()
        status = normalize_braintree_dispute_status(dispute_status)
        if not payment_id or not status or not local_dispute_id:
            return None
        payment = await self._payment_repo.find_one(id=payment_id)
        if payment is None:
            return None
        updates: dict[str, object] = {
            "dispute_status": status,
            "braintree_status_updated_at": datetime.now(UTC),
        }
        if dispute_amount is not None:
            updates["dispute_amount"] = _money(dispute_amount)
        if dispute_fee is not None:
            updates["dispute_fee"] = _money(dispute_fee)
        updated = await self._payment_repo.update_by_id(payment.id, updates)
        event_type_map = {
            BraintreeDisputeStatus.OPEN.value: PaymentEventType.BRAINTREE_DISPUTE_OPENED.value,
            BraintreeDisputeStatus.DISPUTED.value: PaymentEventType.BRAINTREE_DISPUTE_DISPUTED.value,
            BraintreeDisputeStatus.UNDER_REVIEW.value: PaymentEventType.BRAINTREE_DISPUTE_UNDER_REVIEW.value,
            BraintreeDisputeStatus.WON.value: PaymentEventType.BRAINTREE_DISPUTE_WON.value,
            BraintreeDisputeStatus.LOST.value: PaymentEventType.BRAINTREE_DISPUTE_LOST.value,
            BraintreeDisputeStatus.ACCEPTED.value: PaymentEventType.BRAINTREE_DISPUTE_ACCEPTED.value,
            BraintreeDisputeStatus.EXPIRED.value: PaymentEventType.BRAINTREE_DISPUTE_EXPIRED.value,
            BraintreeDisputeStatus.AUTO_ACCEPTED.value: PaymentEventType.BRAINTREE_DISPUTE_AUTO_ACCEPTED.value,
        }
        event_type = event_type_map.get(status, PaymentEventType.BRAINTREE_DISPUTE_CHANGED.value)
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": event_type,
                "actor_id": None,
                "payload_json": {
                    "provider": PaymentProvider.BRAINTREE.value,
                    "billing_payment_id": payment.id,
                    "provider_txn_id": tx_id,
                    "dispute_id": local_dispute_id,
                    "dispute_status": status,
                    "dispute_amount": str(dispute_amount) if dispute_amount is not None else None,
                    "dispute_fee": str(dispute_fee) if dispute_fee is not None else None,
                    "webhook_kind": webhook_kind,
                    "metadata": metadata_json or {},
                },
            }
        )
        return updated

    async def apply_braintree_dispute_won(
        self,
        *,
        billing_payment_id: str,
        braintree_transaction_id: str,
        dispute_id: str,
        webhook_kind: str | None = None,
        metadata_json: dict | None = None,
    ) -> BillingPayment | None:
        updated = await self.apply_braintree_dispute_status(
            billing_payment_id=billing_payment_id,
            braintree_transaction_id=braintree_transaction_id,
            dispute_id=dispute_id,
            dispute_status=BraintreeDisputeStatus.WON.value,
            webhook_kind=webhook_kind,
            metadata_json=metadata_json,
        )
        # TODO: trigger QuickBooks dispute-won reconciliation flow.
        return updated

    async def apply_braintree_dispute_lost(
        self,
        *,
        billing_payment_id: str,
        braintree_transaction_id: str,
        dispute_id: str,
        dispute_status: str = BraintreeDisputeStatus.LOST.value,
        webhook_kind: str | None = None,
        metadata_json: dict | None = None,
    ) -> BillingPayment | None:
        updated = await self.apply_braintree_dispute_status(
            billing_payment_id=billing_payment_id,
            braintree_transaction_id=braintree_transaction_id,
            dispute_id=dispute_id,
            dispute_status=dispute_status,
            webhook_kind=webhook_kind,
            metadata_json=metadata_json,
        )
        # TODO: trigger QuickBooks dispute-loss reconciliation flow.
        return updated

    async def apply_braintree_refund_status(
        self,
        *,
        refund_id: str,
        braintree_transaction_id: str,
        braintree_status: str,
        metadata_json: dict | None = None,
    ) -> Refund | None:
        local_refund_id = str(refund_id).strip()
        tx_id = str(braintree_transaction_id).strip()
        status = normalize_braintree_status(braintree_status)
        if not local_refund_id or not status:
            return None
        refund = await self._refund_repo.get_by_refund_id(local_refund_id)
        if refund is None:
            return None
        now = datetime.now(UTC)
        updates: dict[str, object] = {
            "braintree_status": status,
            "braintree_status_updated_at": now,
        }
        payload_json = {
            "provider": PaymentProvider.BRAINTREE.value,
            "braintree_transaction_id": tx_id,
            "braintree_status": status,
            "metadata": metadata_json or {},
        }
        updated = await self._refund_repo.update_by_id(
            refund.id,
            updates,
        )
        await self._refund_event_repo.create(
            {
                "refund_id": refund.id,
                "event_type": RefundEventType.BRAINTREE_STATUS_CHANGED.value,
                "actor_id": None,
                "payload_json": payload_json,
            }
        )
        # TODO: handling failed refund & quickbook sync is required here.
        logger.info(
            "billing.refund.braintree_status_applied",
            refund_id=updated.id,
            braintree_transaction_id=tx_id,
            braintree_status=status,
            status=updated.status,
        )
        return updated

    async def get_payment_detail(self, *, organization_id: str, payment_id: str) -> tuple[BillingPayment, list]:
        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        allocations = await self._allocation_repo.latest_for_payment(payment_id)
        await self._attach_allocation_invoice_snapshots(organization_id=organization_id, allocations=allocations)
        return payment, allocations

    async def payment_allocation_summaries(self, payment_ids: list[str]) -> dict[str, list[dict[str, object]]]:
        return await self._allocation_repo.summaries_for_payments(payment_ids)

    async def _validate_record_payment_payer(
        self,
        *,
        organization_id: str,
        customer_id: str,
        client_type: str,
    ) -> None:
        raw_ct = str(client_type).strip().upper()
        try:
            ct = ClientType(raw_ct)
        except ValueError:
            raise ValidationError(
                "Invalid client_type",
                details=[
                    {
                        "field": "client_type",
                        "message": "Must be CUSTOMER_B2B or CUSTOMER_B2C",
                        "type": "enum",
                    }
                ],
            ) from None
        if ct not in (ClientType.CUSTOMER_B2B, ClientType.CUSTOMER_B2C):
            raise ValidationError(
                "client_type must be CUSTOMER_B2B or CUSTOMER_B2C",
                details=[{"field": "client_type", "message": "Unsupported client type for payer", "type": "enum"}],
            )
        uid = str(customer_id).strip()
        if not uid:
            raise ValidationError(
                "customer_id is required",
                details=[{"field": "customer_id", "message": "Required", "type": "value_error"}],
            )
        try:
            UUID(uid)
        except ValueError:
            raise ValidationError(
                "Invalid customer_id",
                details=[
                    {
                        "field": "customer_id",
                        "message": (
                            "Must be a valid UUID — the payer **user** id (`users.id`), not an organisation id "
                            "or external reference."
                        ),
                        "type": "value_error.uuid",
                    }
                ],
            ) from None
        user = await self._session.get(User, uid)
        if user is None:
            mistaken_org = await self._session.get(Organization, uid)
            if mistaken_org is not None:
                raise ValidationError(
                    "Unknown customer_id",
                    details=[
                        {
                            "field": "customer_id",
                            "message": (
                                "This id is an **organisation**, not a user. Use the customer's "
                                "**CUSTOMER_B2B user** id for that client (org contact login user — "
                                "same value as invoice `customer_id`)."
                            ),
                            "type": "value_error",
                        }
                    ],
                )
            raise ValidationError(
                "Unknown customer_id",
                details=[
                    {
                        "field": "customer_id",
                        "message": (
                            "No user exists with this id. Use the payer account UUID (`users.id`): "
                            "for B2B, the client's portal user id in this organisation."
                        ),
                        "type": "value_error",
                    }
                ],
            )
        role_val = user.role if isinstance(user.role, str) else user.role.value
        if ct == ClientType.CUSTOMER_B2B:
            if role_val != UserRole.CUSTOMER_B2B.value:
                raise ValidationError(
                    "client_type does not match user role",
                    details=[
                        {
                            "field": "client_type",
                            "message": "Expected CUSTOMER_B2B account for this client type",
                            "type": "value_error",
                        }
                    ],
                )
            if not user.organization_id or str(user.organization_id) != str(organization_id):
                raise ValidationError(
                    "Customer is not a member of this organisation",
                    details=[{"field": "customer_id", "message": "Organisation mismatch", "type": "value_error"}],
                )
        elif ct == ClientType.CUSTOMER_B2C:
            if role_val != UserRole.CUSTOMER_B2C.value:
                raise ValidationError(
                    "client_type does not match user role",
                    details=[
                        {
                            "field": "client_type",
                            "message": "Expected CUSTOMER_B2C account for this client type",
                            "type": "value_error",
                        }
                    ],
                )

    @staticmethod
    def _validate_record_payment_org_scope_mode(*, client_type: str) -> None:
        raw_ct = str(client_type).strip().upper()
        try:
            ct = ClientType(raw_ct)
        except ValueError:
            raise ValidationError(
                "Invalid client_type",
                details=[
                    {
                        "field": "client_type",
                        "message": "Must be CUSTOMER_B2B or CUSTOMER_B2C",
                        "type": "enum",
                    }
                ],
            ) from None
        if ct == ClientType.CUSTOMER_B2C:
            raise ValidationError(
                "CUSTOMER_B2C record payment is out of scope for this endpoint",
                details=[
                    {
                        "field": "client_type",
                        "message": "Use CUSTOMER_B2B for org-scoped record payment.",
                        "type": "value_error",
                    }
                ],
            )
        if ct != ClientType.CUSTOMER_B2B:
            raise ValidationError(
                "client_type must be CUSTOMER_B2B",
                details=[
                    {
                        "field": "client_type",
                        "message": "Only CUSTOMER_B2B is supported in this record payment flow",
                        "type": "enum",
                    }
                ],
            )

    @staticmethod
    def _invoice_candidate_payment_status(inv: Invoice) -> str:
        if inv.payment_status == "UNPAID" and inv.due_date < date.today():
            return "OVERDUE"
        return str(inv.payment_status)

    async def list_invoice_allocation_candidates(
        self,
        *,
        organization_id: str,
        customer_id: str | None,
        client_type: str,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[dict[str, object]], int]:
        customer_filter = str(customer_id or "").strip() or None
        if customer_filter is not None:
            await self._validate_record_payment_payer(
                organization_id=organization_id,
                customer_id=customer_filter,
                client_type=client_type,
            )
        elif str(client_type).strip().upper() == ClientType.CUSTOMER_B2C.value:
            raise ValidationError(
                "customer_id is required for CUSTOMER_B2C invoice candidates",
                details=[
                    {
                        "field": "customer_id",
                        "message": "Required when client_type is CUSTOMER_B2C",
                        "type": "value_error",
                    }
                ],
            )
        offset = max(page - 1, 0) * size
        eligible_total = 0
        rows: list[dict[str, object]] = []
        db_page = 1

        while True:
            invoices, _ = await self._invoice_repo.list_invoices(
                page=db_page,
                size=_INVOICE_ALLOCATION_CANDIDATE_LIST_BATCH,
                search=search,
                status=[InvoiceStatus.SENT.value],
                payment_status=["UNPAID", "PARTIALLY_PAID", "OVERDUE"],
                show_draft=False,
                organization_id=organization_id,
                customer_id=customer_filter,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            if not invoices:
                break
            ids = [str(inv.id) for inv in invoices]
            paid_map = await self._allocation_repo.totals_allocated_for_invoices(ids)
            credit_map = await self._credit_app_repo.totals_applied_for_invoices(ids)
            void_map = await self._invoice_event_repo.latest_void_written_off_for_invoice_ids(ids)
            for inv in invoices:
                iid = str(inv.id)
                paid_total = _money(paid_map.get(iid, Decimal("0")))
                credit_total = _money(credit_map.get(iid, Decimal("0")))
                balance_due = _money(inv.total - credit_total - paid_total)
                if balance_due <= Decimal("0"):
                    continue
                if void_map.get(iid) in {"VOIDED", "WRITTEN_OFF"}:
                    continue
                if eligible_total >= offset and len(rows) < size:
                    rows.append(
                        {
                            "invoice_id": inv.id,
                            "invoice_number": inv.invoice_number,
                            "issue_date": inv.issue_date,
                            "due_date": inv.due_date,
                            "payment_status": self._invoice_candidate_payment_status(inv),
                            "balance_due": balance_due,
                        }
                    )
                eligible_total += 1
            db_page += 1

        return rows, eligible_total

    async def update_payment_notes(
        self,
        *,
        organization_id: str,
        payment_id: str,
        notes: str,
        actor_id: str | None,
        expected_version: int | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        if len(notes) > 500:
            raise ValidationError(
                "notes exceeds maximum length",
                details=[{"field": "notes", "message": "Maximum 500 characters", "type": "value_error"}],
            )
        ev = expected_version if expected_version is not None else payment.version
        updated = await self._payment_repo.update_by_id(
            payment.id,
            {"notes": notes},
            expected_version=ev,
            organization_id=organization_id,
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.NOTES_UPDATED.value,
                "actor_id": actor_id,
                "payload_json": {},
            }
        )
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.notes_updated",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={"notes_len": len(notes)},
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_NOTES_UPDATED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return updated

    async def void_payment(
        self,
        *,
        organization_id: str,
        payment_id: str,
        actor_id: str | None,
        reason: str | None = None,
        expected_version: int | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        if payment.status == PaymentRecordStatus.VOIDED.value:
            return payment

        allocated_total = _money(await self._allocation_repo.total_latest_allocated_for_payment(payment.id))
        if allocated_total > Decimal("0"):
            raise ValidationError(
                "Only unallocated payments can be voided",
                details=[
                    {
                        "field": "payment_id",
                        "message": "Payment has allocations and cannot be voided",
                        "type": "value_error",
                    }
                ],
            )

        ev = expected_version if expected_version is not None else payment.version
        updated = await self._payment_repo.update_by_id(
            payment.id,
            {"status": PaymentRecordStatus.VOIDED.value},
            expected_version=ev,
            organization_id=organization_id,
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.VOIDED.value,
                "actor_id": actor_id,
                "payload_json": {"from_status": payment.status, "to_status": updated.status, "reason": reason},
            }
        )
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.voided",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                old_value={"status": payment.status},
                new_value={"status": updated.status, "reason": reason},
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_VOIDED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return updated

    async def record_payment(
        self,
        *,
        organization_id: str,
        amount: Decimal,
        payment_date: date,
        client_type: str,
        recorded_by_id: str | None = None,
        customer_id: str | None = None,
        status: PaymentRecordStatus = PaymentRecordStatus.NOT_DEPOSITED,
        provider: PaymentProvider = PaymentProvider.MANUAL,
        provider_txn_id: str | None = None,
        transaction_fee: Decimal = Decimal("0"),
        braintree_status: str | None = None,
        notes: str | None = None,
        metadata_json: dict | None = None,
        audit_ctx: AuditContext | None = None,
        remittance_advice: tuple[bytes, str, str] | None = None,
    ) -> BillingPayment:
        self._validate_record_payment_org_scope_mode(client_type=client_type)
        amount_q = _money(amount)
        tx_fee_q = _money(transaction_fee)
        if amount_q <= 0:
            raise ValidationError(
                "amount must be greater than 0",
                details=[
                    {
                        "field": "amount",
                        "message": "Must be greater than 0",
                        "type": "value_error",
                    }
                ],
            )
        if tx_fee_q < 0:
            raise ValidationError("transaction_fee must be greater than or equal to 0")
        if notes is not None and len(notes) > 500:
            raise ValidationError(
                "notes exceeds maximum length",
                details=[{"field": "notes", "message": "Maximum 500 characters", "type": "value_error"}],
            )

        merged_meta: dict = {**(metadata_json or {})}
        merged_meta["recorded_client_type"] = str(client_type).strip().upper()
        legacy_customer_id = str(customer_id or "").strip() or None
        if legacy_customer_id is not None:
            merged_meta["deprecated_customer_id"] = legacy_customer_id

        payment = await self._payment_repo.create(
            {
                "organization_id": organization_id,
                "customer_id": None,
                "recorded_by_id": recorded_by_id,
                "amount": amount_q,
                "currency": "GBP",
                "status": status.value,
                "allocation_status": AllocationStatus.UNALLOCATED.value,
                "allocated_amount": Decimal("0"),
                "unallocated_amount": amount_q,
                "payment_date": payment_date,
                "provider": provider.value,
                "provider_txn_id": provider_txn_id,
                "transaction_fee": tx_fee_q,
                "braintree_status": (braintree_status or None),
                "braintree_status_updated_at": datetime.now(UTC) if braintree_status else None,
                "notes": notes,
                "metadata_json": merged_meta,
            }
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.RECORDED.value,
                "actor_id": recorded_by_id,
                "payload_json": {"amount": str(amount_q), "provider": provider.value, "status": status.value},
            }
        )
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.recorded",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "organization_id": organization_id,
                    "customer_id": None,
                    "amount": str(amount_q),
                    "status": status.value,
                    "provider": provider.value,
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        if remittance_advice is not None:
            content, ctype, filename = remittance_advice
            payment = await self._persist_remittance_advice(
                payment=payment,
                organization_id=organization_id,
                content=content,
                content_type=ctype,
                original_filename=filename,
                actor_id=recorded_by_id,
            )
        # QuickBooks payment sync requires at least one allocation — enqueue from add_or_revise_allocation only.
        return payment

    async def record_payment_with_allocations(
        self,
        *,
        organization_id: str,
        amount: Decimal,
        payment_date: date,
        client_type: str,
        recorded_by_id: str | None = None,
        customer_id: str | None = None,
        status: PaymentRecordStatus = PaymentRecordStatus.NOT_DEPOSITED,
        provider: PaymentProvider = PaymentProvider.MANUAL,
        provider_txn_id: str | None = None,
        transaction_fee: Decimal = Decimal("0"),
        braintree_status: str | None = None,
        notes: str | None = None,
        metadata_json: dict | None = None,
        audit_ctx: AuditContext | None = None,
        remittance_advice: tuple[bytes, str, str] | None = None,
        allocations: list[dict[str, object]] | None = None,
    ) -> BillingPayment:
        """Atomic helper for create + optional allocation use-cases."""
        async with self._session.begin_nested():
            payment = await self.record_payment(
                organization_id=organization_id,
                customer_id=customer_id,
                client_type=client_type,
                amount=amount,
                payment_date=payment_date,
                recorded_by_id=recorded_by_id,
                status=status,
                provider=provider,
                provider_txn_id=provider_txn_id,
                transaction_fee=transaction_fee,
                braintree_status=braintree_status,
                notes=notes,
                metadata_json=metadata_json,
                audit_ctx=audit_ctx,
                remittance_advice=remittance_advice,
            )
            try:
                if allocations:
                    await self.add_or_revise_allocations(
                        payment_id=payment.id,
                        allocations=allocations,
                        actor_id=recorded_by_id,
                        audit_ctx=audit_ctx,
                    )
            except Exception:
                # Nested transaction rolls DB writes back, but storage side-effects must be cleaned explicitly.
                key = str(getattr(payment, "remittance_advice_r2_key", "") or "").strip()
                if key:
                    try:
                        from app.storage.upload import delete_from_r2

                        await delete_from_r2(key)
                    except Exception as cleanup_exc:
                        logger.warning(
                            LogEvent.STORAGE_PROVIDER_ERROR,
                            provider="r2",
                            reason="remittance_cleanup_after_allocation_failure",
                            key=key,
                            error=str(cleanup_exc),
                        )
                raise
            return payment

    async def create_pending_payment(
        self,
        *,
        organization_id: str,
        amount: Decimal,
        payment_date: date,
        provider: PaymentProvider,
        customer_id: str | None = None,
        recorded_by_id: str | None = None,
        notes: str | None = None,
        metadata_json: dict | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        amount_q = _money(amount)
        if amount_q <= 0:
            raise ValidationError("amount must be greater than 0")
        payment = await self._payment_repo.create(
            {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "recorded_by_id": recorded_by_id,
                "amount": amount_q,
                "currency": "GBP",
                "status": PaymentRecordStatus.PENDING.value,
                "allocation_status": AllocationStatus.UNALLOCATED.value,
                "allocated_amount": Decimal("0"),
                "unallocated_amount": amount_q,
                "payment_date": payment_date,
                "provider": provider.value,
                "provider_txn_id": None,
                "transaction_fee": Decimal("0"),
                "braintree_status": None,
                "braintree_status_updated_at": None,
                "notes": notes,
                "metadata_json": metadata_json,
            }
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.RECORDED.value,
                "actor_id": recorded_by_id,
                "payload_json": {"amount": str(amount_q), "provider": provider.value, "status": PaymentRecordStatus.PENDING.value},
            }
        )
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.recorded",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "organization_id": organization_id,
                    "customer_id": customer_id,
                    "amount": str(amount_q),
                    "status": PaymentRecordStatus.PENDING.value,
                    "provider": provider.value,
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return payment

    async def mark_payment_status(
        self,
        *,
        organization_id: str,
        payment_id: str,
        to_status: PaymentRecordStatus,
        provider_txn_id: str | None = None,
        braintree_status: str | None = None,
        transaction_fee: Decimal | None = None,
        actor_id: str | None = None,
        queue_qb_sync: bool = False,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        if payment.status == PaymentRecordStatus.VOIDED.value and to_status != PaymentRecordStatus.VOIDED:
            raise ValidationError("Voided payments cannot transition to a different status")
        tx_fee_q = _money(transaction_fee) if transaction_fee is not None else Decimal(payment.transaction_fee or 0)
        if transaction_fee is not None and tx_fee_q < 0:
            raise ValidationError("transaction_fee must be greater than or equal to 0")
        normalized_braintree_status = normalize_braintree_status(braintree_status)
        now = datetime.now(UTC)
        resolved_provider_txn_id = provider_txn_id if provider_txn_id is not None else payment.provider_txn_id
        updated = await self._payment_repo.update_by_id(
            payment.id,
            {
                "status": to_status.value,
                "provider_txn_id": resolved_provider_txn_id,
                "transaction_fee": tx_fee_q,
                "braintree_status": normalized_braintree_status or payment.braintree_status,
                "braintree_status_updated_at": now if normalized_braintree_status else payment.braintree_status_updated_at,
            },
            expected_version=payment.version,
            organization_id=organization_id,
        )
        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.STATUS_CHANGED.value,
                "actor_id": actor_id,
                "payload_json": {
                    "from_status": payment.status,
                    "to_status": updated.status,
                    "provider_txn_id": resolved_provider_txn_id,
                    "braintree_status": normalized_braintree_status,
                },
            }
        )
        if queue_qb_sync:
            await self._enqueue_qb_payment_sync(payment_id=updated.id, organization_id=organization_id, version=updated.version)
        return updated

    async def list_refunds(
        self,
        *,
        organization_id: str,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[str] | None = None,
        refund_type: list[str] | None = None,
        refund_method: list[str] | None = None,
        reason_category: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        return await self._refund_repo.list_refunds(
            organization_id=organization_id,
            page=page,
            size=size,
            search=search,
            status=status,
            refund_type=refund_type,
            refund_method=refund_method,
            reason_category=reason_category,
            date_from=date_from,
            date_to=date_to,
        )

    async def refund_kpis(
        self,
        *,
        organization_id: str,
        status: list[str] | None = None,
        refund_type: list[str] | None = None,
        refund_method: list[str] | None = None,
        reason_category: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Decimal | int]:
        return await self._refund_repo.kpis(
            organization_id=organization_id,
            status=status,
            refund_type=refund_type,
            refund_method=refund_method,
            reason_category=reason_category,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_refund_detail(self, *, organization_id: str, refund_id: str):
        refund = await self._refund_repo.get_by_id_or_404(refund_id, organization_id=organization_id)
        events = list(
            (
                await self._session.execute(
                    select(RefundEvent)
                    .where(RefundEvent.refund_id == refund.id)
                    .order_by(RefundEvent.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return refund, events

    async def create_refund(
        self,
        *,
        organization_id: str,
        billing_payment_id: str,
        amount: Decimal,
        refund_type: RefundType,
        refund_method: RefundMethod,
        reason_category: RefundReasonCategory,
        reason_description: str,
        actor_id: str,
        invoice_id: str | None = None,
        linked_booking_ref: str | None = None,
        metadata_json: dict | None = None,
        idempotency_key: str | None = None,
    ):
        amount_q = _money(amount)
        if amount_q <= 0:
            raise ValidationError("amount must be greater than 0")
        if idempotency_key:
            existing = await self._refund_repo.find_one(organization_id=organization_id, idempotency_key=idempotency_key)
            if existing:
                return existing

        try:
            payment = await self._refund_repo.lock_payment(billing_payment_id, organization_id)
        except ValueError:
            raise NotFoundError(resource="billing_payment", id=billing_payment_id) from None
        if invoice_id is not None:
            await self._invoice_repo.get_by_id_or_404(invoice_id, organization_id=organization_id)

        already_refunded = _money(await self._refund_repo.total_non_reversed_refunded_for_payment(payment.id))
        remaining = _money(payment.amount - already_refunded)
        if remaining <= 0:
            raise ValidationError("Payment is already fully refunded")
        if refund_type == RefundType.FULL and amount_q != remaining:
            raise ValidationError("Full refund amount must equal remaining refundable amount")
        if refund_type == RefundType.PARTIAL and amount_q >= remaining:
            raise ValidationError("Partial refund amount must be less than remaining refundable amount")
        if refund_method == RefundMethod.CARD_REFUND and not str(payment.provider_txn_id or "").strip():
            raise ValidationError("Card refund requires payment.provider_txn_id")

        now = datetime.now(UTC)
        refund = await self._refund_repo.create(
            {
                "refund_number": await self._refund_repo.next_refund_number(),
                "organization_id": organization_id,
                "billing_payment_id": payment.id,
                "invoice_id": invoice_id,
                "linked_booking_ref": linked_booking_ref,
                "provider": payment.provider if refund_method == RefundMethod.CARD_REFUND else PaymentProvider.MANUAL.value,
                "refund_method": refund_method.value,
                "refund_type": refund_type.value,
                "status": RefundStatus.PROCESSING.value if refund_method == RefundMethod.CARD_REFUND else RefundStatus.INITIATED.value,
                "reason_category": reason_category.value,
                "reason_description": reason_description,
                "requested_amount": amount_q,
                "processed_amount": amount_q if refund_method != RefundMethod.CARD_REFUND else Decimal("0"),
                "currency": payment.currency,
                "initiated_by_id": actor_id,
                "initiated_at": now,
                "idempotency_key": idempotency_key,
                "metadata_json": metadata_json,
            }
        )
        if refund_method != RefundMethod.CARD_REFUND:
            refund = await self._refund_repo.update_by_id(
                refund.id,
                {
                    "status": RefundStatus.COMPLETED.value if refund_method == RefundMethod.CREDIT_NOTE else RefundStatus.INITIATED.value,
                    "completed_at": now if refund_method == RefundMethod.CREDIT_NOTE else None,
                    "processed_amount": amount_q if refund_method == RefundMethod.CREDIT_NOTE else Decimal("0"),
                    "processed_by_id": actor_id if refund_method == RefundMethod.CREDIT_NOTE else None,
                },
                expected_version=refund.version,
                organization_id=organization_id,
            )
        await self._refund_event_repo.create(
            {
                "refund_id": refund.id,
                "event_type": RefundEventType.INITIATED.value,
                "actor_id": actor_id,
                "payload_json": {"requested_amount": str(amount_q), "refund_method": refund_method.value},
            }
        )
        if refund_method == RefundMethod.CARD_REFUND:
            source_txn_id = str(payment.provider_txn_id or "").strip()

            reversal = refund_or_void_transaction(
                get_braintree_gateway(),
                transaction_id=source_txn_id,
                amount=amount_q,
                order_id=refund.id,
                owner_label=f"refund={refund.id}",
            )
            reversed_tx_id = str(reversal.transaction_id or "").strip() or None
            if not reversal.success:
                failed = await self._refund_repo.update_by_id(
                    refund.id,
                    {
                        "status": RefundStatus.FAILED.value,
                        "failure_message": reversal.message or "Braintree refund/void failed",
                        # Store source_txn_id when reversal txn id is missing so retry can call reversal again.
                        "braintree_transaction_id": reversed_tx_id or source_txn_id,
                        "braintree_status": BraintreeTransactionStatus.FAILED.value,
                        "braintree_status_updated_at": datetime.now(UTC),
                    },
                    expected_version=refund.version,
                    organization_id=organization_id,
                )
                await self._refund_event_repo.create(
                    {
                        "refund_id": failed.id,
                        "event_type": RefundEventType.FAILED.value,
                        "actor_id": actor_id,
                        "payload_json": {
                            "action": reversal.action,
                            "original_transaction_id": reversal.original_transaction_id,
                            "transaction_id": reversal.transaction_id,
                            "message": reversal.message,
                        },
                    }
                )
                return failed

            completed_at = datetime.now(UTC)
            completed = await self._refund_repo.update_by_id(
                refund.id,
                {
                    "status": RefundStatus.COMPLETED.value,
                    "processed_amount": amount_q,
                    "processed_by_id": actor_id,
                    "completed_at": completed_at,
                    "failure_code": None,
                    "failure_message": None,
                    "braintree_transaction_id": reversed_tx_id or source_txn_id,
                    "braintree_status": (
                        BraintreeTransactionStatus.VOIDED.value
                        if reversal.action == "void"
                        else BraintreeTransactionStatus.REFUND_SUBMITTED.value
                    ),
                    "braintree_status_updated_at": completed_at,
                },
                expected_version=refund.version,
                organization_id=organization_id,
            )
            await self._refund_event_repo.create(
                {
                    "refund_id": completed.id,
                    "event_type": RefundEventType.COMPLETED.value,
                    "actor_id": actor_id,
                    "payload_json": {
                        "action": reversal.action,
                        "original_transaction_id": reversal.original_transaction_id,
                        "transaction_id": reversal.transaction_id,
                    },
                }
            )
            return completed
        return refund

    async def mark_refund_complete(
        self,
        *,
        organization_id: str,
        refund_id: str,
        actor_id: str,
        braintree_status: str | None = None,
        note: str | None = None,
    ):
        refund = await self._refund_repo.get_by_id_or_404(refund_id, organization_id=organization_id)
        if refund.refund_method == RefundMethod.CARD_REFUND.value:
            raise ValidationError("Card refunds cannot be manually marked complete")
        if refund.status not in {RefundStatus.INITIATED.value, RefundStatus.PROCESSING.value, RefundStatus.FAILED.value}:
            raise ValidationError("Refund is not in a completable state")
        now = datetime.now(UTC)
        updated = await self._refund_repo.update_by_id(
            refund.id,
            {
                "status": RefundStatus.COMPLETED.value,
                "processed_amount": refund.requested_amount,
                "processed_by_id": actor_id,
                "completed_at": now,
                "braintree_status": braintree_status or refund.braintree_status,
                "braintree_status_updated_at": now if braintree_status else refund.braintree_status_updated_at,
            },
            expected_version=refund.version,
            organization_id=organization_id,
        )
        await self._refund_event_repo.create(
            {
                "refund_id": refund.id,
                "event_type": RefundEventType.MARKED_COMPLETED.value,
                "actor_id": actor_id,
                "payload_json": {"note": note},
            }
        )
        return updated

    async def retry_refund(
        self,
        *,
        organization_id: str,
        refund_id: str,
        actor_id: str,
        braintree_status: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ):
        refund = await self._refund_repo.get_by_id_or_404(refund_id, organization_id=organization_id)
        if refund.status != RefundStatus.FAILED.value:
            raise ValidationError("Only failed refunds can be retried")
        now = datetime.now(UTC)
        if refund.refund_method != RefundMethod.CARD_REFUND.value:
            updated = await self._refund_repo.update_by_id(
                refund.id,
                {
                    "status": RefundStatus.INITIATED.value,
                    "retry_count": int(refund.retry_count) + 1,
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "braintree_status": braintree_status or refund.braintree_status,
                    "braintree_status_updated_at": now if braintree_status else refund.braintree_status_updated_at,
                },
                expected_version=refund.version,
                organization_id=organization_id,
            )
        else:
            source_txn_id = str(refund.braintree_transaction_id or "").strip()
            if not source_txn_id:
                raise ValidationError("Failed card refund cannot be retried without source transaction id")

            retry_base = await self._refund_repo.update_by_id(
                refund.id,
                {
                    "status": RefundStatus.PROCESSING.value,
                    "retry_count": int(refund.retry_count) + 1,
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "braintree_status": BraintreeTransactionStatus.RETRYING.value,
                    "braintree_status_updated_at": now,
                },
                expected_version=refund.version,
                organization_id=organization_id,
            )
            reversal = refund_or_void_transaction(
                get_braintree_gateway(),
                transaction_id=source_txn_id,
                amount=refund.requested_amount,
                order_id=retry_base.id,
                owner_label=f"refund_retry={retry_base.id}",
            )
            reversed_tx_id = str(reversal.transaction_id or "").strip() or None
            if not reversal.success:
                updated = await self._refund_repo.update_by_id(
                    retry_base.id,
                    {
                        "status": RefundStatus.FAILED.value,
                        "failure_message": reversal.message or "Braintree refund/void failed on retry",
                        # Keep source transaction id for subsequent retries when gateway provides no new id.
                        "braintree_transaction_id": reversed_tx_id or source_txn_id,
                        "braintree_status": BraintreeTransactionStatus.FAILED.value,
                        "braintree_status_updated_at": datetime.now(UTC),
                    },
                    expected_version=retry_base.version,
                    organization_id=organization_id,
                )
            else:
                completed_at = datetime.now(UTC)
                updated = await self._refund_repo.update_by_id(
                    retry_base.id,
                    {
                        "status": RefundStatus.COMPLETED.value,
                        "processed_amount": retry_base.requested_amount,
                        "processed_by_id": actor_id,
                        "completed_at": completed_at,
                        "failure_code": None,
                        "failure_message": None,
                        "braintree_transaction_id": reversed_tx_id or source_txn_id,
                        "braintree_status": (
                            BraintreeTransactionStatus.VOIDED.value
                            if reversal.action == "void"
                            else BraintreeTransactionStatus.REFUND_SUBMITTED.value
                        ),
                        "braintree_status_updated_at": completed_at,
                    },
                    expected_version=retry_base.version,
                    organization_id=organization_id,
                )
        await self._refund_event_repo.create(
            {
                "refund_id": refund.id,
                "event_type": RefundEventType.RETRIED.value,
                "actor_id": actor_id,
                "payload_json": {"retry_count": updated.retry_count},
            }
        )
        return updated

    async def issue_credit_note_for_refund(
        self,
        *,
        organization_id: str,
        refund_id: str,
        actor_id: str,
        note: str | None = None,
    ):
        refund = await self._refund_repo.get_by_id_or_404(refund_id, organization_id=organization_id)
        if refund.refund_method != RefundMethod.CREDIT_NOTE.value:
            raise ValidationError("Only CREDIT_NOTE refunds can be issued as credit notes")
        if refund.status == RefundStatus.COMPLETED.value:
            return refund
        now = datetime.now(UTC)
        refund_customer_id: str | None = None
        if refund.invoice_id:
            refund_invoice = await self._invoice_repo.get_by_id(refund.invoice_id, organization_id=organization_id)
            if refund_invoice is not None:
                refund_customer_id = refund_invoice.customer_id
        cn = await self._credit_note_repo.create(
            {
                "credit_note_number": await self._credit_note_repo.next_credit_note_number(),
                "organization_id": organization_id,
                "customer_id": refund_customer_id,
                "source_invoice_id": refund.invoice_id,
                "issue_date": now.date(),
                "total_credit_amount": _money(refund.requested_amount),
                "currency": refund.currency,
                "status": "ISSUED",
                "reason_category": refund.reason_category or "OTHER",
                "reason": note or refund.reason_description,
            }
        )
        updated = await self._refund_repo.update_by_id(
            refund.id,
            {
                "status": RefundStatus.COMPLETED.value,
                "processed_amount": refund.requested_amount,
                "processed_by_id": actor_id,
                "completed_at": now,
                "metadata_json": {**(refund.metadata_json or {}), "credit_note_id": cn.id, "credit_note_number": cn.credit_note_number},
            },
            expected_version=refund.version,
            organization_id=organization_id,
        )
        await self._refund_event_repo.create(
            {
                "refund_id": refund.id,
                "event_type": RefundEventType.ISSUED_CREDIT_NOTE.value,
                "actor_id": actor_id,
                "payload_json": {"credit_note_id": cn.id, "credit_note_number": cn.credit_note_number},
            }
        )
        return updated

    @staticmethod
    def _credit_note_portal_status(*, credit_note_status: str, applied_total: Decimal, total_credit_amount: Decimal) -> str:
        if credit_note_status in {"VOIDED", "WRITTEN_OFF"}:
            return "VOID"
        if applied_total <= Decimal("0"):
            return "OPEN"
        remaining = _money(total_credit_amount - applied_total)
        if remaining <= Decimal("0"):
            return "FULLY_APPLIED"
        return "PARTIALLY_APPLIED"

    async def list_credit_notes(
        self,
        *,
        organization_id: str | None,
        page: int = 1,
        size: int = 20,
        customer_id: str | None = None,
        customer_unassigned_only: bool = False,
        search: str | None = None,
        status: list[str] | None = None,
        reason_category: list[str] | None = None,
        issued_from: date | None = None,
        issued_to: date | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[CreditNote], int]:
        return await self._credit_note_repo.list_credit_notes(
            page=page,
            size=size,
            organization_id=organization_id,
            customer_id=customer_id,
            customer_unassigned_only=customer_unassigned_only,
            search=search,
            status=status,
            reason_category=reason_category,
            issued_from=issued_from,
            issued_to=issued_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def list_credit_notes_for_b2b(
        self,
        *,
        organization_id: str,
        customer_filter: B2bCreditNoteCustomerFilter,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[str] | None = None,
        reason_category: list[str] | None = None,
        issued_from: date | None = None,
        issued_to: date | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[CreditNote], int]:
        customer_id: str | None = None
        customer_unassigned_only = False
        if customer_filter.mode == B2bCreditNoteCustomerFilterMode.SPECIFIC_CUSTOMER:
            await self._validate_b2b_customer_in_org(customer_filter.customer_id or "", organization_id)
            customer_id = customer_filter.customer_id
        elif customer_filter.mode == B2bCreditNoteCustomerFilterMode.UNASSIGNED_ONLY:
            customer_unassigned_only = True
        return await self.list_credit_notes(
            organization_id=organization_id,
            page=page,
            size=size,
            customer_id=customer_id,
            customer_unassigned_only=customer_unassigned_only,
            search=search,
            status=status,
            reason_category=reason_category,
            issued_from=issued_from,
            issued_to=issued_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    @staticmethod
    def _assert_b2b_credit_note_org_access(
        credit_note: CreditNote,
        *,
        organization_id: str,
        credit_note_id: str,
    ) -> None:
        if str(credit_note.organization_id or "") != str(organization_id):
            raise NotFoundError(resource="credit_note", id=credit_note_id)

    async def _validate_b2b_customer_in_org(self, customer_id: str, organization_id: str) -> None:
        uid = str(customer_id or "").strip()
        if not uid:
            raise ValidationError(
                "customer_id is required",
                details=[{"field": "customer_id", "message": "Required", "type": "value_error"}],
            )
        try:
            UUID(uid)
        except ValueError as exc:
            raise ValidationError(
                "Invalid customer_id",
                details=[{"field": "customer_id", "message": "Must be a valid UUID", "type": "value_error"}],
            ) from exc
        user = await self._session.get(User, uid)
        if user is None:
            raise NotFoundError(resource="customer", id=uid)
        role_val = user.role.value if isinstance(user.role, UserRole) else str(user.role)
        if role_val != UserRole.CUSTOMER_B2B.value:
            raise ValidationError(
                "customer_id must reference a CUSTOMER_B2B user",
                details=[{"field": "customer_id", "message": "Invalid customer role", "type": "value_error"}],
            )
        if str(user.organization_id or "") != str(organization_id):
            raise ValidationError(
                "customer_id does not belong to this organisation",
                details=[{"field": "customer_id", "message": "Organisation mismatch", "type": "value_error"}],
            )

    async def _resolve_credit_note_customer_id(self, cn: CreditNote, *, organization_id: str) -> str | None:
        cid = str(cn.customer_id or "").strip() or None
        if cid:
            return cid
        if cn.source_invoice_id:
            inv = await self._invoice_repo.get_by_id(cn.source_invoice_id, organization_id=organization_id)
            if inv is not None and inv.customer_id:
                return str(inv.customer_id)
        return None

    @staticmethod
    def _credit_note_missing_customer_error(*, for_apply: bool = False) -> ValidationError:
        action = "applying credit" if for_apply else "listing invoice candidates"
        return ValidationError(
            f"Credit note has no customer; link a source invoice or assign a customer before {action}"
        )

    async def get_credit_note_detail(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
    ) -> CreditNote:
        cn = await self._credit_note_repo.get_with_relations(credit_note_id, organization_id=organization_id)
        if cn is None:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        if b2b_org_scope:
            self._assert_b2b_credit_note_org_access(cn, organization_id=organization_id, credit_note_id=credit_note_id)
        elif customer_id is not None and cn.customer_id != customer_id:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        return cn

    async def get_credit_note_client_email(self, *, credit_note_id: str, organization_id: str) -> str | None:
        cn = await self.get_credit_note_detail(credit_note_id=credit_note_id, organization_id=organization_id)
        if cn.customer_id:
            from app.modules.user.repository import UserRepository

            user = await UserRepository(self._session).get_by_id(cn.customer_id)
            if user and user.email:
                return user.email
        if cn.source_invoice_id:
            inv = await self._invoice_repo.get_by_id(cn.source_invoice_id, organization_id=organization_id)
            if inv and inv.customer_id:
                from app.modules.user.repository import UserRepository

                user = await UserRepository(self._session).get_by_id(inv.customer_id)
                if user and user.email:
                    return user.email
        return None

    async def list_credit_note_invoice_candidates(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        cn = await self.get_credit_note_detail(
            credit_note_id=credit_note_id,
            organization_id=organization_id,
            customer_id=customer_id,
            b2b_org_scope=b2b_org_scope,
        )
        invoice_customer_id = await self._resolve_credit_note_customer_id(cn, organization_id=organization_id)
        if invoice_customer_id is None:
            raise self._credit_note_missing_customer_error(for_apply=False)

        offset = max(page - 1, 0) * size
        eligible_total = 0
        rows: list[dict[str, object]] = []
        db_page = 1

        while True:
            invoices, _ = await self._invoice_repo.list_invoices(
                page=db_page,
                size=_INVOICE_ALLOCATION_CANDIDATE_LIST_BATCH,
                search=search,
                status=[InvoiceStatus.SENT.value],
                payment_status=["UNPAID", "PARTIALLY_PAID", "OVERDUE"],
                show_draft=False,
                organization_id=organization_id,
                customer_id=invoice_customer_id,
                sort_by="due_date",
                sort_order="asc",
            )
            if not invoices:
                break
            ids = [str(inv.id) for inv in invoices]
            paid_map = await self._allocation_repo.totals_allocated_for_invoices(ids)
            credit_map = await self._credit_app_repo.totals_applied_for_invoices(ids)
            void_map = await self._invoice_event_repo.latest_void_written_off_for_invoice_ids(ids)
            for inv in invoices:
                iid = str(inv.id)
                paid_total = _money(paid_map.get(iid, Decimal("0")))
                credit_total = _money(credit_map.get(iid, Decimal("0")))
                outstanding = _money(inv.total - credit_total - paid_total)
                if outstanding <= Decimal("0"):
                    continue
                if void_map.get(iid) in {"VOIDED", "WRITTEN_OFF"}:
                    continue
                if eligible_total >= offset and len(rows) < size:
                    rows.append(
                        {
                            "invoice_id": inv.id,
                            "invoice_number": inv.invoice_number,
                            "issue_date": inv.issue_date,
                            "due_date": inv.due_date,
                            "payment_status": self._invoice_candidate_payment_status(inv),
                            "outstanding_amount": outstanding,
                        }
                    )
                eligible_total += 1
            db_page += 1

        return rows, eligible_total

    async def apply_credit_note_auto(
        self,
        *,
        credit_note_id: str,
        invoice_id: str,
        organization_id: str,
        actor_id: str | None = None,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
    ) -> InvoiceCreditApplication:
        cn_stmt = select(CreditNote).where(CreditNote.id == credit_note_id, CreditNote.organization_id == organization_id).with_for_update()
        cn = (await self._session.execute(cn_stmt)).scalar_one_or_none()
        if cn is None:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        if b2b_org_scope:
            self._assert_b2b_credit_note_org_access(cn, organization_id=organization_id, credit_note_id=credit_note_id)
        elif customer_id is not None and cn.customer_id != customer_id:
            raise NotFoundError(resource="credit_note", id=credit_note_id)
        if cn.status in {"VOIDED", "WRITTEN_OFF"}:
            raise ValidationError("Credit note is not applicable")
        if cn.status != "ISSUED":
            raise ValidationError("Only ISSUED credit notes can be applied")
        inv_stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == organization_id).with_for_update()
        inv = (await self._session.execute(inv_stmt)).scalar_one_or_none()
        if inv is None:
            raise NotFoundError(resource="invoice", id=invoice_id)
        if inv.status != InvoiceStatus.SENT.value:
            raise ValidationError("Only SENT invoices can receive credit")
        outcome = await self._invoice_event_repo.latest_outcome_event_type(inv.id)
        if outcome in {"VOIDED", "WRITTEN_OFF"}:
            raise ValidationError("Invoice is not applicable for credit")
        effective_customer_id = await self._resolve_credit_note_customer_id(cn, organization_id=organization_id)
        if effective_customer_id is None:
            raise self._credit_note_missing_customer_error(for_apply=True)
        if inv.customer_id != effective_customer_id:
            raise ValidationError(
                "Invoice does not match the credit note customer",
                details=[
                    {
                        "field": "invoice_id",
                        "message": "Invoice customer must match the credit note customer",
                        "type": "value_error",
                    }
                ],
            )

        credit_applied_total = _money(await self._credit_app_repo.get_applied_total_for_credit_note(cn.id))
        credit_remaining = _money(cn.total_credit_amount - credit_applied_total)
        invoice_paid_total = _money(await self._allocation_repo.total_allocated_for_invoice(inv.id))
        invoice_credit_total = _money(await self._credit_app_repo.get_applied_total_for_invoice(inv.id))
        invoice_outstanding = _money(inv.total - invoice_credit_total - invoice_paid_total)
        apply_amount = _money(min(credit_remaining, invoice_outstanding))
        if apply_amount <= Decimal("0"):
            raise ValidationError("Nothing to apply")

        app = await self._credit_app_repo.create(
            {
                "invoice_id": inv.id,
                "credit_note_id": cn.id,
                "applied_amount": apply_amount,
                "applied_at": date.today(),
                "applied_by": actor_id,
            }
        )
        await self._invoice_event_repo.append(
            inv.id,
            "CREDIT_APPLIED",
            actor_id=actor_id,
            actor_role="CUSTOMER_B2B",
            reason=f"Credit note {cn.credit_note_number} applied: {apply_amount}",
        )
        await self._recompute_invoice_projection(inv.id)
        return app

    async def create_credit_note(
        self,
        *,
        organization_id: str,
        source_invoice_id: str | None,
        customer_id: str | None,
        issue_date_value: date,
        amount: Decimal,
        reason_category: str,
        reason: str | None,
    ) -> CreditNote:
        amt = _money(amount)
        if amt <= Decimal("0"):
            raise ValidationError("amount must be greater than 0")
        resolved_customer_id = str(customer_id or "").strip() or None
        if resolved_customer_id:
            await self._validate_b2b_customer_in_org(resolved_customer_id, organization_id)
        source_invoice = None
        if source_invoice_id:
            source_invoice = await self._invoice_repo.get_by_id_or_404(source_invoice_id, organization_id=organization_id)
        cn = await self._credit_note_repo.create(
            {
                "credit_note_number": await self._credit_note_repo.next_credit_note_number(),
                "organization_id": organization_id,
                "customer_id": resolved_customer_id or (source_invoice.customer_id if source_invoice else None),
                "source_invoice_id": source_invoice.id if source_invoice else None,
                "issue_date": issue_date_value,
                "total_credit_amount": amt,
                "currency": "GBP",
                "status": "ISSUED",
                "reason_category": reason_category,
                "reason": reason,
            }
        )
        return cn

    async def void_credit_note(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        reason: str | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> CreditNote:
        from app.integrations.quickbooks.service import QuickBooksService
        from app.modules.invoices.service import InvoiceService

        cn = await self.get_credit_note_detail(credit_note_id=credit_note_id, organization_id=organization_id)
        if cn.status in {"VOIDED", "WRITTEN_OFF"}:
            return cn
        reason_clean = (reason or "Credit note voided").strip()
        if not reason_clean:
            raise ValidationError("reason is required for void")

        applied_total = _money(await self._credit_app_repo.get_applied_total_for_credit_note(cn.id))
        applications = await self._credit_app_repo.list_for_credit_note(cn.id)
        reversal_invoice_id: str | None = None

        if applied_total > Decimal("0"):
            inv_service = InvoiceService(self._session)
            reversal = await inv_service.create_reversal_for_credit_note_void(
                credit_note=cn,
                applied_total=applied_total,
                void_reason=reason_clean,
                audit_user_id=actor_id,
                audit_user_role=actor_role,
            )
            reversal_invoice_id = reversal.id

        update_data: dict = {"status": "VOIDED"}
        if reversal_invoice_id:
            update_data["reversal_invoice_id"] = reversal_invoice_id

        updated = await self._credit_note_repo.update_by_id(
            cn.id,
            update_data,
            expected_version=cn.version,
            organization_id=organization_id,
        )

        audit_action = (
            "billing.credit_note.voided_with_reversal"
            if reversal_invoice_id
            else "billing.credit_note.voided"
        )
        if actor_id:
            await self._audit.log(
                action=audit_action,
                entity_type="credit_note",
                entity_id=updated.id,
                user_id=actor_id,
                user_role=actor_role or "ADMIN",
                reason=reason_clean,
                new_value={
                    "status": "VOIDED",
                    "reversal_invoice_id": reversal_invoice_id,
                },
                category=AuditCategory.BILLING,
                event_type=AuditEventType.CREDIT_NOTE_ISSUED,
                severity="WARNING",
                organization_id=organization_id,
            )

        qb = QuickBooksService(self._session)
        if applied_total > Decimal("0") and reversal_invoice_id:
            affected = list({app.invoice_id for app in applications})
            await qb.enqueue_void_credit_note_chain(
                organization_id=organization_id,
                credit_note_id=updated.id,
                reversal_invoice_id=reversal_invoice_id,
                affected_invoice_ids=affected,
                version=updated.version,
                void_reason=reason_clean,
                credit_note_number=cn.credit_note_number,
                applied_total=str(applied_total),
            )
        else:
            await qb.enqueue_void_credit_note(
                organization_id=organization_id,
                credit_note_id=updated.id,
                version=updated.version,
                void_reason=reason_clean,
                credit_note_number=cn.credit_note_number,
            )
        return updated

    async def send_credit_note_to_client(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        recipient_email: str,
        actor_id: str | None = None,
    ) -> CreditNote:
        from app.mailer.client import send_email

        cn = await self.get_credit_note_detail(credit_note_id=credit_note_id, organization_id=organization_id)
        if cn.status in {"VOIDED", "WRITTEN_OFF"}:
            raise ValidationError("Cannot send a voided credit note")
        email_clean = (recipient_email or "").strip()
        if not email_clean:
            raise ValidationError("recipient_email is required")
        html = (
            f"<p>Hello,</p><p>Your credit note <b>{cn.credit_note_number}</b> is available.</p>"
            f"<p>Amount: {cn.total_credit_amount} {cn.currency}</p>"
        )
        await send_email(email_clean, f"Credit Note {cn.credit_note_number}", html_body=html)
        updated = await self._credit_note_repo.update_by_id(
            cn.id,
            {"sent_to_email": email_clean, "sent_at": datetime.now(UTC)},
            expected_version=cn.version,
            organization_id=organization_id,
        )
        if actor_id:
            await self._audit.log(
                action="billing.credit_note.sent_to_client",
                entity_type="credit_note",
                entity_id=updated.id,
                user_id=actor_id,
                user_role="ADMIN",
                new_value={"recipient_email": email_clean},
                category=AuditCategory.BILLING,
                event_type=AuditEventType.CREDIT_NOTE_ISSUED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return updated

    @staticmethod
    def _credit_note_pdf_signature(cn: CreditNote, apps: list[InvoiceCreditApplication]) -> str:
        payload = {
            "id": cn.id,
            "number": cn.credit_note_number,
            "issue_date": cn.issue_date.isoformat() if cn.issue_date else None,
            "amount": str(cn.total_credit_amount),
            "status": cn.status,
            "reason_category": cn.reason_category,
            "reason": cn.reason,
            "applications": [
                {"invoice_id": a.invoice_id, "amount": str(a.applied_amount), "applied_at": a.applied_at.isoformat()}
                for a in apps
            ],
        }
        return re.sub(r"[^a-f0-9]", "", hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())

    @staticmethod
    def _credit_note_pdf_job_id(*, credit_note_id: str, signature_hash: str, idempotency_key: str | None) -> str:
        idem_raw = (idempotency_key or "").strip()
        idem_part = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()[:12] if idem_raw else "noidem"
        return f"cnpdf:{credit_note_id}:{signature_hash[:12]}:{idem_part}"

    async def request_credit_note_pdf(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, str | None], object | None]:
        cn = await self.get_credit_note_detail(
            credit_note_id=credit_note_id,
            organization_id=organization_id,
            customer_id=customer_id,
            b2b_org_scope=b2b_org_scope,
        )
        apps = await self._credit_app_repo.list_for_credit_note(cn.id)
        signature_hash = self._credit_note_pdf_signature(cn, apps)
        ready = await self._credit_note_pdf_repo.get_ready_by_signature(cn.id, CREDIT_NOTE_PDF_TEMPLATE_VERSION, signature_hash)
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
        generating = await self._credit_note_pdf_repo.get_generating_by_signature(cn.id, CREDIT_NOTE_PDF_TEMPLATE_VERSION, signature_hash)
        if generating is not None:
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
        pdf_version = await self._credit_note_pdf_repo.get_next_pdf_version(cn.id)
        artifact = await self._credit_note_pdf_repo.create(
            {
                "credit_note_id": cn.id,
                "template_version": CREDIT_NOTE_PDF_TEMPLATE_VERSION,
                "signature_hash": signature_hash,
                "pdf_version": pdf_version,
                "status": "GENERATING",
            }
        )
        job = await enqueue(
            "generate_credit_note_pdf_task",
            credit_note_id=cn.id,
            artifact_id=artifact.id,
            template_version=CREDIT_NOTE_PDF_TEMPLATE_VERSION,
            _job_id=self._credit_note_pdf_job_id(credit_note_id=cn.id, signature_hash=signature_hash, idempotency_key=idempotency_key),
            priority=QueuePriority.LOW,
        )
        if job and job.job_id:
            artifact.job_id = job.job_id
            await self._session.flush()
        return (
            {
                "status": "GENERATING",
                "job_id": artifact.job_id,
                "error_code": None,
                "error_message": None,
                "artifact_id": artifact.id,
            },
            artifact,
        )

    async def get_credit_note_pdf_status(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
    ) -> dict[str, str | None]:
        _ = await self.get_credit_note_detail(
            credit_note_id=credit_note_id,
            organization_id=organization_id,
            customer_id=customer_id,
            b2b_org_scope=b2b_org_scope,
        )
        latest = await self._credit_note_pdf_repo.get_latest_for_credit_note(credit_note_id)
        if latest is None:
            return {"status": "NOT_REQUESTED", "job_id": None, "error_code": None, "error_message": None, "artifact_id": None}
        return {
            "status": latest.status,
            "job_id": latest.job_id,
            "error_code": latest.error_code,
            "error_message": latest.error_message,
            "artifact_id": latest.id,
        }

    async def get_credit_note_pdf_signed_url(
        self,
        *,
        credit_note_id: str,
        organization_id: str,
        customer_id: str | None = None,
        b2b_org_scope: bool = False,
        disposition: str = "attachment",
    ) -> tuple[str, datetime]:
        from app.storage.r2_client import generate_presigned_url

        cn = await self.get_credit_note_detail(
            credit_note_id=credit_note_id,
            organization_id=organization_id,
            customer_id=customer_id,
            b2b_org_scope=b2b_org_scope,
        )
        latest = await self._credit_note_pdf_repo.get_latest_for_credit_note(credit_note_id)
        if latest is None or latest.status != "READY" or not latest.r2_file_key:
            raise NotFoundError(resource="credit_note_pdf", id=credit_note_id)
        safe_name = str(cn.credit_note_number).replace('"', "").replace("\n", "").replace("\r", "")
        disp = disposition if disposition in {"inline", "attachment"} else "attachment"
        content_disposition = f'{disp}; filename="{safe_name}.pdf"'
        url = generate_presigned_url(
            latest.r2_file_key,
            expiry_seconds=SIGNED_URL_EXPIRY_SECONDS,
            content_type="application/pdf",
            response_content_disposition=content_disposition,
        )
        return url, datetime.now(UTC) + timedelta(seconds=SIGNED_URL_EXPIRY_SECONDS)

    async def attach_remittance_advice(
        self,
        *,
        organization_id: str,
        payment_id: str,
        content: bytes,
        content_type: str,
        original_filename: str,
        actor_id: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        updated = await self._persist_remittance_advice(
            payment=payment,
            organization_id=organization_id,
            content=content,
            content_type=content_type,
            original_filename=original_filename,
            actor_id=actor_id,
        )
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.remittance_advice.attached",
                entity_type="billing_payment",
                entity_id=updated.id,
                entity_ref=updated.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "content_type": content_type,
                    "size_bytes": len(content),
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return updated

    async def delete_remittance_advice(
        self,
        *,
        organization_id: str,
        payment_id: str,
        actor_id: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        from app.storage.upload import delete_from_r2

        payment = await self._payment_repo.get_by_id_or_404(payment_id, organization_id=organization_id)
        if not payment.remittance_advice_r2_key:
            raise NotFoundError(resource="billing_payment_remittance_advice", id=payment_id)

        old_key = payment.remittance_advice_r2_key
        await self._payment_repo.update_by_id(
            payment.id,
            {
                "remittance_advice_r2_key": None,
                "remittance_advice_content_type": None,
                "remittance_advice_original_filename": None,
                "remittance_advice_size_bytes": None,
                "remittance_advice_uploaded_at": None,
            },
            expected_version=payment.version,
            organization_id=organization_id,
        )
        try:
            await delete_from_r2(old_key)
        except Exception as exc:
            logger.warning(
                LogEvent.STORAGE_PROVIDER_ERROR,
                provider="r2",
                reason="remittance_delete_failed",
                key=old_key,
                error=str(exc),
            )

        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.REMITTANCE_ADVICE_REMOVED.value,
                "actor_id": actor_id,
                "payload_json": None,
            }
        )
        refreshed = await self._payment_repo.get_by_id_or_404(payment.id, organization_id=organization_id)
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.remittance_advice.removed",
                entity_type="billing_payment",
                entity_id=refreshed.id,
                entity_ref=refreshed.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={},
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=organization_id,
            )
        return refreshed

    def remittance_advice_signed_url(
        self,
        *,
        payment: BillingPayment,
        disposition: Literal["inline", "attachment"] = "inline",
        expiry_seconds: int = REMITTANCE_SIGNED_URL_EXPIRY_SECONDS,
    ) -> tuple[str, datetime]:
        from app.storage.r2_client import generate_presigned_url

        if not payment.remittance_advice_r2_key or not payment.remittance_advice_content_type:
            raise NotFoundError(resource="billing_payment_remittance_advice", id=payment.id)

        raw_name = payment.remittance_advice_original_filename or "remittance-advice"
        safe_stem = _safe_remittance_filename(raw_name, max_len=120)
        if payment.remittance_advice_content_type == "application/pdf":
            filename = f"{safe_stem}.pdf" if not safe_stem.lower().endswith(".pdf") else safe_stem
        elif payment.remittance_advice_content_type == "image/png":
            filename = f"{safe_stem}.png" if not safe_stem.lower().endswith(".png") else safe_stem
        else:
            filename = f"{safe_stem}.jpg" if not safe_stem.lower().endswith((".jpg", ".jpeg")) else safe_stem

        safe_disp = filename.replace('"', "").replace("\n", "").replace("\r", "")
        cd = f'{disposition}; filename="{safe_disp}"'

        url = generate_presigned_url(
            payment.remittance_advice_r2_key,
            expiry_seconds=expiry_seconds,
            content_type=payment.remittance_advice_content_type,
            response_content_disposition=cd,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=expiry_seconds)
        return url, expires_at

    async def _persist_remittance_advice(
        self,
        *,
        payment: BillingPayment,
        organization_id: str,
        content: bytes,
        content_type: str,
        original_filename: str,
        actor_id: str | None,
    ) -> BillingPayment:
        from app.storage.upload import delete_from_r2, upload_to_r2

        if not content:
            raise ValidationError("Remittance advice file is empty")

        suffix = _remittance_object_suffix(content_type)
        new_key = f"billing/remittance-advice/{organization_id}/{payment.id}/{uuid4().hex}.{suffix}"
        old_key = payment.remittance_advice_r2_key

        await upload_to_r2(new_key, content, content_type)
        uploaded_at = datetime.now(UTC)
        try:
            await self._payment_repo.update_by_id(
                payment.id,
                {
                    "remittance_advice_r2_key": new_key,
                    "remittance_advice_content_type": content_type,
                    "remittance_advice_original_filename": _safe_remittance_filename(original_filename),
                    "remittance_advice_size_bytes": len(content),
                    "remittance_advice_uploaded_at": uploaded_at,
                },
                expected_version=payment.version,
                organization_id=organization_id,
            )
        except Exception:
            try:
                await delete_from_r2(new_key)
            except Exception as cleanup_exc:
                logger.warning(
                    LogEvent.STORAGE_PROVIDER_ERROR,
                    provider="r2",
                    reason="remittance_upload_rollback_failed",
                    key=new_key,
                    error=str(cleanup_exc),
                )
            raise

        if old_key and old_key != new_key:
            try:
                await delete_from_r2(old_key)
            except Exception as exc:
                logger.warning(
                    LogEvent.STORAGE_PROVIDER_ERROR,
                    provider="r2",
                    reason="remittance_old_object_delete_failed",
                    key=old_key,
                    error=str(exc),
                )

        await self._event_repo.create(
            {
                "payment_id": payment.id,
                "event_type": PaymentEventType.REMITTANCE_ADVICE_ATTACHED.value,
                "actor_id": actor_id,
                "payload_json": {
                    "content_type": content_type,
                    "size_bytes": len(content),
                },
            }
        )
        return await self._payment_repo.get_by_id_or_404(payment.id, organization_id=organization_id)

    async def add_or_revise_allocations(
        self,
        *,
        payment_id: str,
        allocations: list[dict[str, object]],
        actor_id: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        if not allocations:
            raise ValidationError(
                "At least one allocation is required",
                details=[{"field": "allocations", "message": "Must contain at least one item", "type": "value_error"}],
            )

        payment = await self._payment_repo.get_by_id_or_404(payment_id)
        if payment.status == PaymentRecordStatus.VOIDED.value:
            raise ValidationError("Cannot allocate a voided payment")
        normalized: list[dict[str, object]] = []
        for idx, row in enumerate(allocations, start=1):
            iid = str(row.get("invoice_id") or "").strip()
            amt = _money(Decimal(str(row.get("allocated_amount") or "0")))
            notes = row.get("notes")
            if not iid:
                raise ValidationError(
                    "invoice_id is required",
                    details=[{"field": f"allocations[{idx - 1}].invoice_id", "message": "Required", "type": "value_error"}],
                )
            if amt <= 0:
                raise ValidationError(
                    "allocated_amount must be greater than 0",
                    details=[
                        {
                            "field": f"allocations[{idx - 1}].allocated_amount",
                            "message": "Must be greater than 0",
                            "type": "value_error",
                        }
                    ],
                )
            normalized.append({"invoice_id": iid, "allocated_amount": amt, "notes": notes})

        invoice_ids = [str(r["invoice_id"]) for r in normalized]
        if len(set(invoice_ids)) != len(invoice_ids):
            raise ValidationError(
                "Duplicate invoice_id in allocations payload",
                details=[{"field": "allocations", "message": "Each invoice_id may appear only once", "type": "value_error"}],
            )

        stmt = select(Invoice).where(Invoice.id.in_(invoice_ids), Invoice.organization_id == payment.organization_id)
        invoices = list((await self._session.execute(stmt)).scalars().all())
        inv_by_id = {str(i.id): i for i in invoices}
        missing = [iid for iid in invoice_ids if iid not in inv_by_id]
        if missing:
            raise NotFoundError(resource="invoice", id=missing[0])
        for iid in invoice_ids:
            if inv_by_id[iid].status != "SENT":
                raise ValidationError("Only SENT invoices can receive payment allocations")

        current_payment_allocated = _money(await self._allocation_repo.total_latest_allocated_for_payment(payment_id))
        batch_total = _money(sum(Decimal(str(r["allocated_amount"])) for r in normalized))
        if current_payment_allocated + batch_total > _money(payment.amount):
            raise ValidationError("PAYMENT_OVER_ALLOCATED: allocation exceeds payment unallocated balance")

        current_alloc_by_invoice = await self._allocation_repo.totals_allocated_for_invoices(invoice_ids)
        current_credit_by_invoice = await self._credit_app_repo.totals_applied_for_invoices(invoice_ids)
        for row in normalized:
            iid = str(row["invoice_id"])
            allocated_q = _money(Decimal(str(row["allocated_amount"])))
            invoice = inv_by_id[iid]
            current_invoice_allocated = _money(Decimal(str(current_alloc_by_invoice.get(iid, Decimal("0")))))
            invoice_credit_total = _money(Decimal(str(current_credit_by_invoice.get(iid, Decimal("0")))))
            invoice_remaining = _money(invoice.total - invoice_credit_total - current_invoice_allocated)
            if allocated_q > invoice_remaining:
                raise ValidationError("INVOICE_OVER_ALLOCATED: allocation exceeds invoice outstanding balance")

        for row in normalized:
            iid = str(row["invoice_id"])
            allocated_q = _money(Decimal(str(row["allocated_amount"])))
            notes = row.get("notes")
            revision_no = await self._allocation_repo.next_revision_no(payment_id=payment_id, invoice_id=iid)
            await self._allocation_repo.create(
                {
                    "payment_id": payment_id,
                    "invoice_id": iid,
                    "revision_no": revision_no,
                    "allocated_amount": allocated_q,
                    "allocated_by_id": actor_id,
                    "notes": notes,
                }
            )
            await self._event_repo.create(
                {
                    "payment_id": payment_id,
                    "event_type": PaymentEventType.ALLOCATED.value if revision_no == 1 else PaymentEventType.ALLOCATION_REVISED.value,
                    "actor_id": actor_id,
                    "payload_json": {
                        "invoice_id": iid,
                        "revision_no": revision_no,
                        "allocated_amount": str(allocated_q),
                    },
                }
            )

        await self._recompute_payment_projection(payment)
        for iid in invoice_ids:
            await self._recompute_invoice_projection(iid)
        await self._enqueue_qb_payment_sync(payment_id=payment.id, organization_id=payment.organization_id, version=payment.version + 1)
        refreshed = await self._payment_repo.get_by_id_or_404(payment.id)
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.allocation.revised",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "allocations": [
                        {
                            "invoice_id": str(r["invoice_id"]),
                            "allocated_amount": str(_money(Decimal(str(r["allocated_amount"])))),
                        }
                        for r in normalized
                    ],
                    "allocated_total": str(refreshed.allocated_amount),
                    "unallocated_total": str(refreshed.unallocated_amount),
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=payment.organization_id,
            )
        return refreshed

    async def add_or_revise_allocation(
        self,
        *,
        payment_id: str,
        invoice_id: str,
        allocated_amount: Decimal,
        actor_id: str | None = None,
        notes: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        return await self.add_or_revise_allocations(
            payment_id=payment_id,
            allocations=[
                {
                    "invoice_id": invoice_id,
                    "allocated_amount": allocated_amount,
                    "notes": notes,
                }
            ],
            actor_id=actor_id,
            audit_ctx=audit_ctx,
        )

    async def replace_allocations(
        self,
        *,
        payment_id: str,
        allocations: list[dict[str, object]],
        actor_id: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id)
        if payment.status == PaymentRecordStatus.VOIDED.value:
            raise ValidationError("Cannot allocate a voided payment")

        normalized: list[dict[str, object]] = []
        for idx, row in enumerate(allocations, start=1):
            iid = str(row.get("invoice_id") or "").strip()
            amt = _money(Decimal(str(row.get("allocated_amount") or "0")))
            notes = row.get("notes")
            if not iid:
                raise ValidationError(
                    "invoice_id is required",
                    details=[{"field": f"allocations[{idx - 1}].invoice_id", "message": "Required", "type": "value_error"}],
                )
            if amt <= 0:
                raise ValidationError(
                    "allocated_amount must be greater than 0",
                    details=[
                        {
                            "field": f"allocations[{idx - 1}].allocated_amount",
                            "message": "Must be greater than 0",
                            "type": "value_error",
                        }
                    ],
                )
            normalized.append({"invoice_id": iid, "allocated_amount": amt, "notes": notes})

        invoice_ids = [str(r["invoice_id"]) for r in normalized]
        if len(set(invoice_ids)) != len(invoice_ids):
            raise ValidationError(
                "Duplicate invoice_id in allocations payload",
                details=[{"field": "allocations", "message": "Each invoice_id may appear only once", "type": "value_error"}],
            )

        target_map = {str(r["invoice_id"]): _money(Decimal(str(r["allocated_amount"]))) for r in normalized}
        notes_by_invoice = {str(r["invoice_id"]): r.get("notes") for r in normalized}

        touched_invoice_ids = await self._apply_allocation_targets(
            payment=payment,
            target_map=target_map,
            notes_by_invoice=notes_by_invoice,
            actor_id=actor_id,
        )

        refreshed = await self._payment_repo.get_by_id_or_404(payment.id)
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.allocations.replaced",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "allocations": [{"invoice_id": iid, "allocated_amount": str(amt)} for iid, amt in target_map.items()],
                    "allocated_total": str(refreshed.allocated_amount),
                    "unallocated_total": str(refreshed.unallocated_amount),
                    "touched_invoice_ids": touched_invoice_ids,
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=payment.organization_id,
            )
        return refreshed

    async def remove_allocation(
        self,
        *,
        payment_id: str,
        invoice_id: str,
        actor_id: str | None = None,
        audit_ctx: AuditContext | None = None,
    ) -> BillingPayment:
        payment = await self._payment_repo.get_by_id_or_404(payment_id)
        if payment.status == PaymentRecordStatus.VOIDED.value:
            raise ValidationError("Cannot allocate a voided payment")

        current_rows = await self._allocation_repo.latest_for_payment(payment_id)
        current_map = {str(r.invoice_id): _money(Decimal(str(r.allocated_amount))) for r in current_rows if r.allocated_amount > 0}
        invoice_key = str(invoice_id).strip()
        if not invoice_key:
            raise ValidationError("invoice_id is required")
        if invoice_key not in current_map:
            raise ValidationError("No existing positive allocation found for invoice on this payment")

        target_map = {iid: amt for iid, amt in current_map.items() if iid != invoice_key}
        touched_invoice_ids = await self._apply_allocation_targets(
            payment=payment,
            target_map=target_map,
            notes_by_invoice={},
            actor_id=actor_id,
        )

        refreshed = await self._payment_repo.get_by_id_or_404(payment.id)
        if audit_ctx is not None:
            await self._audit.log(
                action="billing.payment.allocation.removed",
                entity_type="billing_payment",
                entity_id=payment.id,
                entity_ref=payment.payment_number,
                user_id=audit_ctx.user_id,
                user_role=audit_ctx.user_role,
                new_value={
                    "removed_invoice_id": invoice_key,
                    "allocated_total": str(refreshed.allocated_amount),
                    "unallocated_total": str(refreshed.unallocated_amount),
                    "touched_invoice_ids": touched_invoice_ids,
                },
                ip_address=audit_ctx.ip_address,
                user_agent=audit_ctx.user_agent,
                category=AuditCategory.BILLING,
                event_type=AuditEventType.PAYMENT_RECORDED,
                severity="NOTICE",
                organization_id=payment.organization_id,
            )
        return refreshed

    async def _apply_allocation_targets(
        self,
        *,
        payment: BillingPayment,
        target_map: dict[str, Decimal],
        notes_by_invoice: dict[str, object | None],
        actor_id: str | None,
    ) -> list[str]:
        current_rows = await self._allocation_repo.latest_for_payment(payment.id)
        current_map = {str(r.invoice_id): _money(Decimal(str(r.allocated_amount))) for r in current_rows if r.allocated_amount > 0}

        target_total = _money(sum(target_map.values(), Decimal("0")))
        if target_total > _money(payment.amount):
            raise ValidationError("PAYMENT_OVER_ALLOCATED: allocation exceeds payment amount")

        target_invoice_ids = list(target_map.keys())
        if target_invoice_ids:
            stmt = select(Invoice).where(Invoice.id.in_(target_invoice_ids), Invoice.organization_id == payment.organization_id)
            invoices = list((await self._session.execute(stmt)).scalars().all())
            inv_by_id = {str(i.id): i for i in invoices}
            missing = [iid for iid in target_invoice_ids if iid not in inv_by_id]
            if missing:
                raise NotFoundError(resource="invoice", id=missing[0])
            for iid in target_invoice_ids:
                if inv_by_id[iid].status != InvoiceStatus.SENT.value:
                    raise ValidationError("Only SENT invoices can receive payment allocations")
        else:
            inv_by_id = {}

        touched_ids = sorted(set(current_map.keys()) | set(target_map.keys()))
        if not touched_ids:
            return []

        current_alloc_by_invoice = await self._allocation_repo.totals_allocated_for_invoices(touched_ids)
        current_credit_by_invoice = await self._credit_app_repo.totals_applied_for_invoices(touched_ids)
        for iid, target_amt in target_map.items():
            invoice = inv_by_id[iid]
            current_payment_alloc = current_map.get(iid, Decimal("0"))
            all_alloc = _money(Decimal(str(current_alloc_by_invoice.get(iid, Decimal("0")))))
            other_alloc = _money(all_alloc - current_payment_alloc)
            credit_total = _money(Decimal(str(current_credit_by_invoice.get(iid, Decimal("0")))))
            remaining = _money(invoice.total - credit_total - other_alloc)
            if target_amt > remaining:
                raise ValidationError("INVOICE_OVER_ALLOCATED: allocation exceeds invoice outstanding balance")

        if target_map == current_map:
            return []

        await self._session.execute(delete(BillingPaymentAllocation).where(BillingPaymentAllocation.payment_id == payment.id))

        changed_invoice_ids = touched_ids
        for iid, after_amt in target_map.items():
            revision_no = await self._allocation_repo.next_revision_no(payment_id=payment.id, invoice_id=iid)
            await self._allocation_repo.create(
                {
                    "payment_id": payment.id,
                    "invoice_id": iid,
                    "revision_no": revision_no,
                    "allocated_amount": after_amt,
                    "allocated_by_id": actor_id,
                    "notes": notes_by_invoice.get(iid),
                }
            )
            await self._event_repo.create(
                {
                    "payment_id": payment.id,
                    "event_type": PaymentEventType.ALLOCATION_REVISED.value,
                    "actor_id": actor_id,
                    "payload_json": {
                        "invoice_id": iid,
                        "revision_no": revision_no,
                        "new_allocated_amount": str(after_amt),
                    },
                }
            )

        await self._recompute_payment_projection(payment)
        for iid in changed_invoice_ids:
            await self._recompute_invoice_projection(iid)
        await self._enqueue_qb_payment_sync(payment_id=payment.id, organization_id=payment.organization_id, version=payment.version + 1)
        return changed_invoice_ids

    async def _recompute_payment_projection(self, payment: BillingPayment) -> None:
        allocated = _money(await self._allocation_repo.total_latest_allocated_for_payment(payment.id))
        unallocated = _money(payment.amount - allocated)
        if unallocated < 0:
            raise ValidationError("allocated total exceeds payment amount")

        if allocated == Decimal("0"):
            allocation_status = AllocationStatus.UNALLOCATED.value
        elif unallocated == Decimal("0"):
            allocation_status = AllocationStatus.ALLOCATED.value
        else:
            allocation_status = AllocationStatus.PARTIALLY_ALLOCATED.value

        await self._payment_repo.update_by_id(
            payment.id,
            {
                "allocated_amount": allocated,
                "unallocated_amount": unallocated,
                "allocation_status": allocation_status,
            },
            expected_version=payment.version,
        )

    async def _recompute_invoice_projection(self, invoice_id: str) -> None:
        invoice = await self._invoice_repo.get_by_id_or_404(invoice_id)
        paid_total = _money(await self._allocation_repo.total_allocated_for_invoice(invoice_id))
        credit_total = _money(await self._credit_app_repo.get_applied_total_for_invoice(invoice_id))
        outcome_event_type = await self._invoice_event_repo.latest_outcome_event_type(invoice_id)
        payment_status = compute_payment_status(
            invoice,
            paid_amount=paid_total,
            credit_total=credit_total,
            outcome_event_type=outcome_event_type,
        )
        await self._invoice_repo.update_by_id(
            invoice.id,
            {
                "paid_amount": paid_total,
                "payment_status": payment_status,
            },
            expected_version=invoice.version,
        )

    async def _enqueue_qb_payment_sync(
        self,
        *,
        payment_id: str,
        organization_id: str,
        version: int,
        trigger_source: str = "billing.payment_sync",
    ) -> None:
        allocated = _money(await self._allocation_repo.total_latest_allocated_for_payment(payment_id))
        if allocated <= Decimal("0"):
            return
        from app.integrations.quickbooks.service import QuickBooksService

        await QuickBooksService(self._session).enqueue_payment_sync(
            organization_id=organization_id,
            payment_id=payment_id,
            trigger_source=trigger_source,
        )
