from __future__ import annotations

_ORDER_STATUS_LABELS: dict[str, str] = {
    "PENDING_PICKUP": "Pending pickup",
    "PICKUP_SCHEDULED": "Pickup scheduled",
    "ENROUTE_PICKUP": "Pickup on route",
    "ENROUTE_WAREHOUSE": "In transit to warehouse",
    "AT_WAREHOUSE": "At warehouse",
    "SORTING_IN_PROGRESS": "Sorting in progress",
    "DELIVERY_IN_PROGRESS": "Delivery in progress",
    "PARTIALLY_DELIVERED": "Partially delivered",
    "DELIVERED": "Delivered",
    "FAILED": "Failed",
    "RETURN_IN_PROGRESS": "Return in progress",
    "RETURN_IN_TRANSIT": "Return in transit",
    "RETURNED": "Returned",
    "CANCELLED": "Cancelled",
}

_DELIVERY_STOP_STATUS_LABELS: dict[str, str] = {
    "PENDING_PICKUP": "Pending pickup",
    "PICKUP_SCHEDULED": "Pickup scheduled",
    "ENROUTE_PICKUP": "Pickup on route",
    "ENROUTE_WAREHOUSE": "In transit to warehouse",
    "AT_WAREHOUSE": "At warehouse",
    "SORTING_IN_PROGRESS": "Sorting in progress",
    "DELIVERY_SCHEDULED": "Delivery scheduled",
    "LOADED_FOR_DELIVERY": "Loaded for delivery",
    "OUT_FOR_DELIVERY": "Out for delivery",
    "DELIVERED": "Delivered successfully",
    "PARTIALLY_DELIVERED": "Partially delivered",
    "DELIVERY_ATTEMPT_1_FAILED": "Delivery attempt 1 failed",
    "DELIVERY_ATTEMPT_2_FAILED": "Delivery attempt 2 failed",
    "DELIVERY_ATTEMPT_3_FAILED": "Delivery attempt 3 failed",
    "MIXED": "Mixed",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
    "RETURN_INITIATED": "Return initiated",
    "RETURN_IN_TRANSIT": "Return in transit",
    "RETURNED": "Returned",
    "DISPOSED": "Disposed",
}

_PACKAGE_STATUS_LABELS: dict[str, str] = {
    "PENDING_PICKUP": "Pending pickup",
    "PICKUP_SCHEDULED": "Pickup scheduled",
    "ENROUTE_PICKUP": "Pickup on route",
    "ENROUTE_WAREHOUSE": "In transit to warehouse",
    "AT_WAREHOUSE": "At warehouse",
    "SORTING_IN_PROGRESS": "Sorting in progress",
    "DELIVERY_SCHEDULED": "Delivery scheduled",
    "LOADED_FOR_DELIVERY": "Loaded for delivery",
    "OUT_FOR_DELIVERY": "Out for delivery",
    "DELIVERED_TO_CUSTOMER": "Delivered to customer",
    "CUSTOMER_NOT_HOME": "Customer not home",
    "REFUSED_BY_CUSTOMER": "Refused by customer",
    "MISSING": "Missing",
    "DAMAGED": "Damaged",
    "LEFT_AT_SAFE_PLACE": "Left at safe place",
    "RETURN_INITIATED": "Return initiated",
    "RETURN_IN_TRANSIT": "Return in transit",
    "RETURNED": "Returned",
    "CANCELLED": "Cancelled",
    "DISPOSED": "Disposed",
}


def order_status_display(value: str | None) -> str:
    if not value:
        return ""
    return _ORDER_STATUS_LABELS.get(value, value.replace("_", " ").title())


def delivery_stop_status_display(value: str | None) -> str:
    if not value:
        return ""
    return _DELIVERY_STOP_STATUS_LABELS.get(value, value.replace("_", " ").title())


def package_status_display(value: str | None) -> str:
    if not value:
        return ""
    return _PACKAGE_STATUS_LABELS.get(value, value.replace("_", " ").title())
