from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import NotFoundError, ValidationError
from app.modules.billing.service import (
    B2bCreditNoteCustomerFilter,
    B2bCreditNoteCustomerFilterMode,
    BillingService,
    parse_b2b_credit_note_customer_filter,
)


def _apply_service_with_mocks(
    *,
    credit_note: SimpleNamespace,
    invoice: SimpleNamespace,
    credit_applied_on_cn: Decimal = Decimal("0.00"),
    credit_on_invoice: Decimal = Decimal("0.00"),
    paid_on_invoice: Decimal = Decimal("0.00"),
) -> BillingService:
    session = SimpleNamespace(execute=AsyncMock())
    service = BillingService(session=session, request=None)
    session.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: credit_note),
        SimpleNamespace(scalar_one_or_none=lambda: invoice),
    ]
    service._invoice_event_repo = SimpleNamespace(
        latest_outcome_event_type=AsyncMock(return_value=None),
        append=AsyncMock(),
    )
    service._credit_app_repo = SimpleNamespace(
        get_applied_total_for_credit_note=AsyncMock(return_value=credit_applied_on_cn),
        get_applied_total_for_invoice=AsyncMock(return_value=credit_on_invoice),
        create=AsyncMock(
            return_value=SimpleNamespace(
                credit_note_id=credit_note.id,
                invoice_id=invoice.id,
                applied_amount=Decimal("1.00"),
                applied_at=date.today(),
            )
        ),
    )
    service._allocation_repo = SimpleNamespace(total_allocated_for_invoice=AsyncMock(return_value=paid_on_invoice))
    service._recompute_invoice_projection = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_apply_credit_note_auto_applies_min_remaining_and_outstanding() -> None:
    credit_note = SimpleNamespace(
        id="cn-1",
        organization_id="org-1",
        customer_id="cust-1",
        status="ISSUED",
        total_credit_amount=Decimal("100.00"),
        credit_note_number="CN-000001",
    )
    invoice = SimpleNamespace(
        id="inv-1",
        organization_id="org-1",
        customer_id="cust-1",
        status="SENT",
        total=Decimal("60.00"),
    )
    service = _apply_service_with_mocks(
        credit_note=credit_note,
        invoice=invoice,
        credit_applied_on_cn=Decimal("20.00"),
    )
    service._credit_app_repo.create = AsyncMock(
        return_value=SimpleNamespace(
            credit_note_id="cn-1",
            invoice_id="inv-1",
            applied_amount=Decimal("60.00"),
            applied_at=date.today(),
        )
    )

    app = await service.apply_credit_note_auto(
        credit_note_id="cn-1",
        invoice_id="inv-1",
        organization_id="org-1",
        actor_id="user-1",
        customer_id="cust-1",
    )
    assert app.applied_amount == Decimal("60.00")
    service._credit_app_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_credit_note_auto_raises_when_nothing_to_apply() -> None:
    credit_note = SimpleNamespace(
        id="cn-2",
        organization_id="org-1",
        customer_id="cust-1",
        status="ISSUED",
        total_credit_amount=Decimal("10.00"),
        credit_note_number="CN-000002",
    )
    invoice = SimpleNamespace(
        id="inv-2",
        organization_id="org-1",
        customer_id="cust-1",
        status="SENT",
        total=Decimal("50.00"),
    )
    service = _apply_service_with_mocks(
        credit_note=credit_note,
        invoice=invoice,
        credit_applied_on_cn=Decimal("10.00"),
    )

    with pytest.raises(ValidationError, match="Nothing to apply"):
        await service.apply_credit_note_auto(
            credit_note_id="cn-2",
            invoice_id="inv-2",
            organization_id="org-1",
            actor_id="user-1",
        )


@pytest.mark.asyncio
async def test_apply_credit_note_auto_rejects_wrong_b2b_customer() -> None:
    credit_note = SimpleNamespace(
        id="cn-3",
        organization_id="org-1",
        customer_id="cust-a",
        status="ISSUED",
        total_credit_amount=Decimal("50.00"),
        credit_note_number="CN-000003",
    )
    invoice = SimpleNamespace(
        id="inv-3",
        organization_id="org-1",
        customer_id="cust-a",
        status="SENT",
        total=Decimal("120.00"),
    )
    service = _apply_service_with_mocks(credit_note=credit_note, invoice=invoice)

    with pytest.raises(NotFoundError):
        await service.apply_credit_note_auto(
            credit_note_id="cn-3",
            invoice_id="inv-3",
            organization_id="org-1",
            customer_id="cust-b",
        )


@pytest.mark.asyncio
async def test_apply_credit_note_auto_rejects_invoice_customer_mismatch() -> None:
    credit_note = SimpleNamespace(
        id="cn-4",
        organization_id="org-1",
        customer_id="cust-a",
        status="ISSUED",
        total_credit_amount=Decimal("50.00"),
        credit_note_number="CN-000004",
    )
    invoice = SimpleNamespace(
        id="inv-4",
        organization_id="org-1",
        customer_id="cust-b",
        status="SENT",
        total=Decimal("120.00"),
    )
    service = _apply_service_with_mocks(credit_note=credit_note, invoice=invoice)

    with pytest.raises(ValidationError, match="customer"):
        await service.apply_credit_note_auto(
            credit_note_id="cn-4",
            invoice_id="inv-4",
            organization_id="org-1",
            customer_id="cust-a",
        )


@pytest.mark.asyncio
async def test_apply_credit_note_auto_rejects_non_issued_credit_note() -> None:
    credit_note = SimpleNamespace(
        id="cn-5",
        organization_id="org-1",
        customer_id="cust-1",
        status="PENDING",
        total_credit_amount=Decimal("50.00"),
        credit_note_number="CN-000005",
    )
    invoice = SimpleNamespace(
        id="inv-5",
        organization_id="org-1",
        customer_id="cust-1",
        status="SENT",
        total=Decimal("120.00"),
    )
    service = _apply_service_with_mocks(credit_note=credit_note, invoice=invoice)

    with pytest.raises(ValidationError, match="ISSUED"):
        await service.apply_credit_note_auto(
            credit_note_id="cn-5",
            invoice_id="inv-5",
            organization_id="org-1",
        )


@pytest.mark.parametrize(
    ("raw", "mode", "customer_id"),
    [
        (None, B2bCreditNoteCustomerFilterMode.ALL_IN_ORG, None),
        ("", B2bCreditNoteCustomerFilterMode.UNASSIGNED_ONLY, None),
        ("  cust-uuid  ", B2bCreditNoteCustomerFilterMode.SPECIFIC_CUSTOMER, "cust-uuid"),
    ],
)
def test_parse_b2b_credit_note_customer_filter(raw: str | None, mode: B2bCreditNoteCustomerFilterMode, customer_id: str | None) -> None:
    parsed = parse_b2b_credit_note_customer_filter(raw)
    assert parsed.mode == mode
    assert parsed.customer_id == customer_id


def test_assert_b2b_credit_note_org_access_raises_on_org_mismatch() -> None:
    cn = SimpleNamespace(organization_id="org-a")
    with pytest.raises(NotFoundError):
        BillingService._assert_b2b_credit_note_org_access(
            cn,
            organization_id="org-b",
            credit_note_id="cn-1",
        )


@pytest.mark.asyncio
async def test_get_credit_note_detail_b2b_org_scope_ignores_customer_mismatch() -> None:
    service = BillingService(session=SimpleNamespace(), request=None)
    service._credit_note_repo = SimpleNamespace(
        get_with_relations=AsyncMock(
            return_value=SimpleNamespace(id="cn-x", customer_id="cust-a", organization_id="org-1")
        )
    )

    cn = await service.get_credit_note_detail(
        credit_note_id="cn-x",
        organization_id="org-1",
        customer_id="cust-b",
        b2b_org_scope=True,
    )
    assert cn.id == "cn-x"


@pytest.mark.asyncio
async def test_get_credit_note_detail_hides_other_customer_when_legacy_customer_gate() -> None:
    service = BillingService(session=SimpleNamespace(), request=None)
    service._credit_note_repo = SimpleNamespace(
        get_with_relations=AsyncMock(
            return_value=SimpleNamespace(id="cn-x", customer_id="cust-a", organization_id="org-1")
        )
    )

    with pytest.raises(NotFoundError):
        await service.get_credit_note_detail(
            credit_note_id="cn-x",
            organization_id="org-1",
            customer_id="cust-b",
        )


@pytest.mark.asyncio
async def test_apply_credit_note_auto_allows_b2b_org_scope_without_customer_gate() -> None:
    credit_note = SimpleNamespace(
        id="cn-org",
        organization_id="org-1",
        customer_id="cust-a",
        source_invoice_id=None,
        status="ISSUED",
        total_credit_amount=Decimal("25.00"),
        credit_note_number="CN-ORG",
    )
    invoice = SimpleNamespace(
        id="inv-org",
        organization_id="org-1",
        customer_id="cust-a",
        status="SENT",
        total=Decimal("40.00"),
    )
    service = _apply_service_with_mocks(credit_note=credit_note, invoice=invoice)

    app = await service.apply_credit_note_auto(
        credit_note_id="cn-org",
        invoice_id="inv-org",
        organization_id="org-1",
        b2b_org_scope=True,
    )
    assert app.invoice_id == "inv-org"


@pytest.mark.asyncio
async def test_resolve_credit_note_customer_id_from_source_invoice() -> None:
    service = BillingService(session=SimpleNamespace(), request=None)
    cn = SimpleNamespace(customer_id=None, source_invoice_id="inv-1", source_invoice=None)
    service._invoice_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(customer_id="cust-from-inv"))
    )

    resolved = await service._resolve_credit_note_customer_id(cn, organization_id="org-1")
    assert resolved == "cust-from-inv"


@pytest.mark.asyncio
async def test_list_credit_notes_for_b2b_validates_customer_in_org() -> None:
    service = BillingService(session=SimpleNamespace(), request=None)
    service._validate_b2b_customer_in_org = AsyncMock()
    service.list_credit_notes = AsyncMock(return_value=([], 0))
    filt = B2bCreditNoteCustomerFilter(
        mode=B2bCreditNoteCustomerFilterMode.SPECIFIC_CUSTOMER,
        customer_id="cust-1",
    )

    await service.list_credit_notes_for_b2b(
        organization_id="org-1",
        customer_filter=filt,
    )
    service._validate_b2b_customer_in_org.assert_awaited_once_with("cust-1", "org-1")
    service.list_credit_notes.assert_awaited_once()
    assert service.list_credit_notes.await_args.kwargs["customer_id"] == "cust-1"


@pytest.mark.asyncio
async def test_apply_credit_note_auto_rejects_when_no_effective_customer() -> None:
    credit_note = SimpleNamespace(
        id="cn-nocust",
        organization_id="org-1",
        customer_id=None,
        source_invoice_id=None,
        status="ISSUED",
        total_credit_amount=Decimal("50.00"),
        credit_note_number="CN-NOCUST",
    )
    invoice = SimpleNamespace(
        id="inv-1",
        organization_id="org-1",
        customer_id="cust-1",
        status="SENT",
        total=Decimal("120.00"),
    )
    service = _apply_service_with_mocks(credit_note=credit_note, invoice=invoice)
    service._resolve_credit_note_customer_id = AsyncMock(return_value=None)

    with pytest.raises(ValidationError, match="applying credit"):
        await service.apply_credit_note_auto(
            credit_note_id="cn-nocust",
            invoice_id="inv-1",
            organization_id="org-1",
        )


@pytest.mark.asyncio
async def test_void_credit_note_requires_reason() -> None:
    service = BillingService(session=SimpleNamespace(), request=None)
    service.get_credit_note_detail = AsyncMock(
        return_value=SimpleNamespace(id="cn-3", status="ISSUED", version=1, organization_id="org-1")
    )
    service._credit_app_repo = SimpleNamespace(
        get_applied_total_for_credit_note=AsyncMock(return_value=Decimal("0")),
        list_for_credit_note=AsyncMock(return_value=[]),
    )

    with pytest.raises(ValidationError, match="reason is required"):
        await service.void_credit_note(credit_note_id="cn-3", organization_id="org-1", reason="  ")


@pytest.mark.parametrize(
    ("status", "applied", "total", "expected"),
    [
        ("ISSUED", Decimal("0"), Decimal("100"), "OPEN"),
        ("ISSUED", Decimal("100"), Decimal("100"), "FULLY_APPLIED"),
        ("ISSUED", Decimal("40"), Decimal("100"), "PARTIALLY_APPLIED"),
        ("VOIDED", Decimal("0"), Decimal("100"), "VOID"),
    ],
)
def test_credit_note_portal_status(status: str, applied: Decimal, total: Decimal, expected: str) -> None:
    assert (
        BillingService._credit_note_portal_status(
            credit_note_status=status,
            applied_total=applied,
            total_credit_amount=total,
        )
        == expected
    )
