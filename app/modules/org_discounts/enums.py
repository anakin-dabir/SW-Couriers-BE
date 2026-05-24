"""Enums for org discount configuration."""

import enum


class DiscountType(enum.StrEnum):
    PERCENTAGE = "PERCENTAGE"
    FIXED_PER_BOOKING = "FIXED_PER_BOOKING"
    VOLUME_TIERED = "VOLUME_TIERED"
