from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_0082_billing_tables_exist(db_session) -> None:
    table_names = [
        "billing_payments",
        "billing_payment_allocations",
        "billing_payment_events",
    ]
    for table_name in table_names:
        result = await db_session.execute(text("SELECT to_regclass(:table_name)"), {"table_name": table_name})
        assert result.scalar_one() == table_name


@pytest.mark.asyncio
async def test_billing_payments_remittance_advice_columns_present(db_session) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'billing_payments'
            """
        )
    )
    cols = {row[0] for row in result.fetchall()}
    for name in (
        "remittance_advice_r2_key",
        "remittance_advice_content_type",
        "remittance_advice_original_filename",
        "remittance_advice_size_bytes",
        "remittance_advice_uploaded_at",
    ):
        assert name in cols


@pytest.mark.asyncio
async def test_0082_invoice_legacy_payment_columns_present(db_session) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'invoices'
            """
        )
    )
    cols = {row[0] for row in result.fetchall()}
    assert "paid_amount" in cols
    assert "payment_status" in cols
    assert "braintree_transaction_id" in cols
