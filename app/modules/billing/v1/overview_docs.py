"""OpenAPI docs for org billing overview."""

from __future__ import annotations

from typing import Any

from app.core.swagger import create_doc_entry, error_401_entry, success_entry

_EX_ORG = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

BILLING_OVERVIEW_GET: dict[str, Any] = create_doc_entry(
    "Billing overview for organization",
    {
        200: success_entry(
            "Overview KPIs and charts",
            data={
                "meta": {
                    "period_start": "2026-04-21",
                    "period_end": "2026-05-21",
                    "prior_period_start": "2026-03-22",
                    "prior_period_end": "2026-04-20",
                    "timezone": "UTC",
                    "definitions_version": "billing-overview-v1",
                    "chart_year": 2026,
                },
                "kpis": {
                    "total_billed": {
                        "value": "45230.00",
                        "change_pct": "12.5",
                        "comparison_label": "vs prior period",
                    },
                    "payments_received": {
                        "value": "38100.00",
                        "change_pct": "8.2",
                        "comparison_label": "vs prior period",
                    },
                    "outstanding_balance": {
                        "value": "7120.00",
                        "change_pct": "-3.1",
                        "comparison_label": "vs prior period",
                    },
                    "overdue_amount": {
                        "value": "2100.00",
                        "change_pct": "5.0",
                        "comparison_label": "vs prior period",
                    },
                    "credit_notes_issued": {
                        "value": "3",
                        "change_pct": "0",
                        "comparison_label": "vs prior period",
                    },
                    "refunds_issued": {
                        "value": "850.00",
                        "change_pct": "-15.0",
                        "comparison_label": "vs prior period",
                    },
                },
                "charts": {
                    "revenue_trend": [
                        {"month": 1, "revenue": "12000.00", "refunds": "200.00", "net_revenue": "11800.00"},
                        {"month": 2, "revenue": "15000.00", "refunds": "150.00", "net_revenue": "14850.00"},
                    ],
                    "payment_method_usage": [
                        {"method": "CARD", "amount": "22000.00", "percent": "57.7"},
                        {"method": "BANK_TRANSFER", "amount": "14100.00", "percent": "37.0"},
                        {"method": "CASH", "amount": "2000.00", "percent": "5.3"},
                    ],
                    "invoice_status": [
                        {"status": "PAID", "count": 42, "total_value": "32000.00"},
                        {"status": "UNPAID", "count": 8, "total_value": "7120.00"},
                        {"status": "OVERDUE", "count": 3, "total_value": "2100.00"},
                    ],
                    "billing_activity": [
                        {
                            "month": 4,
                            "invoices_amount": "18000.00",
                            "invoices_count": 15,
                            "payments_amount": "14000.00",
                            "payments_count": 12,
                        },
                        {
                            "month": 5,
                            "invoices_amount": "22000.00",
                            "invoices_count": 18,
                            "payments_amount": "19000.00",
                            "payments_count": 14,
                        },
                    ],
                },
            },
        ),
        401: error_401_entry(),
    },
    description=(
        f"Canonical Billing → Overview tab for organisation `{_EX_ORG}` (path param). "
        "Query **`period`**: today | yesterday | last_7_days | last_30_days (default last_30_days). "
        "Optional **`chart_year`** for revenue/billing-activity charts (defaults to current UTC year). "
        "KPI `value` fields are decimal strings; `credit_notes_issued.value` is a count string. "
        "Voided/written-off invoices excluded from total_billed. Requires Resource.BILLING READ."
    ),
)
