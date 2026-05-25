from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.modules.audit.models import AuditLog
from app.modules.org_credit.enums import (
    OrgCreditAccountStatus,
    OrgCreditLedgerMovementType,
    OrgCreditLedgerSourceType,
)
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditLedgerEntry
from app.modules.org_credit.service import OrgCreditLedgerService
from app.modules.organizations.models import Organization
from tests.conftest import run_concurrently


@pytest_asyncio.fixture
async def credit_account(db_session: AsyncSession, org_factory) -> tuple[Organization, OrgCreditAccount]:
    org = await org_factory()
    account = OrgCreditAccount(
        organization_id=org.id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=Decimal("100.00"),
        used_credit=Decimal("0"),
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.refresh(account)
    return org, account


@pytest.mark.asyncio
async def test_assert_can_consume_raises_when_no_account(db_session: AsyncSession, org_factory) -> None:
    org = await org_factory()
    svc = OrgCreditLedgerService(session=db_session)

    with pytest.raises(ValidationError, match="no credit account"):
        await svc.assert_can_consume(org.id, amount=Decimal("10.00"))


@pytest.mark.asyncio
async def test_assert_can_consume_raises_when_credit_limit_unset(db_session: AsyncSession, org_factory) -> None:
    org = await org_factory()
    db_session.add(
        OrgCreditAccount(
            organization_id=org.id,
            status=OrgCreditAccountStatus.ACTIVE,
            credit_limit=None,
            used_credit=Decimal("0"),
        )
    )
    await db_session.flush()
    svc = OrgCreditLedgerService(session=db_session)

    with pytest.raises(ValidationError, match="Credit limit"):
        await svc.assert_can_consume(org.id, amount=Decimal("10.00"))


@pytest.mark.asyncio
async def test_assert_can_consume_raises_when_account_inactive(db_session: AsyncSession, credit_account: tuple[Organization, OrgCreditAccount]) -> None:
    org, account = credit_account
    account.status = OrgCreditAccountStatus.SUSPENDED
    await db_session.flush()
    svc = OrgCreditLedgerService(session=db_session)

    with pytest.raises(ValidationError):
        await svc.assert_can_consume(org.id, amount=Decimal("10.00"))


@pytest.mark.asyncio
async def test_assert_can_consume_raises_when_insufficient_available(db_session: AsyncSession, credit_account: tuple[Organization, OrgCreditAccount]) -> None:
    org, account = credit_account
    account.used_credit = Decimal("95.00")  # only 5.00 available
    await db_session.flush()
    svc = OrgCreditLedgerService(session=db_session)

    with pytest.raises(ValidationError, match="Insufficient available credit"):
        await svc.assert_can_consume(org.id, amount=Decimal("10.00"))


@pytest.mark.asyncio
async def test_consume_credit_is_idempotent_under_same_key(db_session: AsyncSession, credit_account: tuple[Organization, OrgCreditAccount]) -> None:
    org, account = credit_account
    svc = OrgCreditLedgerService(session=db_session)
    key = "invoice:test-invoice-id:consume"

    await svc.consume_credit(
        org.id,
        actor=None,
        amount=Decimal("40.00"),
        source_type=OrgCreditLedgerSourceType.INVOICE,
        source_id="test-invoice-id",
        idempotency_key=key,
    )
    await svc.consume_credit(
        org.id,
        actor=None,
        amount=Decimal("40.00"),
        source_type=OrgCreditLedgerSourceType.INVOICE,
        source_id="test-invoice-id",
        idempotency_key=key,
    )

    await db_session.refresh(account)
    assert account.used_credit == Decimal("40.00"), "Second consume_credit with same idempotency key must be a no-op"

    ledger_rows = (await db_session.execute(select(OrgCreditLedgerEntry).where(OrgCreditLedgerEntry.idempotency_key == key))).scalars().all()
    assert len(ledger_rows) == 1, "Idempotent replay must not create a second ledger row"


@pytest.mark.asyncio
async def test_consume_credit_rejects_over_limit_inside_lock(db_session: AsyncSession, credit_account: tuple[Organization, OrgCreditAccount]) -> None:
    org, account = credit_account
    svc = OrgCreditLedgerService(session=db_session)

    # First consume eats most of the limit.
    await svc.consume_credit(
        org.id,
        actor=None,
        amount=Decimal("90.00"),
        source_type=OrgCreditLedgerSourceType.INVOICE,
        source_id="inv-A",
        idempotency_key="invoice:inv-A:consume",
    )

    with pytest.raises(ValidationError, match="Insufficient available credit"):
        await svc.consume_credit(
            org.id,
            actor=None,
            amount=Decimal("20.00"),
            source_type=OrgCreditLedgerSourceType.INVOICE,
            source_id="inv-B",
            idempotency_key="invoice:inv-B:consume",
        )

    await db_session.refresh(account)
    assert account.used_credit == Decimal("90.00"), "Failed consume must not partially debit"


@pytest.mark.asyncio
async def test_consume_credit_writes_consume_ledger_entry(db_session: AsyncSession, credit_account: tuple[Organization, OrgCreditAccount]) -> None:
    org, account = credit_account
    svc = OrgCreditLedgerService(session=db_session)

    await svc.consume_credit(
        org.id,
        actor=None,
        amount=Decimal("25.00"),
        source_type=OrgCreditLedgerSourceType.INVOICE,
        source_id="inv-C",
        idempotency_key="invoice:inv-C:consume",
    )

    entry = (
        await db_session.execute(
            select(OrgCreditLedgerEntry).where(
                OrgCreditLedgerEntry.organization_id == org.id,
                OrgCreditLedgerEntry.idempotency_key == "invoice:inv-C:consume",
            )
        )
    ).scalar_one()

    assert entry.movement_type == OrgCreditLedgerMovementType.CONSUME
    assert entry.source_type == OrgCreditLedgerSourceType.INVOICE
    assert entry.source_id == "inv-C"
    assert entry.used_credit_after == Decimal("25.00")
    assert entry.available_credit_after == Decimal("75.00")


@pytest_asyncio.fixture
async def committed_credit_account(shared_async_engine) -> AsyncGenerator[tuple[str, str]]:
    ref_suffix = uuid.uuid4().hex[:16]
    async with AsyncSession(bind=shared_async_engine, expire_on_commit=False) as setup:
        async with setup.begin():
            org = Organization(
                reference=f"T{ref_suffix}"[:20],
                trading_name=f"Race Org {ref_suffix[:6]}",
                legal_entity_name=f"Race Org {ref_suffix[:6]} Limited",
                companies_house_number=f"CH{ref_suffix[:8]}",
                vat_number=f"GB{ref_suffix[:9]}",
                date_of_incorporation=date(2020, 1, 1),
                industry="OTHER",
                company_size="1-10 employees",
                reg_address_line_1="1 Test Street",
                reg_city="London",
                reg_postcode="EC1A 1BB",
                status="ACTIVE",
            )
            setup.add(org)
            await setup.flush()
            account = OrgCreditAccount(
                organization_id=org.id,
                status=OrgCreditAccountStatus.ACTIVE,
                credit_limit=Decimal("100.00"),
                used_credit=Decimal("0"),
            )
            setup.add(account)
            await setup.flush()
            org_id, account_id = org.id, account.id

    try:
        yield org_id, account_id
    finally:
        async with AsyncSession(bind=shared_async_engine, expire_on_commit=False) as cleanup:
            async with cleanup.begin():
                await cleanup.execute(delete(OrgCreditLedgerEntry).where(OrgCreditLedgerEntry.organization_id == org_id))
                await cleanup.execute(delete(AuditLog).where(AuditLog.organization_id == org_id))
                await cleanup.execute(delete(OrgCreditAccount).where(OrgCreditAccount.organization_id == org_id))
                await cleanup.execute(delete(Organization).where(Organization.id == org_id))


@pytest.mark.asyncio
async def test_concurrent_consume_credit_serializes_on_row_lock(shared_async_engine, committed_credit_account: tuple[str, str]) -> None:
    org_id, account_id = committed_credit_account

    async def attempt(suffix: str) -> OrgCreditAccount:
        async with AsyncSession(bind=shared_async_engine, expire_on_commit=False) as s:
            async with s.begin():
                svc = OrgCreditLedgerService(session=s)
                return await svc.consume_credit(
                    org_id,
                    actor=None,
                    amount=Decimal("80.00"),
                    source_type=OrgCreditLedgerSourceType.INVOICE,
                    source_id=f"inv-{suffix}",
                    idempotency_key=f"invoice:inv-{suffix}:consume",
                )

    results = await run_concurrently(attempt("A"), attempt("B"))
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]

    assert len(successes) == 1, f"Exactly one consume must succeed; got {len(successes)}"
    assert len(failures) == 1, f"Exactly one consume must fail; got {len(failures)}"
    assert isinstance(failures[0], ValidationError), f"Losing caller must raise ValidationError; got {type(failures[0]).__name__}: {failures[0]!r}"
    assert "Insufficient available credit" in str(failures[0])

    async with AsyncSession(bind=shared_async_engine, expire_on_commit=False) as verify:
        acct = await verify.get(OrgCreditAccount, account_id)
        assert acct is not None
        assert acct.used_credit == Decimal("80.00"), f"Row lock must serialise consumers; used_credit={acct.used_credit} " f"(would be 160.00 if FOR UPDATE were dropped)"

        ledger_rows = (await verify.execute(select(OrgCreditLedgerEntry).where(OrgCreditLedgerEntry.organization_id == org_id))).scalars().all()
        assert len(ledger_rows) == 1, f"Exactly one CONSUME ledger entry must be written; got {len(ledger_rows)}"
        assert ledger_rows[0].movement_type == OrgCreditLedgerMovementType.CONSUME
