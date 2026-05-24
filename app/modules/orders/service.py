from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import UserRole
from app.common.enums.jobs import Job
from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.core.queue import QueuePriority, enqueue
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.billing.enums import PaymentProvider, PaymentRecordStatus
from app.modules.billing.models import BillingPayment
from app.modules.billing.service import BillingService
from app.modules.invoices.enums import InvoiceStatus
from app.modules.invoices.models import Invoice
from app.modules.invoices.repository import InvoiceRepository
from app.modules.invoices.service import InvoiceService
from app.modules.orders.enums import (
    FAILED_PACKAGE_STATUSES,
    MAX_DELIVERY_ATTEMPTS,
    MAX_RETURN_EVIDENCE_IMAGES,
    PACKAGE_STATUSES_BLOCKING_CANCELLATION,
    RESCHEDULABLE_PACKAGE_STATUSES,
    RESOLVABLE_RETURN_PACKAGE_STATUSES,
    RETURN_IN_TRANSIT_STATUSES,
    RETURNABLE_PACKAGE_STATUSES,
    ClientTypeEnum,
    DeliveryStopStatus,
    DisposalReason,
    OrderDraftStatus,
    OrderStatus,
    PackageStatus,
    ReturnResolution,
    StopNoteType,
    attempt_number_from_stop_status,
)
from app.modules.orders.models import (
    DeliveryStop,
    DeliveryStopEvent,
    DeliveryStopFailedAttempt,
    DeliveryStopReturnAttempt,
    DeliveryStopReturnEvidenceImage,
    Order,
    OrderDraft,
    OrderEvent,
    Package,
    PackageEvent,
    StopNote,
    StopNoteImage,
)
from app.modules.orders.pricing import (
    PackageInput,
    StopInput,
    compute_price_breakdown,
    effective_tier_to_plan,
    load_pricing_context,
    plan_display_name,
    recompute_price_breakdown_from_snapshot,
    validate_package_restrictions,
)
from app.modules.service_tiers.service import ServiceTierService
from app.modules.orders.repository import (
    DeliveryStopEventRepository,
    OrderDraftRepository,
    OrderEventRepository,
    OrderRepository,
    PackageEventRepository,
    StopNoteRepository,
)
from app.modules.orders.stop_note_utils import (
    assert_package_ids_belong_to_stop,
    assert_stop_note_type_allowed_for_stop_flow,
    batch_package_ids_for_stop_notes,
    is_strict_stop_note_types,
    normalize_stop_note_type,
    parse_and_validate_package_ids_for_note,
    validate_stop_note_type_allowed,
)
from app.modules.orders.timeline_labels import (
    delivery_stop_status_display,
    order_status_display,
    package_status_display,
)
from app.modules.orders.types import (
    FailedDeliveryCounts,
    FailedDeliveryStopRow,
    FailedDeliverySummaryResult,
    FailedPackageRow,
    OrderStatusCounts,
    OrderSummaryResult,
    ReturnPackageRow,
    ReturnsCounts,
    ReturnsSummaryResult,
    ReturnStopRow,
    StatusEventRecord,
)
from app.modules.orders.utils import resolve_summary_window
from app.modules.orders.v1.schemas import (
    DeliveryStopCancelResponse,
    DeliveryStopCreateItem,
    DeliveryStopTimelineSlice,
    DraftContactUserInfo,
    DraftListItem,
    DraftResponse,
    EntityStatusEventItem,
    FailedDeliveriesSummaryResponse,
    FailedDeliveryPackageEntry,
    FailedDeliveryStopItem,
    FloatSummaryStat,
    MasterLabelEntry,
    OrderCancelResponse,
    DeliveryStopDetailPackageEntry,
    DeliveryStopDetailResponse,
    StopAttemptEntry,
    StopPodPhotoEntry,
    StopPodSummary,
    StopReturnEvidenceEntry,
    StopReturnEvidenceSummary,
    OrderCreateRequest,
    OrderDetailResponse,
    OrderDetailStopEntry,
    OrderLabelsResponse,
    OrderPriceBreakdownDetail,
    OrderPriceBreakdownRequest,
    OrderPriceBreakdownResponse,
    OrderSummaryResponse,
    OrderSummaryStat,
    OrderTimelineResponse,
    PackageActionResponse,
    PackageEntry,
    PackageTimelineSlice,
    PickupLabelEntry,
    ResolveReturnResponse,
    ReturnEvidenceImageEntry,
    ReturnPackageEntry,
    ReturnsSummaryResponse,
    ReturnStopItem,
    StopActionResponse,
    StopNoteEntry,
    StopNoteImageEntry,
    SummaryDateRangeParams,
    UpdateStopPackagesResponse,
    UserBrief,
    validate_create_order_for_actor,
)
from app.modules.org_credit.enums import OrgCreditLedgerSourceType
from app.modules.org_credit.service import OrgCreditLedgerService
from app.modules.organizations.enums import BillingSchedule, OrganizationStatus, PaymentModel
from app.modules.organizations.models import OrgContact, Organization, OrgPaymentMethod
from app.modules.organizations.repository import OrgContactRepository
from app.modules.payments.models import CreditCard
from app.modules.planning.models import StopPod, StopPodPhoto
from app.modules.payments.service import BookingChargeResult, CreditCardOwner, PaymentService
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.pickup_addresses.repository import PickupAddressRepository
from app.modules.pickup_addresses.service import PickupAddressService
from app.modules.user.repository import UserRepository
from app.modules.drivers.models import Driver
from app.modules.vehicles.models import Vehicle
from app.modules.user.models import User
from app.storage.upload import bulk_upload_images, delete_image, generate_image_url

logger = structlog.get_logger()


def _to_period_bounds(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(date_from, time.min) if date_from else None
    end_exclusive = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    return start, end_exclusive


def _to_summary_stat(current: int, previous: int):
    change_pct: float | None = None
    if previous > 0:
        change_pct = round(((current - previous) / previous) * 100.0, 2)
    return OrderSummaryStat(current=current, previous=previous, change_pct=change_pct)


def _to_float_stat(current: float | None, previous: float | None) -> FloatSummaryStat:
    c = round(current, 1) if current is not None else None
    p = round(previous, 1) if previous is not None else None
    change_pct: float | None = None
    if c is not None and p is not None and p > 0:
        change_pct = round(((c - p) / p) * 100.0, 2)
    return FloatSummaryStat(current=c, previous=p, change_pct=change_pct)


def _failed_delivery_counts_from_by_status(by_status: dict[str, int]) -> FailedDeliveryCounts:
    counts = FailedDeliveryCounts(
        missing=by_status.get(PackageStatus.MISSING.value, 0),
        damaged=by_status.get(PackageStatus.DAMAGED.value, 0),
        cancelled=by_status.get(PackageStatus.CANCELLED.value, 0),
        customer_not_home=by_status.get(PackageStatus.CUSTOMER_NOT_HOME.value, 0),
        refused=by_status.get(PackageStatus.REFUSED_BY_CUSTOMER.value, 0),
        disposed=by_status.get(PackageStatus.DISPOSED.value, 0),
    )
    counts.total = counts.missing + counts.damaged + counts.cancelled + counts.customer_not_home + counts.refused
    return counts


def _status_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _compose_reason(reason_code: str | None, details: str | None) -> str | None:
    parts = [p for p in [reason_code, details] if p]
    if not parts:
        return None
    return " — ".join(parts)


def _order_privileged_admin(user: AuthUser) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


def _status_to_stored_value(status: object | None) -> str | None:
    if status is None:
        return None
    raw = getattr(status, "value", status)
    return str(raw)


class OrderStatusEventService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def record_order_transition(
        self,
        *,
        order_id: str,
        from_status: OrderStatus | None,
        to_status: OrderStatus,
        actor_user_id: str | None,
    ) -> OrderEvent:
        row = OrderEvent(
            order_id=order_id,
            from_status=_status_to_stored_value(from_status),
            to_status=to_status.value,
            actor_user_id=actor_user_id,
        )
        self._session.add(row)
        return row

    def record_delivery_stop_transition(
        self,
        *,
        delivery_stop_id: str,
        from_status: DeliveryStopStatus | None,
        to_status: DeliveryStopStatus,
        actor_user_id: str | None,
    ) -> DeliveryStopEvent:
        row = DeliveryStopEvent(
            delivery_stop_id=delivery_stop_id,
            from_status=_status_to_stored_value(from_status),
            to_status=to_status.value,
            actor_user_id=actor_user_id,
        )
        self._session.add(row)
        return row

    def record_package_transition(
        self,
        *,
        package_id: str,
        from_status: PackageStatus | None,
        to_status: PackageStatus,
        actor_user_id: str | None,
    ) -> PackageEvent:
        row = PackageEvent(
            package_id=package_id,
            from_status=_status_to_stored_value(from_status),
            to_status=to_status.value,
            actor_user_id=actor_user_id,
        )
        self._session.add(row)
        return row


class OrderService(BaseService):
    def __init__(self, session: AsyncSession, request=None) -> None:
        super().__init__(session, request)
        self._order_repo = OrderRepository(session)
        self._draft_repo = OrderDraftRepository(session)
        self._stop_note_repo = StopNoteRepository(session)
        self._order_event_repo = OrderEventRepository(session)
        self._delivery_stop_event_repo = DeliveryStopEventRepository(session)
        self._package_event_repo = PackageEventRepository(session)
        self._status_events = OrderStatusEventService(session)
        self._audit = AuditService(session)
        self._invoice_repo = InvoiceRepository(session)

    async def _append_order_status_event(
        self,
        *,
        order_id: str,
        from_status: OrderStatus | None,
        to_status: OrderStatus,
        actor_user_id: str | None,
        suppress_automation: bool = False,
    ) -> None:
        event_row = self._status_events.record_order_transition(
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor_user_id,
        )
        if suppress_automation:
            return
        order = await self._order_repo.get_by_id(order_id)
        if order is None:
            return
        await enqueue(
            Job.EVALUATE_STATUS_AUTOMATION_RULES,
            {
                "event_id": str(event_row.id),
                "occurred_at": event_row.created_at.isoformat() if event_row.created_at else None,
                "organization_id": str(order.organization_id),
                "entity_type": "BOOKING_ORDER",
                "entity_id": str(order.id),
                "order_id": str(order.id),
                "delivery_stop_id": None,
                "from_status": from_status.value if from_status else None,
                "to_status": to_status.value if hasattr(to_status, "value") else str(to_status),
                "actor_user_id": actor_user_id,
            },
            priority=QueuePriority.DEFAULT,
            _job_id=f"status-auto:{event_row.id}",
        )

    async def _append_delivery_stop_status_event(
        self,
        *,
        delivery_stop_id: str,
        from_status: DeliveryStopStatus | None,
        to_status: DeliveryStopStatus,
        actor_user_id: str | None,
        suppress_automation: bool = False,
    ) -> None:
        event_row = self._status_events.record_delivery_stop_transition(
            delivery_stop_id=delivery_stop_id,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor_user_id,
        )
        if suppress_automation:
            return
        stop_with_order = await self._order_repo.get_stop_with_order(delivery_stop_id)
        if stop_with_order is None:
            return
        stop, order = stop_with_order
        await enqueue(
            Job.EVALUATE_STATUS_AUTOMATION_RULES,
            {
                "event_id": str(event_row.id),
                "occurred_at": event_row.created_at.isoformat() if event_row.created_at else None,
                "organization_id": str(order.organization_id),
                "entity_type": "DELIVERY_STOP",
                "entity_id": str(delivery_stop_id),
                "order_id": str(order.id),
                "delivery_stop_id": str(stop.id),
                "from_status": from_status.value if from_status else None,
                "to_status": to_status.value if hasattr(to_status, "value") else str(to_status),
                "actor_user_id": actor_user_id,
            },
            priority=QueuePriority.DEFAULT,
            _job_id=f"status-auto:{event_row.id}",
        )

    async def _append_package_status_event(
        self,
        *,
        package_id: str,
        from_status: PackageStatus | None,
        to_status: PackageStatus,
        actor_user_id: str | None,
    ) -> None:
        self._status_events.record_package_transition(
            package_id=package_id,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor_user_id,
        )

    def _audit_ctx_or_none(self, *, user_id: str | None) -> AuditContext | None:
        if not user_id:
            return None
        req = self._request
        ip = req.client.host if req and req.client else None
        ua = req.headers.get("user-agent") if req else None
        return AuditContext(user_id=user_id, user_role="CUSTOMER_B2B", ip_address=ip, user_agent=ua)

    async def _sync_order_invoice(
        self,
        *,
        order: Order,
        created_by_id: str | None,
        stops: list[DeliveryStop],
        packages_by_stop: dict[str, list[Package]],
        issue_date: date | None = None,
        due_date: date | None = None,
    ) -> Invoice:
        return await InvoiceService(self._session, self._request).sync_from_order(
            order=order,
            stops=stops,
            packages_by_stop=packages_by_stop,
            issue_date=issue_date,
            due_date=due_date,
            audit_user_id=created_by_id,
            audit_user_role="CUSTOMER_B2B" if created_by_id else None,
        )

    async def _record_card_billing_for_order(
        self,
        *,
        order: Order,
        invoice: Invoice,
        organization_id: str,
        created_by_id: str | None,
        braintree_transaction_id: str,
        braintree_status: str | None = None,
        transaction_fee: Decimal | None = None,
        payment_id: str,
    ) -> None:
        amount = Decimal(order.total_amount or 0)
        billing_service = BillingService(self._session, self._request)
        payment = await billing_service.mark_payment_status(
            organization_id=organization_id,
            payment_id=payment_id,
            to_status=PaymentRecordStatus.DEPOSITED,
            provider_txn_id=braintree_transaction_id,
            braintree_status=braintree_status,
            transaction_fee=Decimal(transaction_fee or 0),
            actor_id=created_by_id,
            queue_qb_sync=True,
        )
        if str(getattr(invoice, "status", "")) != InvoiceStatus.SENT.value:
            invoice = await InvoiceService(self._session, self._request).finalize(
                invoice.id,
                organization_id,
                audit_user_id=created_by_id,
                audit_user_role="CUSTOMER_B2B" if created_by_id else None,
            )
        await billing_service.add_or_revise_allocation(
            payment_id=payment.id,
            invoice_id=invoice.id,
            allocated_amount=amount,
            actor_id=created_by_id,
            notes=f"Auto-allocation for order {order.order_id}",
            audit_ctx=self._audit_ctx_or_none(user_id=created_by_id),
        )

    async def _precharge_saved_card_for_order(
        self,
        *,
        organization_id: str,
        credit_card_id: str,
        charge_amount: Decimal,
        verified_payment_method_nonce: str,
        order_id: str,
    ) -> BookingChargeResult:
        if charge_amount <= Decimal("0"):
            raise ValidationError("Card payment requires a positive order total")
        owner = CreditCardOwner(organization_id=organization_id)
        payment_result = await PaymentService(self._session, self._request).charge_saved_card_for_booking(
            owner,
            credit_card_id=credit_card_id,
            amount=charge_amount,
            order_id=order_id,
            verified_payment_method_nonce=verified_payment_method_nonce,
        )
        if not payment_result.success or not payment_result.braintree_transaction_id:
            msg = payment_result.processor_message or "Card payment was declined"
            raise ValidationError(msg)
        return payment_result

    async def _verify_org_payment_method(
        self,
        *,
        organization_id: str,
        payment_method: PaymentModel,
        payment_method_id: str,
    ) -> OrgPaymentMethod:
        stmt = select(OrgPaymentMethod).where(
            OrgPaymentMethod.id == payment_method_id,
            OrgPaymentMethod.organization_id == organization_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValidationError(f"payment_method_id '{payment_method_id}' does not belong to this organisation")
        if row.payment_model != payment_method:
            raise ValidationError(
                f"payment_method '{payment_method.value}' does not match the payment method configured on " f"'{payment_method_id}' (expected '{row.payment_model.value}')"
            )
        return row

    @staticmethod
    def _next_fixed_monthly_due_date(issue_date: date, day_of_month: int) -> date:
        if issue_date.day < day_of_month:
            return date(issue_date.year, issue_date.month, day_of_month)
        if issue_date.month == 12:
            return date(issue_date.year + 1, 1, day_of_month)
        return date(issue_date.year, issue_date.month + 1, day_of_month)

    def _invoice_due_date_for_payment_method(self, payment_method: OrgPaymentMethod, issue_date: date) -> date:
        schedule = payment_method.billing_schedule
        if schedule == BillingSchedule.IMMEDIATE:
            return issue_date
        if schedule == BillingSchedule.DAYS_AFTER_ORDER:
            days_after_order = int(payment_method.billing_days_after_order or 0)
            return issue_date.fromordinal(issue_date.toordinal() + days_after_order)
        day_of_month = int(payment_method.billing_day_of_month or 0)
        if day_of_month < 1 or day_of_month > 28:
            raise ValidationError("billing_day_of_month must be between 1 and 28 for FIXED_MONTHLY_DATE")
        return self._next_fixed_monthly_due_date(issue_date, day_of_month)

    @staticmethod
    def _pricing_config_snapshot(ctx_payment_config: object) -> dict[str, object | None]:
        vat_number = getattr(ctx_payment_config, "vat_number", None)
        vat_rate = getattr(ctx_payment_config, "vat_rate", None)
        vat_treatment = getattr(ctx_payment_config, "vat_treatment", None)
        delivery_attempt_fees = getattr(ctx_payment_config, "delivery_attempt_fees", None)
        return_attempt_fees = getattr(ctx_payment_config, "return_attempt_fees", None)
        return {
            "vat_number": str(vat_number).strip() if vat_number is not None else None,
            "vat_rate": getattr(vat_rate, "value", vat_rate),
            "vat_treatment": getattr(vat_treatment, "value", vat_treatment),
            "max_delivery_attempts": getattr(ctx_payment_config, "max_delivery_attempts", None),
            "delivery_attempt_fees": delivery_attempt_fees if isinstance(delivery_attempt_fees, list) else None,
            "max_return_attempts": getattr(ctx_payment_config, "max_return_attempts", None),
            "return_attempt_fees": return_attempt_fees if isinstance(return_attempt_fees, list) else None,
            "weight_margin_kg": getattr(ctx_payment_config, "weight_margin_kg", None),
            "weight_surcharge_per_kg": (
                str(getattr(ctx_payment_config, "weight_surcharge_per_kg")) if getattr(ctx_payment_config, "weight_surcharge_per_kg", None) is not None else None
            ),
        }

    async def resolve_create_order_parties(
        self,
        user: AuthUser,
        body: OrderCreateRequest,
        *,
        allow_any_org_contact: bool = False,
    ) -> tuple[str | None, str, AuthUser]:
        """Resolve (org_id, contact_user_id, actor) for an order-create/draft-submit.

        ``allow_any_org_contact=True`` widens the B2B branch from "contact must equal
        the caller" to "contact must be any active contact of the caller's org". Use
        this on the draft-submit path so a teammate can publish a draft someone else
        in their org started — the contact_user_id stored on the draft is the original
        author, who is still a valid contact of the same organisation.
        """
        if body.client_type == ClientTypeEnum.B2C:
            raise ValidationError("B2C orders are not yet supported")

        org_id = body.organization_id
        contact_user_id = body.contact_user_id
        org_contact_repo = OrgContactRepository(self._session)

        if _order_privileged_admin(user):
            assert org_id is not None
            contact = await org_contact_repo.get_active_contact_for_user(org_id, contact_user_id)
            if contact is None:
                raise ValidationError("contact_user_id is not an active contact for this organisation")
            return org_id, contact_user_id, user

        if user.role == UserRole.CUSTOMER_B2B:
            if not user.organization_id:
                raise ForbiddenError("Organisation context is required")
            assert org_id is not None
            if user.organization_id != org_id:
                raise ForbiddenError("Cannot access another organisation")
            if contact_user_id != user.id and not allow_any_org_contact:
                raise ForbiddenError("contact_user_id must match the authenticated user for organisation orders")
            # Draft-submit allows a teammate's contact_user_id through; skip the active-
            # contact lookup since the org already gates draft access.
            return org_id, contact_user_id, user

        raise ForbiddenError("This action is not allowed for your role")

    async def _attach_effective_plans(
        self,
        stops: list[StopInput],
        *,
        organization_id: str,
    ) -> None:
        """Pre-resolve each stop's pricing plan via the live effective-tier service.

        Writes the converted plan dict back onto ``stop.resolved_plan`` so the synchronous
        ``compute_price_breakdown`` consumes the same data that ``/service-tiers/effective-for-org``
        returns — global + org-override merge with live contract-line state — instead of the
        possibly-stale ``organizations.pricing_plans`` JSON snapshot.
        """
        tier_svc = ServiceTierService(self._session)
        for stop in stops:
            tier = await tier_svc.resolve_effective_tier_for_org(
                organization_id,
                tier_id=stop.service_tier_id,
                tier_name=stop.service_tier_name,
            )
            stop.resolved_plan = effective_tier_to_plan(tier)

    async def compute_order_price_breakdown(
        self,
        *,
        user: AuthUser,
        body: OrderPriceBreakdownRequest,
    ) -> OrderPriceBreakdownResponse:
        if body.client_type == ClientTypeEnum.B2C:
            raise ValidationError("Not yet implemented")
        org_id = (body.organization_id or "").strip()
        if not org_id:
            raise ValidationError("organization_id is required")
        if user.role == UserRole.CUSTOMER_B2B.value and (not user.organization_id or user.organization_id != org_id):
            raise ForbiddenError("Cannot access another organisation")

        pricing_stops: list[StopInput] = [
            StopInput(
                index=idx,
                service_tier_name=stop.service_tier_name,
                service_tier_id=stop.service_tier_id,
                packages=[
                    PackageInput(
                        index=pi,
                        declared_weight_kg=pkg.declared_weight_kg,
                        length_cm=pkg.length_cm,
                        width_cm=pkg.width_cm,
                        height_cm=pkg.height_cm,
                    )
                    for pi, pkg in enumerate(stop.packages, start=1)
                ],
            )
            for idx, stop in enumerate(body.delivery_stops, start=1)
        ]

        ctx = await load_pricing_context(self._session, org_id)
        validate_package_restrictions(ctx.org, pricing_stops)
        await self._attach_effective_plans(pricing_stops, organization_id=org_id)
        pricing = compute_price_breakdown(ctx=ctx, stops=pricing_stops)
        bd = pricing.breakdown
        detail = OrderPriceBreakdownDetail.model_validate(bd)
        return OrderPriceBreakdownResponse(
            subtotal=detail.subtotal,
            vat_amount=detail.vat_amount,
            total_amount=detail.total,
            breakdown=detail,
        )

    async def create_order(
        self,
        *,
        client_type: ClientTypeEnum,
        organization_id: str | None = None,
        contact_user_id: str,
        created_by_id: str | None = None,
        actor: AuthUser | None = None,
        pickup_address_id: str,
        requested_pickup_date: date | None = None,
        payment_method: PaymentModel,
        payment_method_id: str,
        credit_card_id: str | None = None,
        payment_method_nonce: str | None = None,
        delivery_stops: list[DeliveryStopCreateItem],
    ) -> Order:
        if client_type == ClientTypeEnum.B2C:
            raise ValidationError("Not yet implemented")
        if not organization_id:
            raise ValidationError("organization_id is required")

        resolved_payment_method = PaymentModel(payment_method) if not isinstance(payment_method, PaymentModel) else payment_method
        org_payment_method = await self._verify_org_payment_method(
            organization_id=organization_id,
            payment_method=resolved_payment_method,
            payment_method_id=payment_method_id,
        )
        pickup_svc = PickupAddressService(self._session, self._request)
        await pickup_svc.assert_usable_for_order(
            pickup_address_id,
            organization_id=organization_id,
        )
        if resolved_payment_method == PaymentModel.CARD:
            if not credit_card_id:
                raise ValidationError("credit_card_id is required when payment_method is CARD")
            await PaymentService(self._session, self._request).verify_credit_card_belongs_to_org(
                organization_id=organization_id,
                credit_card_id=credit_card_id,
            )

        credit_ledger_svc: OrgCreditLedgerService | None = None
        if resolved_payment_method == PaymentModel.CREDIT_ACCOUNT:
            credit_ledger_svc = OrgCreditLedgerService(self._session, self._request)

        pricing_stops: list[StopInput] = [
            StopInput(
                index=idx,
                service_tier_name=stop.service_tier_name,
                service_tier_id=stop.service_tier_id,
                packages=[
                    PackageInput(
                        index=pi,
                        declared_weight_kg=pkg.declared_weight_kg,
                        length_cm=pkg.length_cm,
                        width_cm=pkg.width_cm,
                        height_cm=pkg.height_cm,
                    )
                    for pi, pkg in enumerate(stop.packages, start=1)
                ],
            )
            for idx, stop in enumerate(delivery_stops, start=1)
        ]

        ctx = await load_pricing_context(self._session, organization_id)
        logger.info(f"Pricing context: {ctx}")
        validate_package_restrictions(ctx.org, pricing_stops)
        await self._attach_effective_plans(pricing_stops, organization_id=organization_id)

        pricing = compute_price_breakdown(ctx=ctx, stops=pricing_stops)
        pricing_config_snapshot = self._pricing_config_snapshot(ctx.payment_config)

        if credit_ledger_svc is not None:
            await credit_ledger_svc.assert_can_consume(
                organization_id=organization_id,
                amount=pricing.total_amount,
            )

        braintree_transaction_id: str | None = None
        braintree_status: str | None = None
        transaction_fee: Decimal = Decimal("0")
        pending_payment_id: str | None = None
        created_invoice: Invoice | None = None
        invoice_issue_date = date.today()
        invoice_due_date = self._invoice_due_date_for_payment_method(org_payment_method, invoice_issue_date)

        async with self._session.begin_nested():
            is_b2b = client_type == ClientTypeEnum.B2B
            order_data: dict[str, object] = {
                "organization_id": organization_id,
                "customer_id": None if is_b2b else contact_user_id,
                "contact_user_id": contact_user_id if is_b2b else None,
                "created_by_id": created_by_id,
                "pickup_address_id": pickup_address_id,
                "requested_pickup_date": requested_pickup_date,
                "payment_method": resolved_payment_method,
                "payment_method_id": payment_method_id,
                "subtotal": pricing.subtotal,
                "vat_amount": pricing.vat_amount,
                "total_amount": pricing.total_amount,
                "braintree_transaction_id": None,
                "pricing_config_snapshot": pricing_config_snapshot,
                "status": OrderStatus.PENDING_PICKUP,
            }
            order = Order(**order_data)
            self._session.add(order)
            await self._session.flush()

            created_stops: list[DeliveryStop] = []
            for stop, pricing_stop in zip(delivery_stops, pricing_stops, strict=True):
                stop_obj = DeliveryStop(
                    order_id=order.id,
                    recipient_first_name=stop.recipient_first_name,
                    recipient_last_name=stop.recipient_last_name,
                    recipient_phone=stop.recipient_phone,
                    recipient_email=stop.recipient_email,
                    line_1=stop.line_1,
                    line_2=stop.line_2,
                    city=stop.city,
                    postcode=stop.postcode,
                    latitude=stop.latitude,
                    longitude=stop.longitude,
                    signature_required=stop.signature_required,
                    safe_place_allowed=stop.safe_place_allowed,
                    status=DeliveryStopStatus.PENDING_PICKUP,
                    service_tier_id=None,
                    service_tier=plan_display_name(pricing_stop.resolved_plan) or pricing_stop.service_tier_name,
                )
                created_stops.append(stop_obj)
            self._session.add_all(created_stops)
            await self._session.flush()

            created_packages: list[list[Package]] = []
            stop_notes: list[StopNote] = []
            for stop_obj, stop, pricing_stop in zip(created_stops, delivery_stops, pricing_stops, strict=True):
                pricing_stop.stop_uuid = stop_obj.id
                pricing_stop.tracking_id = stop_obj.tracking_id

                if stop.customer_note and stop.customer_note.strip():
                    stop_notes.append(
                        StopNote(
                            delivery_stop_id=stop_obj.id,
                            note_type=StopNoteType.CUSTOMER.value,
                            message=stop.customer_note.strip(),
                            is_blocking=False,
                            sort_order=0,
                        )
                    )

                stop_packages = [
                    Package(
                        order_id=order.id,
                        delivery_stop_id=stop_obj.id,
                        length_cm=pkg.length_cm,
                        width_cm=pkg.width_cm,
                        height_cm=pkg.height_cm,
                        declared_weight_kg=pkg.declared_weight_kg,
                        declared_value=pkg.declared_value,
                        status=PackageStatus.PENDING_PICKUP,
                    )
                    for pkg in stop.packages
                ]
                created_packages.append(stop_packages)
                self._session.add_all(stop_packages)

            if stop_notes:
                self._session.add_all(stop_notes)
            await self._session.flush()

            pricing.breakdown["id"] = order.id
            pricing.breakdown["order_id"] = order.order_id
            for stop_obj, pricing_stop, stop_packages in zip(created_stops, pricing_stops, created_packages, strict=True):
                pricing_stop.stop_uuid = stop_obj.id
                pricing_stop.tracking_id = stop_obj.tracking_id
                if pricing_stop.price_breakdown is not None:
                    pricing_stop.price_breakdown["id"] = stop_obj.id
                    pricing_stop.price_breakdown["tracking_id"] = stop_obj.tracking_id
                for pkg_obj, pkg_input in zip(stop_packages, pricing_stop.packages, strict=True):
                    pkg_input.package_uuid = pkg_obj.id
                    pkg_input.package_ref = pkg_obj.package_id
                    if pkg_input.price_breakdown is not None:
                        pkg_input.price_breakdown["id"] = pkg_obj.id
                        pkg_input.price_breakdown["package_id"] = pkg_obj.package_id
                stop_obj.price_breakdown = pricing_stop.price_breakdown
                for pkg_obj, pkg_input in zip(stop_packages, pricing_stop.packages, strict=True):
                    pkg_obj.price_breakdown = pkg_input.price_breakdown

            order.price_breakdown = pricing.breakdown

            await self._session.flush()

            await self._append_order_status_event(
                order_id=order.id,
                from_status=None,
                to_status=OrderStatus.PENDING_PICKUP,
                actor_user_id=created_by_id,
            )
            for stop_obj in created_stops:
                await self._append_delivery_stop_status_event(
                    delivery_stop_id=stop_obj.id,
                    from_status=None,
                    to_status=DeliveryStopStatus.PENDING_PICKUP,
                    actor_user_id=created_by_id,
                )
            for stop_packages in created_packages:
                for pkg_obj in stop_packages:
                    await self._append_package_status_event(
                        package_id=pkg_obj.id,
                        from_status=None,
                        to_status=PackageStatus.PENDING_PICKUP,
                        actor_user_id=created_by_id,
                    )
            invoice = await self._sync_order_invoice(
                order=order,
                created_by_id=created_by_id,
                stops=created_stops,
                packages_by_stop={stop.id: pkgs for stop, pkgs in zip(created_stops, created_packages, strict=True)},
                issue_date=invoice_issue_date,
                due_date=invoice_due_date,
            )
            created_invoice = invoice
            if credit_ledger_svc is not None:
                await credit_ledger_svc.consume_credit(
                    organization_id=organization_id,
                    actor=actor,
                    amount=order.total_amount,
                    source_type=OrgCreditLedgerSourceType.INVOICE,
                    source_id=created_invoice.id,
                    idempotency_key=f"invoice:{created_invoice.id}:consume",
                )
            if resolved_payment_method == PaymentModel.CARD and credit_card_id:
                pending_payment = await BillingService(self._session, self._request).create_pending_payment(
                    organization_id=organization_id,
                    customer_id=contact_user_id,
                    amount=Decimal(order.total_amount or 0),
                    payment_date=date.today(),
                    recorded_by_id=created_by_id,
                    provider=PaymentProvider.BRAINTREE,
                    notes=f"Pending card payment for order {order.order_id}",
                    metadata_json={
                        "order_id": order.id,
                        "order_ref": order.order_id,
                        "credit_card_id": credit_card_id,
                    },
                    audit_ctx=self._audit_ctx_or_none(user_id=created_by_id),
                )
                pending_payment_id = pending_payment.id
            await self._session.flush()

        if resolved_payment_method == PaymentModel.CARD and credit_card_id:
            assert payment_method_nonce is not None and str(payment_method_nonce).strip()
            assert pending_payment_id is not None
            charge_result: BookingChargeResult = await self._precharge_saved_card_for_order(
                organization_id=organization_id,
                credit_card_id=credit_card_id,
                charge_amount=pricing.total_amount,
                verified_payment_method_nonce=str(payment_method_nonce).strip(),
                order_id=pending_payment_id,
            )
            braintree_transaction_id = charge_result.braintree_transaction_id
            braintree_status = getattr(charge_result, "braintree_status", None)
            transaction_fee = Decimal(charge_result.transaction_fee or 0)
            if braintree_transaction_id:
                assert created_invoice is not None
                await self._record_card_billing_for_order(
                    order=order,
                    invoice=created_invoice,
                    organization_id=organization_id,
                    created_by_id=created_by_id,
                    braintree_transaction_id=braintree_transaction_id,
                    braintree_status=braintree_status,
                    transaction_fee=transaction_fee,
                    payment_id=pending_payment_id,
                )

        return order

    async def save_draft(
        self,
        *,
        created_by_id: str | None,
        payload: dict,
    ) -> OrderDraft:
        total_amount = self._pop_draft_total_amount(payload)
        return await self._draft_repo.create(
            {
                "organization_id": payload.get("organization_id"),
                "customer_id": payload.get("customer_id"),
                "created_by_id": created_by_id,
                "status": OrderDraftStatus.PENDING,
                "payload": payload,
                "total_amount": total_amount,
            }
        )

    async def get_draft_or_404(self, draft_id: str) -> OrderDraft:
        draft = await self._draft_repo.get_by_id(draft_id)
        if draft is None:
            raise NotFoundError(resource="order_draft", id=draft_id)
        return draft

    async def update_draft(
        self,
        draft_id: str,
        *,
        payload: dict,
    ) -> OrderDraft:
        draft = await self.get_draft_or_404(draft_id)
        incoming_total = self._pop_draft_total_amount(payload)
        merged = {**draft.payload, **payload}
        legacy_total = self._pop_draft_total_amount(merged)
        update_fields: dict[str, object] = {
            "payload": merged,
            "organization_id": merged.get("organization_id"),
            "customer_id": merged.get("customer_id"),
        }
        if incoming_total is not None:
            update_fields["total_amount"] = incoming_total
        elif legacy_total is not None and draft.total_amount is None:
            update_fields["total_amount"] = legacy_total
        return await self._draft_repo.update_by_id(draft_id, update_fields)

    @staticmethod
    def _pop_draft_total_amount(payload: dict) -> Decimal | None:
        raw = payload.pop("total_amount", None)
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    async def list_drafts(
        self,
        organization_id: str | None,
        *,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[tuple[OrderDraft, str | None, str | None]], int]:
        return await self._draft_repo.list_for_org(
            organization_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=limit,
        )

    async def delete_draft(self, draft_id: str) -> None:
        await self.get_draft_or_404(draft_id)
        await self._draft_repo.hard_delete(draft_id)

    async def submit_draft(self, draft_id: str, *, payload: dict, user: AuthUser) -> Order:
        draft = await self.get_draft_or_404(draft_id)
        if draft.status != OrderDraftStatus.PENDING:
            raise ValidationError("Only pending drafts can be submitted")

        raw = {**(draft.payload or {}), **(payload or {})}
        raw.setdefault("client_type", ClientTypeEnum.B2B)
        resolved_organization_id = raw.get("organization_id") or draft.organization_id
        if resolved_organization_id is not None:
            raw["organization_id"] = resolved_organization_id

        try:
            parsed = OrderCreateRequest.model_validate(raw)
        except PydanticValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {"msg": "Draft payload is incomplete"}
            location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
            message = first.get("msg", "Invalid draft payload")
            raise ValidationError(f"{location}: {message}" if location else message) from None

        validate_create_order_for_actor(user, parsed)
        # Draft submit: the stored contact_user_id may be a different teammate (the
        # original author of the draft), so widen the contact check to "any active
        # contact of this org" instead of requiring identity with the caller.
        org_id, contact_user_id, created_by = await self.resolve_create_order_parties(user, parsed, allow_any_org_contact=True)
        order = await self.create_order(
            client_type=parsed.client_type,
            organization_id=org_id,
            contact_user_id=contact_user_id,
            created_by_id=created_by.id,
            actor=created_by,
            pickup_address_id=parsed.pickup_address_id,
            requested_pickup_date=parsed.requested_pickup_date,
            payment_method=parsed.payment_method,
            payment_method_id=parsed.payment_method_id,
            credit_card_id=parsed.credit_card_id,
            payment_method_nonce=parsed.payment_method_nonce,
            delivery_stops=parsed.delivery_stops,
        )
        await self._draft_repo.update_by_id(
            draft_id,
            {
                "status": OrderDraftStatus.PUBLISHED,
                "published_by_id": user.id,
            },
        )
        return order

    async def get_order_or_404(self, order_id: str) -> Order:
        order = await self._order_repo.get_by_id(order_id)
        if order is None:
            raise NotFoundError(resource="order", id=order_id)
        return order

    async def get_order_tree_or_404(
        self,
        order_id: str,
    ) -> tuple[Order, list[DeliveryStop], dict[str, list[Package]]]:
        order, stops, packages_by_stop = await self._order_repo.get_order_with_stops_and_packages(order_id)
        if order is None:
            raise NotFoundError(resource="order", id=order_id)
        return order, stops, packages_by_stop

    async def get_order_by_master_label_or_404(self, master_label_id: str) -> Order:
        order = await self._order_repo.get_by_master_label(master_label_id)
        if order is None:
            raise NotFoundError(resource="order_master_label", id=master_label_id)
        return order

    async def get_order_tree_by_master_label_or_404(
        self,
        master_label_id: str,
    ) -> tuple[Order, list[DeliveryStop], dict[str, list[Package]]]:
        order = await self.get_order_by_master_label_or_404(master_label_id)
        loaded, stops, packages_by_stop = await self._order_repo.get_order_with_stops_and_packages(order.id)
        return loaded or order, stops, packages_by_stop

    async def list_orders(
        self,
        organization_id: str | None,
        *,
        statuses: list[str] | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict], int]:
        return await self._order_repo.list_for_org(
            organization_id,
            statuses=statuses,
            search=search,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=limit,
        )

    async def _returns_counts_for_date_range(
        self,
        organization_id: str | None,
        d0: date,
        d1: date,
    ) -> ReturnsCounts:
        df, dte = _to_period_bounds(d0, d1)
        by_status = await self._order_repo.package_status_counts(
            organization_id,
            date_from=df,
            date_to_exclusive=dte,
        )
        in_transit = sum(by_status.get(s.value, 0) for s in RETURN_IN_TRANSIT_STATUSES)
        disposed = by_status.get(PackageStatus.DISPOSED.value, 0)
        returned = by_status.get(PackageStatus.RETURNED.value, 0)
        initiated = by_status.get(PackageStatus.RETURN_INITIATED.value, 0)
        avg_days = await self._order_repo.avg_return_resolution_days(
            organization_id,
            date_from=df,
            date_to_exclusive=dte,
        )
        return ReturnsCounts(
            total=in_transit + disposed + returned,
            in_transit=in_transit,
            disposed=disposed,
            returned=returned,
            initiated=initiated,
            avg_resolution_days=round(avg_days, 1) if avg_days is not None else None,
        )

    async def get_order_summary(
        self,
        organization_id: str | None,
        params: SummaryDateRangeParams,
    ) -> OrderSummaryResult:
        window = resolve_summary_window(
            period=params.period,
            date_from=params.date_from,
            date_to=params.date_to,
            today=date.today(),
        )
        current_from_dt, current_to_dt_excl = _to_period_bounds(window.current_from, window.current_to)
        prev_from_dt, prev_to_dt_excl = _to_period_bounds(window.previous_from, window.previous_to)

        current_by_status = await self._order_repo.order_status_counts(
            organization_id,
            date_from=current_from_dt,
            date_to_exclusive=current_to_dt_excl,
        )
        previous_by_status = await self._order_repo.order_status_counts(
            organization_id,
            date_from=prev_from_dt,
            date_to_exclusive=prev_to_dt_excl,
        )

        current_aggr = self._order_repo.aggregate_order_card_counts(current_by_status)
        previous_aggr = self._order_repo.aggregate_order_card_counts(previous_by_status)

        return OrderSummaryResult(
            period_from=window.current_from,
            period_to=window.current_to,
            previous_period_from=window.previous_from,
            previous_period_to=window.previous_to,
            comparison_label=window.comparison_label,
            current=OrderStatusCounts(**current_aggr),
            previous=OrderStatusCounts(**previous_aggr),
        )

    async def get_failed_deliveries_summary(
        self,
        organization_id: str | None,
        params: SummaryDateRangeParams,
    ) -> FailedDeliverySummaryResult:
        window = resolve_summary_window(
            period=params.period,
            date_from=params.date_from,
            date_to=params.date_to,
            today=date.today(),
        )
        c0, c1 = _to_period_bounds(window.current_from, window.current_to)
        p0, p1 = _to_period_bounds(window.previous_from, window.previous_to)
        cur_by = await self._order_repo.package_status_counts(
            organization_id,
            date_from=c0,
            date_to_exclusive=c1,
        )
        prev_by = await self._order_repo.package_status_counts(
            organization_id,
            date_from=p0,
            date_to_exclusive=p1,
        )
        return FailedDeliverySummaryResult(
            period_from=window.current_from,
            period_to=window.current_to,
            previous_period_from=window.previous_from,
            previous_period_to=window.previous_to,
            comparison_label=window.comparison_label,
            current=_failed_delivery_counts_from_by_status(cur_by),
            previous=_failed_delivery_counts_from_by_status(prev_by),
        )

    async def get_returns_summary(
        self,
        organization_id: str | None,
        params: SummaryDateRangeParams,
    ) -> ReturnsSummaryResult:
        window = resolve_summary_window(
            period=params.period,
            date_from=params.date_from,
            date_to=params.date_to,
            today=date.today(),
        )
        current = await self._returns_counts_for_date_range(organization_id, window.current_from, window.current_to)
        previous = await self._returns_counts_for_date_range(organization_id, window.previous_from, window.previous_to)
        return ReturnsSummaryResult(
            period_from=window.current_from,
            period_to=window.current_to,
            previous_period_from=window.previous_from,
            previous_period_to=window.previous_to,
            comparison_label=window.comparison_label,
            current=current,
            previous=previous,
        )

    async def list_failed_deliveries(
        self,
        organization_id: str | None,
        *,
        package_statuses: list[PackageStatus] | None = None,
        attempt_numbers: list[int] | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[FailedDeliveryStopRow], int]:
        date_from_dt, date_to_dt_excl = _to_period_bounds(date_from, date_to)
        rows, total = await self._order_repo.list_failed_delivery_stops(
            organization_id,
            package_statuses=package_statuses,
            attempt_numbers=attempt_numbers,
            search=search,
            date_from=date_from_dt,
            date_to_exclusive=date_to_dt_excl,
            offset=offset,
            limit=limit,
        )
        if not rows:
            return [], 0
        stop_ids = [row["delivery_stop_id"] for row in rows]
        package_rows = await self._order_repo.packages_for_failed_stops(
            stop_ids,
            package_statuses=package_statuses,
        )
        pkg_pks = [p["package_pk"] for p in package_rows]
        all_stop_events = await self._delivery_stop_event_repo.list_by_delivery_stop_ids(stop_ids)
        all_pkg_events = await self._package_event_repo.list_by_package_ids(pkg_pks) if pkg_pks else []
        by_stop_ev: dict[str, list[DeliveryStopEvent]] = {sid: [] for sid in stop_ids}
        for ev in all_stop_events:
            by_stop_ev.setdefault(ev.delivery_stop_id, []).append(ev)
        for evs in by_stop_ev.values():
            evs.sort(key=lambda e: (e.created_at, e.id))
        by_pkg_ev: dict[str, list[PackageEvent]] = {pid: [] for pid in pkg_pks}
        for ev in all_pkg_events:
            by_pkg_ev.setdefault(ev.package_id, []).append(ev)
        for evs in by_pkg_ev.values():
            evs.sort(key=lambda e: (e.created_at, e.id))

        packages_by_stop: dict[str, list[FailedPackageRow]] = {}
        for pkg in package_rows:
            stop_id = pkg["delivery_stop_id"]
            pk = pkg["package_pk"]
            packages_by_stop.setdefault(stop_id, []).append(
                FailedPackageRow(
                    package_pk=pk,
                    package_id=pkg["package_id"],
                    status=_status_value(pkg["status"]),
                    reason=_compose_reason(pkg.get("reason_code"), pkg.get("details")),
                    status_events=[self._status_event_record_from_package(ev) for ev in by_pkg_ev.get(pk, [])],
                )
            )

        items: list[FailedDeliveryStopRow] = []
        for row in rows:
            stop_status_value = _status_value(row["stop_status"])
            stop_status_enum = DeliveryStopStatus(stop_status_value) if stop_status_value else None
            attempt_number = int(row.get("attempt_number") or 0) or attempt_number_from_stop_status(stop_status_enum)
            previous_attempt_at = row["stop_updated_at"] if attempt_number > 0 else None
            sid = row["delivery_stop_id"]
            items.append(
                FailedDeliveryStopRow(
                    delivery_stop_id=sid,
                    tracking_id=row["tracking_id"],
                    postcode=row["postcode"],
                    order_id=row["order_id"],
                    order_reference=row["order_reference"],
                    stop_status=stop_status_value,
                    attempt_number=attempt_number,
                    max_attempts=MAX_DELIVERY_ATTEMPTS,
                    previous_attempt_at=previous_attempt_at,
                    next_attempt_at=None,
                    stop_status_events=[self._status_event_record_from_stop(ev) for ev in by_stop_ev.get(sid, [])],
                    packages=packages_by_stop.get(sid, []),
                )
            )
        return items, total

    async def list_returns(
        self,
        organization_id: str | None,
        *,
        package_statuses: list[PackageStatus] | None = None,
        attempt_numbers: list[int] | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ReturnStopRow], int]:
        date_from_dt, date_to_dt_excl = _to_period_bounds(date_from, date_to)
        rows, total = await self._order_repo.list_return_stops(
            organization_id,
            package_statuses=package_statuses,
            attempt_numbers=attempt_numbers,
            search=search,
            date_from=date_from_dt,
            date_to_exclusive=date_to_dt_excl,
            offset=offset,
            limit=limit,
        )
        if not rows:
            return [], 0
        stop_ids = [row["delivery_stop_id"] for row in rows]
        package_rows = await self._order_repo.packages_for_return_stops(
            stop_ids,
            package_statuses=package_statuses,
        )
        pkg_pks = [p["package_pk"] for p in package_rows]
        all_stop_events = await self._delivery_stop_event_repo.list_by_delivery_stop_ids(stop_ids)
        all_pkg_events = await self._package_event_repo.list_by_package_ids(pkg_pks) if pkg_pks else []
        by_stop_ev: dict[str, list[DeliveryStopEvent]] = {sid: [] for sid in stop_ids}
        for ev in all_stop_events:
            by_stop_ev.setdefault(ev.delivery_stop_id, []).append(ev)
        for evs in by_stop_ev.values():
            evs.sort(key=lambda e: (e.created_at, e.id))
        by_pkg_ev: dict[str, list[PackageEvent]] = {pid: [] for pid in pkg_pks}
        for ev in all_pkg_events:
            by_pkg_ev.setdefault(ev.package_id, []).append(ev)
        for evs in by_pkg_ev.values():
            evs.sort(key=lambda e: (e.created_at, e.id))

        packages_by_stop: dict[str, list[ReturnPackageRow]] = {}
        for pkg in package_rows:
            stop_id = pkg["delivery_stop_id"]
            pk = pkg["package_pk"]
            packages_by_stop.setdefault(stop_id, []).append(
                ReturnPackageRow(
                    package_pk=pk,
                    package_id=pkg["package_id"],
                    status=_status_value(pkg["status"]),
                    return_reason=_compose_reason(pkg.get("reason_code"), pkg.get("details")),
                    initiated_at=pkg.get("initiated_at"),
                    status_events=[self._status_event_record_from_package(ev) for ev in by_pkg_ev.get(pk, [])],
                )
            )

        items: list[ReturnStopRow] = []
        for row in rows:
            sid = row["delivery_stop_id"]
            stop_status_value = _status_value(row.get("stop_status"))
            stop_status_enum = DeliveryStopStatus(stop_status_value) if stop_status_value else None
            attempt_number = int(row.get("attempt_number") or 0) or attempt_number_from_stop_status(stop_status_enum)
            items.append(
                ReturnStopRow(
                    delivery_stop_id=sid,
                    tracking_id=row["tracking_id"],
                    postcode=row["postcode"],
                    order_id=row["order_id"],
                    order_reference=row["order_reference"],
                    initiated_at=row.get("initiated_at"),
                    stop_status=stop_status_value,
                    attempt_number=attempt_number,
                    max_attempts=MAX_DELIVERY_ATTEMPTS,
                    stop_status_events=[self._status_event_record_from_stop(ev) for ev in by_stop_ev.get(sid, [])],
                    packages=packages_by_stop.get(sid, []),
                )
            )
        return items, total

    @staticmethod
    def order_summary_to_response(result: OrderSummaryResult) -> OrderSummaryResponse:
        return OrderSummaryResponse(
            period_from=result.period_from,
            period_to=result.period_to,
            previous_period_from=result.previous_period_from,
            previous_period_to=result.previous_period_to,
            comparison_label=result.comparison_label,
            total_orders=_to_summary_stat(result.current.total, result.previous.total),
            pickups_on_route=_to_summary_stat(result.current.pickups_on_route, result.previous.pickups_on_route),
            delivered=_to_summary_stat(result.current.delivered, result.previous.delivered),
            cancelled=_to_summary_stat(result.current.cancelled, result.previous.cancelled),
            failed=_to_summary_stat(result.current.failed, result.previous.failed),
            returned=_to_summary_stat(result.current.returned, result.previous.returned),
        )

    @staticmethod
    def failed_deliveries_summary_to_response(
        result: FailedDeliverySummaryResult,
    ) -> FailedDeliveriesSummaryResponse:
        c, p = result.current, result.previous
        return FailedDeliveriesSummaryResponse(
            period_from=result.period_from,
            period_to=result.period_to,
            previous_period_from=result.previous_period_from,
            previous_period_to=result.previous_period_to,
            comparison_label=result.comparison_label,
            total_failed=_to_summary_stat(c.total, p.total),
            missing=_to_summary_stat(c.missing, p.missing),
            damaged=_to_summary_stat(c.damaged, p.damaged),
            cancelled=_to_summary_stat(c.cancelled, p.cancelled),
            customer_not_home=_to_summary_stat(c.customer_not_home, p.customer_not_home),
            refused=_to_summary_stat(c.refused, p.refused),
            disposed=_to_summary_stat(c.disposed, p.disposed),
        )

    @staticmethod
    def returns_summary_to_response(
        result: ReturnsSummaryResult,
    ) -> ReturnsSummaryResponse:
        c, p = result.current, result.previous
        return ReturnsSummaryResponse(
            period_from=result.period_from,
            period_to=result.period_to,
            previous_period_from=result.previous_period_from,
            previous_period_to=result.previous_period_to,
            comparison_label=result.comparison_label,
            total_returns=_to_summary_stat(c.total, p.total),
            returns_in_transit=_to_summary_stat(c.in_transit, p.in_transit),
            disposed_packages=_to_summary_stat(c.disposed, p.disposed),
            returned_packages=_to_summary_stat(c.returned, p.returned),
            initiated=_to_summary_stat(c.initiated, p.initiated),
            avg_resolution_days=_to_float_stat(c.avg_resolution_days, p.avg_resolution_days),
        )

    @staticmethod
    def failed_delivery_stop_to_item(row: FailedDeliveryStopRow) -> FailedDeliveryStopItem:
        return FailedDeliveryStopItem(
            delivery_stop_id=row.delivery_stop_id,
            tracking_id=row.tracking_id,
            postcode=row.postcode,
            order_id=row.order_id,
            order_reference=row.order_reference,
            stop_status=DeliveryStopStatus(row.stop_status),
            attempt_number=row.attempt_number,
            max_attempts=row.max_attempts,
            previous_attempt_at=row.previous_attempt_at,
            next_attempt_at=row.next_attempt_at,
            stop_status_events=[OrderService._entity_item_from_stop_event_record(e) for e in row.stop_status_events],
            packages=[
                FailedDeliveryPackageEntry(
                    id=p.package_pk,
                    package_id=p.package_id,
                    status=PackageStatus(p.status),
                    reason=p.reason,
                    status_events=[OrderService._entity_item_from_package_event_record(e) for e in p.status_events],
                )
                for p in row.packages
            ],
        )

    @staticmethod
    def return_stop_to_item(row: ReturnStopRow) -> ReturnStopItem:
        stop_status = DeliveryStopStatus(row.stop_status) if row.stop_status else None
        return ReturnStopItem(
            delivery_stop_id=row.delivery_stop_id,
            tracking_id=row.tracking_id,
            postcode=row.postcode,
            order_id=row.order_id,
            order_reference=row.order_reference,
            stop_status=stop_status,
            attempt_number=row.attempt_number,
            max_attempts=row.max_attempts,
            initiated_at=row.initiated_at,
            stop_status_events=[OrderService._entity_item_from_stop_event_record(e) for e in row.stop_status_events],
            packages=[
                ReturnPackageEntry(
                    id=p.package_pk,
                    package_id=p.package_id,
                    status=PackageStatus(p.status),
                    return_reason=p.return_reason,
                    initiated_at=p.initiated_at,
                    status_events=[OrderService._entity_item_from_package_event_record(e) for e in p.status_events],
                )
                for p in row.packages
            ],
        )

    async def list_delivery_stops(self, order_id: str) -> list[DeliveryStop]:
        await self.get_order_or_404(order_id)
        return await self._order_repo.list_stops(order_id)

    async def get_delivery_stop_or_404(self, order_id: str, stop_id: str) -> DeliveryStop:
        stop = await self._order_repo.get_stop(order_id, stop_id)
        if stop is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        return stop

    async def get_delivery_stop_by_tracking_or_404(self, tracking_id: str) -> DeliveryStop:
        stop = await self._order_repo.get_stop_by_tracking(tracking_id)
        if stop is None:
            raise NotFoundError(resource="delivery_stop_tracking", id=tracking_id)
        return stop

    async def list_packages_for_stop(self, order_id: str, stop_id: str) -> list[Package]:
        await self.get_delivery_stop_or_404(order_id, stop_id)
        return await self._order_repo.list_packages_for_stop(stop_id)

    async def get_delivery_stop_detail_response_or_404(
        self, order_id: str, stop_id: str
    ) -> DeliveryStopDetailResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        stop_index = next(
            (i + 1 for i, s in enumerate(stops) if s.id == stop_id),
            None,
        )
        stop = next((s for s in stops if s.id == stop_id), None)
        if stop is None or stop_index is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        packages = packages_by_stop.get(stop.id, [])
        stop_events = await self._delivery_stop_event_repo.list_by_delivery_stop(stop.id)
        package_ids = [p.id for p in packages]
        all_pkg_events = (
            await self._package_event_repo.list_by_package_ids(package_ids) if package_ids else []
        )
        events_by_pkg: dict[str, list[PackageEvent]] = {pid: [] for pid in package_ids}
        for ev in all_pkg_events:
            events_by_pkg.setdefault(ev.package_id, []).append(ev)

        pricing_plan = None
        if isinstance(stop.price_breakdown, dict):
            pp = stop.price_breakdown.get("pricing_plan")
            if isinstance(pp, dict):
                pricing_plan = pp

        delivery_attempts = sum(
            1 for ev in stop_events if "FAIL" in (ev.to_status or "").upper()
        )
        actual_delivery_date = None
        for ev in stop_events:
            if (ev.to_status or "").upper() == "DELIVERED":
                actual_delivery_date = ev.created_at.date() if ev.created_at else None
                break

        package_entries: list[DeliveryStopDetailPackageEntry] = []
        for pkg in packages:
            package_entries.append(
                DeliveryStopDetailPackageEntry(
                    id=pkg.id,
                    order_id=pkg.order_id,
                    delivery_stop_id=pkg.delivery_stop_id,
                    package_id=pkg.package_id,
                    status=pkg.status,
                    length_cm=pkg.length_cm,
                    width_cm=pkg.width_cm,
                    height_cm=pkg.height_cm,
                    declared_weight_kg=pkg.declared_weight_kg,
                    weight_kg=pkg.weight_kg,
                    declared_value=pkg.declared_value,
                    is_damaged=pkg.is_damaged,
                    created_at=pkg.created_at,
                    updated_at=pkg.updated_at,
                    version=pkg.version,
                    events=[
                        self._package_event_to_timeline_item(ev)
                        for ev in events_by_pkg.get(pkg.id, [])
                    ],
                )
            )

        pod_summary = await self._resolve_stop_pod_summary(stop.id)
        return_evidence = await self._resolve_stop_return_evidence(stop.id)
        failed_attempts = await self._resolve_stop_failed_attempts(stop.id)
        return_attempts = await self._resolve_stop_return_attempts(stop.id)

        return DeliveryStopDetailResponse(
            id=stop.id,
            order_id=stop.order_id,
            order_reference=order.order_id,
            organization_id=order.organization_id,
            stop_index=stop_index,
            tracking_id=stop.tracking_id,
            recipient_first_name=stop.recipient_first_name,
            recipient_last_name=stop.recipient_last_name,
            recipient_phone=stop.recipient_phone,
            recipient_email=stop.recipient_email,
            line_1=stop.line_1,
            line_2=stop.line_2,
            city=stop.city,
            postcode=stop.postcode,
            latitude=stop.latitude,
            longitude=stop.longitude,
            service_tier=stop.service_tier,
            service_tier_id=stop.service_tier_id,
            pricing_plan=pricing_plan,
            signature_required=stop.signature_required,
            safe_place_allowed=stop.safe_place_allowed,
            status=stop.status,
            scheduled_delivery_date=stop.scheduled_for,
            actual_delivery_date=actual_delivery_date,
            delivery_attempts=delivery_attempts,
            max_delivery_attempts=3,
            packages_count=len(packages),
            packages=package_entries,
            events=[self._delivery_stop_event_to_timeline_item(ev) for ev in stop_events],
            pod=pod_summary,
            return_evidence=return_evidence,
            failed_attempts=failed_attempts,
            return_attempts=return_attempts,
            created_at=stop.created_at,
            updated_at=stop.updated_at,
            version=stop.version,
        )

    async def _resolve_stop_return_evidence(self, stop_id: str) -> StopReturnEvidenceSummary | None:
        rows = await self._order_repo.list_evidence_images_for_stop(stop_id)
        if not rows:
            return None
        return StopReturnEvidenceSummary(
            photos_count=len(rows),
            photos=[
                StopReturnEvidenceEntry(
                    id=r.id,
                    image_key=r.image_key,
                    image_url=generate_image_url(r.image_key),
                    sort_order=r.sort_order,
                )
                for r in rows
            ],
        )

    async def _resolve_stop_failed_attempts(self, stop_id: str) -> list[StopAttemptEntry]:
        rows = await self._order_repo.list_failed_attempts_for_stop(stop_id)
        return await self._hydrate_attempt_entries(rows)

    async def _resolve_stop_return_attempts(self, stop_id: str) -> list[StopAttemptEntry]:
        rows = await self._order_repo.list_return_attempts_for_stop(stop_id)
        return await self._hydrate_attempt_entries(rows)

    async def _hydrate_attempt_entries(
        self,
        rows: list[DeliveryStopFailedAttempt] | list[DeliveryStopReturnAttempt],
    ) -> list[StopAttemptEntry]:
        if not rows:
            return []
        driver_ids = {r.driver_id for r in rows if r.driver_id}
        vehicle_ids = {r.vehicle_id for r in rows if r.vehicle_id}
        driver_names: dict[str, str] = {}
        if driver_ids:
            stmt = (
                select(Driver.id, User.first_name, User.last_name)
                .join(User, User.id == Driver.user_id)
                .where(Driver.id.in_(driver_ids))
            )
            for did, fn, ln in (await self._session.execute(stmt)).all():
                full = " ".join(p for p in (fn, ln) if p).strip()
                if full:
                    driver_names[did] = full
        vehicle_names: dict[str, str] = {}
        if vehicle_ids:
            stmt = select(
                Vehicle.id, Vehicle.fleet_custom_name, Vehicle.registration_number
            ).where(Vehicle.id.in_(vehicle_ids))
            for vid, custom, reg in (await self._session.execute(stmt)).all():
                label = (custom or "").strip() or (reg or "").strip()
                if label:
                    vehicle_names[vid] = label
        return [
            StopAttemptEntry(
                id=r.id,
                attempt_number=r.attempt_number,
                attempted_at=r.attempted_at,
                driver_id=r.driver_id,
                driver_name=driver_names.get(r.driver_id) if r.driver_id else None,
                vehicle_id=r.vehicle_id,
                vehicle_name=vehicle_names.get(r.vehicle_id) if r.vehicle_id else None,
                route_id=r.route_id,
                failure_reason=r.failure_reason,
                notes=r.notes,
                is_final=r.is_final,
            )
            for r in rows
        ]

    async def _resolve_stop_pod_summary(self, stop_id: str) -> StopPodSummary | None:
        pod_row = (
            await self._session.execute(
                select(StopPod).where(StopPod.delivery_stop_id == stop_id)
            )
        ).scalars().first()
        if pod_row is None:
            return None
        photos = (
            (
                await self._session.execute(
                    select(StopPodPhoto)
                    .where(StopPodPhoto.delivery_stop_id == stop_id)
                    .order_by(StopPodPhoto.sort_order.asc(), StopPodPhoto.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        if not photos and not pod_row.signature_image_key and not pod_row.completed_at:
            return None
        return StopPodSummary(
            photos_count=pod_row.photos_count or len(photos),
            signature_image_key=pod_row.signature_image_key,
            signature_image_url=(
                generate_image_url(pod_row.signature_image_key)
                if pod_row.signature_image_key
                else None
            ),
            signature_required_snapshot=pod_row.signature_required_snapshot,
            completed_at=pod_row.completed_at,
            photos=[
                StopPodPhotoEntry(
                    id=p.id,
                    image_key=p.image_key,
                    image_url=generate_image_url(p.image_key),
                    sort_order=p.sort_order,
                )
                for p in photos
            ],
        )

    async def get_order_detail_response_or_404(self, order_id: str) -> OrderDetailResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        linked_invoice = await InvoiceRepository(self._session).get_by_order_id(order.id)
        card_last_four = await self._resolve_card_last_four_for_order(order)
        return self._build_order_detail_response(
            order,
            stops,
            packages_by_stop,
            order.pickup_address,
            linked_invoice=linked_invoice,
            card_last_four=card_last_four,
        )

    async def get_order_timeline_response_or_404(self, order_id: str) -> OrderTimelineResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        order_event_rows = await self._order_event_repo.list_by_order(order.id)
        stop_ids = [s.id for s in stops]
        all_stop_events = await self._delivery_stop_event_repo.list_by_delivery_stop_ids(stop_ids)
        events_by_stop: dict[str, list[DeliveryStopEvent]] = {sid: [] for sid in stop_ids}
        for ev in all_stop_events:
            events_by_stop.setdefault(ev.delivery_stop_id, []).append(ev)
        flat_packages: list[Package] = []
        for stop in stops:
            flat_packages.extend(packages_by_stop.get(stop.id, []))
        package_ids = [p.id for p in flat_packages]
        all_pkg_events = await self._package_event_repo.list_by_package_ids(package_ids)
        events_by_pkg: dict[str, list[PackageEvent]] = {pid: [] for pid in package_ids}
        for ev in all_pkg_events:
            events_by_pkg.setdefault(ev.package_id, []).append(ev)
        return OrderTimelineResponse(
            order_id=order.order_id,
            order_events=[self._order_event_to_timeline_item(e) for e in order_event_rows],
            delivery_stops=[
                DeliveryStopTimelineSlice(
                    delivery_stop_id=stop.id,
                    tracking_id=stop.tracking_id,
                    events=[self._delivery_stop_event_to_timeline_item(e) for e in events_by_stop.get(stop.id, [])],
                )
                for stop in stops
            ],
            packages=[
                PackageTimelineSlice(
                    package_id=package.id,
                    package_reference=package.package_id,
                    delivery_stop_id=package.delivery_stop_id,
                    events=[self._package_event_to_timeline_item(e) for e in events_by_pkg.get(package.id, [])],
                )
                for package in flat_packages
            ],
        )

    async def get_delivery_stop_timeline_response_or_404(self, order_id: str, stop_id: str) -> DeliveryStopTimelineSlice:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        stop_events = await self._delivery_stop_event_repo.list_by_delivery_stop(stop.id)
        return DeliveryStopTimelineSlice(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            events=[self._delivery_stop_event_to_timeline_item(e) for e in stop_events],
        )

    async def get_package_timeline_response_or_404(self, order_id: str, package_id: str) -> PackageTimelineSlice:
        pkg_with_relations = await self._order_repo.get_package_with_stop_and_order(package_id)
        if pkg_with_relations is None:
            raise NotFoundError(resource="package", id=package_id)
        package, _, order = pkg_with_relations
        if order.id != order_id:
            raise NotFoundError(resource="package", id=package_id)
        package_events = await self._package_event_repo.list_by_package(package.id)
        return PackageTimelineSlice(
            package_id=package.id,
            package_reference=package.package_id,
            delivery_stop_id=package.delivery_stop_id,
            events=[self._package_event_to_timeline_item(e) for e in package_events],
        )

    @staticmethod
    def _order_event_to_timeline_item(row: OrderEvent) -> EntityStatusEventItem:
        return EntityStatusEventItem(
            id=row.id,
            created_at=row.created_at,
            from_status=row.from_status,
            to_status=row.to_status,
            display_label=order_status_display(row.to_status),
            actor_user_id=row.actor_user_id,
        )

    @staticmethod
    def _delivery_stop_event_to_timeline_item(row: DeliveryStopEvent) -> EntityStatusEventItem:
        return EntityStatusEventItem(
            id=row.id,
            created_at=row.created_at,
            from_status=row.from_status,
            to_status=row.to_status,
            display_label=delivery_stop_status_display(row.to_status),
            actor_user_id=row.actor_user_id,
        )

    @staticmethod
    def _package_event_to_timeline_item(row: PackageEvent) -> EntityStatusEventItem:
        return EntityStatusEventItem(
            id=row.id,
            created_at=row.created_at,
            from_status=row.from_status,
            to_status=row.to_status,
            display_label=package_status_display(row.to_status),
            actor_user_id=row.actor_user_id,
        )

    @staticmethod
    def _status_event_record_from_stop(ev: DeliveryStopEvent) -> StatusEventRecord:
        return StatusEventRecord(
            id=ev.id,
            created_at=ev.created_at,
            from_status=ev.from_status,
            to_status=ev.to_status,
            actor_user_id=ev.actor_user_id,
        )

    @staticmethod
    def _status_event_record_from_package(ev: PackageEvent) -> StatusEventRecord:
        return StatusEventRecord(
            id=ev.id,
            created_at=ev.created_at,
            from_status=ev.from_status,
            to_status=ev.to_status,
            actor_user_id=ev.actor_user_id,
        )

    @staticmethod
    def _entity_item_from_stop_event_record(r: StatusEventRecord) -> EntityStatusEventItem:
        return EntityStatusEventItem(
            id=r.id,
            created_at=r.created_at,
            from_status=r.from_status,
            to_status=r.to_status,
            display_label=delivery_stop_status_display(r.to_status),
            actor_user_id=r.actor_user_id,
        )

    @staticmethod
    def _entity_item_from_package_event_record(r: StatusEventRecord) -> EntityStatusEventItem:
        return EntityStatusEventItem(
            id=r.id,
            created_at=r.created_at,
            from_status=r.from_status,
            to_status=r.to_status,
            display_label=package_status_display(r.to_status),
            actor_user_id=r.actor_user_id,
        )

    async def get_order_detail_response_by_master_label_or_404(self, master_label_id: str) -> OrderDetailResponse:
        order, stops, packages_by_stop = await self.get_order_tree_by_master_label_or_404(master_label_id)
        linked_invoice = await InvoiceRepository(self._session).get_by_order_id(order.id)
        card_last_four = await self._resolve_card_last_four_for_order(order)
        return self._build_order_detail_response(
            order,
            stops,
            packages_by_stop,
            order.pickup_address,
            linked_invoice=linked_invoice,
            card_last_four=card_last_four,
        )

    async def get_order_labels_response_or_404(self, order_id: str) -> OrderLabelsResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        pickup_address = await self._resolve_pickup_address_text(order.pickup_address_id)
        return self._build_order_labels_response(order, stops, packages_by_stop, pickup_address)

    async def get_order_labels_response_by_master_label_or_404(self, master_label_id: str) -> OrderLabelsResponse:
        order, stops, packages_by_stop = await self.get_order_tree_by_master_label_or_404(master_label_id)
        pickup_address = await self._resolve_pickup_address_text(order.pickup_address_id)
        return self._build_order_labels_response(order, stops, packages_by_stop, pickup_address)

    async def _resolve_card_last_four_for_order(self, order: Order) -> str | None:
        if order.payment_method != PaymentModel.CARD:
            return None
        stmt = (
            select(BillingPayment)
            .where(BillingPayment.metadata_json["order_id"].astext == order.id)
            .order_by(BillingPayment.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        payment = result.scalars().first()
        if payment is None or not isinstance(payment.metadata_json, dict):
            return None
        credit_card_id = payment.metadata_json.get("credit_card_id")
        if not credit_card_id:
            return None
        card = await self._session.get(CreditCard, credit_card_id)
        if card is None:
            return None
        return (card.last_four or "").strip() or None

    @staticmethod
    def _user_brief(user: "User | None") -> UserBrief | None:
        if user is None:
            return None
        return UserBrief(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            phone=user.phone,
        )

    @staticmethod
    def _primary_org_contact_for_pickup(contacts: list[OrgContact]) -> OrgContact | None:
        """Prefer the org's primary active contact; otherwise the first active contact."""
        if not contacts:
            return None
        for c in contacts:
            if c.is_primary:
                return c
        return contacts[0]

    @staticmethod
    def _org_contact_pickup_fields(contact: OrgContact) -> tuple[str | None, str | None]:
        """Name from the contact's linked portal user; phone from org_contacts.contact_number."""
        user = contact.user
        name: str | None = None
        if user is not None:
            name = " ".join(p for p in [user.first_name, user.last_name] if p).strip() or None
        phone = (contact.contact_number or "").strip() or None
        return name, phone

    def _build_order_detail_response(
        self,
        order: Order,
        stops: list[DeliveryStop],
        packages_by_stop: dict[str, list[Package]],
        pickup_row: PickupAddress | None,
        *,
        linked_invoice: Invoice | None = None,
        card_last_four: str | None = None,
    ) -> OrderDetailResponse:
        delivery_stops: list[OrderDetailStopEntry] = []
        for stop in stops:
            pkgs = packages_by_stop.get(stop.id, [])
            delivery_stops.append(
                OrderDetailStopEntry.model_validate(
                    {
                        "id": stop.id,
                        "order_id": stop.order_id,
                        "tracking_id": stop.tracking_id,
                        "recipient_first_name": stop.recipient_first_name,
                        "recipient_last_name": stop.recipient_last_name,
                        "recipient_phone": stop.recipient_phone,
                        "recipient_email": stop.recipient_email,
                        "line_1": stop.line_1,
                        "line_2": stop.line_2,
                        "city": stop.city,
                        "postcode": stop.postcode,
                        "latitude": stop.latitude,
                        "longitude": stop.longitude,
                        "service_tier": stop.service_tier,
                        "service_tier_id": stop.service_tier_id,
                        "signature_required": stop.signature_required,
                        "safe_place_allowed": stop.safe_place_allowed,
                        "status": stop.status,
                        "packages_count": len(pkgs),
                        "created_at": stop.created_at,
                        "updated_at": stop.updated_at,
                        "version": stop.version,
                    }
                )
            )
        pickup_address = None
        pickup_line_1 = None
        pickup_line_2 = None
        pickup_city = None
        pickup_state = None
        pickup_country = None
        pickup_postcode = None
        if pickup_row:
            pickup_line_1 = pickup_row.line_1
            pickup_line_2 = pickup_row.line_2
            pickup_city = pickup_row.city
            pickup_state = pickup_row.state
            pickup_country = pickup_row.country
            pickup_postcode = (pickup_row.postcode or "").strip() or None
            pickup_address = (
                ", ".join(
                    p
                    for p in [
                        pickup_row.line_1,
                        pickup_row.line_2,
                        pickup_row.city,
                        pickup_row.postcode,
                        (pickup_row.country or "").strip() or None,
                    ]
                    if p is not None and str(p).strip()
                )
                or None
            )

        pickup_contact_name: str | None = None
        pickup_contact_phone: str | None = None
        if order.contact_user is not None:
            pickup_contact_name = (
                " ".join(p for p in [order.contact_user.first_name, order.contact_user.last_name] if p).strip()
                or None
            )
            pickup_contact_phone = (order.contact_user.phone or "").strip() or None

        created_by_brief = self._user_brief(order.created_by)
        contact_user_brief = self._user_brief(order.contact_user)
        return OrderDetailResponse(
            id=order.id,
            order_id=order.order_id,
            master_label_id=order.master_label_id,
            organization_id=order.organization_id,
            customer_id=order.customer_id,
            pickup_address_id=order.pickup_address_id,
            pickup_address=pickup_address,
            pickup_line_1=pickup_line_1,
            pickup_line_2=pickup_line_2,
            pickup_city=pickup_city,
            pickup_state=pickup_state,
            pickup_country=pickup_country,
            pickup_postcode=pickup_postcode,
            pickup_contact_name=pickup_contact_name,
            pickup_contact_phone=pickup_contact_phone,
            requested_pickup_date=order.requested_pickup_date,
            status=order.status,
            payment_method=order.payment_method,
            payment_method_id=order.payment_method_id,
            card_last_four=card_last_four,
            created_by_id=order.created_by_id,
            created_by=created_by_brief,
            contact_user_id=order.contact_user_id,
            contact_user=contact_user_brief,
            linked_invoice_id=linked_invoice.id if linked_invoice else None,
            linked_invoice_number=linked_invoice.invoice_number if linked_invoice else None,
            subtotal=order.subtotal,
            vat_amount=order.vat_amount,
            total_amount=order.total_amount,
            price_breakdown=order.price_breakdown,
            delivery_stops=delivery_stops,
            created_at=order.created_at,
            updated_at=order.updated_at,
            version=order.version,
        )

    async def _resolve_pickup_address_text(self, pickup_address_id: str | None) -> str | None:
        if not pickup_address_id:
            return None
        row = await PickupAddressRepository(self._session).get_by_id(pickup_address_id)
        if row is None:
            return None
        return row.full_address

    @staticmethod
    def _package_weight_kg(package: Package) -> float | None:
        return package.weight_kg if package.weight_kg is not None else package.declared_weight_kg

    @staticmethod
    def _package_volume_m3(package: Package) -> float | None:
        if package.length_cm is None or package.width_cm is None or package.height_cm is None:
            return None
        return (package.length_cm * package.width_cm * package.height_cm) / 1_000_000

    @staticmethod
    def _delivery_days_from_stop(stop: DeliveryStop) -> int | None:
        plan = (stop.price_breakdown or {}).get("pricing_plan") if isinstance(stop.price_breakdown, dict) else None
        if not isinstance(plan, dict):
            return None
        raw_days = plan.get("days")
        if raw_days is None:
            return None
        if isinstance(raw_days, dict):
            raw_days = raw_days.get("days") or raw_days.get("value") or raw_days.get("duration_days")
        if isinstance(raw_days, list):
            raw_days = raw_days[0] if raw_days else None
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None

    @staticmethod
    def _delivery_label(days: int | None) -> str | None:
        if days is None:
            return None
        unit = "DAY" if days == 1 else "DAYS"
        return f"{days} {unit} DELIVERY"

    def _build_order_labels_response(
        self,
        order: Order,
        stops: list[DeliveryStop],
        packages_by_stop: dict[str, list[Package]],
        pickup_address: str | None,
    ) -> OrderLabelsResponse:
        pickup_labels: list[PickupLabelEntry] = []
        total_weight = 0.0
        has_weight = False
        total_volume = 0.0
        has_volume = False
        total_packages = 0

        for stop in stops:
            recipient_name = " ".join(p for p in [stop.recipient_first_name, stop.recipient_last_name] if p).strip()
            recipient_address = ", ".join(p for p in [stop.line_1, stop.line_2, stop.city, stop.postcode] if p).strip()
            delivery_days = self._delivery_days_from_stop(stop)
            delivery_label = self._delivery_label(delivery_days)

            for package in packages_by_stop.get(stop.id, []):
                total_packages += 1
                dimensions_cm: str | None = None
                if package.length_cm is not None and package.width_cm is not None and package.height_cm is not None:
                    dimensions_cm = f"{package.length_cm:g} x {package.width_cm:g} x {package.height_cm:g}"

                weight_kg = self._package_weight_kg(package)
                if weight_kg is not None:
                    total_weight += weight_kg
                    has_weight = True

                volume_m3 = self._package_volume_m3(package)
                if volume_m3 is not None:
                    total_volume += volume_m3
                    has_volume = True

                pickup_labels.append(
                    PickupLabelEntry(
                        package_id=package.package_id or package.id,
                        tracking_id=stop.tracking_id,
                        recipient_name=recipient_name,
                        recipient_address=recipient_address,
                        pickup_address=pickup_address,
                        return_address=pickup_address,
                        signature_required=stop.signature_required,
                        weight_kg=weight_kg,
                        dimensions_cm=dimensions_cm,
                        volume_m3=round(volume_m3, 3) if volume_m3 is not None else None,
                        delivery_days=delivery_days,
                        delivery_label=delivery_label,
                    )
                )

        return OrderLabelsResponse(
            id=order.id,
            order_id=order.order_id,
            master_label=MasterLabelEntry(
                master_label_id=order.master_label_id,
                pickup_address=pickup_address,
                barcode_value=order.master_label_id,
                qr_value=order.master_label_id,
                delivery_stops_count=len(stops),
                total_packages=total_packages,
                total_weight_kg=round(total_weight, 1) if has_weight else None,
                total_volume_m3=round(total_volume, 3) if has_volume else None,
            ),
            pickup_labels=pickup_labels,
        )

    async def draft_to_response(
        self,
        draft: OrderDraft,
        *,
        caller: AuthUser | None = None,
    ) -> DraftResponse:
        contact_user = await self._draft_contact_user_info(draft, caller=caller)
        return DraftResponse(
            id=draft.id,
            organization_id=draft.organization_id,
            customer_id=draft.customer_id,
            contact_user=contact_user,
            payload=draft.payload,
            total_amount=draft.total_amount,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
            version=draft.version,
        )

    async def _draft_contact_user_info(
        self,
        draft: OrderDraft,
        *,
        caller: AuthUser | None,
    ) -> DraftContactUserInfo | None:
        if caller is None or caller.role != UserRole.CUSTOMER_B2B:
            return None
        payload = draft.payload or {}
        contact_user_id = payload.get("contact_user_id")
        org_id = payload.get("organization_id") or draft.organization_id
        if not contact_user_id or not org_id:
            return None
        user = await UserRepository(self._session).get_by_id(contact_user_id)
        contact = await OrgContactRepository(self._session).get_active_contact_for_user(org_id, contact_user_id)
        if user is None and contact is None:
            return None
        contact_role = contact.contact_role.value if contact and contact.contact_role is not None else None
        return DraftContactUserInfo(
            id=contact_user_id,
            first_name=getattr(user, "first_name", None) if user else None,
            last_name=getattr(user, "last_name", None) if user else None,
            email=getattr(user, "email", None) if user else None,
            phone=contact.contact_number if contact else None,
            contact_role=contact_role,
        )

    def draft_to_list_item(
        self,
        draft: OrderDraft,
        *,
        pickup_address: str | None = None,
        created_by: str | None = None,
    ) -> DraftListItem:
        payload = draft.payload or {}
        stops = payload.get("delivery_stops") or []
        package_count = sum(len((stop or {}).get("packages") or []) for stop in stops)
        # Prefer the dedicated column (set by save_draft/update_draft) and fall back to
        # the legacy JSONB key for drafts created before migration 0149.
        if draft.total_amount is not None:
            total_value: Decimal | None = draft.total_amount
        else:
            total_value_raw = payload.get("total_amount")
            total_value = Decimal(str(total_value_raw)) if total_value_raw is not None else None
        pickup_address_id = payload.get("pickup_address_id") or payload.get("pickup_request_id")
        return DraftListItem(
            id=draft.id,
            created_at=draft.created_at,
            draft_id=getattr(draft, "draft_id", None),
            order_id=payload.get("order_id"),
            organization_id=draft.organization_id,
            customer_id=draft.customer_id,
            pickup_address_id=pickup_address_id,
            contact_name=payload.get("contact_name"),
            created_by=created_by,
            pickup_address=pickup_address or payload.get("pickup_address"),
            package_count=package_count if stops else 0,
            delivery_stop_count=len(stops) if stops else 0,
            total_value=total_value,
        )

    async def list_stop_notes(self, *, order_id: str, stop_id: str) -> list[StopNoteEntry]:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        notes = await self._stop_note_repo.list_for_delivery_stop(stop_id)
        note_ids = [n.id for n in notes]
        images = await self._stop_note_repo.list_images_for_note_ids(note_ids)
        images_by_note: dict[str, list[StopNoteImageEntry]] = {}
        for image in images:
            images_by_note.setdefault(image.stop_note_id, []).append(
                StopNoteImageEntry(
                    id=image.id,
                    stop_note_id=image.stop_note_id,
                    image_key=image.image_key,
                    image_url=generate_image_url(image.image_key),
                    sort_order=image.sort_order,
                    created_at=image.created_at,
                    updated_at=image.updated_at,
                    version=image.version,
                )
            )
        pkg_by_note = await batch_package_ids_for_stop_notes(
            self._session,
            delivery_stop_id=stop_id,
            order_id=stop.order_id,
            notes=notes,
        )
        return [
            StopNoteEntry(
                id=note.id,
                delivery_stop_id=note.delivery_stop_id,
                note_type=note.note_type,
                message=note.message,
                is_blocking=note.is_blocking,
                sort_order=note.sort_order,
                package_ids=pkg_by_note.get(note.id, []),
                images=images_by_note.get(note.id, []),
                created_at=note.created_at,
                updated_at=note.updated_at,
                version=note.version,
            )
            for note in notes
        ]

    async def create_stop_note(
        self,
        *,
        order_id: str,
        stop_id: str,
        note_type: str,
        message: str,
        is_blocking: bool,
        sort_order: int,
        images: list[tuple[bytes, str, str]] | None,
        package_ids: list[str] | None = None,
    ) -> StopNoteEntry:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        persisted_type = normalize_stop_note_type(note_type)
        validate_stop_note_type_allowed(persisted_type, strict=is_strict_stop_note_types())
        assert_stop_note_type_allowed_for_stop_flow(note_type=persisted_type, stop=stop)
        cleaned_pkg = parse_and_validate_package_ids_for_note(
            note_type=persisted_type,
            package_ids=package_ids,
        )
        if cleaned_pkg:
            await assert_package_ids_belong_to_stop(
                self._session,
                delivery_stop_id=stop_id,
                order_id=stop.order_id,
                package_ids=cleaned_pkg,
            )
        note = await self._stop_note_repo.create(
            {
                "delivery_stop_id": stop_id,
                "note_type": persisted_type,
                "message": message.strip(),
                "is_blocking": is_blocking,
                "sort_order": sort_order,
                "package_ids": cleaned_pkg,
            }
        )
        if images:
            upload_items = [(content, filename, {"stop_note_id": note.id, "delivery_stop_id": stop_id}) for content, filename, _ in images]
            result = await bulk_upload_images(upload_items)
            for idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
                self._session.add(
                    StopNoteImage(
                        stop_note_id=note.id,
                        image_key=cf_result.id,
                        sort_order=idx + 1,
                    )
                )
            await self._session.flush()
        entries = await self.list_stop_notes(order_id=order_id, stop_id=stop_id)
        for entry in entries:
            if entry.id == note.id:
                return entry
        raise NotFoundError(resource="stop_note", id=note.id)

    async def update_stop_note(
        self,
        *,
        order_id: str,
        stop_id: str,
        note_id: str,
        note_type: str | None,
        message: str | None,
        is_blocking: bool | None,
        sort_order: int | None,
        images: list[tuple[bytes, str, str]] | None,
        deleted_image_ids: list[str] | None,
        package_ids: list[str] | None = None,
        update_package_ids: bool = False,
    ) -> StopNoteEntry:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        note = await self._stop_note_repo.get_for_stop(note_id=note_id, delivery_stop_id=stop_id)
        if note is None:
            raise NotFoundError(resource="stop_note", id=note_id)

        strict = is_strict_stop_note_types()
        resolved_type = note.note_type
        if note_type is not None:
            resolved_type = normalize_stop_note_type(note_type)
            validate_stop_note_type_allowed(resolved_type, strict=strict)
            assert_stop_note_type_allowed_for_stop_flow(note_type=resolved_type, stop=stop)

        patch: dict[str, object] = {}
        if note_type is not None:
            patch["note_type"] = resolved_type
        if message is not None:
            patch["message"] = message.strip()
        if is_blocking is not None:
            patch["is_blocking"] = is_blocking
        if sort_order is not None:
            patch["sort_order"] = sort_order

        if update_package_ids:
            cleaned_pkg = parse_and_validate_package_ids_for_note(
                note_type=resolved_type,
                package_ids=package_ids,
            )
            if cleaned_pkg:
                await assert_package_ids_belong_to_stop(
                    self._session,
                    delivery_stop_id=stop_id,
                    order_id=stop.order_id,
                    package_ids=cleaned_pkg,
                )
            patch["package_ids"] = cleaned_pkg
        elif note_type is not None and resolved_type != StopNoteType.PACKAGE_ISSUE_NOTE.value:
            patch["package_ids"] = None

        if patch:
            await self._stop_note_repo.update_by_id(note.id, patch)

        if deleted_image_ids:
            for image_id in deleted_image_ids:
                image = await self._stop_note_repo.get_image_for_note(note_id=note.id, image_id=image_id)
                if image is None:
                    continue
                await delete_image(image.image_key)
                await self._session.delete(image)
            await self._session.flush()

        if images:
            existing_images = await self._stop_note_repo.list_images_for_note(note.id)
            next_sort = len(existing_images) + 1
            upload_items = [(content, filename, {"stop_note_id": note.id, "delivery_stop_id": stop_id}) for content, filename, _ in images]
            result = await bulk_upload_images(upload_items)
            for _idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
                self._session.add(
                    StopNoteImage(
                        stop_note_id=note.id,
                        image_key=cf_result.id,
                        sort_order=next_sort,
                    )
                )
                next_sort += 1
            await self._session.flush()

        entries = await self.list_stop_notes(order_id=order_id, stop_id=stop_id)
        for entry in entries:
            if entry.id == note.id:
                return entry
        raise NotFoundError(resource="stop_note", id=note.id)

    async def delete_stop_note(self, *, order_id: str, stop_id: str, note_id: str) -> None:
        await self.get_delivery_stop_or_404(order_id, stop_id)
        note = await self._stop_note_repo.get_for_stop(note_id=note_id, delivery_stop_id=stop_id)
        if note is None:
            raise NotFoundError(resource="stop_note", id=note_id)
        images = await self._stop_note_repo.list_images_for_note(note.id)
        for image in images:
            await delete_image(image.image_key)
        await self._stop_note_repo.hard_delete(note.id)

    async def _get_stop_with_order_or_404(self, stop_id: str, *, order_id: str) -> tuple[DeliveryStop, Order]:
        loaded = await self._order_repo.get_stop_with_order(stop_id)
        if loaded is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        stop, order = loaded
        if order.id != order_id:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        return stop, order

    async def _get_package_or_404(
        self,
        package_id: str,
        *,
        order_id: str,
    ) -> tuple[Package, DeliveryStop | None, Order]:
        loaded = await self._order_repo.get_package_with_stop_and_order(package_id)
        if loaded is None:
            raise NotFoundError(resource="package", id=package_id)
        package, stop, order = loaded
        if order.id != order_id:
            raise NotFoundError(resource="package", id=package_id)
        return package, stop, order

    async def _assert_order_access_for_mutation(
        self,
        order: Order,
        user: AuthUser,
        *,
        query_organization_id: str | None,
    ) -> None:
        if user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
            if not query_organization_id:
                raise ValidationError("organization_id is required for admin users")
            if order.organization_id != query_organization_id:
                raise NotFoundError(resource="order", id=order.id)
            return
        if user.role in (UserRole.CUSTOMER_B2B, UserRole.WAREHOUSE_STAFF):
            if not user.organization_id or order.organization_id != user.organization_id:
                raise ForbiddenError("Not allowed to modify this order")
            return
        if user.role == UserRole.CUSTOMER_B2C:
            if order.customer_id != user.id:
                raise ForbiddenError("Not allowed to modify this order")
            return
        raise ForbiddenError("Not allowed to modify this order")

    async def _apply_package_cancellations(
        self,
        packages: list[Package],
        *,
        ctx: AuditContext,
    ) -> list[str]:
        affected: list[str] = []
        for pkg in packages:
            ps = PackageStatus(pkg.status)
            if ps == PackageStatus.CANCELLED:
                continue
            if ps in PACKAGE_STATUSES_BLOCKING_CANCELLATION:
                raise ValidationError(f"Cannot cancel while package {pkg.package_id} is in status {ps.value}")
            prev = ps
            pkg.status = PackageStatus.CANCELLED
            await self._append_package_status_event(
                package_id=pkg.id,
                from_status=prev,
                to_status=PackageStatus.CANCELLED,
                actor_user_id=ctx.user_id,
            )
            affected.append(pkg.id)
        await self._session.flush()
        return affected

    async def _mark_delivery_stop_cancelled(
        self,
        stop: DeliveryStop,
        *,
        ctx: AuditContext,
    ) -> None:
        if stop.status == DeliveryStopStatus.CANCELLED:
            return
        previous = stop.status
        stop.status = DeliveryStopStatus.CANCELLED
        await self._append_delivery_stop_status_event(
            delivery_stop_id=stop.id,
            from_status=DeliveryStopStatus(previous),
            to_status=DeliveryStopStatus.CANCELLED,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

    async def cancel_order(
        self,
        order_id: str,
        *,
        user: AuthUser,
        query_organization_id: str | None,
        notes: str | None,
        ctx: AuditContext,
    ) -> OrderCancelResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        await self._assert_order_access_for_mutation(
            order,
            user,
            query_organization_id=query_organization_id,
        )
        if OrderStatus(order.status) == OrderStatus.CANCELLED:
            raise ValidationError("This order is already cancelled")

        all_packages: list[Package] = []
        for sid in (s.id for s in stops):
            all_packages.extend(packages_by_stop.get(sid, []))
        if not all_packages:
            raise ValidationError("This order has no packages to cancel")

        await self._apply_package_cancellations(all_packages, ctx=ctx)
        for stop in stops:
            await self._mark_delivery_stop_cancelled(stop, ctx=ctx)

        previous = OrderStatus(order.status)
        await self._recompute_order_status(order, actor_user_id=ctx.user_id, record_order_status_event=False)

        await self._audit.log(
            action="order.cancelled",
            entity_type="order",
            entity_id=order.id,
            entity_ref=order.order_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"status": _status_value(previous)},
            new_value={"status": _status_value(order.status), "notes": notes},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_CANCELLED,
        )
        logger.info("order.cancelled", order_id=order.id, order_ref=order.order_id)

        return OrderCancelResponse(
            id=order.id,
            order_id=order.order_id,
            status=OrderStatus(order.status),
        )

    async def cancel_delivery_stop(
        self,
        order_id: str,
        stop_id: str,
        *,
        user: AuthUser,
        query_organization_id: str | None,
        notes: str | None,
        ctx: AuditContext,
    ) -> DeliveryStopCancelResponse:
        order, stops, packages_by_stop = await self.get_order_tree_or_404(order_id)
        await self._assert_order_access_for_mutation(
            order,
            user,
            query_organization_id=query_organization_id,
        )
        target_stop: DeliveryStop | None = next((s for s in stops if s.id == stop_id), None)
        if target_stop is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        packages = list(packages_by_stop.get(stop_id, []))
        if not packages:
            raise ValidationError("This delivery stop has no packages to cancel")

        if all(PackageStatus(p.status) == PackageStatus.CANCELLED for p in packages) and target_stop.status == DeliveryStopStatus.CANCELLED:
            return DeliveryStopCancelResponse(
                order_id=order.order_id,
                delivery_stop_id=target_stop.id,
                tracking_id=target_stop.tracking_id,
                stop_status=DeliveryStopStatus(target_stop.status),
                order_status=OrderStatus(order.status),
                affected_package_ids=[],
            )

        affected = await self._apply_package_cancellations(packages, ctx=ctx)
        previous_stop = target_stop.status
        await self._mark_delivery_stop_cancelled(target_stop, ctx=ctx)
        previous_order = OrderStatus(order.status)
        await self._recompute_order_status(order, actor_user_id=ctx.user_id, record_order_status_event=False)

        await self._audit.log(
            action="delivery_stop.cancelled",
            entity_type="delivery_stop",
            entity_id=target_stop.id,
            entity_ref=target_stop.tracking_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"stop_status": _status_value(previous_stop), "order_status": _status_value(previous_order)},
            new_value={
                "stop_status": _status_value(target_stop.status),
                "order_status": _status_value(order.status),
                "package_ids": affected,
                "notes": notes,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        logger.info("delivery_stop.cancelled", delivery_stop_id=target_stop.id, order_id=order.id)

        return DeliveryStopCancelResponse(
            order_id=order.order_id,
            delivery_stop_id=target_stop.id,
            tracking_id=target_stop.tracking_id,
            stop_status=DeliveryStopStatus(target_stop.status),
            order_status=OrderStatus(order.status),
            affected_package_ids=affected,
        )

    def _resolve_order_status_from_packages(self, breakdown: dict[str, int]) -> OrderStatus | None:
        total = sum(breakdown.values())
        if total == 0:
            return None
        delivered = breakdown.get(PackageStatus.DELIVERED_TO_CUSTOMER.value, 0) + breakdown.get(PackageStatus.LEFT_AT_SAFE_PLACE.value, 0)
        cancelled = breakdown.get(PackageStatus.CANCELLED.value, 0)
        returned = breakdown.get(PackageStatus.RETURNED.value, 0) + breakdown.get(PackageStatus.DISPOSED.value, 0)
        return_in_flight = sum(breakdown.get(s.value, 0) for s in RETURN_IN_TRANSIT_STATUSES)
        failed = sum(breakdown.get(s.value, 0) for s in FAILED_PACKAGE_STATUSES)

        if returned == total:
            return OrderStatus.RETURNED
        if cancelled == total:
            return OrderStatus.CANCELLED
        if delivered == total:
            return OrderStatus.DELIVERED
        if return_in_flight > 0 and (return_in_flight + returned) == total:
            return OrderStatus.RETURN_IN_PROGRESS
        if delivered > 0 and (delivered + failed + cancelled + returned + return_in_flight) == total:
            return OrderStatus.PARTIALLY_DELIVERED
        if failed == total:
            return OrderStatus.FAILED
        return None

    async def _recompute_order_status(
        self,
        order: Order,
        *,
        actor_user_id: str | None = None,
        record_order_status_event: bool = True,
    ) -> Order:
        breakdown = await self._order_repo.package_status_breakdown_for_order(order.id)
        new_status = self._resolve_order_status_from_packages(breakdown)
        if new_status is not None and new_status != order.status:
            previous = OrderStatus(order.status)
            order.status = new_status
            await self._session.flush()
            if record_order_status_event:
                await self._append_order_status_event(
                    order_id=order.id,
                    from_status=previous,
                    to_status=new_status,
                    actor_user_id=actor_user_id,
                )
                await self._session.flush()
        return order

    def _resolve_stop_status_from_packages(
        self,
        breakdown: dict[str, int],
        *,
        active_status: DeliveryStopStatus | None = None,
    ) -> DeliveryStopStatus | None:
        total = sum(breakdown.values())
        if total == 0:
            return None
        delivered = breakdown.get(PackageStatus.DELIVERED_TO_CUSTOMER.value, 0) + breakdown.get(PackageStatus.LEFT_AT_SAFE_PLACE.value, 0)
        cancelled = breakdown.get(PackageStatus.CANCELLED.value, 0)
        returned = breakdown.get(PackageStatus.RETURNED.value, 0)
        disposed = breakdown.get(PackageStatus.DISPOSED.value, 0)
        in_transit = breakdown.get(PackageStatus.RETURN_IN_TRANSIT.value, 0)
        initiated = breakdown.get(PackageStatus.RETURN_INITIATED.value, 0)
        failed = sum(breakdown.get(s.value, 0) for s in FAILED_PACKAGE_STATUSES)

        if returned == total:
            return DeliveryStopStatus.RETURNED
        if disposed == total:
            return DeliveryStopStatus.DISPOSED
        if cancelled == total:
            return DeliveryStopStatus.CANCELLED
        if delivered == total:
            return DeliveryStopStatus.DELIVERED
        if in_transit > 0 and (in_transit + returned + disposed) == total:
            return DeliveryStopStatus.RETURN_IN_TRANSIT
        if initiated > 0 and (initiated + returned + disposed + in_transit) == total:
            return DeliveryStopStatus.RETURN_INITIATED
        if delivered > 0 and (delivered + failed + cancelled + returned + disposed + in_transit + initiated) == total:
            return DeliveryStopStatus.PARTIALLY_DELIVERED
        if active_status is not None:
            return active_status
        if failed == total:
            return DeliveryStopStatus.FAILED
        return None

    async def reschedule_stop(
        self,
        stop_id: str,
        *,
        scheduled_for: date,
        order_id: str,
        ctx: AuditContext,
    ) -> StopActionResponse:
        stop, order = await self._get_stop_with_order_or_404(stop_id, order_id=order_id)

        eligible_packages = await self._order_repo.list_packages_by_stop_and_statuses(
            stop_id,
            list(RESCHEDULABLE_PACKAGE_STATUSES),
        )
        if not eligible_packages:
            raise ValidationError("No packages are eligible for reschedule on this stop. Only packages where the customer was not home can be rescheduled.")

        previous_status = stop.status
        previous_scheduled = stop.scheduled_for

        for pkg in eligible_packages:
            prev_pkg = PackageStatus(pkg.status)
            pkg.status = PackageStatus.AT_WAREHOUSE
            await self._append_package_status_event(
                package_id=pkg.id,
                from_status=prev_pkg,
                to_status=PackageStatus.AT_WAREHOUSE,
                actor_user_id=ctx.user_id,
            )

        stop.scheduled_for = scheduled_for
        stop.status = DeliveryStopStatus.DELIVERY_SCHEDULED
        await self._append_delivery_stop_status_event(
            delivery_stop_id=stop.id,
            from_status=previous_status,
            to_status=DeliveryStopStatus.DELIVERY_SCHEDULED,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="delivery_stop.rescheduled",
            entity_type="delivery_stop",
            entity_id=stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "status": _status_value(previous_status),
                "scheduled_for": previous_scheduled.isoformat() if previous_scheduled else None,
            },
            new_value={
                "status": _status_value(stop.status),
                "scheduled_for": scheduled_for.isoformat(),
                "package_ids": [p.id for p in eligible_packages],
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        logger.info(
            "delivery_stop.rescheduled",
            delivery_stop_id=stop.id,
            scheduled_for=scheduled_for.isoformat(),
            affected_packages=len(eligible_packages),
        )

        return StopActionResponse(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            stop_status=stop.status,
            scheduled_for=stop.scheduled_for,
            affected_package_ids=[p.id for p in eligible_packages],
        )

    async def update_stop_packages(
        self,
        *,
        order_id: str,
        stop_id: str,
        package_updates: list[dict],
        ctx: AuditContext,
    ) -> UpdateStopPackagesResponse:
        order, stops, packages_by_stop = await self._order_repo.get_order_with_stops_and_packages(order_id)
        if order is None:
            raise NotFoundError(resource="order", id=order_id)

        target_stop: DeliveryStop | None = next((s for s in stops if s.id == stop_id), None)
        if target_stop is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)

        if OrderStatus(order.status) != OrderStatus.PENDING_PICKUP:
            raise ValidationError(f"Packages can only be edited while the order is in PENDING_PICKUP (current: '{_status_value(order.status)}')")

        target_packages = packages_by_stop.get(stop_id, [])
        pkg_index: dict[str, Package] = {p.id: p for p in target_packages}

        missing = [u["id"] for u in package_updates if u["id"] not in pkg_index]
        if missing:
            raise NotFoundError(resource="package", id=", ".join(missing))

        if not order.price_breakdown:
            raise ValidationError("Order has no pricing snapshot — it was created before snapshotted pricing was enabled")

        async with self._session.begin_nested():
            for update in package_updates:
                pkg = pkg_index[update["id"]]
                for field_name in ("length_cm", "width_cm", "height_cm", "declared_weight_kg", "declared_value"):
                    if field_name in update and update[field_name] is not None:
                        setattr(pkg, field_name, update[field_name])

            pricing_stops: list[StopInput] = []
            stop_to_pkgs: list[tuple[DeliveryStop, list[Package]]] = []
            for idx, stop in enumerate(stops, start=1):
                if not stop.price_breakdown or "pricing_plan" not in stop.price_breakdown:
                    raise ValidationError(f"Delivery stop {stop.id} has no pricing snapshot — cannot recompute pricing")
                pkgs = packages_by_stop.get(stop.id, [])
                pkg_inputs = [
                    PackageInput(
                        index=pi,
                        declared_weight_kg=pkg.declared_weight_kg,
                        length_cm=pkg.length_cm,
                        width_cm=pkg.width_cm,
                        height_cm=pkg.height_cm,
                        package_uuid=pkg.id,
                        package_ref=pkg.package_id,
                    )
                    for pi, pkg in enumerate(pkgs, start=1)
                ]
                pricing_stops.append(
                    StopInput(
                        index=idx,
                        service_tier_name=stop.service_tier,
                        service_tier_id=stop.service_tier_id,
                        packages=pkg_inputs,
                        stop_uuid=stop.id,
                        tracking_id=stop.tracking_id,
                        resolved_plan=stop.price_breakdown["pricing_plan"],
                    )
                )
                stop_to_pkgs.append((stop, pkgs))

            org_stmt = select(Organization).where(
                Organization.id == order.organization_id,
                Organization.status == OrganizationStatus.ACTIVE,
            )
            org = (await self._session.execute(org_stmt)).scalar_one_or_none()
            if org is None:
                raise ValidationError("Organisation is not active")
            validate_package_restrictions(org, pricing_stops)

            pricing = recompute_price_breakdown_from_snapshot(
                order_snapshot=order.price_breakdown,
                stops=pricing_stops,
                order_uuid=order.id,
                order_id=order.order_id,
            )

            for pricing_stop, (stop_obj, pkgs) in zip(pricing_stops, stop_to_pkgs, strict=True):
                stop_obj.price_breakdown = pricing_stop.price_breakdown
                for pkg_obj, pkg_input in zip(pkgs, pricing_stop.packages, strict=True):
                    pkg_obj.price_breakdown = pkg_input.price_breakdown

            order.subtotal = pricing.subtotal
            order.vat_amount = pricing.vat_amount
            order.total_amount = pricing.total_amount
            order.price_breakdown = pricing.breakdown

            await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="order.packages_updated",
            entity_type="delivery_stop",
            entity_id=target_stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value=None,
            new_value={
                "order_id": order.id,
                "updated_package_ids": [u["id"] for u in package_updates],
                "subtotal": str(order.subtotal),
                "vat_amount": str(order.vat_amount),
                "total_amount": str(order.total_amount),
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        logger.info(
            "order.packages_updated",
            order_id=order.id,
            delivery_stop_id=target_stop.id,
            updated_count=len(package_updates),
        )

        refreshed_packages = packages_by_stop.get(target_stop.id, [])
        return UpdateStopPackagesResponse(
            order_id=order.id,
            delivery_stop_id=target_stop.id,
            tracking_id=target_stop.tracking_id,
            service_tier=target_stop.service_tier,
            service_tier_id=target_stop.service_tier_id,
            packages=[
                PackageEntry(
                    id=pkg.id,
                    order_id=pkg.order_id,
                    delivery_stop_id=pkg.delivery_stop_id,
                    package_id=pkg.package_id,
                    status=pkg.status,
                    length_cm=pkg.length_cm,
                    width_cm=pkg.width_cm,
                    height_cm=pkg.height_cm,
                    declared_weight_kg=pkg.declared_weight_kg,
                    weight_kg=pkg.weight_kg,
                    declared_value=pkg.declared_value,
                    is_damaged=pkg.is_damaged,
                    price_breakdown=pkg.price_breakdown,
                    created_at=pkg.created_at,
                    updated_at=pkg.updated_at,
                    version=pkg.version,
                )
                for pkg in refreshed_packages
            ],
            stop_price_breakdown=target_stop.price_breakdown,
            order_subtotal=order.subtotal,
            order_vat_amount=order.vat_amount,
            order_total_amount=order.total_amount,
            order_price_breakdown=order.price_breakdown,
        )

    async def update_stop_preferences(
        self,
        *,
        order_id: str,
        stop_id: str,
        signature_required: bool | None,
        safe_place_allowed: bool | None,
        ctx: AuditContext,
    ) -> DeliveryStop:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        previous = {
            "signature_required": stop.signature_required,
            "safe_place_allowed": stop.safe_place_allowed,
        }
        if signature_required is not None:
            stop.signature_required = signature_required
        if safe_place_allowed is not None:
            stop.safe_place_allowed = safe_place_allowed
        await self._session.flush()
        await self._audit.log(
            action="delivery_stop.preferences_updated",
            entity_type="delivery_stop",
            entity_id=stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value=previous,
            new_value={
                "signature_required": stop.signature_required,
                "safe_place_allowed": stop.safe_place_allowed,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        return stop

    async def update_stop_details(
        self,
        *,
        order_id: str,
        stop_id: str,
        fields: dict,
        ctx: AuditContext,
    ) -> DeliveryStop:
        stop = await self.get_delivery_stop_or_404(order_id, stop_id)
        before: dict = {}
        after: dict = {}
        for name in (
            "recipient_first_name",
            "recipient_last_name",
            "recipient_phone",
            "recipient_email",
            "line_1",
            "line_2",
            "city",
            "postcode",
        ):
            if name in fields and fields[name] is not None:
                before[name] = getattr(stop, name)
                setattr(stop, name, fields[name])
                after[name] = fields[name]
        if not after:
            return stop
        await self._session.flush()
        await self._audit.log(
            action="delivery_stop.details_updated",
            entity_type="delivery_stop",
            entity_id=stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value=before,
            new_value=after,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        return stop

    async def update_stop_service_tier(
        self,
        *,
        order_id: str,
        stop_id: str,
        service_tier_id: str,
        ctx: AuditContext,
    ) -> UpdateStopPackagesResponse:
        order, stops, packages_by_stop = await self._order_repo.get_order_with_stops_and_packages(order_id)
        if order is None:
            raise NotFoundError(resource="order", id=order_id)
        target_stop: DeliveryStop | None = next((s for s in stops if s.id == stop_id), None)
        if target_stop is None:
            raise NotFoundError(resource="delivery_stop", id=stop_id)
        if OrderStatus(order.status) != OrderStatus.PENDING_PICKUP:
            raise ValidationError(
                f"Service tier can only be changed while the order is in PENDING_PICKUP (current: '{_status_value(order.status)}')"
            )
        if not order.price_breakdown:
            raise ValidationError("Order has no pricing snapshot — cannot re-price")

        previous_tier_id = target_stop.service_tier_id
        previous_tier_name = target_stop.service_tier

        tier_svc = ServiceTierService(self._session)
        new_tier = await tier_svc.resolve_effective_tier_for_org(
            order.organization_id,
            tier_id=service_tier_id,
        )
        new_plan = effective_tier_to_plan(new_tier)

        async with self._session.begin_nested():
            target_stop.service_tier_id = new_tier.get("id")
            target_stop.service_tier = new_tier.get("tier_name")

            pricing_stops: list[StopInput] = []
            stop_to_pkgs: list[tuple[DeliveryStop, list[Package]]] = []
            for idx, stop in enumerate(stops, start=1):
                if not stop.price_breakdown or "pricing_plan" not in stop.price_breakdown:
                    raise ValidationError(
                        f"Delivery stop {stop.id} has no pricing snapshot — cannot recompute pricing"
                    )
                pkgs = packages_by_stop.get(stop.id, [])
                pkg_inputs = [
                    PackageInput(
                        index=pi,
                        declared_weight_kg=pkg.declared_weight_kg,
                        length_cm=pkg.length_cm,
                        width_cm=pkg.width_cm,
                        height_cm=pkg.height_cm,
                        package_uuid=pkg.id,
                        package_ref=pkg.package_id,
                    )
                    for pi, pkg in enumerate(pkgs, start=1)
                ]
                resolved = new_plan if stop.id == target_stop.id else stop.price_breakdown["pricing_plan"]
                pricing_stops.append(
                    StopInput(
                        index=idx,
                        service_tier_name=stop.service_tier,
                        service_tier_id=stop.service_tier_id,
                        packages=pkg_inputs,
                        stop_uuid=stop.id,
                        tracking_id=stop.tracking_id,
                        price_breakdown=stop.price_breakdown,
                        resolved_plan=resolved,
                    )
                )
                stop_to_pkgs.append((stop, pkgs))

            pricing = recompute_price_breakdown_from_snapshot(
                order_snapshot=order.price_breakdown,
                stops=pricing_stops,
                order_uuid=order.id,
                order_id=order.order_id,
            )
            for pricing_stop, (stop_obj, pkgs) in zip(pricing_stops, stop_to_pkgs, strict=True):
                stop_obj.price_breakdown = pricing_stop.price_breakdown
                for pkg_obj, pkg_input in zip(pkgs, pricing_stop.packages, strict=True):
                    pkg_obj.price_breakdown = pkg_input.price_breakdown
            order.subtotal = pricing.subtotal
            order.vat_amount = pricing.vat_amount
            order.total_amount = pricing.total_amount
            order.price_breakdown = pricing.breakdown
            await self._session.flush()

        await self._audit.log(
            action="delivery_stop.service_tier_changed",
            entity_type="delivery_stop",
            entity_id=target_stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"service_tier_id": previous_tier_id, "service_tier": previous_tier_name},
            new_value={
                "service_tier_id": target_stop.service_tier_id,
                "service_tier": target_stop.service_tier,
                "order_subtotal": str(order.subtotal),
                "order_vat_amount": str(order.vat_amount),
                "order_total_amount": str(order.total_amount),
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )

        refreshed_packages = packages_by_stop.get(target_stop.id, [])
        return UpdateStopPackagesResponse(
            order_id=order.id,
            delivery_stop_id=target_stop.id,
            tracking_id=target_stop.tracking_id,
            service_tier=target_stop.service_tier,
            service_tier_id=target_stop.service_tier_id,
            packages=[
                PackageEntry(
                    id=pkg.id,
                    order_id=pkg.order_id,
                    delivery_stop_id=pkg.delivery_stop_id,
                    package_id=pkg.package_id,
                    status=pkg.status,
                    length_cm=pkg.length_cm,
                    width_cm=pkg.width_cm,
                    height_cm=pkg.height_cm,
                    declared_weight_kg=pkg.declared_weight_kg,
                    weight_kg=pkg.weight_kg,
                    declared_value=pkg.declared_value,
                    is_damaged=pkg.is_damaged,
                    price_breakdown=pkg.price_breakdown,
                    created_at=pkg.created_at,
                    updated_at=pkg.updated_at,
                    version=pkg.version,
                )
                for pkg in refreshed_packages
            ],
            stop_price_breakdown=target_stop.price_breakdown,
            order_subtotal=order.subtotal,
            order_vat_amount=order.vat_amount,
            order_total_amount=order.total_amount,
            order_price_breakdown=order.price_breakdown,
        )

    async def initiate_package_return(
        self,
        package_id: str,
        *,
        order_id: str,
        ctx: AuditContext,
    ) -> PackageActionResponse:
        package, stop, order = await self._get_package_or_404(package_id, order_id=order_id)
        if PackageStatus(package.status) not in RETURNABLE_PACKAGE_STATUSES:
            raise ValidationError(f"Cannot initiate return on a package with status '{_status_value(package.status)}'")

        previous_status = package.status
        package.status = PackageStatus.RETURN_INITIATED
        await self._append_package_status_event(
            package_id=package.id,
            from_status=PackageStatus(previous_status),
            to_status=PackageStatus.RETURN_INITIATED,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

        if stop is not None:
            previous_stop_status = stop.status
            stop_breakdown = await self._order_repo.package_status_breakdown_for_stop(stop.id)
            new_stop_status = self._resolve_stop_status_from_packages(
                stop_breakdown,
                active_status=DeliveryStopStatus.RETURN_INITIATED,
            )
            if new_stop_status is not None and new_stop_status != stop.status:
                stop.status = new_stop_status
                await self._append_delivery_stop_status_event(
                    delivery_stop_id=stop.id,
                    from_status=DeliveryStopStatus(previous_stop_status),
                    to_status=new_stop_status,
                    actor_user_id=ctx.user_id,
                )
            if stop.return_initiated_at is None:
                stop.return_initiated_at = datetime.now(UTC)
                stop.return_initiated_by_id = ctx.user_id
            await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="package.return_initiated",
            entity_type="package",
            entity_id=package.id,
            entity_ref=package.package_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"status": _status_value(previous_status)},
            new_value={"status": _status_value(package.status)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.RETURN_INITIATED,
        )
        logger.info(
            "package.return_initiated",
            package_id=package.id,
            package_reference=package.package_id,
            previous_status=_status_value(previous_status),
        )

        return PackageActionResponse(
            id=package.id,
            package_id=package.package_id,
            delivery_stop_id=package.delivery_stop_id,
            status=PackageStatus(package.status),
            stop_status=DeliveryStopStatus(stop.status) if stop is not None else None,
            order_status=OrderStatus(order.status),
        )

    async def mark_package_as_found(
        self,
        package_id: str,
        *,
        order_id: str,
        ctx: AuditContext,
    ) -> PackageActionResponse:
        package, stop, order = await self._get_package_or_404(package_id, order_id=order_id)
        if PackageStatus(package.status) != PackageStatus.MISSING:
            raise ValidationError(f"Only missing packages can be marked as found (current status: '{_status_value(package.status)}')")

        previous_status = package.status
        package.status = PackageStatus.AT_WAREHOUSE
        await self._append_package_status_event(
            package_id=package.id,
            from_status=PackageStatus(previous_status),
            to_status=PackageStatus.AT_WAREHOUSE,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

        if stop is not None:
            previous_stop_status = stop.status
            stop_breakdown = await self._order_repo.package_status_breakdown_for_stop(stop.id)
            new_stop_status = self._resolve_stop_status_from_packages(
                stop_breakdown,
                active_status=stop.status,
            )
            if new_stop_status is not None and new_stop_status != stop.status:
                stop.status = new_stop_status
                await self._append_delivery_stop_status_event(
                    delivery_stop_id=stop.id,
                    from_status=DeliveryStopStatus(previous_stop_status),
                    to_status=new_stop_status,
                    actor_user_id=ctx.user_id,
                )
                await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="package.marked_as_found",
            entity_type="package",
            entity_id=package.id,
            entity_ref=package.package_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"status": _status_value(previous_status)},
            new_value={"status": _status_value(package.status)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.PACKAGE_STATUS_CHANGED,
        )
        logger.info(
            "package.marked_as_found",
            package_id=package.id,
            package_reference=package.package_id,
        )

        return PackageActionResponse(
            id=package.id,
            package_id=package.package_id,
            delivery_stop_id=package.delivery_stop_id,
            status=PackageStatus(package.status),
            stop_status=DeliveryStopStatus(stop.status) if stop is not None else None,
            order_status=OrderStatus(order.status),
        )

    async def reschedule_package(
        self,
        package_id: str,
        *,
        scheduled_for: date,
        order_id: str,
        ctx: AuditContext,
    ) -> PackageActionResponse:
        package, stop, order = await self._get_package_or_404(package_id, order_id=order_id)
        if PackageStatus(package.status) not in RESCHEDULABLE_PACKAGE_STATUSES:
            raise ValidationError(
                f"Cannot reschedule a package with status '{_status_value(package.status)}'. " "Only packages where the customer was not home can be rescheduled."
            )
        if stop is None:
            raise ValidationError("Package is not attached to a delivery stop and cannot be rescheduled")

        previous_package_status = package.status
        previous_stop_status = stop.status
        previous_scheduled = stop.scheduled_for

        package.status = PackageStatus.AT_WAREHOUSE
        await self._append_package_status_event(
            package_id=package.id,
            from_status=PackageStatus(previous_package_status),
            to_status=PackageStatus.AT_WAREHOUSE,
            actor_user_id=ctx.user_id,
        )
        stop.scheduled_for = scheduled_for
        stop.status = DeliveryStopStatus.DELIVERY_SCHEDULED
        await self._append_delivery_stop_status_event(
            delivery_stop_id=stop.id,
            from_status=DeliveryStopStatus(previous_stop_status),
            to_status=DeliveryStopStatus.DELIVERY_SCHEDULED,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="package.rescheduled",
            entity_type="package",
            entity_id=package.id,
            entity_ref=package.package_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "package_status": _status_value(previous_package_status),
                "stop_status": _status_value(previous_stop_status),
                "scheduled_for": previous_scheduled.isoformat() if previous_scheduled else None,
            },
            new_value={
                "package_status": _status_value(package.status),
                "stop_status": _status_value(stop.status),
                "scheduled_for": scheduled_for.isoformat(),
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.BOOKING_MODIFIED,
        )
        logger.info(
            "package.rescheduled",
            package_id=package.id,
            delivery_stop_id=stop.id,
            scheduled_for=scheduled_for.isoformat(),
        )

        return PackageActionResponse(
            id=package.id,
            package_id=package.package_id,
            delivery_stop_id=package.delivery_stop_id,
            status=PackageStatus(package.status),
            stop_status=DeliveryStopStatus(stop.status),
            order_status=OrderStatus(order.status),
        )

    async def initiate_stop_return(
        self,
        stop_id: str,
        *,
        order_id: str,
        ctx: AuditContext,
    ) -> StopActionResponse:
        stop, order = await self._get_stop_with_order_or_404(stop_id, order_id=order_id)

        eligible_packages = await self._order_repo.list_packages_by_stop_and_statuses(
            stop_id,
            list(RETURNABLE_PACKAGE_STATUSES),
        )
        if not eligible_packages:
            raise ValidationError(
                "No packages on this stop are eligible for return. " "Only packages in CUSTOMER_NOT_HOME, REFUSED_BY_CUSTOMER, MISSING, or DAMAGED can be returned."
            )

        previous_stop_status = stop.status
        previous_package_states = {p.id: _status_value(p.status) for p in eligible_packages}

        for pkg in eligible_packages:
            prev_pkg = PackageStatus(pkg.status)
            pkg.status = PackageStatus.RETURN_INITIATED
            await self._append_package_status_event(
                package_id=pkg.id,
                from_status=prev_pkg,
                to_status=PackageStatus.RETURN_INITIATED,
                actor_user_id=ctx.user_id,
            )
        await self._session.flush()

        stop_breakdown = await self._order_repo.package_status_breakdown_for_stop(stop.id)
        new_stop_status = self._resolve_stop_status_from_packages(
            stop_breakdown,
            active_status=DeliveryStopStatus.RETURN_INITIATED,
        )
        if new_stop_status is not None and new_stop_status != stop.status:
            stop.status = new_stop_status
            await self._append_delivery_stop_status_event(
                delivery_stop_id=stop.id,
                from_status=DeliveryStopStatus(previous_stop_status),
                to_status=new_stop_status,
                actor_user_id=ctx.user_id,
            )
        if stop.return_initiated_at is None:
            stop.return_initiated_at = datetime.now(UTC)
            stop.return_initiated_by_id = ctx.user_id
        await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="delivery_stop.return_initiated",
            entity_type="delivery_stop",
            entity_id=stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "status": _status_value(previous_stop_status),
                "packages": previous_package_states,
            },
            new_value={
                "status": _status_value(stop.status),
                "package_ids": [p.id for p in eligible_packages],
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.RETURN_INITIATED,
        )
        logger.info(
            "delivery_stop.return_initiated",
            delivery_stop_id=stop.id,
            affected_packages=len(eligible_packages),
        )

        return StopActionResponse(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            stop_status=stop.status,
            scheduled_for=stop.scheduled_for,
            affected_package_ids=[p.id for p in eligible_packages],
        )

    async def mark_stop_as_found(
        self,
        stop_id: str,
        *,
        order_id: str,
        ctx: AuditContext,
    ) -> StopActionResponse:
        stop, order = await self._get_stop_with_order_or_404(stop_id, order_id=order_id)

        eligible_packages = await self._order_repo.list_packages_by_stop_and_statuses(
            stop_id,
            [PackageStatus.MISSING],
        )
        if not eligible_packages:
            raise ValidationError("No missing packages on this stop to mark as found")

        previous_stop_status = stop.status

        for pkg in eligible_packages:
            prev_pkg = PackageStatus(pkg.status)
            pkg.status = PackageStatus.AT_WAREHOUSE
            await self._append_package_status_event(
                package_id=pkg.id,
                from_status=prev_pkg,
                to_status=PackageStatus.AT_WAREHOUSE,
                actor_user_id=ctx.user_id,
            )
        await self._session.flush()

        stop_breakdown = await self._order_repo.package_status_breakdown_for_stop(stop.id)
        new_stop_status = self._resolve_stop_status_from_packages(
            stop_breakdown,
            active_status=stop.status,
        )
        if new_stop_status is not None and new_stop_status != stop.status:
            stop.status = new_stop_status
            await self._append_delivery_stop_status_event(
                delivery_stop_id=stop.id,
                from_status=DeliveryStopStatus(previous_stop_status),
                to_status=new_stop_status,
                actor_user_id=ctx.user_id,
            )
            await self._session.flush()

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        await self._audit.log(
            action="delivery_stop.marked_as_found",
            entity_type="delivery_stop",
            entity_id=stop.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"status": _status_value(previous_stop_status)},
            new_value={
                "status": _status_value(stop.status),
                "package_ids": [p.id for p in eligible_packages],
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=AuditEventType.PACKAGE_STATUS_CHANGED,
        )
        logger.info(
            "delivery_stop.marked_as_found",
            delivery_stop_id=stop.id,
            affected_packages=len(eligible_packages),
        )

        return StopActionResponse(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            stop_status=stop.status,
            scheduled_for=stop.scheduled_for,
            affected_package_ids=[p.id for p in eligible_packages],
        )

    async def resolve_stop_return(
        self,
        stop_id: str,
        *,
        order_id: str,
        resolution: ReturnResolution,
        return_dispatch_date: date | None,
        return_cost: Decimal | None,
        waive_return_cost: bool,
        return_notes: str | None,
        disposal_reason: DisposalReason | None,
        resolution_notes: str | None,
        evidence_images: list[tuple[bytes, str, str]] | None,
        ctx: AuditContext,
    ) -> ResolveReturnResponse:
        stop, order = await self._get_stop_with_order_or_404(stop_id, order_id=order_id)

        eligible_packages = await self._order_repo.list_packages_by_stop_and_statuses(
            stop_id,
            list(RESOLVABLE_RETURN_PACKAGE_STATUSES),
        )
        if not eligible_packages:
            raise ValidationError("No packages on this stop are awaiting return resolution (status RETURN_INITIATED).")

        if stop.return_resolution is not None:
            raise ValidationError("This return has already been resolved")

        if resolution == ReturnResolution.DISPOSE:
            if not evidence_images:
                raise ValidationError("Disposal evidence image is required when resolution is DISPOSE")
            if len(evidence_images) > MAX_RETURN_EVIDENCE_IMAGES:
                raise ValidationError(f"Maximum {MAX_RETURN_EVIDENCE_IMAGES} evidence images allowed")
        elif evidence_images:
            raise ValidationError("Evidence images are only allowed when resolution is DISPOSE")

        previous_stop_status = stop.status
        previous_package_states = {p.id: _status_value(p.status) for p in eligible_packages}

        if resolution == ReturnResolution.RETURN_TO_SENDER:
            new_package_status = PackageStatus.RETURN_IN_TRANSIT
            new_stop_status = DeliveryStopStatus.RETURN_IN_TRANSIT
        else:
            new_package_status = PackageStatus.DISPOSED
            new_stop_status = DeliveryStopStatus.DISPOSED

        for pkg in eligible_packages:
            prev_pkg = PackageStatus(pkg.status)
            pkg.status = new_package_status
            await self._append_package_status_event(
                package_id=pkg.id,
                from_status=prev_pkg,
                to_status=new_package_status,
                actor_user_id=ctx.user_id,
            )

        stop.return_resolution = resolution
        stop.return_resolved_at = datetime.now(UTC)
        stop.return_resolved_by_id = ctx.user_id
        stop.status = new_stop_status
        await self._append_delivery_stop_status_event(
            delivery_stop_id=stop.id,
            from_status=DeliveryStopStatus(previous_stop_status),
            to_status=new_stop_status,
            actor_user_id=ctx.user_id,
        )
        if resolution == ReturnResolution.RETURN_TO_SENDER:
            stop.return_dispatch_date = return_dispatch_date
            stop.return_cost_waived = waive_return_cost
            stop.return_cost = None if waive_return_cost else return_cost
            stop.return_notes = return_notes
            stop.disposal_reason = None
        else:
            stop.disposal_reason = disposal_reason
            stop.return_notes = resolution_notes
            stop.return_dispatch_date = None
            stop.return_cost = None
            stop.return_cost_waived = False

        await self._session.flush()

        uploaded_images: list[DeliveryStopReturnEvidenceImage] = []
        if evidence_images:
            upload_items = [(content, filename, {"delivery_stop_id": stop.id, "purpose": "return_disposal_evidence"}) for content, filename, _ in evidence_images]
            result = await bulk_upload_images(upload_items)
            existing = await self._order_repo.list_evidence_images_for_stop(stop.id)
            next_sort = len(existing) + 1
            for _idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
                row = DeliveryStopReturnEvidenceImage(
                    delivery_stop_id=stop.id,
                    image_key=cf_result.id,
                    sort_order=next_sort,
                )
                self._session.add(row)
                uploaded_images.append(row)
                next_sort += 1
            await self._session.flush()
            for row in uploaded_images:
                await self._session.refresh(row)

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        evidence_entries = [
            ReturnEvidenceImageEntry(
                id=row.id,
                delivery_stop_id=row.delivery_stop_id,
                image_key=row.image_key,
                image_url=generate_image_url(row.image_key),
                sort_order=row.sort_order,
                created_at=row.created_at,
                updated_at=row.updated_at,
                version=row.version,
            )
            for row in uploaded_images
        ]

        event_type = AuditEventType.RETURN_COMPLETED if resolution == ReturnResolution.RETURN_TO_SENDER else AuditEventType.RETURN_COMPLETED
        dispatch_date = stop.return_dispatch_date
        disposal = stop.disposal_reason
        await self._audit.log(
            action=("delivery_stop.return_resolved.return_to_sender" if resolution == ReturnResolution.RETURN_TO_SENDER else "delivery_stop.return_resolved.disposed"),
            entity_type="delivery_stop",
            entity_id=stop.id,
            entity_ref=stop.tracking_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "status": _status_value(previous_stop_status),
                "packages": previous_package_states,
            },
            new_value={
                "status": _status_value(stop.status),
                "resolution": resolution.value,
                "return_dispatch_date": dispatch_date.isoformat() if dispatch_date is not None else None,
                "return_cost": str(stop.return_cost) if stop.return_cost is not None else None,
                "return_cost_waived": stop.return_cost_waived,
                "disposal_reason": disposal.value if disposal is not None else None,
                "evidence_count": len(uploaded_images),
                "package_ids": [p.id for p in eligible_packages],
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=event_type,
        )
        logger.info(
            "delivery_stop.return_resolved",
            delivery_stop_id=stop.id,
            resolution=resolution.value,
            affected_packages=len(eligible_packages),
            evidence_uploaded=len(uploaded_images),
        )

        return ResolveReturnResponse(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            stop_status=DeliveryStopStatus(stop.status),
            return_resolution=resolution,
            return_resolved_at=stop.return_resolved_at,
            return_dispatch_date=stop.return_dispatch_date,
            return_cost=stop.return_cost,
            return_cost_waived=stop.return_cost_waived,
            return_notes=stop.return_notes,
            disposal_reason=stop.disposal_reason,
            affected_package_ids=[p.id for p in eligible_packages],
            evidence_images=evidence_entries,
        )

    async def resolve_package_return(
        self,
        package_id: str,
        *,
        order_id: str,
        resolution: ReturnResolution,
        return_dispatch_date: date | None,
        return_cost: Decimal | None,
        waive_return_cost: bool,
        return_notes: str | None,
        disposal_reason: DisposalReason | None,
        resolution_notes: str | None,
        evidence_images: list[tuple[bytes, str, str]] | None,
        ctx: AuditContext,
    ) -> ResolveReturnResponse:
        package, stop, order = await self._get_package_or_404(package_id, order_id=order_id)
        if stop is None:
            raise ValidationError("Package is not attached to a delivery stop")

        if stop.return_resolution is not None:
            raise ValidationError("This return has already been resolved")

        if PackageStatus(package.status) not in RESOLVABLE_RETURN_PACKAGE_STATUSES:
            raise ValidationError("This package is not awaiting return resolution (status must be RETURN_INITIATED).")

        if resolution == ReturnResolution.DISPOSE:
            if not evidence_images:
                raise ValidationError("Disposal evidence image is required when resolution is DISPOSE")
            if len(evidence_images) > MAX_RETURN_EVIDENCE_IMAGES:
                raise ValidationError(f"Maximum {MAX_RETURN_EVIDENCE_IMAGES} evidence images allowed")
        elif evidence_images:
            raise ValidationError("Evidence images are only allowed when resolution is DISPOSE")

        if resolution == ReturnResolution.RETURN_TO_SENDER:
            new_package_status = PackageStatus.RETURN_IN_TRANSIT
        else:
            new_package_status = PackageStatus.DISPOSED

        previous_stop_status = stop.status
        previous_package_status = package.status

        package.status = new_package_status
        await self._append_package_status_event(
            package_id=package.id,
            from_status=PackageStatus(previous_package_status),
            to_status=new_package_status,
            actor_user_id=ctx.user_id,
        )
        await self._session.flush()

        stop_breakdown = await self._order_repo.package_status_breakdown_for_stop(stop.id)
        initiated_remaining = stop_breakdown.get(PackageStatus.RETURN_INITIATED.value, 0)
        new_stop_status = self._resolve_stop_status_from_packages(
            stop_breakdown,
            active_status=DeliveryStopStatus(previous_stop_status),
        )
        if new_stop_status is not None and new_stop_status != stop.status:
            stop.status = new_stop_status
            await self._append_delivery_stop_status_event(
                delivery_stop_id=stop.id,
                from_status=DeliveryStopStatus(previous_stop_status),
                to_status=new_stop_status,
                actor_user_id=ctx.user_id,
            )
            await self._session.flush()

        last_package_on_stop = initiated_remaining == 0
        if last_package_on_stop:
            stop.return_resolution = resolution
            stop.return_resolved_at = datetime.now(UTC)
            stop.return_resolved_by_id = ctx.user_id
            if resolution == ReturnResolution.RETURN_TO_SENDER:
                stop.return_dispatch_date = return_dispatch_date
                stop.return_cost_waived = waive_return_cost
                stop.return_cost = None if waive_return_cost else return_cost
                stop.return_notes = return_notes
                stop.disposal_reason = None
            else:
                stop.disposal_reason = disposal_reason
                stop.return_notes = resolution_notes
                stop.return_dispatch_date = None
                stop.return_cost = None
                stop.return_cost_waived = False
            await self._session.flush()

        uploaded_images: list[DeliveryStopReturnEvidenceImage] = []
        if evidence_images:
            upload_items = [(content, filename, {"delivery_stop_id": stop.id, "purpose": "return_disposal_evidence"}) for content, filename, _ in evidence_images]
            result = await bulk_upload_images(upload_items)
            existing = await self._order_repo.list_evidence_images_for_stop(stop.id)
            next_sort = len(existing) + 1
            for _idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
                row = DeliveryStopReturnEvidenceImage(
                    delivery_stop_id=stop.id,
                    image_key=cf_result.id,
                    sort_order=next_sort,
                )
                self._session.add(row)
                uploaded_images.append(row)
                next_sort += 1
            await self._session.flush()
            for row in uploaded_images:
                await self._session.refresh(row)

        await self._recompute_order_status(order, actor_user_id=ctx.user_id)

        evidence_entries = [
            ReturnEvidenceImageEntry(
                id=row.id,
                delivery_stop_id=row.delivery_stop_id,
                image_key=row.image_key,
                image_url=generate_image_url(row.image_key),
                sort_order=row.sort_order,
                created_at=row.created_at,
                updated_at=row.updated_at,
                version=row.version,
            )
            for row in uploaded_images
        ]

        if last_package_on_stop:
            res_at = stop.return_resolved_at
            dispatch_d = stop.return_dispatch_date
            r_cost = stop.return_cost
            r_waived = stop.return_cost_waived
            r_notes = stop.return_notes
            disp = stop.disposal_reason
        else:
            res_at = None
            if resolution == ReturnResolution.RETURN_TO_SENDER:
                dispatch_d = return_dispatch_date
                r_cost = None if waive_return_cost else return_cost
                r_waived = waive_return_cost
                r_notes = return_notes
                disp = None
            else:
                dispatch_d = None
                r_cost = None
                r_waived = False
                r_notes = resolution_notes
                disp = disposal_reason

        event_type = AuditEventType.RETURN_COMPLETED
        dispatch_for_audit: date | None = None
        if resolution == ReturnResolution.RETURN_TO_SENDER:
            dispatch_for_audit = stop.return_dispatch_date if last_package_on_stop else return_dispatch_date
        await self._audit.log(
            action=("package.return_resolved.return_to_sender" if resolution == ReturnResolution.RETURN_TO_SENDER else "package.return_resolved.disposed"),
            entity_type="package",
            entity_id=package.id,
            entity_ref=package.package_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "status": _status_value(previous_package_status),
                "stop_status": _status_value(previous_stop_status),
            },
            new_value={
                "status": _status_value(package.status),
                "stop_status": _status_value(stop.status),
                "resolution": resolution.value,
                "return_dispatch_date": dispatch_for_audit.isoformat() if dispatch_for_audit is not None else None,
                "return_cost": str(r_cost) if r_cost is not None else None,
                "return_cost_waived": r_waived,
                "disposal_reason": disp.value if disp is not None else None,
                "stop_fully_resolved": last_package_on_stop,
                "evidence_count": len(uploaded_images),
                "delivery_stop_id": stop.id,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            organization_id=order.organization_id,
            category=AuditCategory.ORDER,
            event_type=event_type,
        )
        logger.info(
            "package.return_resolved",
            package_id=package.id,
            delivery_stop_id=stop.id,
            resolution=resolution.value,
            stop_fully_resolved=last_package_on_stop,
            evidence_uploaded=len(uploaded_images),
        )

        return ResolveReturnResponse(
            delivery_stop_id=stop.id,
            tracking_id=stop.tracking_id,
            stop_status=DeliveryStopStatus(stop.status),
            return_resolution=resolution,
            return_resolved_at=res_at,
            return_dispatch_date=dispatch_d,
            return_cost=r_cost,
            return_cost_waived=r_waived,
            return_notes=r_notes,
            disposal_reason=disp,
            affected_package_ids=[package.id],
            evidence_images=evidence_entries,
        )
