from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_ORG = "11111111-1111-1111-1111-111111111111"
_ORDER = "22222222-2222-2222-2222-222222222222"
_STOP = "33333333-3333-3333-3333-333333333333"
_PKG = "44444444-4444-4444-4444-444444444444"
_CUST = "55555555-5555-5555-5555-555555555555"
_TS = "2026-04-20T10:00:00Z"
_TS2 = "2026-04-20T10:00:10Z"

_DELETED = {"deleted": True}

_ORDER_LABELS_ON_CREATE = {
    "order_id": "SWC-ORD-000001",
    "master_label": {
        "master_label_id": "ML-0000000001",
        "pickup_address": "Unit 5, Acme Park, B1 1AA",
        "barcode_value": "ML-0000000001",
        "qr_value": "ML-0000000001",
        "delivery_stops_count": 0,
        "total_packages": 0,
        "total_weight_kg": None,
        "total_volume_m3": None,
    },
    "pickup_labels": [],
}

_DRAFT_DETAIL = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "organization_id": _ORG,
    "customer_id": _CUST,
    "payload": {
        "pickup_address": "1 High Street",
        "contact_name": "Jane Smith",
        "delivery_stops": [
            {
                "packages": [
                    {"length_cm": 30, "width_cm": 20, "height_cm": 15, "declared_weight_kg": 2.5, "declared_value": "50.00"}
                ]
            }
        ],
    },
}

_DRAFT_LIST_ITEM = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "created_at": _TS,
    "draft_id": "DR-0001",
    "order_id": None,
    "organization_id": _ORG,
    "customer_id": _CUST,
    "pickup_address_id": None,
    "contact_name": "Jane Smith",
    "pickup_address": "1 High Street, Birmingham B1 1AA",
    "package_count": 1,
    "delivery_stop_count": 1,
    "total_value": "50.00",
}

_DRAFT_LIST_PAGE = {
    "items": [_DRAFT_LIST_ITEM],
    "total": 1,
    "page": 1,
    "size": 20,
    "pages": 1,
    "current_url": None,
    "next_url": None,
}

_ORDER_LIST_ITEM = {
    "id": _ORDER,
    "created_at": _TS,
    "order_id": "SWC-ORD-000123",
    "organization_id": _ORG,
    "pickup_address_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    "contact_name": "Acme Ltd",
    "pickup_address": "Unit 5, Acme Park, B1 1AA",
    "created_by": {"id": "77777777-7777-7777-7777-777777777777", "name": "Ops User"},
    "status": "PENDING_PICKUP",
    "package_count": 2,
    "delivery_stop_count": 1,
}

_ORDER_LIST_PAGE = {
    "items": [_ORDER_LIST_ITEM],
    "total": 1,
    "page": 1,
    "size": 20,
    "pages": 1,
    "current_url": None,
    "next_url": None,
}

_ORDER_DETAIL_PACKAGE = {
    "id": _PKG,
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "order_id": _ORDER,
    "delivery_stop_id": _STOP,
    "package_id": "SWC-PKG-00000001",
    "status": "AT_WAREHOUSE",
    "length_cm": 56.0,
    "width_cm": 26.0,
    "height_cm": 78.0,
    "declared_weight_kg": 10.0,
    "weight_kg": 9.8,
    "declared_value": "100.00",
    "is_damaged": False,
}

_ORDER_DETAIL_STOP = {
    "id": _STOP,
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "order_id": _ORDER,
    "tracking_id": "SWC-STK-00000001",
    "recipient_first_name": "Alex",
    "recipient_last_name": "Brown",
    "recipient_phone": "+44123456789",
    "recipient_email": "alex@example.com",
    "line_1": "10 Queen St",
    "line_2": None,
    "city": "London",
    "postcode": "W1D 1AA",
    "latitude": 51.5,
    "longitude": -0.13,
    "service_tier": "STANDARD",
    "service_tier_id": None,
    "signature_required": True,
    "safe_place_allowed": False,
    "status": "DELIVERY_SCHEDULED",
    "packages_count": 1,
    "packages": [_ORDER_DETAIL_PACKAGE],
}

_ORDER_DETAIL = {
    "id": _ORDER,
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "order_id": "SWC-ORD-000123",
    "master_label_id": "ML-0000000123",
    "organization_id": _ORG,
    "customer_id": _CUST,
    "pickup_address_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    "status": "PENDING_PICKUP",
    "payment_method": "CREDIT_ACCOUNT",
    "payment_method_id": "99999999-9999-9999-9999-999999999999",
    "subtotal": "60.00",
    "vat_amount": "12.00",
    "total_amount": "72.00",
    "price_breakdown": {
        "id": _ORDER,
        "order_id": "SWC-ORD-000123",
        "currency": "GBP",
        "computed_at": "2026-04-20T10:00:00+00:00",
        "stops": [
            {
                "id": _STOP,
                "tracking_id": "SWC-STK-00000001",
                "stop_index": 0,
                "service_tier": "Standard",
                "service_tier_id": None,
                "pricing_plan": {
                    "id_price_tier": "66666666-6666-6666-6666-666666666666",
                    "plain_name": "Standard",
                    "plain_type": "STANDARD",
                    "days": 1,
                    "base_price": "50.00",
                    "price_per_package": "5.00",
                    "price_per_kg": "1.50",
                    "tier_name_at_order_time": "Standard",
                },
                "base_price": "50.00",
                "packages": [
                    {
                        "id": _PKG,
                        "package_id": "SWC-PKG-00000001",
                        "package_index": 0,
                        "declared_weight_kg": 10.0,
                        "per_package_charge": "5.00",
                        "weight_charge": {
                            "price_per_kg": "1.50",
                            "weight_kg": 10.0,
                            "amount": "15.00",
                        },
                        "total": "20.00",
                    }
                ],
                "packages_count": 1,
                "packages_subtotal": "20.00",
                "pre_discount_subtotal": "70.00",
                "discounts": [
                    {
                        "type": "FIXED_PER_BOOKING",
                        "service_tier_id": "66666666-6666-6666-6666-666666666666",
                        "value": "10.00",
                        "amount": "10.00",
                    }
                ],
                "total_discount": "10.00",
                "subtotal_after_discount": "60.00",
                "min_charge": "25.00",
                "min_charge_applied": False,
                "subtotal": "60.00",
                "vat_rate": "STANDARD_20",
                "vat_rate_pct": "20.00",
                "vat_amount": "12.00",
                "total": "72.00",
            }
        ],
        "packages_count": 1,
        "subtotal": "60.00",
        "vat_amount": "12.00",
        "total": "72.00",
    },
    "delivery_stops": [_ORDER_DETAIL_STOP],
}

_MASTER_LABELS = {
    "order_id": "SWC-ORD-000123",
    "master_label": {
        "master_label_id": "ML-0000000123",
        "pickup_address": "Unit 5, Acme Park, B1 1AA",
        "barcode_value": "ML-0000000123",
        "qr_value": "ML-0000000123",
        "delivery_stops_count": 1,
        "total_packages": 1,
        "total_weight_kg": 9.8,
        "total_volume_m3": 0.114,
    },
    "pickup_labels": [
        {
            "package_id": "SWC-PKG-00000001",
            "tracking_id": "SWC-STK-00000001",
            "recipient_name": "Alex Brown",
            "recipient_address": "10 Queen St, London, W1D 1AA",
            "pickup_address": "Unit 5, Acme Park, B1 1AA",
            "return_address": "Unit 5, Acme Park, B1 1AA",
            "signature_required": True,
            "weight_kg": 9.8,
            "dimensions_cm": "56 x 26 x 78",
            "volume_m3": 0.114,
            "delivery_days": 5,
            "delivery_label": "5 DAYS DELIVERY",
        }
    ],
}

_PACKAGE_ON_STOP = {
    "id": _PKG,
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "order_id": _ORDER,
    "delivery_stop_id": _STOP,
    "package_id": "SWC-PKG-00000001",
    "status": "AT_WAREHOUSE",
    "length_cm": 56.0,
    "width_cm": 26.0,
    "height_cm": 78.0,
    "declared_weight_kg": 10.0,
    "weight_kg": 9.8,
    "declared_value": "100.00",
    "is_damaged": False,
    "price_breakdown": {"line": "10.00"},
}

_STOP_WITH_PACKAGES = {
    "id": _STOP,
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "order_id": _ORDER,
    "tracking_id": "SWC-STK-00000001",
    "recipient_first_name": "Alex",
    "recipient_last_name": "Brown",
    "recipient_phone": "+44123456789",
    "recipient_email": "alex@example.com",
    "line_1": "10 Queen St",
    "line_2": None,
    "city": "London",
    "postcode": "W1D 1AA",
    "latitude": 51.5,
    "longitude": -0.13,
    "service_tier": "STANDARD",
    "service_tier_id": None,
    "signature_required": True,
    "safe_place_allowed": False,
    "status": "DELIVERY_SCHEDULED",
    "packages_count": 1,
    "packages": [_PACKAGE_ON_STOP],
    "price_breakdown": {"subtotal": "50.00"},
}

_STOPS_LIST = {
    "order_id": "SWC-ORD-000123",
    "master_label_id": "ML-0000000123",
    "items": [_STOP_WITH_PACKAGES],
}

_STOP_PACKAGES = {
    "order_id": _ORDER,
    "stop_id": _STOP,
    "tracking_id": "SWC-STK-00000001",
    "items": [_PACKAGE_ON_STOP],
}

_STOP_NOTE_ADMIN = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "delivery_stop_id": _STOP,
    "note_type": "ADMIN",
    "message": "Call customer 10 minutes before arrival.",
    "is_blocking": True,
    "sort_order": 2,
    "package_ids": [],
    "images": [],
}

_STOP_NOTE_CUSTOMER = {
    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "delivery_stop_id": _STOP,
    "note_type": "CUSTOMER",
    "message": "Leave parcel with neighbour at number 12 if unavailable.",
    "is_blocking": False,
    "sort_order": 0,
    "package_ids": [],
    "images": [],
}

_STOP_NOTE_PACKAGE_ISSUE = {
    "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "created_at": _TS,
    "updated_at": _TS2,
    "version": 1,
    "delivery_stop_id": _STOP,
    "note_type": "PACKAGE_ISSUE_NOTE",
    "message": "Parcel received with damaged outer packaging.",
    "is_blocking": False,
    "sort_order": 1,
    "package_ids": [_PKG, "44444444-4444-4444-4444-444444444445"],
    "images": [
        {
            "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "created_at": _TS,
            "updated_at": _TS2,
            "version": 1,
            "stop_note_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "image_key": "orders/stops/notes/damage-1.jpg",
            "sort_order": 0,
            "image_url": "https://example.com/signed/damage-1.jpg",
        }
    ],
}

# Single-note response example (e.g. POST/PATCH): admin note with optional image
_STOP_NOTE = {
    **_STOP_NOTE_ADMIN,
    "images": [
        {
            "id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            "created_at": _TS,
            "updated_at": _TS2,
            "version": 1,
            "stop_note_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "image_key": "orders/stops/notes/instruction.jpg",
            "sort_order": 0,
            "image_url": "https://example.com/signed/instruction.jpg",
        }
    ],
}

_STOP_NOTES = {
    "order_id": _ORDER,
    "stop_id": _STOP,
    "items": [_STOP_NOTE_CUSTOMER, _STOP_NOTE_PACKAGE_ISSUE, _STOP_NOTE_ADMIN],
}

_ORDERS_SUMMARY_DATA = {
    "period_from": "2026-02-19",
    "period_to": "2026-02-25",
    "previous_period_from": "2026-02-12",
    "previous_period_to": "2026-02-18",
    "comparison_label": "previous 7 days",
    "total_orders": {"current": 1247, "previous": 1057, "change_pct": 17.97},
    "pickups_on_route": {"current": 12, "previous": 10, "change_pct": 20.0},
    "delivered": {"current": 1092, "previous": 925, "change_pct": 18.05},
    "cancelled": {"current": 18, "previous": 15, "change_pct": 20.0},
    "failed": {"current": 48, "previous": 40, "change_pct": 20.0},
    "returned": {"current": 18, "previous": 15, "change_pct": 20.0},
}

_FAILED_DELIVERIES_SUMMARY_DATA = {
    "period_from": "2026-02-19",
    "period_to": "2026-02-25",
    "previous_period_from": "2026-02-12",
    "previous_period_to": "2026-02-18",
    "comparison_label": "previous 7 days",
    "total_failed": {"current": 242, "previous": 205, "change_pct": 18.05},
    "missing": {"current": 23, "previous": 20, "change_pct": 15.0},
    "damaged": {"current": 65, "previous": 55, "change_pct": 18.18},
    "cancelled": {"current": 14, "previous": 16, "change_pct": -12.5},
    "customer_not_home": {"current": 65, "previous": 72, "change_pct": -9.72},
    "refused": {"current": 54, "previous": 45, "change_pct": 20.0},
}

_ENTITY_STATUS_EVENT_STOP = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "created_at": _TS,
    "from_status": "DELIVERED",
    "to_status": "DELIVERY_ATTEMPT_1_FAILED",
    "display_label": "Attempt 1 failed",
    "actor_user_id": None,
}
_ENTITY_STATUS_EVENT_PACKAGE = {
    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "created_at": _TS2,
    "from_status": "AT_WAREHOUSE",
    "to_status": "DAMAGED",
    "display_label": "Damaged",
    "actor_user_id": None,
}

_FAILED_DELIVERIES_LIST_DATA = {
    "items": [
        {
            "delivery_stop_id": _STOP,
            "tracking_id": "SWBHM-984523",
            "postcode": "W8 5ED",
            "order_id": _ORDER,
            "order_reference": "SWC-ORD-000123",
            "stop_status": "DELIVERY_ATTEMPT_3_FAILED",
            "attempt_number": 3,
            "max_attempts": 3,
            "previous_attempt_at": "2026-03-12T10:24:00Z",
            "next_attempt_at": None,
            "stop_status_events": [_ENTITY_STATUS_EVENT_STOP],
            "packages": [
                {
                    "id": _PKG,
                    "package_id": "SWC-PKG-00000001",
                    "status": "DAMAGED",
                    "reason": "PACKAGE_DAMAGED — Box opened, contents missing",
                    "status_events": [_ENTITY_STATUS_EVENT_PACKAGE],
                }
            ],
        }
    ],
    "total": 24,
    "page": 1,
    "size": 20,
    "pages": 2,
    "current_url": None,
    "next_url": None,
}

_RETURNS_SUMMARY_DATA = {
    "period_from": "2026-02-19",
    "period_to": "2026-02-25",
    "previous_period_from": "2026-02-12",
    "previous_period_to": "2026-02-18",
    "comparison_label": "previous 7 days",
    "total_returns": {"current": 14, "previous": 12, "change_pct": 16.67},
    "returns_in_transit": {"current": 2, "previous": 1, "change_pct": 100.0},
    "disposed_packages": {"current": 6, "previous": 5, "change_pct": 20.0},
    "returned_packages": {"current": 6, "previous": 5, "change_pct": 20.0},
    "initiated": {"current": 2, "previous": 2, "change_pct": 0.0},
    "avg_resolution_days": {"current": 4.2, "previous": 3.5, "change_pct": 20.0},
}

_RETURNS_LIST_DATA = {
    "items": [
        {
            "delivery_stop_id": _STOP,
            "tracking_id": "SWBHM-984523",
            "postcode": "W8 5ED",
            "order_id": _ORDER,
            "order_reference": "SWC-ORD-000123",
            "initiated_at": "2026-03-12T10:24:00Z",
            "stop_status_events": [_ENTITY_STATUS_EVENT_STOP],
            "packages": [
                {
                    "id": _PKG,
                    "package_id": "SWC-PKG-00000001",
                    "status": "RETURN_INITIATED",
                    "return_reason": "Customer Refused",
                    "initiated_at": "2026-03-12T10:24:00Z",
                    "status_events": [_ENTITY_STATUS_EVENT_PACKAGE],
                }
            ],
        }
    ],
    "total": 14,
    "page": 1,
    "size": 20,
    "pages": 1,
    "current_url": None,
    "next_url": None,
}

_STOP_RESCHEDULE_DATA = {
    "delivery_stop_id": _STOP,
    "tracking_id": "SWBHM-984523",
    "stop_status": "DELIVERY_SCHEDULED",
    "scheduled_for": "2026-04-30",
    "affected_package_ids": [_PKG],
}

_PACKAGE_RESCHEDULE_DATA = {
    "id": _PKG,
    "package_id": "SWC-PKG-00000001",
    "delivery_stop_id": _STOP,
    "status": "AT_WAREHOUSE",
    "stop_status": "DELIVERY_SCHEDULED",
    "order_status": "DELIVERY_IN_PROGRESS",
}

_PACKAGE_INITIATE_RETURN_DATA = {
    "id": _PKG,
    "package_id": "SWC-PKG-00000001",
    "delivery_stop_id": _STOP,
    "status": "RETURN_INITIATED",
    "stop_status": "RETURN_INITIATED",
    "order_status": "RETURN_IN_PROGRESS",
}

_STOP_INITIATE_RETURN_DATA = {
    "delivery_stop_id": _STOP,
    "tracking_id": "SWBHM-984523",
    "stop_status": "RETURN_INITIATED",
    "scheduled_for": None,
    "affected_package_ids": [_PKG, "44444444-4444-4444-4444-444444444444"],
}

_STOP_MARK_FOUND_DATA = {
    "delivery_stop_id": _STOP,
    "tracking_id": "SWBHM-984523",
    "stop_status": "DELIVERY_ATTEMPT_2_FAILED",
    "scheduled_for": None,
    "affected_package_ids": [_PKG, "44444444-4444-4444-4444-444444444444"],
}

_PACKAGE_MARK_FOUND_DATA = {
    "id": _PKG,
    "package_id": "SWC-PKG-00000001",
    "delivery_stop_id": _STOP,
    "status": "AT_WAREHOUSE",
    "stop_status": "DELIVERY_ATTEMPT_2_FAILED",
    "order_status": "DELIVERY_IN_PROGRESS",
}

_STOP_RESOLVE_RETURN_DATA = {
    "delivery_stop_id": _STOP,
    "tracking_id": "SWBHM-984523",
    "stop_status": "RETURN_IN_TRANSIT",
    "return_resolution": "RETURN_TO_SENDER",
    "return_resolved_at": "2026-04-22T16:30:00Z",
    "return_dispatch_date": "2026-04-25",
    "return_cost": "12.50",
    "return_cost_waived": False,
    "return_notes": "Sent via standard reverse logistics",
    "disposal_reason": None,
    "affected_package_ids": [_PKG],
    "evidence_images": [],
}

_PRICE_BREAKDOWN_DATA = {
    "subtotal": "80.00",
    "vat_amount": "16.00",
    "total_amount": "96.00",
    "breakdown": {
        "id": None,
        "order_id": None,
        "currency": "GBP",
        "computed_at": _TS,
        "packages_count": 1,
        "subtotal": "80.00",
        "vat_amount": "16.00",
        "total": "96.00",
        "stops": [
            {
                "id": None,
                "tracking_id": None,
                "stop_index": 1,
                "service_tier": "Standard",
                "service_tier_id": "tier-std",
                "pricing_plan": {
                    "id_price_tier": "tier-std",
                    "plain_name": "Standard",
                    "plain_type": None,
                    "days": None,
                    "base_price": "50.00",
                    "price_per_package": "10.00",
                    "price_per_kg": "2.00",
                    "tier_name_at_order_time": "Standard",
                },
                "base_price": "50.00",
                "packages": [
                    {
                        "id": None,
                        "package_id": None,
                        "package_index": 1,
                        "declared_weight_kg": 2.5,
                        "per_package_charge": "10.00",
                        "weight_charge": {
                            "price_per_kg": "2.00",
                            "weight_kg": 2.5,
                            "amount": "5.00",
                        },
                        "total": "15.00",
                    }
                ],
                "packages_count": 1,
                "packages_subtotal": "15.00",
                "pre_discount_subtotal": "65.00",
                "discounts": [],
                "total_discount": "0.00",
                "subtotal_after_discount": "65.00",
                "min_charge": "80.00",
                "min_charge_applied": True,
                "subtotal": "80.00",
                "vat_rate": "STANDARD_20",
                "vat_rate_pct": "20.00",
                "vat_amount": "16.00",
                "total": "96.00",
            }
        ],
    },
}

ORDERS_PRICE_BREAKDOWN = create_doc_entry(
    summary="Preview order price breakdown before create",
    description=(
        "POST /v1/orders/price-breakdown. Body: client_type, organization_id (required for B2B), delivery_stops "
        "(tiers and packages). B2B portal callers may only price their own organisation. "
        "Does not persist an order, charge a card, or validate pickup or payment methods. "
        "Returns per-stop and order-level totals matching the pricing engine used at create time."
    ),
    responses={
        200: success_entry("Computed price breakdown", data=_PRICE_BREAKDOWN_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid stops or pricing data"),
    },
)

_STOP_PACKAGES_UPDATE_DATA = {
    "order_id": _ORDER,
    "delivery_stop_id": _STOP,
    "tracking_id": "SWC-STK-00000001",
    "service_tier": "STANDARD",
    "service_tier_id": None,
    "packages": [
        {
            "id": _PKG,
            "package_id": "SWC-PKG-00000001",
            "length_cm": 56,
            "width_cm": 26,
            "height_cm": 78,
            "declared_weight_kg": 10,
            "declared_value": "100.00",
        }
    ],
    "stop_price_breakdown": {"base_price": "50.00", "subtotal": "84.00"},
    "order_subtotal": "84.00",
    "order_vat_amount": "16.80",
    "order_total_amount": "100.80",
    "order_price_breakdown": {"total": "100.80"},
}

ORDERS_CREATE = create_doc_entry(
    summary="Create a new order for an organisation",
    description=(
        "POST /v1/orders. Body: client_type (B2B default, or B2C), organization_id (required when client_type is B2B; "
        "B2C: optional, must match the org of payment_method_id when set; otherwise the org is taken from "
        "payment_method_id), contact_user_id (portal users.id "
        "the order is for), requested_pickup_date (optional), pickup_address_id (saved pickup from /v1/pickup-addresses), "
        "payment_method and payment_method_id (row in org_payment_methods; must match), "
        "credit_card_id (required when payment_method is CARD), "
        "payment_method_nonce (required when payment_method is CARD — from Braintree threeDSecure.verifyCard for the order total), "
        "delivery_stops. "
        "B2B self-serve: contact_user_id must be the caller. Admins: contact_user_id must be an active org contact. "
        "B2C: only B2C customers; contact_user_id must be the caller. "
        "Each stop needs service_tier_name and/or service_tier_id (the latter must equal id_price_tier on one of the "
        "organisation's pricing_plans entries — same string the org stores there, not necessarily service_tier.id). "
        "The delivery stop row does not persist that id on service_tier_id (it stays null); the chosen plan is "
        "snapshotted under price_breakdown.pricing_plan on the server."
    ),
    responses={
        201: success_entry("Order created", data=_ORDER_LABELS_ON_CREATE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        409: error_entry("Conflict", code="CONFLICT", message="Unable to generate unique identifier"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="At least one delivery stop is required"),
    },
)

ORDERS_SAVE_DRAFT = create_doc_entry(
    summary="Save a work-in-progress order as a JSON draft",
    description=(
        "POST /v1/orders/drafts?organization_id=... Partial payload: same fields as order create "
        "(contact_user_id, pickup_address_id, payment fields, delivery_stops, etc.); additional keys allowed. "
        "pickup_address_id must be the UUID from GET/POST /v1/pickup-addresses (`data[].id`), not draft/order codes."
    ),
    responses={
        201: success_entry("Draft created", data=_DRAFT_DETAIL),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

ORDER_DRAFTS_LIST = create_doc_entry(
    summary="List order drafts for an organisation",
    description="GET /v1/orders/drafts?organization_id=... with pagination and list filters from query params.",
    responses={
        200: success_entry("Draft list", data=_DRAFT_LIST_PAGE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

ORDER_DRAFT_GET = create_doc_entry(
    summary="Get a single order draft by id",
    description="GET /v1/orders/drafts/{draft_id}?organization_id=...",
    responses={
        200: success_entry("Draft detail", data=_DRAFT_DETAIL),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Draft not found", code="NOT_FOUND", message="order_draft with id '...' not found"),
    },
)

ORDER_DRAFT_UPDATE = create_doc_entry(
    summary="Update a saved order draft",
    description="PATCH /v1/orders/drafts/{draft_id}?organization_id=... with a partial or full JSON payload.",
    responses={
        200: success_entry("Draft updated", data=_DRAFT_DETAIL),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Draft not found", code="NOT_FOUND", message="order_draft with id '...' not found"),
    },
)

ORDER_DRAFT_SUBMIT = create_doc_entry(
    summary="Submit a draft to create a live order",
    description="POST /v1/orders/drafts/{draft_id}/submit?organization_id=...: validates, creates the order, deletes the draft. Returns order detail in data.",
    responses={
        200: success_entry("Order created from draft", data=_ORDER_DETAIL),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Draft not found", code="NOT_FOUND", message="order_draft with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Missing required fields for submission",
        ),
    },
)

ORDER_DRAFT_DELETE = create_doc_entry(
    summary="Delete an order draft",
    description="DELETE /v1/orders/drafts/{draft_id}?organization_id=...",
    responses={
        200: success_entry("Draft deleted", data=_DELETED),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Draft not found", code="NOT_FOUND", message="order_draft with id '...' not found"),
    },
)

ORDERS_LIST = create_doc_entry(
    summary="List orders with pagination and optional filters",
    description="GET /v1/orders?organization_id=...&search=&status[]=&date_from=&date_to= (plus pagination).",
    responses={
        200: success_entry("Order list", data=_ORDER_LIST_PAGE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

ORDERS_GET = create_doc_entry(
    summary="Get full order detail by order id",
    description=(
        "GET /v1/orders/detail/{order_id}. Nests stops and packages. price_breakdown and order totals are only on the "
        "order; nested stops and packages omit per-line pricing snapshots (see response schema)."
    ),
    responses={
        200: success_entry("Order detail", data=_ORDER_DETAIL),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Order not found", code="NOT_FOUND", message="order with id '...' not found"),
    },
)

_ORDER_TIMELINE_EXAMPLE = {
    "order_id": "SWC-ORD-000123",
    "order_events": [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-000000000001",
            "created_at": _TS,
            "from_status": None,
            "to_status": "PENDING_PICKUP",
            "display_label": "Pending pickup",
            "actor_user_id": _CUST,
        }
    ],
    "delivery_stops": [
        {
            "delivery_stop_id": _STOP,
            "tracking_id": "SWC-STK-00000001",
            "events": [
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-000000000002",
                    "created_at": _TS,
                    "from_status": None,
                    "to_status": "PENDING_PICKUP",
                    "display_label": "Pending pickup",
                    "actor_user_id": _CUST,
                }
            ],
        }
    ],
    "packages": [
        {
            "package_id": _PKG,
            "package_reference": "SWC-PKG-00000001",
            "delivery_stop_id": _STOP,
            "events": [
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-000000000003",
                    "created_at": _TS,
                    "from_status": None,
                    "to_status": "PENDING_PICKUP",
                    "display_label": "Pending pickup",
                    "actor_user_id": _CUST,
                }
            ],
        }
    ],
}

ORDERS_TIMELINE = create_doc_entry(
    summary="Get status-change timeline for an order",
    description=(
        "GET /v1/orders/{order_id}/timeline. Append-only history: order-level status transitions, per-delivery-stop "
        "status transitions, and per-package status transitions (including driver scans). Duplicate to_status values "
        "are allowed; rows are ordered by created_at then id. display_label is a human-readable label for to_status."
    ),
    responses={
        200: success_entry("Order timeline", data=_ORDER_TIMELINE_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Order not found", code="NOT_FOUND", message="order with id '...' not found"),
    },
)

ORDER_DELIVERY_STOP_TIMELINE = create_doc_entry(
    summary="Get timeline for one delivery stop in an order",
    description=(
        "GET /v1/orders/{order_id}/timeline/delivery-stops/{stop_id}. Returns status transition history for the specific "
        "delivery stop only, ordered by created_at then id."
    ),
    responses={
        200: success_entry("Delivery stop timeline", data=_ORDER_TIMELINE_EXAMPLE["delivery_stops"][0]),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
    },
)

ORDER_PACKAGE_TIMELINE = create_doc_entry(
    summary="Get timeline for one package in an order",
    description=(
        "GET /v1/orders/{order_id}/timeline/packages/{package_id}. Returns status transition history for the specific "
        "package only, ordered by created_at then id."
    ),
    responses={
        200: success_entry("Package timeline", data=_ORDER_TIMELINE_EXAMPLE["packages"][0]),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Package not found", code="NOT_FOUND", message="package with id '...' not found"),
    },
)

ORDERS_GET_BY_MASTER_LABEL = create_doc_entry(
    summary="Get master and pickup label data for printing",
    description="GET /v1/orders/{order_id}/master-label for warehouse label printing (barcode/QR and lines).",
    responses={
        200: success_entry("Master and pickup labels", data=_MASTER_LABELS),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Order not found", code="NOT_FOUND", message="order with id '...' not found"),
    },
)

ORDER_STOPS_LIST = create_doc_entry(
    summary="List delivery stops belonging to an order",
    description="GET stops for the order (ordered as returned by the API).",
    responses={
        200: success_entry("Delivery stops", data=_STOPS_LIST),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Order not found", code="NOT_FOUND", message="order with id '...' not found"),
    },
)

ORDER_STOP_GET = create_doc_entry(
    summary="Get one delivery stop under an order",
    description="GET a single stop by order id and stop id.",
    responses={
        200: success_entry("Delivery stop", data=_STOP_WITH_PACKAGES),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
    },
)

ORDER_STOP_BY_TRACKING = create_doc_entry(
    summary="Resolve a delivery stop by public tracking id",
    description="Look up a stop for tracking UIs using the human-facing tracking id.",
    responses={
        200: success_entry("Delivery stop", data=_STOP_WITH_PACKAGES),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Tracking not found", code="NOT_FOUND", message="delivery_stop_tracking with id '...' not found"),
    },
)

ORDER_STOP_PACKAGES_LIST = create_doc_entry(
    summary="List packages on a specific delivery stop",
    description="GET packages for a stop, including optional price_breakdown per package on this read path.",
    responses={
        200: success_entry("Stop packages", data=_STOP_PACKAGES),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
    },
)

ORDER_STOP_NOTES_LIST = create_doc_entry(
    summary="List delivery notes for a stop (customer, package issue, admin)",
    description=(
        "Returns all operational notes for the stop in display order (`sort_order`). "
        "Matches the delivery notes UI: **CUSTOMER** (booking/customer instruction), "
        "**PACKAGE_ISSUE_NOTE** (optional `package_ids` and damage images), **ADMIN** (operations). "
        "`package_ids` are `packages.id` UUIDs; non-issue notes return an empty list."
    ),
    responses={
        200: success_entry("Stop notes", data=_STOP_NOTES),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
    },
)

_ORDER_STOP_NOTE_LONG_DESC = (
    "Multipart: `note_data` = JSON (`StopNoteCreateRequest` / `StopNoteUpdateRequest`), optional `images[]`. "
    "**Note types (persisted):** `ADMIN` (alias `ADMIN_NOTE`), `CUSTOMER` (aliases `CLIENT`, `CLIENT_NOTE`), "
    "`PACKAGE_ISSUE_NOTE`. Only package issue notes may send `package_ids` (array of `packages.id`). "
    "Customer and admin notes must omit `package_ids` or send null."
)

ORDER_STOP_NOTE_CREATE = create_doc_entry(
    summary="Create a delivery note (customer, package issue, or admin)",
    description=_ORDER_STOP_NOTE_LONG_DESC,
    responses={
        201: success_entry("Stop note created", data=_STOP_NOTE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="package_ids is only allowed when note_type is PACKAGE_ISSUE_NOTE",
        ),
    },
)

ORDER_STOP_NOTE_UPDATE = create_doc_entry(
    summary="Update a delivery note",
    description=(
        "Multipart PATCH. `package_ids`: omit to leave unchanged; include only when the note is (or is being set to) "
        "`PACKAGE_ISSUE_NOTE`. Changing type from package issue to admin/customer clears linkage server-side. "
        + _ORDER_STOP_NOTE_LONG_DESC
    ),
    responses={
        200: success_entry("Stop note updated", data=_STOP_NOTE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Note not found", code="NOT_FOUND", message="stop_note with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="package_ids is only allowed when note_type is PACKAGE_ISSUE_NOTE",
        ),
    },
)

ORDER_STOP_NOTE_DELETE = create_doc_entry(
    summary="Delete a stop note and its images",
    description="DELETE the note; storage is cleaned up server-side.",
    responses={
        200: success_entry("Stop note deleted", data=_DELETED),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Note not found", code="NOT_FOUND", message="stop_note with id '...' not found"),
    },
)

ORDERS_SUMMARY = create_doc_entry(
    summary="Dashboard order volume and status counts for a date range",
    description=(
        "Use `period` (TODAY, LAST_7_DAYS, LAST_WEEK, LAST_30_DAYS, LAST_MONTH) or custom `date_from`/`date_to`. "
        "Comparison window matches the preset (e.g. TODAY vs yesterday, LAST_MONTH vs the prior calendar month). "
        "Each KPI includes current, previous, change_pct, plus `comparison_label` for UI copy (vs yesterday, vs previous week, etc.)."
    ),
    responses={
        200: success_entry("Order summary", data=_ORDERS_SUMMARY_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

FAILED_DELIVERIES_SUMMARY = create_doc_entry(
    summary="Failed-delivery package counts by reason for a date range",
    description=(
        "Same `period` or `date_from`/`date_to` as order summary. Returns per-reason KPIs with current, previous, and change_pct."
    ),
    responses={
        200: success_entry("Failed deliveries summary", data=_FAILED_DELIVERIES_SUMMARY_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

FAILED_DELIVERIES_LIST = create_doc_entry(
    summary="List failed deliveries with packages grouped by stop",
    description=(
        "GET /v1/orders/failed-deliveries?organization_id=...: paginated stops with failed packages; "
        "supports search, filters, and dates. Each row includes `stop_status_events` and each package `status_events` "
        "(append-only status history: id, created_at, from_status, to_status, display_label, actor_user_id), "
        "aligned with the order timeline entity events."
    ),
    responses={
        200: success_entry("Failed deliveries list", data=_FAILED_DELIVERIES_LIST_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

RETURNS_SUMMARY = create_doc_entry(
    summary="Returns and disposal metrics for a date range",
    description=(
        "Same `period` or date range as other summaries. Integer metrics use OrderSummaryStat; "
        "`avg_resolution_days` uses current/previous floats and change_pct."
    ),
    responses={
        200: success_entry("Returns summary", data=_RETURNS_SUMMARY_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

STOP_RESCHEDULE = create_doc_entry(
    summary="Reschedule a whole failed stop for a new day",
    description="POST .../stops/{stop_id}/reschedule with { scheduled_for }. Eligible when packages can be rescheduled.",
    responses={
        200: success_entry("Stop rescheduled", data=_STOP_RESCHEDULE_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="No packages are eligible for reschedule on this stop.",
        ),
    },
)

PACKAGE_RESCHEDULE = create_doc_entry(
    summary="Reschedule a single package after a customer-not-home attempt",
    description="POST .../packages/{package_id}/reschedule with { scheduled_for }.",
    responses={
        200: success_entry("Package rescheduled", data=_PACKAGE_RESCHEDULE_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Package not found", code="NOT_FOUND", message="package with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Cannot reschedule a package with status 'DELIVERED_TO_CUSTOMER'",
        ),
    },
)

PACKAGE_INITIATE_RETURN = create_doc_entry(
    summary="Start a return for one package",
    description="POST .../packages/{package_id}/initiate-return from eligible package statuses.",
    responses={
        200: success_entry("Return initiated", data=_PACKAGE_INITIATE_RETURN_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Package not found", code="NOT_FOUND", message="package with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Cannot initiate return on a package with status 'DELIVERED_TO_CUSTOMER'",
        ),
    },
)

STOP_INITIATE_RETURN = create_doc_entry(
    summary="Start returns for every eligible package on a stop",
    description="POST .../stops/{stop_id}/initiate-return for all eligible packages on the stop.",
    responses={
        200: success_entry("Returns initiated", data=_STOP_INITIATE_RETURN_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="No packages on this stop are eligible for return.",
        ),
    },
)

STOP_MARK_AS_FOUND = create_doc_entry(
    summary="Mark all missing packages on a stop as found at the warehouse",
    description="POST .../stops/{stop_id}/mark-as-found for MISSING packages on that stop.",
    responses={
        200: success_entry("Packages marked as found", data=_STOP_MARK_FOUND_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="No missing packages on this stop to mark as found",
        ),
    },
)

PACKAGE_MARK_AS_FOUND = create_doc_entry(
    summary="Mark one missing package as found",
    description="POST .../packages/{package_id}/mark-as-found when status is MISSING.",
    responses={
        200: success_entry("Package marked as found", data=_PACKAGE_MARK_FOUND_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Package not found", code="NOT_FOUND", message="package with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Only missing packages can be marked as found",
        ),
    },
)

STOP_RESOLVE_RETURN = create_doc_entry(
    summary="Resolve a return: ship back to sender or dispose with evidence",
    description="POST multipart .../resolve-return with resolution_data JSON; evidence images for disposal.",
    responses={
        200: success_entry("Return resolved", data=_STOP_RESOLVE_RETURN_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Stop not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="No packages on this stop are awaiting return resolution.",
        ),
    },
)

_ORDER_CANCEL_DATA = {
    "id": _ORDER,
    "order_id": "SWC-ORD-000123",
    "status": "CANCELLED",
}

ORDER_CANCEL = create_doc_entry(
    summary="Cancel the entire order",
    description=(
        "POST .../orders/{order_id}/cancel with optional JSON body `{ \"notes\": \"...\" }`. "
        "Query `organization_id` is required for admin. Sets every non-cancelled package to CANCELLED, each delivery "
        "stop to CANCELLED, and the order to CANCELLED. Fails if any package is already delivered, in return, or "
        "otherwise in a final state that cannot be voided (see validation errors)."
    ),
    responses={
        200: success_entry("Order cancelled", data=_ORDER_CANCEL_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="order with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="This order is already cancelled"),
    },
)

_STOP_CANCEL_DATA = {
    "order_id": "SWC-ORD-000123",
    "delivery_stop_id": _STOP,
    "tracking_id": "SWBHM-984523",
    "stop_status": "CANCELLED",
    "order_status": "DELIVERY_IN_PROGRESS",
    "affected_package_ids": [_PKG],
}

STOP_CANCEL = create_doc_entry(
    summary="Cancel one delivery stop (all of its packages)",
    description=(
        "POST .../orders/{order_id}/stops/{stop_id}/cancel with optional notes body. "
        "Cancels only packages on that stop; other stops on the same order are unchanged. "
        "Same `organization_id` query rule as order cancel. Idempotent if the stop is already fully cancelled."
    ),
    responses={
        200: success_entry("Delivery stop cancelled", data=_STOP_CANCEL_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="order or stop not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Cannot cancel a package in this state"),
    },
)

_PACKAGE_RESOLVE_RETURN_DATA = {
    **_STOP_RESOLVE_RETURN_DATA,
    "affected_package_ids": [_PKG],
}

PACKAGE_RESOLVE_RETURN = create_doc_entry(
    summary="Resolve a return for one package on a stop",
    description=(
        "POST multipart .../packages/{package_id}/resolve-return with the same resolution_data JSON and optional "
        "evidence images as the stop resolve endpoint. Use when a stop has multiple packages and each is resolved "
        "separately. Stop-level return metadata and return_resolved_at are set when the last RETURN_INITIATED package "
        "on that stop is resolved; earlier responses set return_resolved_at to null and echo dispatch/disposal from "
        "the request without persisting them on the stop until the stop is fully resolved."
    ),
    responses={
        200: success_entry("Return resolved", data=_PACKAGE_RESOLVE_RETURN_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Package not found", code="NOT_FOUND", message="package with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="This package is not awaiting return resolution.",
        ),
    },
)

RETURNS_LIST = create_doc_entry(
    summary="List in-progress and completed return packages by stop",
    description=(
        "GET /v1/orders/returns?organization_id=...: paginated; filters for search, status[], date range on initiation. "
        "Each row includes `stop_status_events` and each package `status_events` (same structure as failed-deliveries list)."
    ),
    responses={
        200: success_entry("Returns list", data=_RETURNS_LIST_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
    },
)

STOP_PACKAGES_UPDATE = create_doc_entry(
    summary="Update pre-pickup package dimensions/weight and reprice the order",
    description=(
        "PATCH .../stops/{stop_id}/packages for PENDING_PICKUP only. Reprices and returns stop and order "
        "breakdown fields (unlike get order detail, which only surfaces order-level pricing)."
    ),
    responses={
        200: success_entry("Packages updated", data=_STOP_PACKAGES_UPDATE_DATA),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="delivery_stop with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Packages can only be edited while the order is in PENDING_PICKUP",
        ),
    },
)
