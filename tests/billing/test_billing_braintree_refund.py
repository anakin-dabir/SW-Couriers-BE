from __future__ import annotations

from datetime import date
from decimal import Decimal
from locale import currency

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError, ValidationError
from app.modules import billing
from app.modules.billing.service import BillingService
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.organizations.models import Organization
from app.modules.user.models import User
from app.modules.billing.models import BillingPayment, Refund, RefundEvent
from app.modules.billing.enums import PaymentProvider, RefundEventType, RefundMethod, RefundReasonCategory, RefundStatus, RefundType


@pytest_asyncio.fixture
async def billing_payment(db_session: AsyncSession, org_factory, user_factory) -> tuple[Organization, User, BillingPayment]:

    org = await org_factory()
    actor = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    payment = BillingPayment(
        payment_number=f"PAY-T{org.id[:6]}",
        organization_id=org.id,
        amount=Decimal("100.00"),
        payment_date=date.today(),
        provider=PaymentProvider.BRAINTREE.value,
        provider_txn_id="bt_txn_abc123",
        status="DEPOSITED",
        allocation_status="UNALLOCATED",
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("100.00"),
        currency="GBP",
    )

    db_session.add(payment)
    await db_session.flush()
    await db_session.refresh(payment)
    return org, actor, payment


@pytest.mark.asyncio
async def test_create_refund_rejects_zero_amount(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)

    with pytest.raises(ValidationError, match="amount must be greater than 0"):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id=payment.id,
            amount=Decimal("0"),
            refund_type=RefundType.FULL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="Test",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_rejects_unknown_payment(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:
    org, actor, _ = billing_payment
    svc = BillingService(session=db_session)
    with pytest.raises(NotFoundError):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id="00000000-0000-0000-0000-000000000000",
            amount=Decimal("10.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="Test",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_full_must_equal_remaining(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:
    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    with pytest.raises(ValidationError, match="Full refund amount must equal remaining"):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id=payment.id,
            amount=Decimal("80.00"),
            refund_type=RefundType.FULL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="Wrong full amount",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_partial_must_be_less_than_remaining(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:
    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    with pytest.raises(ValidationError, match="Partial refund amount must be less than remaining"):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id=payment.id,
            amount=Decimal("100.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="Partial equal to remaining is not partial",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_card_without_provider_txn_id_raises(db_session: AsyncSession, org_factory, user_factory) -> None:
    org = await org_factory()
    actor = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

    payment = BillingPayment(
        payment_number=f"PAY-N{org.id[:6]}",
        organization_id=org.id,
        amount=Decimal("50.00"),
        payment_date=date.today(),
        provider=PaymentProvider.BRAINTREE.value,
        provider_txn_id=None,
        status="DEPOSITED",
        allocation_status="UNALLOCATED",
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("50.00"),
        currency="GBP",
    )

    db_session.add(payment)
    await db_session.flush()

    svc = BillingService(session=db_session)
    with pytest.raises(ValidationError, match="provider_txn_id"):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id=payment.id,
            amount=Decimal("50.00"),
            refund_type=RefundType.FULL,
            refund_method=RefundMethod.CARD_REFUND,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="No source txn",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_rejects_fully_refunded_payment(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)

    await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("100.00"),
        refund_type=RefundType.FULL,
        refund_method=RefundMethod.CREDIT_NOTE,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="Drain",
        actor_id=actor.id,
    )

    with pytest.raises(ValidationError, match="already fully refunded"):
        await svc.create_refund(
            organization_id=org.id,
            billing_payment_id=payment.id,
            amount=Decimal("10.00"),
            refund_type=RefundType.PARTIAL,
            refund_method=RefundMethod.BANK_TRANSFER,
            reason_category=RefundReasonCategory.CLIENT_REQUEST,
            reason_description="Should fail",
            actor_id=actor.id,
        )


@pytest.mark.asyncio
async def test_create_refund_returns_existing_under_same_idempotency_key(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    # Two refund calls with same idempotency mustn't create 2 refund entries
    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    key = "refund:test-idem-key"

    first = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("25.00"),
        refund_type=RefundType.PARTIAL,
        refund_method=RefundMethod.BANK_TRANSFER,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="First call",
        actor_id=actor.id,
        idempotency_key=key,
    )

    second = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("25.00"),
        refund_type=RefundType.PARTIAL,
        refund_method=RefundMethod.BANK_TRANSFER,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="Replay",
        actor_id=actor.id,
        idempotency_key=key,
    )
    assert first.id == second.id, "Same idempotency key must return the same refund"

    rows = (await db_session.execute(select(Refund).where(Refund.organization_id == org.id, Refund.idempotency_key == key))).scalars().all()
    assert len(rows) == 1, "Idempotent replay mustn't create a second refund row"


@pytest.mark.asyncio
async def test_create_refund_credit_note_completes_without_braintree(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    refund = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("100.00"),
        refund_type=RefundType.FULL,
        refund_method=RefundMethod.CREDIT_NOTE,
        reason_category=RefundReasonCategory.VOIDED_INVOICE,
        reason_description="Credit-note refund",
        actor_id=actor.id,
    )

    assert refund.status == RefundStatus.COMPLETED.value
    assert refund.provider == PaymentProvider.MANUAL.value
    assert refund.processed_amount == Decimal("100.00")
    assert refund.completed_at is not None
    assert refund.braintree_transaction_id is None

    events = (await db_session.execute(select(RefundEvent).where(RefundEvent.refund_id == refund.id))).scalars().all()
    assert any(e.event_type == RefundEventType.INITIATED.value for e in events), "INITIATED event must be written even for synchronous credit-note refunds"


@pytest.mark.asyncio
async def test_create_refund_bank_transfer_initiated_without_braintree(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:
    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    refund = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("30.00"),
        refund_type=RefundType.PARTIAL,
        refund_method=RefundMethod.BANK_TRANSFER,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="Bank transfer refund",
        actor_id=actor.id,
    )
    assert refund.status == RefundStatus.INITIATED.value
    assert refund.provider == PaymentProvider.MANUAL.value
    assert refund.completed_at is None
    assert refund.processed_amount == Decimal("0")
    assert refund.braintree_transaction_id is None


@pytest.mark.asyncio
async def test_apply_braintree_refund_status_returns_none_for_unknown_refund(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    svc = BillingService(session=db_session)
    result = await svc.apply_braintree_refund_status(
        refund_id="00000000-0000-0000-0000-000000000000",
        braintree_transaction_id="bt_unknown",
        braintree_status="submitted_for_settlement",
    )
    assert result is None


@pytest.mark.asyncio
async def test_apply_braintree_refund_status_updates_existing_refund_and_writes_event(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    refund = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("40.00"),
        refund_type=RefundType.PARTIAL,
        refund_method=RefundMethod.BANK_TRANSFER,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="Will receive a Braintree status update",
        actor_id=actor.id,
    )

    updated = await svc.apply_braintree_refund_status(
        refund_id=refund.id,
        braintree_transaction_id="bt_refund_001",
        braintree_status="submitted_for_settlement",
    )
    assert updated is not None
    assert updated.braintree_status is not None
    assert updated.braintree_status_updated_at is not None

    events = (
        (
            await db_session.execute(
                select(RefundEvent).where(
                    RefundEvent.refund_id == refund.id,
                    RefundEvent.event_type == RefundEventType.BRAINTREE_STATUS_CHANGED.value,
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(events) == 1


@pytest.mark.asyncio
async def test_apply_braintree_refund_status_idempotent_state_under_replay(db_session: AsyncSession, billing_payment: tuple[Organization, User, BillingPayment]) -> None:

    org, actor, payment = billing_payment
    svc = BillingService(session=db_session)
    refund = await svc.create_refund(
        organization_id=org.id,
        billing_payment_id=payment.id,
        amount=Decimal("20.00"),
        refund_type=RefundType.PARTIAL,
        refund_method=RefundMethod.BANK_TRANSFER,
        reason_category=RefundReasonCategory.CLIENT_REQUEST,
        reason_description="Replay target",
        actor_id=actor.id,
    )

    first = await svc.apply_braintree_refund_status(
        refund_id=refund.id,
        braintree_transaction_id="bt_refund_002",
        braintree_status="settled",
    )
    second = await svc.apply_braintree_refund_status(
        refund_id=refund.id,
        braintree_transaction_id="bt_refund_002",
        braintree_status="settled",
    )

    assert first is not None and second is not None
    assert first.braintree_status == second.braintree_status, "Replayed callbacks must converge on the same braintree_status"
    assert first.processed_amount == second.processed_amount
    assert first.status == second.status

    fresh = (await db_session.execute(select(Refund).where(Refund.id == refund.id))).scalar_one()
    assert fresh.processed_amount == Decimal("0"), "Replayed status callbacks must not double-bump processed_amount"

@pytest.mark.asyncio
async def test_repay_credit_rejects_over_repay(
    db_session: AsyncSession, org_factory
) -> None:

    from app.modules.org_credit.enums import OrgCreditAccountStatus, OrgCreditLedgerSourceType

    from app.modules.org_credit.service import OrgCreditLedgerService

    org = await org_factory()
    account = OrgCreditAccount(
        organization_id=org.id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=Decimal("100.00"),
        used_credit=Decimal("30.00"),
    )
    db_session.add(account)
    await db_session.flush()


    svc = OrgCreditLedgerService(session=db_session)
    with pytest.raises(ValidationError, match="Cannot repay more"):
        await svc.repay_credit(
            org.id,
            actor=None,
            amount=Decimal("50.00"),
            source_type=OrgCreditLedgerSourceType.INVOICE,
            source_id="refund-X",
            idempotency_key="refund:X:repay",
        )

    await db_session.refresh(account)
    assert account.used_credit == Decimal("30.00"), "Failed repay must not partially credit"

