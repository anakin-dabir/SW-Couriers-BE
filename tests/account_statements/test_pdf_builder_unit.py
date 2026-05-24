"""Unit tests for statement PDF HTML builder (no Jinja)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.account_statements.ledger import LedgerRow, StatementLedgerResult
from app.modules.account_statements.pdf_builder import build_statement_html


def test_build_statement_html_escapes_client_name() -> None:
    ledger = StatementLedgerResult(
        opening_balance=Decimal("0"),
        closing_balance=Decimal("100"),
        rows=[
            LedgerRow(
                row_type="INVOICE",
                reference_id="1",
                reference_number="INV-000001",
                issue_date=date(2026, 1, 10),
                payment_date=None,
                order_ref=None,
                status="UNPAID",
                amount=Decimal("100"),
                display_amount=Decimal("100"),
            )
        ],
        total_invoice_amount=Decimal("100"),
        total_paid=Decimal("0"),
        total_unpaid=Decimal("100"),
        total_overdue=Decimal("0"),
        aging={"days_1_30": "0", "days_31_60": "0", "days_61_90": "0", "days_90_plus": "0"},
        truncated=False,
    )
    html_content = build_statement_html(
        ledger=ledger,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        client_name='<script>"Corp"</script>',
        client_address="1 Road",
        client_email="a@b.co",
        statement_number="ST-000001",
    )
    assert "<script>" not in html_content
    assert "&lt;script&gt;" in html_content
    assert "<motion" not in html_content.lower() or "<div" in html_content
