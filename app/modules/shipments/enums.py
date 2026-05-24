"""Delivery preference and outcome enums (shipment delivery flow)."""

import enum


class DeliveryPreference(enum.StrEnum):
    SIGNATURE = "SIGNATURE"
    SAFE_PLACE = "SAFE_PLACE"
    HAND_TO_RECIPIENT = "HAND_TO_RECIPIENT"


class DeliveryOutcome(enum.StrEnum):
    DELIVERED_TO_CUSTOMER = "DELIVERED_TO_CUSTOMER"
    LEFT_AT_SAFE_PLACE = "LEFT_AT_SAFE_PLACE"
    CUSTOMER_NOT_HOME = "CUSTOMER_NOT_HOME"
    REFUSED_BY_CUSTOMER = "REFUSED_BY_CUSTOMER"
