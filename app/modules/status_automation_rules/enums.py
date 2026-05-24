"""Enums for status automation rule configuration."""

from __future__ import annotations

import enum


class StatusAutomationRuleStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class StatusAutomationScopeType(enum.StrEnum):
    GLOBAL = "GLOBAL"
    ORG = "ORG"


class EntityType(enum.StrEnum):
    PACKAGE = "PACKAGE"
    DELIVERY_STOP = "DELIVERY_STOP"
    BOOKING_ORDER = "BOOKING_ORDER"


class TimingValue(enum.StrEnum):
    AFTER_PICKUP = "AFTER_PICKUP"

