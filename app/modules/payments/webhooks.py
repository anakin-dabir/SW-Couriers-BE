from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
import structlog
from braintree.exceptions.invalid_signature_error import InvalidSignatureError
from braintree.webhook_notification import WebhookNotification
from fastapi import APIRouter, Form, Request, status
from starlette.responses import JSONResponse

from app.common.deps import SessionDep
from app.common.response import fail_body, ok
from app.integrations.braintree import get_braintree_gateway
from app.modules.billing.service import BillingService
from app.modules.payments.repository import BraintreeFeeProfileRepository, BraintreeWebhookEventRepository

logger = structlog.get_logger()

router = APIRouter()

_TRANSACTION_SETTLEMENT_KINDS = frozenset(
    {
        WebhookNotification.Kind.TransactionSettled,
        WebhookNotification.Kind.TransactionSettlementDeclined,
    }
)

_DISPUTE_KINDS = frozenset(
    {
        WebhookNotification.Kind.DisputeOpened,
        WebhookNotification.Kind.DisputeLost,
        WebhookNotification.Kind.DisputeWon,
        WebhookNotification.Kind.DisputeExpired,
        WebhookNotification.Kind.DisputeAccepted,
        WebhookNotification.Kind.DisputeDisputed,
        WebhookNotification.Kind.DisputeAutoAccepted,
        WebhookNotification.Kind.DisputeUnderReview,
    }
)

_DISPUTE_INITIATED_KINDS = frozenset(
    {
        WebhookNotification.Kind.DisputeOpened,
        WebhookNotification.Kind.DisputeDisputed,
    }
)

_DISPUTE_LOSS_KINDS = frozenset(
    {
        WebhookNotification.Kind.DisputeLost,
        WebhookNotification.Kind.DisputeExpired,
        WebhookNotification.Kind.DisputeAccepted,
        WebhookNotification.Kind.DisputeAutoAccepted,
    }
)


def _safe_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    return value


def _parse_notification(bt_signature: str, bt_payload: str) -> Any:
    gateway = get_braintree_gateway()
    return gateway.webhook_notification.parse(str(bt_signature).strip(), str(bt_payload).strip())


def _extract_dispute_summary(notification: Any, *, kind: str) -> dict[str, Any] | None:
    dispute = getattr(notification, "dispute", None)
    if dispute is None:
        return None
    dispute_id = str(getattr(dispute, "id", "") or "").strip()
    if not dispute_id:
        return None

    txn_holder = getattr(dispute, "transaction_details", None) or getattr(dispute, "transaction", None)
    transaction_id = str(getattr(txn_holder, "id", "") or "").strip()
    order_id = str(getattr(txn_holder, "order_id", "") or "").strip() or None
    status = str(getattr(dispute, "status", "") or "").strip() or "UNKNOWN"
    kind_short = str(getattr(dispute, "kind", "") or "").strip().lower() or None
    amount = _safe_json_value(getattr(dispute, "amount", None))
    currency = str(getattr(dispute, "currency_iso_code", "") or "").strip().upper() or "GBP"
    card_details = getattr(txn_holder, "credit_card_details", None)
    card_type = str(getattr(card_details, "card_type", "") or "").strip() or None
    reason = getattr(dispute, "reason", None) or getattr(dispute, "reason_code", None)
    reply_by = getattr(dispute, "reply_by_date", None) or getattr(dispute, "reply_deadline", None)
    opened_at = getattr(dispute, "received_date", None) or getattr(dispute, "created_at", None)

    return {
        "webhook_kind": kind,
        "dispute_id": dispute_id,
        "braintree_transaction_id": transaction_id or None,
        "order_id": order_id,
        "status": status,
        "kind": kind_short,
        "amount": amount,
        "currency": currency,
        "card_type": card_type,
        "reason_code": str(reason).strip() if reason is not None else None,
        "reply_by_date": _safe_json_value(reply_by),
        "opened_at": _safe_json_value(opened_at),
    }


def _extract_transaction_summary(notification: Any, *, kind: str) -> dict[str, Any] | None:
    tx = getattr(notification, "transaction", None)
    if tx is None:
        return None
    tx_id = str(getattr(tx, "id", "") or "").strip()
    if not tx_id:
        return None
    return {
        "webhook_kind": kind,
        "braintree_transaction_id": tx_id,
        "status": str(getattr(tx, "status", "") or "").strip() or None,
        "transaction_type": str(getattr(tx, "type", "") or "").strip() or None,
        "amount": _safe_json_value(getattr(tx, "amount", None)),
        "currency": str(getattr(tx, "currency_iso_code", "") or "").strip().upper() or None,
        "order_id": str(getattr(tx, "order_id", "") or "").strip() or None,
        "refunded_transaction_id": str(getattr(tx, "refunded_transaction_id", "") or "").strip() or None,
    }


@router.post("/braintree", status_code=status.HTTP_200_OK, response_model=None)
async def braintree_webhook(
    request: Request,
    session: SessionDep,
    bt_signature: str = Form(..., min_length=1),
    bt_payload: str = Form(..., min_length=1),
) -> dict[str, Any] | JSONResponse:
    client = request.client
    client_host = client.host if client else None

    try:
        notification = _parse_notification(bt_signature, bt_payload)
    except InvalidSignatureError as exc:
        logger.warning("payments.braintree_webhook.invalid_signature", error=str(exc), client_host=client_host)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=fail_body(message="Invalid webhook signature", code="invalid_signature"),
        )

    kind = str(getattr(notification, "kind", "") or "").strip()
    event_repo = BraintreeWebhookEventRepository(session)
    fee_repo = BraintreeFeeProfileRepository(session)
    billing_svc = BillingService(session)

    if kind in _DISPUTE_KINDS:
        event_payload = _extract_dispute_summary(notification, kind=kind)
        if event_payload is None:
            await event_repo.create({"webhook_kind": kind, "payload_json": {"kind": kind, "error": "missing_dispute_payload"}})
            return ok({"kind": kind, "handled": False, "reason": "missing_dispute_payload"})

        dispute_id = str(event_payload["dispute_id"])

        # Idempotency guard
        if await event_repo.exists(dispute_id=dispute_id, webhook_kind=kind):
            logger.info("payments.braintree_webhook.dispute_duplicate", dispute_id=dispute_id, webhook_kind=kind)
            return ok({"kind": kind, "handled": True, "dispute_id": dispute_id, "idempotent": True})

        try:
            async with session.begin_nested():
                await event_repo.create(
                    {
                        "webhook_kind": kind,
                        "braintree_transaction_id": event_payload.get("braintree_transaction_id"),
                        "dispute_id": dispute_id,
                        "payload_json": event_payload,
                    }
                )
        except IntegrityError:
            logger.info("payments.braintree_webhook.dispute_duplicate_race", webhook_kind=kind, dispute_id=dispute_id)
            return ok({"kind": kind, "handled": True, "dispute_id": dispute_id, "idempotent": True})

        payment_id = str(event_payload.get("order_id") or "").strip() or None
        tx_id = str(event_payload.get("braintree_transaction_id") or "").strip() or None
        dispute_status = str(event_payload.get("status") or "").strip() or None
        if kind in _DISPUTE_INITIATED_KINDS:
            dispute_currency = str(event_payload.get("currency") or "").strip().upper() or "GBP"
            dispute_card_type = str(event_payload.get("card_type") or "").strip() or None
            dispute_amount_raw = event_payload.get("amount")
            dispute_amount: Decimal | None = None
            if dispute_amount_raw is not None:
                try:
                    dispute_amount = Decimal(str(dispute_amount_raw))
                except Exception:
                    dispute_amount = None
            dispute_fee = await fee_repo.estimate_dispute_fee(currency=dispute_currency, card_type=dispute_card_type)
            if payment_id and tx_id and dispute_status:
                await billing_svc.apply_braintree_dispute_status(
                    billing_payment_id=payment_id,
                    braintree_transaction_id=tx_id,
                    dispute_id=dispute_id,
                    dispute_status=dispute_status,
                    dispute_amount=dispute_amount,
                    dispute_fee=dispute_fee,
                    webhook_kind=kind,
                    metadata_json={"order_id": event_payload.get("order_id")},
                )
                # TODO: trigger QuickBooks dispute workflow and fee adjustments after account mappings are configured.
        else:
            if payment_id and tx_id:
                if kind == WebhookNotification.Kind.DisputeWon:
                    await billing_svc.apply_braintree_dispute_won(
                        billing_payment_id=payment_id,
                        braintree_transaction_id=tx_id,
                        dispute_id=dispute_id,
                        webhook_kind=kind,
                        metadata_json={"order_id": event_payload.get("order_id")},
                    )
                elif kind in _DISPUTE_LOSS_KINDS:
                    await billing_svc.apply_braintree_dispute_lost(
                        billing_payment_id=payment_id,
                        braintree_transaction_id=tx_id,
                        dispute_id=dispute_id,
                        dispute_status=dispute_status or "LOST",
                        webhook_kind=kind,
                        metadata_json={"order_id": event_payload.get("order_id")},
                    )
            # TODO: trigger QuickBooks dispute outcome workflow once dedicated transition handlers are implemented.
        logger.info("payments.braintree_webhook.dispute_ingested", webhook_kind=kind, dispute_id=dispute_id)
        return ok({"kind": kind, "handled": True, "dispute_id": dispute_id})

    if kind in _TRANSACTION_SETTLEMENT_KINDS:
        tx_payload = _extract_transaction_summary(notification, kind=kind) or {"webhook_kind": kind}
        tx_id_for_dedup = str(tx_payload.get("braintree_transaction_id") or "").strip() or None

        if tx_id_for_dedup and await event_repo.exists(braintree_transaction_id=tx_id_for_dedup, webhook_kind=kind):
            logger.info("payments.braintree_webhook.transaction_duplicate", braintree_transaction_id=tx_id_for_dedup, webhook_kind=kind)
            return ok({"kind": kind, "handled": True, "braintree_transaction_id": tx_id_for_dedup})

        try:
            async with session.begin_nested():
                await event_repo.create(
                {
                    "webhook_kind": kind,
                    "braintree_transaction_id": tx_payload.get("braintree_transaction_id"),
                    "payload_json": tx_payload,
                }
            )
        except IntegrityError:
            logger.info("payments.braintree_webhook.transaction_duplicate_race", webhook_kind=kind, braintree_transaction_id=tx_id_for_dedup)
            return ok({"kind": kind, "handled": True, "braintree_transaction_id": tx_id_for_dedup})

        tx_id = str(tx_payload.get("braintree_transaction_id") or "").strip() or None
        payment_id = str(tx_payload.get("order_id") or "").strip() or None
        tx_status = str(tx_payload.get("status") or "").strip() or None
        tx_type = str(tx_payload.get("transaction_type") or "").strip().lower() or None
        refunded_transaction_id = str(tx_payload.get("refunded_transaction_id") or "").strip() or None
        if payment_id and tx_id and tx_status:
            await billing_svc.apply_braintree_payment_status(
                billing_payment_id=payment_id,
                braintree_transaction_id=tx_id,
                braintree_status=tx_status,
                metadata_json={"webhook_kind": kind, "order_id": tx_payload.get("order_id")},
            )
            # TODO: trigger QuickBooks sync after webhook reconciliation when account mappings are configured.
            if tx_type == "credit" or refunded_transaction_id:
                await billing_svc.apply_braintree_refund_status(
                    refund_id=payment_id,
                    braintree_transaction_id=tx_id,
                    braintree_status=tx_status,
                    metadata_json={"webhook_kind": kind},
                )
        logger.info(
            "payments.braintree_webhook.transaction_settlement_ingested",
            webhook_kind=kind,
            braintree_transaction_id=tx_payload.get("braintree_transaction_id"),
        )
        return ok({"kind": kind, "handled": True, "braintree_transaction_id": tx_payload.get("braintree_transaction_id")})

    # TODO: handling failed refund & quickbook sync is required here.

    await event_repo.create({"webhook_kind": kind or "UNKNOWN", "payload_json": {"kind": kind or None}})
    logger.info("payments.braintree_webhook.ignored", webhook_kind=kind or None)
    return ok({"kind": kind, "handled": False})
