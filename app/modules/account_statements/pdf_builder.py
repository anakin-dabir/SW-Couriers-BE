"""Build account statement HTML and PDF via WeasyPrint (no Jinja)."""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from typing import Any

from app.common.schemas import quantize_currency
from app.modules.account_statements.constants import COMPANY_ADDRESS, COMPANY_EMAIL, COMPANY_NAME
from app.modules.account_statements.ledger import LedgerRow, StatementLedgerResult


def _fmt_currency(amount: Decimal | str, currency: str = "GBP") -> str:
    try:
        d = quantize_currency(amount)
    except (TypeError, ValueError, ArithmeticError):
        return str(amount)
    symbol = "£" if currency == "GBP" else ""
    return f"{symbol}{d:,.2f}"


def _fmt_date(d: date | None) -> str:
    if d is None:
        return "—"
    return d.strftime("%d/%m/%Y")


def _esc(value: Any) -> str:
    return html.escape(str(value) if value is not None else "", quote=True)


def _row_type_label(row_type: str) -> str:
    return {
        "INVOICE": "Invoice",
        "PAYMENT": "Payment",
        "CREDIT_NOTE": "Credit Note",
        "REFUND": "Refund",
    }.get(row_type, row_type)


def build_statement_html(
    *,
    ledger: StatementLedgerResult,
    period_start: date,
    period_end: date,
    client_name: str,
    client_address: str,
    client_email: str,
    statement_number: str,
) -> str:
    """Assemble statement HTML as a Python string (WeasyPrint input)."""
    aging = ledger.aging
    rows_html = []
    running = ledger.opening_balance
    rows_html.append(
        "<tr class='opening'>"
        f"<td colspan='6'>Opening balance</td>"
        f"<td class='num'>{_esc(_fmt_currency(ledger.opening_balance, ledger.currency))}</td>"
        f"<td class='num'>{_esc(_fmt_currency(ledger.opening_balance, ledger.currency))}</td>"
        "</tr>"
    )
    for row in ledger.rows:
        running += row.amount
        pay_date = _fmt_date(row.payment_date) if row.payment_date else "—"
        rows_html.append(
            "<tr>"
            f"<td>{_esc(row.reference_number)}</td>"
            f"<td>{_esc(_fmt_date(row.issue_date))}</td>"
            f"<td><span class='tag'>{_esc(_row_type_label(row.row_type))}</span></td>"
            f"<td>{_esc(row.order_ref or '—')}</td>"
            f"<td>{_esc(pay_date)}</td>"
            f"<td>{_esc(row.status)}</td>"
            f"<td class='num'>{_esc(_fmt_currency(row.display_amount, ledger.currency))}</td>"
            f"<td class='num'>{_esc(_fmt_currency(running, ledger.currency))}</td>"
            "</tr>"
        )
        if row.line_items:
            for li in row.line_items:
                rows_html.append(
                    "<tr class='line-item'>"
                    f"<td colspan='6'>{_esc(li.description)} (qty {li.quantity})</td>"
                    f"<td class='num'>{_esc(_fmt_currency(li.total_price, ledger.currency))}</td>"
                    "<td></td>"
                    "</tr>"
                )

    truncated_note = ""
    if ledger.truncated:
        truncated_note = "<p class='warn'>Transaction list truncated for PDF size. Totals reflect the full period.</p>"

    a1 = _esc(_fmt_currency(aging.get("days_1_30", "0"), ledger.currency))
    a2 = _esc(_fmt_currency(aging.get("days_31_60", "0"), ledger.currency))
    a3 = _esc(_fmt_currency(aging.get("days_61_90", "0"), ledger.currency))
    a4 = _esc(_fmt_currency(aging.get("days_90_plus", "0"), ledger.currency))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Statement {_esc(statement_number)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; font-size: 11px; color: #222; margin: 24px; }}
    h1 {{ font-size: 20px; margin: 0 0 4px; }}
    h2 {{ font-size: 14px; margin: 16px 0 8px; }}
    .header {{ display: flex; justify-content: space-between; margin-bottom: 24px; }}
    .muted {{ color: #666; }}
    .aging {{ display: flex; gap: 12px; margin: 12px 0; }}
    .aging div {{ border: 1px solid #ddd; padding: 8px 12px; border-radius: 4px; min-width: 90px; }}
    .aging strong {{ display: block; font-size: 10px; color: #666; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ border-bottom: 1px solid #eee; padding: 6px 4px; text-align: left; }}
    th {{ background: #f7f7f7; font-size: 10px; }}
    td.num {{ text-align: right; white-space: nowrap; }}
    tr.opening td {{ font-weight: bold; background: #fafafa; }}
    tr.line-item td {{ font-size: 10px; color: #555; padding-left: 16px; }}
    .tag {{ background: #eee; padding: 2px 6px; border-radius: 3px; font-size: 9px; }}
    .totals {{ margin-top: 16px; width: 280px; margin-left: auto; }}
    .totals td {{ border: none; padding: 4px 0; }}
    .warn {{ color: #b45309; font-size: 10px; }}
    .closing {{ font-weight: bold; font-size: 13px; margin-top: 12px; text-align: right; }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>{_esc(COMPANY_NAME)}</h1>
      <p class="muted">{_esc(COMPANY_ADDRESS)}<br/>{_esc(COMPANY_EMAIL)}</p>
    </div>
    <div>
      <p><strong>{_esc(client_name)}</strong><br/>
      {_esc(client_address)}<br/>
      {_esc(client_email)}</p>
    </div>
  </div>

  <h2>Statement of Accounts</h2>
  <p class="muted">Period: {_esc(_fmt_date(period_start))} to {_esc(_fmt_date(period_end))} · Statement ID: {_esc(statement_number)}</p>

  <div class="aging">
    <div><strong>1-30 Days</strong>{a1}</div>
    <div><strong>31-60 Days</strong>{a2}</div>
    <div><strong>61-90 Days</strong>{a3}</div>
    <div><strong>90+ Days</strong>{a4}</div>
  </div>

  {truncated_note}

  <table>
    <thead>
      <tr>
        <th>Reference</th><th>Issue Date</th><th>Type</th><th>Order</th>
        <th>Payment Date</th><th>Status</th><th>Amount</th><th>Balance</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>

  <table class="totals">
    <tr><td>Total Invoice Amount</td><td class="num">{_esc(_fmt_currency(ledger.total_invoice_amount, ledger.currency))}</td></tr>
    <tr><td>Total Paid</td><td class="num">{_esc(_fmt_currency(ledger.total_paid, ledger.currency))}</td></tr>
    <tr><td>Total Unpaid</td><td class="num">{_esc(_fmt_currency(ledger.total_unpaid, ledger.currency))}</td></tr>
    <tr><td>Total Overdue</td><td class="num">{_esc(_fmt_currency(ledger.total_overdue, ledger.currency))}</td></tr>
  </table>
  <p class="closing">Closing Balance: {_esc(_fmt_currency(ledger.closing_balance, ledger.currency))}</p>
</body>
</html>"""


def html_to_pdf(html_content: str) -> bytes:
    """Convert HTML string to PDF bytes using WeasyPrint."""
    from weasyprint import HTML

    pdf_bytes = HTML(string=html_content).write_pdf()
    if pdf_bytes is None:
        raise RuntimeError("WeasyPrint returned no PDF bytes")
    return pdf_bytes
