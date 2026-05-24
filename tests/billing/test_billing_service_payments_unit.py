"""Unit tests for billing payment helpers (mocked session / repos)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.enums import UserRole
from app.common.exceptions import ValidationError
from app.modules.billing.enums import PaymentRecordStatus
from app.modules.billing.service import BillingService
from app.modules.organizations.models import Organization
from app.modules.user.models import User

UID_USER = "11111111-1111-1111-1111-111111111111"
UID_MISSING_USER = "33333333-3333-3333-3333-333333333333"
UID_ORG_MISMATCH = "44444444-4444-4444-4444-444444444444"
UID_ORG_AS_CUSTOMER = "55555555-5555-5555-5555-555555555555"


@pytest.mark.asyncio
async def test_validate_payer_rejects_invalid_client_type_string() -> None:
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="Invalid client_type"):
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id="any-id",
            client_type="NOT_A_CLIENT_TYPE",
        )


@pytest.mark.asyncio
async def test_validate_payer_rejects_malformed_customer_uuid() -> None:
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="Invalid customer_id"):
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id="missing-user-id",
            client_type="CUSTOMER_B2B",
        )
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_payer_rejects_unknown_user() -> None:
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="Unknown customer_id"):
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id=UID_MISSING_USER,
            client_type="CUSTOMER_B2B",
        )
    assert session.get.await_count == 2
    assert session.get.await_args_list[0].args[0] is User
    assert session.get.await_args_list[1].args[0] is Organization


@pytest.mark.asyncio
async def test_validate_payer_hints_when_customer_id_is_org_uuid() -> None:
    async def _getter(model: object, _pk: object) -> object | None:
        if model is User:
            return None
        if model is Organization:
            return SimpleNamespace()
        return None

    session = SimpleNamespace(get=AsyncMock(side_effect=_getter))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError) as excinfo:
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id=UID_ORG_AS_CUSTOMER,
            client_type="CUSTOMER_B2B",
        )
    msg = (excinfo.value.details or [{}])[0].get("message", "")
    assert "organisation" in msg.lower()


@pytest.mark.asyncio
async def test_validate_payer_rejects_b2b_role_when_client_type_b2b() -> None:
    user = SimpleNamespace(id=UID_USER, role=UserRole.CUSTOMER_B2C, organization_id=None)
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="client_type does not match user role"):
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id=UID_USER,
            client_type="CUSTOMER_B2B",
        )


@pytest.mark.asyncio
async def test_validate_payer_rejects_b2b_org_mismatch() -> None:
    user = SimpleNamespace(id=UID_ORG_MISMATCH, role=UserRole.CUSTOMER_B2B, organization_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="member of this organisation"):
        await service._validate_record_payment_payer(
            organization_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            customer_id=UID_ORG_MISMATCH,
            client_type="CUSTOMER_B2B",
        )


@pytest.mark.asyncio
async def test_validate_payer_rejects_b2c_role_when_client_type_b2c() -> None:
    user = SimpleNamespace(id=UID_USER, role=UserRole.CUSTOMER_B2B, organization_id="22222222-2222-2222-2222-222222222222")
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    service = BillingService(session=session, request=None)

    with pytest.raises(ValidationError, match="client_type does not match user role"):
        await service._validate_record_payment_payer(
            organization_id="22222222-2222-2222-2222-222222222222",
            customer_id=UID_USER,
            client_type="CUSTOMER_B2C",
        )


@pytest.mark.asyncio
async def test_validate_payer_accepts_b2c_without_org_check() -> None:
    user = SimpleNamespace(id=UID_USER, role=UserRole.CUSTOMER_B2C, organization_id=None)
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    service = BillingService(session=session, request=None)

    await service._validate_record_payment_payer(
        organization_id="22222222-2222-2222-2222-222222222222",
        customer_id=UID_USER,
        client_type="CUSTOMER_B2C",
    )


def test_validate_record_payment_org_scope_mode_accepts_b2b() -> None:
    BillingService._validate_record_payment_org_scope_mode(client_type="CUSTOMER_B2B")


def test_validate_record_payment_org_scope_mode_rejects_b2c() -> None:
    with pytest.raises(ValidationError, match="out of scope"):
        BillingService._validate_record_payment_org_scope_mode(client_type="CUSTOMER_B2C")


def test_invoice_candidate_payment_status_overdue_when_unpaid_past_due() -> None:
    inv = SimpleNamespace(payment_status="UNPAID", due_date=date.today() - timedelta(days=1))
    assert BillingService._invoice_candidate_payment_status(inv) == "OVERDUE"


def test_invoice_candidate_payment_status_unpaid_not_overdue_on_due_today() -> None:
    inv = SimpleNamespace(payment_status="UNPAID", due_date=date.today())
    assert BillingService._invoice_candidate_payment_status(inv) == "UNPAID"


def test_invoice_candidate_payment_status_passthrough_for_partially_paid() -> None:
    inv = SimpleNamespace(payment_status="PARTIALLY_PAID", due_date=date.today() - timedelta(days=30))
    assert BillingService._invoice_candidate_payment_status(inv) == "PARTIALLY_PAID"


@pytest.mark.asyncio
async def test_enqueue_qb_payment_sync_no_op_when_zero_allocated(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[tuple, dict]] = []

    async def capture_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.append((args, kwargs))

    monkeypatch.setattr("app.modules.billing.service.enqueue", capture_enqueue)

    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._allocation_repo = SimpleNamespace(
        total_latest_allocated_for_payment=AsyncMock(return_value=Decimal("0.00")),
    )

    await service._enqueue_qb_payment_sync(payment_id="pay-1", organization_id="org-1", version=3)
    assert captured == []


@pytest.mark.asyncio
async def test_enqueue_qb_payment_sync_calls_enqueue_when_positive_allocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    async def capture_enqueue_payment_sync(self, **kwargs):  # noqa: ANN003, ARG001
        captured.append(kwargs)
        return {"queued": True, "job_id": "job-1"}

    monkeypatch.setattr(
        "app.integrations.quickbooks.service.QuickBooksService.enqueue_payment_sync",
        capture_enqueue_payment_sync,
    )

    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._allocation_repo = SimpleNamespace(
        total_latest_allocated_for_payment=AsyncMock(return_value=Decimal("0.01")),
    )

    await service._enqueue_qb_payment_sync(payment_id="pay-1", organization_id="org-1", version=3)
    assert len(captured) == 1
    assert captured[0]["payment_id"] == "pay-1"
    assert captured[0]["organization_id"] == "org-1"
    assert captured[0]["trigger_source"] == "billing.payment_sync"


@pytest.mark.asyncio
async def test_update_payment_notes_rejects_over_500_chars() -> None:
    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._payment_repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(
            return_value=SimpleNamespace(id="p1", version=1, payment_number="PAY-1", organization_id="org-1")
        ),
    )

    with pytest.raises(ValidationError, match="notes exceeds maximum length"):
        await service.update_payment_notes(
            organization_id="org-1",
            payment_id="p1",
            notes="x" * 501,
            actor_id="actor-1",
        )


@pytest.mark.asyncio
async def test_void_payment_rejects_allocated_payment() -> None:
    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._payment_repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(
            return_value=SimpleNamespace(
                id="p1",
                status="DEPOSITED",
                version=1,
                payment_number="PAY-1",
                organization_id="org-1",
            )
        ),
    )
    service._allocation_repo = SimpleNamespace(total_latest_allocated_for_payment=AsyncMock(return_value=Decimal("4.00")))

    with pytest.raises(ValidationError, match="Only unallocated payments can be voided"):
        await service.void_payment(
            organization_id="org-1",
            payment_id="p1",
            actor_id="u1",
        )


@pytest.mark.asyncio
async def test_void_payment_returns_same_payment_when_already_voided() -> None:
    payment = SimpleNamespace(id="p1", status="VOIDED", version=2, payment_number="PAY-1", organization_id="org-1")
    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._payment_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=payment))

    result = await service.void_payment(organization_id="org-1", payment_id="p1", actor_id="u1")
    assert result is payment


@pytest.mark.asyncio
async def test_mark_payment_status_rejects_transition_from_voided() -> None:
    payment = SimpleNamespace(
        id="p1",
        status="VOIDED",
        version=3,
        payment_number="PAY-1",
        organization_id="org-1",
        transaction_fee=Decimal("0.00"),
        provider_txn_id=None,
        braintree_status=None,
        braintree_status_updated_at=None,
    )
    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._payment_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=payment))

    with pytest.raises(ValidationError, match="Voided payments cannot transition"):
        await service.mark_payment_status(
            organization_id="org-1",
            payment_id="p1",
            to_status=PaymentRecordStatus.DEPOSITED,
            actor_id="u1",
        )


@pytest.mark.asyncio
async def test_add_or_revise_allocation_rejects_voided_payment() -> None:
    payment = SimpleNamespace(
        id="p1",
        status="VOIDED",
        version=1,
        amount=Decimal("20.00"),
        organization_id="org-1",
    )
    session = SimpleNamespace()
    service = BillingService(session=session, request=None)
    service._payment_repo = SimpleNamespace(get_by_id_or_404=AsyncMock(return_value=payment))

    with pytest.raises(ValidationError, match="Cannot allocate a voided payment"):
        await service.add_or_revise_allocation(
            payment_id="p1",
            invoice_id="inv-1",
            allocated_amount=Decimal("5.00"),
            actor_id="u1",
        )
