"""OpenAPI docs for dashboard endpoints."""

from app.core.swagger import create_doc_entry, success_entry

_OPERATIONS_KPIS_EXAMPLE = {
    "as_of_date": "2026-05-19",
    "organization_id": None,
    "next_7_day_stops": {
        "current": 1284,
        "previous": 1209,
        "change_abs": 75,
        "change_pct": 6.2,
        "comparison_label": "last 7 days",
    },
    "delivered_today": {
        "current": 518,
        "previous": 490,
        "change_abs": 28,
        "change_pct": 5.71,
        "success_rate_pct": 95.0,
        "previous_success_rate_pct": 93.2,
        "comparison_label": "yesterday",
    },
    "today_orders": {
        "current": 76,
        "previous": 64,
        "change_abs": 12,
        "change_pct": 18.75,
        "comparison_label": "yesterday",
    },
    "pending_orders": {
        "current": 42,
        "previous": 37,
        "change_abs": 5,
        "change_pct": 13.51,
        "comparison_label": "yesterday",
    },
    "active_drivers": {
        "current": 89,
        "previous": 81,
        "change_abs": 8,
        "change_pct": 9.88,
        "comparison_label": "yesterday",
    },
}

OPERATIONS_DASHBOARD_KPIS = create_doc_entry(
    summary="Operations home dashboard KPI cards",
    description=(
        "Single payload for the admin operations dashboard tiles.\n\n"
        "**next_7_day_stops** — route stops on plans with ``service_date`` from today through the next 6 days, "
        "compared to the prior 7-day window (today-7 … today-1).\n\n"
        "**delivered_today** — distinct delivery stops that reached a delivered outcome today (UTC calendar day), "
        "with **success_rate_pct** = delivered / (delivered + failed outcomes) for the same day.\n\n"
        "**today_orders** — orders created today vs yesterday.\n\n"
        "**pending_orders** — open (non-terminal) orders now vs open orders that existed before today "
        "(approximates net backlog change).\n\n"
        "**active_drivers** — ACTIVE drivers with a linked user now vs those activated before today.\n\n"
        "Requires ``DASHBOARD`` READ. ADMIN/SUPER_ADMIN may omit ``organization_id`` for a global view."
    ),
    responses={
        200: success_entry("Operations dashboard KPIs", data=_OPERATIONS_KPIS_EXAMPLE),
    },
)

_TODAYS_FINANCIALS_EXAMPLE = {
    "as_of_date": "2026-05-19",
    "organization_id": None,
    "revenue_today": "184560.00",
    "unpaid_invoices_count": 6,
    "overdue_invoices_count": 2,
    "revenue_trend": [
        {"date": "2026-05-13", "weekday": "Monday", "revenue": "184560.00"},
        {"date": "2026-05-14", "weekday": "Tuesday", "revenue": "195230.00"},
        {"date": "2026-05-15", "weekday": "Wednesday", "revenue": "172840.00"},
        {"date": "2026-05-16", "weekday": "Thursday", "revenue": "201450.00"},
        {"date": "2026-05-17", "weekday": "Friday", "revenue": "189320.00"},
        {"date": "2026-05-18", "weekday": "Saturday", "revenue": "165780.00"},
        {"date": "2026-05-19", "weekday": "Sunday", "revenue": "142650.00"},
    ],
}

TODAYS_FINANCIALS = create_doc_entry(
    summary="Today's financials dashboard widget",
    description=(
        "Revenue and collections summary for the admin home dashboard.\n\n"
        "**revenue_trend** — payments received per day for the last 7 calendar days (inclusive).\n\n"
        "**revenue_today** — payments received on ``as_of_date``.\n\n"
        "**unpaid_invoices_count** / **overdue_invoices_count** — open sent invoices.\n\n"
        "Requires ``DASHBOARD`` READ. ADMIN/SUPER_ADMIN may omit ``organization_id`` for global totals."
    ),
    responses={200: success_entry("Today's financials", data=_TODAYS_FINANCIALS_EXAMPLE)},
)

_HIGHLIGHTED_ISSUES_EXAMPLE = {
    "items": [
        {
            "delivery_stop_id": "00000000-0000-0000-0000-000000000001",
            "tracking_number": "SW-2024-001236",
            "order_id": "00000000-0000-0000-0000-000000000010",
            "order_reference": "SWC-ORD-000123",
            "client_name": "Bob Johnson",
            "status": "OUT_FOR_DELIVERY",
            "driver_name": "K. Mehta",
            "delivery_deadline": "2026-05-20",
            "deadline_urgency": "critical",
            "issue": "No driver assigned",
            "issue_code": "NO_DRIVER_ASSIGNED",
            "auto_remediation": "Auto rescheduled",
            "remediation_applied": True,
        }
    ],
    "total": 1,
    "page": 1,
    "size": 20,
    "pages": 1,
}

HIGHLIGHTED_ISSUES = create_doc_entry(
    summary="Highlighted operational issues table",
    description=(
        "Paginated list of delivery stops that need immediate operational attention.\n\n"
        "Supports ``search`` (tracking id, client name, order reference, organisation name) and "
        "``status`` (repeatable delivery stop status filter).\n\n"
        "Requires ``DASHBOARD`` READ."
    ),
    responses={200: success_entry("Highlighted issues", data=_HIGHLIGHTED_ISSUES_EXAMPLE)},
)
