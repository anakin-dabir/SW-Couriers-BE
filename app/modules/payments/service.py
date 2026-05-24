from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import MAX_PAYMENT_METHODS_PER_OWNER
from app.common.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.core.config import settings
from app.integrations.braintree import (
    braintree_result_is_duplicate_payment_method,
    braintree_transaction_card_snapshot,
    create_nonce_from_vaulted_payment_method,
    get_braintree_gateway,
    normalize_braintree_status,
    refund_or_void_transaction,
    require_three_d_secure_nonce_for_vault,
    transaction_sale_with_payment_method_nonce,
)
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.organizations.enums import PaymentModel
from app.modules.organizations.models import Organization
from app.modules.payments.enums import BookingPaymentStatus, PaymentMethodStatus
from app.modules.payments.models import CreditCard
from app.modules.payments.repository import BraintreeFeeProfileRepository, CreditCardRepository
from app.modules.payments.v1.schemas import PaymentMethodResponse, PreparePaymentNonceResponse
from app.modules.suspension_rules.repository import PaymentRiskEventRepository
from app.modules.user.models import User

logger = structlog.get_logger()


def _braintree_company_payload(trading_name: str) -> dict[str, str]:
    return {"company": trading_name.strip()}


@dataclass(frozen=True, slots=True)
class CreditCardOwner:
    organization_id: str | None = None
    user_id: str | None = None

    @property
    def braintree_customer_key(self) -> str:
        return self.organization_id or self.user_id or ""

    @property
    def label(self) -> str:
        if self.organization_id:
            return f"org={self.organization_id}"
        return f"user={self.user_id}"

    @property
    def repo_kwargs(self) -> dict:
        return {"organization_id": self.organization_id, "user_id": self.user_id}

    @property
    def record_kwargs(self) -> dict:
        return {
            "organization_id": self.organization_id,
            "user_id": self.user_id,
        }


@dataclass(frozen=True, slots=True)
class BookingChargeResult:
    success: bool
    braintree_transaction_id: str | None
    braintree_status: str | None
    payment_status: str
    processor_message: str | None = None
    transaction_fee: Decimal | None = None
    expected_fee_amount: Decimal | None = None
    fee_card_brand: str | None = None
    country_of_issuance: str | None = None


class PaymentService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = CreditCardRepository(session)
        self._fee_repo = BraintreeFeeProfileRepository(session)
        self._risk_repo = PaymentRiskEventRepository(session)
        self._audit = AuditService(session)
        self._gateway = get_braintree_gateway()

    async def _braintree_customer_profile_for_owner(self, owner: CreditCardOwner) -> dict[str, str]:
        if owner.organization_id:
            org = (
                await self._session.execute(select(Organization).where(Organization.id == owner.organization_id))
            ).scalar_one_or_none()
            if org is None:
                raise NotFoundError(resource="organization", id=owner.organization_id)
            return _braintree_company_payload(org.trading_name or "Organization")
        if owner.user_id:
            user = (await self._session.execute(select(User).where(User.id == owner.user_id))).scalar_one_or_none()
            if user is None:
                raise NotFoundError(resource="user", id=owner.user_id)
            return {"first_name": user.first_name.strip(), "last_name": user.last_name.strip()}
        raise ValidationError("Invalid payment owner")

    def _require_three_d_secure_nonce(self, nonce: str, owner: CreditCardOwner) -> None:
        require_three_d_secure_nonce_for_vault(self._gateway, nonce, owner_label=owner.label)

    def _raise_for_failed_braintree_payment_method_result(
        self,
        result: Any,
        *,
        owner: CreditCardOwner,
        log_event: str,
        generic_message: str,
    ) -> None:
        if getattr(result, "is_success", False):
            return
        if braintree_result_is_duplicate_payment_method(result):
            logger.info("braintree.payment_method_duplicate", owner=owner.label)
            raise ConflictError("This card is already saved.")
        logger.error(log_event, owner=owner.label, message=getattr(result, "message", None))
        raise ValidationError(generic_message)

    # -- Client token --

    async def generate_client_token(self, owner: CreditCardOwner) -> str:
        try:
            existing = await self._repo.find_by_owner(**owner.repo_kwargs)
            bt_customer_id = next((c.braintree_customer_id for c in existing if c.braintree_customer_id), None)
            params: dict[str, Any] = {}
            if bt_customer_id:
                params = {
                    "customer_id": bt_customer_id,
                }
            return self._gateway.client_token.generate(params)
        except AppError:
            raise
        except Exception as e:
            logger.error("braintree.client_token_failed", owner=owner.label, error=str(e))
            raise AppError("Failed to generate client token") from None

    @staticmethod
    def _ensure_dev_card_testing_allowed() -> None:
        if settings.APP_ENV not in {"development", "test"}:
            raise ValidationError("This endpoint is available only in development/test with sandbox")
        if settings.BRAINTREE_ENVIRONMENT != "sandbox":
            raise ValidationError("This endpoint is available only in development/test with sandbox")

    # -- CRUD --

    async def create_payment_method(
        self,
        owner: CreditCardOwner,
        nonce: str,
        ctx: AuditContext,
        *,
        cardholder_name: str | None = None,
        set_as_default: bool = False,
    ) -> PaymentMethodResponse:
        count = await self._repo.count_by_owner(**owner.repo_kwargs)
        if count >= MAX_PAYMENT_METHODS_PER_OWNER:
            raise ValidationError(f"Maximum {MAX_PAYMENT_METHODS_PER_OWNER} payment methods allowed")

        self._require_three_d_secure_nonce(nonce, owner)

        # Find or create Braintree customer (one per owner)
        existing_cards = await self._repo.find_by_owner(**owner.repo_kwargs)
        braintree_customer_id = next((c.braintree_customer_id for c in existing_cards if c.braintree_customer_id), None)

        if braintree_customer_id is None:
            profile = await self._braintree_customer_profile_for_owner(owner)
            customer_payload: dict[str, Any] = {"id": owner.braintree_customer_key, **profile}
            customer_result = self._gateway.customer.create(customer_payload)
            if customer_result is None or not getattr(customer_result, "is_success", False):
                logger.error(
                    "braintree.customer_create_failed",
                    owner=owner.label,
                    message=getattr(customer_result, "message", None),
                )
                raise AppError("Failed to create payment customer")
            customer_obj = getattr(customer_result, "customer", None)
            if customer_obj is None or not getattr(customer_obj, "id", None):
                logger.error("braintree.customer_create_failed_missing_customer", owner=owner.label)
                raise AppError("Failed to create payment customer")
            braintree_customer_id = cast(str, customer_obj.id)

        pm_params: dict = {
            "customer_id": braintree_customer_id,
            "payment_method_nonce": nonce,
            "options": {
                "verify_card": True,
                "fail_on_duplicate_payment_method": True,
            },
        }
        if cardholder_name and cardholder_name.strip():
            pm_params["cardholder_name"] = cardholder_name.strip()

        result = self._gateway.payment_method.create(pm_params)
        self._raise_for_failed_braintree_payment_method_result(
            result,
            owner=owner,
            log_event="braintree.payment_method_create_failed",
            generic_message="Card verification failed",
        )

        pm = cast(Any, getattr(result, "payment_method", None))
        if pm is None:
            logger.error("braintree.payment_method_create_failed_missing_payload", owner=owner.label)
            raise ValidationError("Card verification failed")
        card_type = getattr(pm, "card_type", None) or "UNKNOWN"
        raw_country = str(getattr(pm, "country_of_issuance", "") or "").strip().upper()
        country_of_issuance = None if not raw_country or raw_country == "UNKNOWN" else raw_country[:2]
        last_four = getattr(pm, "last_4", None)
        expiry_month = int(getattr(pm, "expiration_month", 0)) or None
        expiry_year = int(getattr(pm, "expiration_year", 0)) or None

        is_first_card = count == 0
        should_be_default = set_as_default or is_first_card

        if should_be_default:
            await self._repo.clear_defaults(**owner.repo_kwargs)

        record = await self._repo.create(
            {
                **owner.record_kwargs,
                "created_by_id": ctx.user_id,
                "braintree_token": pm.token,
                "braintree_customer_id": braintree_customer_id,
                "card_type": card_type.upper(),
                "country_of_issuance": country_of_issuance,
                "last_four": last_four,
                "expiry_month": expiry_month,
                "expiry_year": expiry_year,
                "cardholder_name": cardholder_name,
                "is_default": should_be_default,
                "status": PaymentMethodStatus.ACTIVE,
            }
        )

        await self._audit.log(
            action="payment_method.created",
            entity_type="payment_method",
            entity_id=record.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"card_type": card_type, "last_four": last_four, "is_default": should_be_default, "owner": owner.label},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.info("payment_method.created", owner=owner.label, card_id=record.id, last_four=last_four, created_by=ctx.user_id)
        return self._to_response(record)

    async def create_payment_method_from_raw_card_for_dev(
        self,
        owner: CreditCardOwner,
        ctx: AuditContext,
        *,
        card_number: str,
        expiry_month: int,
        expiry_year: int,
        cvv: str,
        cardholder_name: str | None = None,
        set_as_default: bool = True,
    ) -> PaymentMethodResponse:
        self._ensure_dev_card_testing_allowed()

        count = await self._repo.count_by_owner(**owner.repo_kwargs)
        if count >= MAX_PAYMENT_METHODS_PER_OWNER:
            raise ValidationError(f"Maximum {MAX_PAYMENT_METHODS_PER_OWNER} payment methods allowed") from None

        customer_id = owner.braintree_customer_key
        try:
            self._gateway.customer.find(customer_id)
        except Exception:
            profile = await self._braintree_customer_profile_for_owner(owner)
            customer_result = self._gateway.customer.create({"id": customer_id, **profile})
            if customer_result is None or not getattr(customer_result, "is_success", False):
                raise ValidationError("Failed to create payment customer") from None

        card_payload: dict[str, Any] = {
            "customer_id": customer_id,
            "number": card_number,
            "expiration_month": f"{expiry_month:02d}",
            "expiration_year": str(expiry_year),
            "cvv": cvv,
            "options": {
                "verify_card": True,
                "fail_on_duplicate_payment_method": True,
            },
        }
        if cardholder_name:
            card_payload["cardholder_name"] = cardholder_name

        create_result = self._gateway.credit_card.create(card_payload)
        self._raise_for_failed_braintree_payment_method_result(
            create_result,
            owner=owner,
            log_event="braintree.credit_card_create_failed",
            generic_message="Card verification failed",
        )
        pm = cast(Any, getattr(create_result, "credit_card", None))
        if pm is None:
            raise ValidationError("Card verification failed")

        if set_as_default or count == 0:
            await self._repo.clear_defaults(**owner.repo_kwargs)

        record = await self._repo.create(
            {
                **owner.record_kwargs,
                "created_by_id": ctx.user_id,
                "braintree_token": pm.token,
                "braintree_customer_id": customer_id,
                "card_type": (getattr(pm, "card_type", None) or "UNKNOWN").upper(),
                "country_of_issuance": (
                    None
                    if str(getattr(pm, "country_of_issuance", "") or "").strip().upper() in {"", "UNKNOWN"}
                    else str(getattr(pm, "country_of_issuance", "") or "").strip().upper()[:2]
                ),
                "last_four": getattr(pm, "last_4", None),
                "expiry_month": int(getattr(pm, "expiration_month", 0)) or None,
                "expiry_year": int(getattr(pm, "expiration_year", 0)) or None,
                "cardholder_name": cardholder_name,
                "is_default": set_as_default or count == 0,
                "status": PaymentMethodStatus.ACTIVE,
            }
        )
        await self._audit.log(
            action="payment_method.created.dev_raw_card",
            entity_type="payment_method",
            entity_id=record.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"card_type": getattr(pm, "card_type", None), "last_four": getattr(pm, "last_4", None)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.warning("payment_method.created.dev_raw_card", owner=owner.label, card_id=record.id)
        return self._to_response(record)

    async def charge_saved_card_for_booking(
        self,
        owner: CreditCardOwner,
        *,
        credit_card_id: str,
        amount: Decimal,
        verified_payment_method_nonce: str,
        order_id: str | None = None,
    ) -> BookingChargeResult:
        async def _risk(event_type: str, tx_id: str | None) -> None:
            if not owner.organization_id:
                return
            await self._risk_repo.create(
                {
                    "organization_id": owner.organization_id,
                    "customer_id": owner.user_id,
                    "order_id": order_id,
                    "payment_model": PaymentModel.CARD.value,
                    "event_type": event_type,
                    "occurred_on": datetime.utcnow().date(),
                    "rule_metadata": {"transaction_id": tx_id},
                }
            )

        if amount <= 0:
            await _risk("RETRY_FAILED", None)
            return BookingChargeResult(
                success=False,
                braintree_transaction_id=None,
                braintree_status=None,
                payment_status=BookingPaymentStatus.FAILED,
                processor_message="Amount must be positive",
            )
        card = await self._get_card_or_404(owner, credit_card_id)
        result = transaction_sale_with_payment_method_nonce(
            self._gateway,
            amount=amount,
            payment_method_nonce=verified_payment_method_nonce,
            order_id=order_id,
            owner_label=owner.label,
        )
        if getattr(result, "is_success", False):
            tx = cast(Any, getattr(result, "transaction", None))
            tx_id = str(getattr(tx, "id", "") or "") or None
            card_snapshot = braintree_transaction_card_snapshot(tx)
            resolved_card_type = card_snapshot.card_type or card.card_type
            resolved_country = card_snapshot.country_of_issuance or card.country_of_issuance
            currency = str(getattr(tx, "currency_iso_code", "") or "").strip().upper() or "GBP"
            expected_fee = await self._fee_repo.estimate_fee_by_card_type(
                amount=amount,
                card_type=resolved_card_type,
                currency=currency,
                country_of_issuance=resolved_country,
            )
            fee_brand = self._fee_repo.normalize_card_brand(resolved_card_type)
            await _risk("PAYMENT_SUCCESS", tx_id)
            return BookingChargeResult(
                success=True,
                braintree_transaction_id=tx_id,
                braintree_status=normalize_braintree_status(str(getattr(tx, "status", "") or "").strip()),
                payment_status=BookingPaymentStatus.PAID,
                transaction_fee=expected_fee,
                expected_fee_amount=expected_fee,
                fee_card_brand=fee_brand,
                country_of_issuance=resolved_country,
            )
        tx = cast(Any, getattr(result, "transaction", None))
        tx_id = str(getattr(tx, "id", "") or "") or None
        await _risk("PAYMENT_FAILED", tx_id)
        _raw = getattr(result, "message", None)
        if _raw is None:
            proc_msg: str | None = None
        else:
            s = str(_raw).strip()
            proc_msg = s or None
        return BookingChargeResult(
            success=False,
            braintree_transaction_id=tx_id,
            braintree_status=normalize_braintree_status(str(getattr(tx, "status", "") or "").strip()),
            payment_status=BookingPaymentStatus.FAILED,
            processor_message=proc_msg,
        )

    async def reverse_transaction_for_dev(
        self,
        *,
        transaction_id: str,
        amount: Decimal | None = None,
    ) -> dict[str, Any]:
        self._ensure_dev_card_testing_allowed()
        result = refund_or_void_transaction(
            self._gateway,
            transaction_id=transaction_id,
            amount=amount,
        )
        return {
            "success": result.success,
            "action": result.action,
            "original_transaction_id": result.original_transaction_id,
            "transaction_id": result.transaction_id,
            "processor_message": result.message,
        }

    async def get_dispute_fee_for_card(self, *, credit_card_id: str, owner: CreditCardOwner, currency: str = "GBP") -> Decimal | None:
        card = await self._get_card_or_404(owner, credit_card_id)
        return await self._fee_repo.estimate_dispute_fee(currency=currency, card_type=card.card_type)

    async def list_payment_methods(self, owner: CreditCardOwner) -> list[PaymentMethodResponse]:
        cards = await self._repo.find_by_owner(**owner.repo_kwargs)
        return [self._to_response(c) for c in cards]

    async def get_payment_method(self, owner: CreditCardOwner, card_id: str) -> PaymentMethodResponse:
        card = await self._get_card_or_404(owner, card_id)
        return self._to_response(card)

    async def set_default(self, owner: CreditCardOwner, card_id: str, ctx: AuditContext) -> PaymentMethodResponse:
        return await self.mark_as_default(owner, card_id, ctx)

    async def mark_as_default(self, owner: CreditCardOwner, card_id: str, ctx: AuditContext) -> PaymentMethodResponse:
        card = await self._get_card_or_404(owner, card_id)
        if card.is_default:
            return self._to_response(card)

        await self._repo.clear_defaults(**owner.repo_kwargs)
        updated = await self._repo.update_by_id(card_id, {"is_default": True})

        await self._audit.log(
            action="payment_method.set_default",
            entity_type="payment_method",
            entity_id=card_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"is_default": True, "last_four": card.last_four},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.info("payment_method.set_default", owner=owner.label, card_id=card_id)
        return self._to_response(updated)

    async def unmark_as_default(self, owner: CreditCardOwner, card_id: str, ctx: AuditContext) -> PaymentMethodResponse:
        card = await self._get_card_or_404(owner, card_id)
        if not card.is_default:
            return self._to_response(card)

        updated = await self._repo.update_by_id(card_id, {"is_default": False})
        await self._audit.log(
            action="payment_method.unset_default",
            entity_type="payment_method",
            entity_id=card_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"is_default": True, "last_four": card.last_four},
            new_value={"is_default": False, "last_four": card.last_four},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.info("payment_method.unset_default", owner=owner.label, card_id=card_id)
        return self._to_response(updated)

    async def prepare_checkout_nonce(
        self, owner: CreditCardOwner, card_id: str, ctx: AuditContext
    ) -> PreparePaymentNonceResponse:
        card = await self._get_card_or_404(owner, card_id)
        data = create_nonce_from_vaulted_payment_method(self._gateway, card.braintree_token)
        nonce_str = data["nonce"]
        if not nonce_str:
            raise ValidationError("Could not start card verification. Try again.")
        bin_from_bt = data.get("bin")
        await self._audit.log(
            action="payment_method.checkout_nonce_issued",
            entity_type="payment_method",
            entity_id=card_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "last_four": card.last_four,
                "card_type": card.card_type,
                "bin_metadata_present": bool(bin_from_bt and str(bin_from_bt).strip()),
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.info(
            "payment_method.checkout_nonce_issued",
            owner=owner.label,
            card_id=card_id,
            last_four=card.last_four,
        )
        return PreparePaymentNonceResponse(nonce=nonce_str, bin=bin_from_bt)

    async def delete_payment_method(self, owner: CreditCardOwner, card_id: str, ctx: AuditContext) -> None:
        card = await self._get_card_or_404(owner, card_id)

        try:
            self._gateway.payment_method.delete(card.braintree_token)
        except Exception:
            logger.warning("braintree.payment_method_delete_failed", card_id=card_id)

        was_default = card.is_default
        await self._repo.hard_delete(card_id)

        if was_default:
            remaining = await self._repo.find_by_owner(**owner.repo_kwargs)
            if remaining:
                await self._repo.update_by_id(remaining[0].id, {"is_default": True})

        await self._audit.log(
            action="payment_method.deleted",
            entity_type="payment_method",
            entity_id=card_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"card_type": card.card_type, "last_four": card.last_four, "was_default": was_default},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        logger.info("payment_method.deleted", owner=owner.label, card_id=card_id)

    # -- Helpers --

    async def verify_credit_card_belongs_to_org(
        self,
        *,
        organization_id: str,
        credit_card_id: str,
    ) -> CreditCard:
        return await self._get_card_or_404(CreditCardOwner(organization_id=organization_id), credit_card_id)

    async def _get_card_or_404(self, owner: CreditCardOwner, card_id: str) -> CreditCard:
        card = await self._repo.get_by_id(card_id)
        if card is None or card.status != PaymentMethodStatus.ACTIVE:
            raise NotFoundError(resource="credit_card", id=card_id)
        if owner.organization_id is not None:
            if card.organization_id != owner.organization_id:
                raise NotFoundError(resource="credit_card", id=card_id)
        elif owner.user_id is not None:
            if card.user_id != owner.user_id:
                raise NotFoundError(resource="credit_card", id=card_id)
        else:
            raise NotFoundError(resource="credit_card", id=card_id)
        return card

    def _to_response(self, card: CreditCard) -> PaymentMethodResponse:
        return PaymentMethodResponse(
            id=card.id,
            card_type=card.card_type,
            country_of_issuance=card.country_of_issuance,
            last_four=card.last_four,
            expiry_month=card.expiry_month,
            expiry_year=card.expiry_year,
            cardholder_name=card.cardholder_name,
            is_default=card.is_default,
            status=PaymentMethodStatus(card.status),
            created_at=card.created_at,
        )
