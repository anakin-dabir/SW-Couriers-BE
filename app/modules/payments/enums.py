"""Payment method enums."""

import enum


class PaymentMethodStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"


class CardType(enum.StrEnum):
    VISA = "VISA"
    MASTERCARD = "MASTERCARD"
    AMEX = "AMEX"
    DISCOVER = "DISCOVER"
    UNKNOWN = "UNKNOWN"


class BookingPaymentStatus(enum.StrEnum):
    """Values for `bookings.payment_status` when paying by card."""

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
