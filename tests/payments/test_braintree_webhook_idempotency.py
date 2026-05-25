from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payments.models import BraintreeWebhookEvent
from app.modules.payments.repository import BraintreeWebhookEventRepository


@pytest_asyncio.fixture
async def webhook_repo(db_session: AsyncSession) -> BraintreeWebhookEventRepository:
    return BraintreeWebhookEventRepository(db_session)


@pytest.mark.asyncio
async def test_duplicate_dispute_event_violates_unique_index(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:
    payload = {
        "webhook_kind": "dispute_opened",
        "dispute_id": "dispute-abc",
        "braintree_transaction_id": "txn-1",
        "payload_json": {"kind": "dispute_opened"},
    }
    await webhook_repo.create(payload)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await webhook_repo.create(payload)
        await db_session.flush()


@pytest.mark.asyncio
async def test_dispute_lifecycle_progression_is_allowed(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:
    await webhook_repo.create(
        {
            "webhook_kind": "dispute_opened",
            "dispute_id": "dispute-xyz",
            "payload_json": {"kind": "dispute_opened"},
        }
    )
    await webhook_repo.create(
        {
            "webhook_kind": "dispute_won",
            "dispute_id": "dispute-xyz",
            "payload_json": {"kind": "dispute_won"},
        }
    )
    await db_session.flush()
    rows = (await db_session.execute(select(BraintreeWebhookEvent).where(BraintreeWebhookEvent.dispute_id == "dispute-xyz"))).scalars().all()
    assert {r.webhook_kind for r in rows} == {"dispute_opened", "dispute_won"}


@pytest.mark.asyncio
async def test_duplicate_transaction_settlement_violates_unique_index(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:
    payload = {
        "webhook_kind": "transaction_settled",
        "braintree_transaction_id": "txn-settle-1",
        "payload_json": {"kind": "transaction_settled"},
    }
    await webhook_repo.create(payload)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await webhook_repo.create(payload)
        await db_session.flush()


@pytest.mark.asyncio
async def test_transaction_settled_then_settlement_declined_both_allowed(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:

    await webhook_repo.create(
        {
            "webhook_kind": "transaction_settled",
            "braintree_transaction_id": "txn-multi",
            "payload_json": {"kind": "transaction_settled"},
        }
    )

    await webhook_repo.create(
        {"webhook_kind": "transaction_settlement_declined", "braintree_transaction_id": "txn-multi", "payload_json": {"kind": "transaction_settlement_declined"}}
    )

    await db_session.flush()
    rows = (await db_session.execute(select(BraintreeWebhookEvent).where(BraintreeWebhookEvent.braintree_transaction_id == "txn-multi"))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_events_with_null_dispute_and_null_transaction_are_not_constrained(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:

    await webhook_repo.create({"webhook_kind": "check", "payload_json": {"kind": "check"}})
    await webhook_repo.create({"webhook_kind": "check", "payload_json": {"kind": "check"}})
    await db_session.flush()

    rows = (await db_session.execute(select(BraintreeWebhookEvent).where(BraintreeWebhookEvent.webhook_kind == "check"))).scalars().all()
    assert len(rows) >= 2


@pytest.mark.asyncio
async def test_repo_exists_detects_duplicate_dispute_event(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:

    await webhook_repo.create(
        {
            "webhook_kind": "dispute_lost",
            "dispute_id": "dispute-guard",
            "payload_json": {"kind": "dispute_lost"},
        }
    )
    await db_session.flush()

    assert await webhook_repo.exists(dispute_id="dispute-guard", webhook_kind="dispute_lost") is True
    assert await webhook_repo.exists(dispute_id="dispute-guard", webhook_kind="dispute_won") is False
    assert await webhook_repo.exists(dispute_id="unknown-dispute", webhook_kind="dispute_lost") is False


@pytest.mark.asyncio
async def test_repo_exists_detects_duplicate_transaction_event(db_session: AsyncSession, webhook_repo: BraintreeWebhookEventRepository) -> None:

    await webhook_repo.create(
        {
            "webhook_kind": "transaction_settled",
            "braintree_transaction_id": "txn-guard",
            "payload_json": {"kind": "transaction_settled"},
        }
    )
    await db_session.flush()
    assert await webhook_repo.exists(braintree_transaction_id="txn-guard", webhook_kind="transaction_settlement_declined") is False
