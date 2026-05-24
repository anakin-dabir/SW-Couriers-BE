"""Dashboard KPI domain constants."""

from __future__ import annotations

from app.modules.orders.enums import DeliveryStopStatus, OrderStatus

TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
        OrderStatus.RETURNED,
    }
)

DELIVERED_STOP_EVENT_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStopStatus.DELIVERED.value,
        DeliveryStopStatus.PARTIALLY_DELIVERED.value,
    }
)

FAILED_STOP_EVENT_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED.value,
        DeliveryStopStatus.FAILED.value,
    }
)

# Stops surfaced on the highlighted-issues board (non-terminal operational states).
HIGHLIGHTED_STOP_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStopStatus.OUT_FOR_DELIVERY.value,
        DeliveryStopStatus.LOADED_FOR_DELIVERY.value,
        DeliveryStopStatus.DELIVERY_SCHEDULED.value,
        DeliveryStopStatus.ENROUTE_PICKUP.value,
        DeliveryStopStatus.PICKUP_SCHEDULED.value,
        DeliveryStopStatus.ENROUTE_WAREHOUSE.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED.value,
        DeliveryStopStatus.RETURN_IN_TRANSIT.value,
        DeliveryStopStatus.RETURN_INITIATED.value,
    }
)

HIGHLIGHTED_ORDER_STATUSES: frozenset[str] = frozenset(
    {
        OrderStatus.AT_WAREHOUSE.value,
        OrderStatus.SORTING_IN_PROGRESS.value,
        OrderStatus.PICKUP_SCHEDULED.value,
        OrderStatus.ENROUTE_PICKUP.value,
    }
)

FAILED_ATTEMPT_STOP_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value,
        DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED.value,
    }
)
