"""Warehouse zone enums."""

import enum


class WarehouseZoneType(enum.StrEnum):
    INBOUND = "INBOUND"
    SORTING = "SORTING"
    OUTBOUND = "OUTBOUND"
    STAGING = "STAGING"
