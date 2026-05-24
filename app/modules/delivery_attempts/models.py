"""Delivery Attempts ORM model — global singleton for delivery & return attempt charges."""

from sqlalchemy import Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class DeliveryAttemptConfig(BaseModel):
    """Global admin-managed config for delivery and return attempt fees.

    Singleton table — exactly one row is maintained. Use the service's
    get_or_create() to read it and upsert() to update it.

    delivery_attempt_fees / return_attempt_fees are JSONB arrays:
        [{"attempt": 1, "fee": "10.00"}, {"attempt": 2, "fee": "15.00"}, ...]
    Array length must equal max_delivery_attempts / max_return_attempts
    respectively; this is enforced at the service layer.
    """

    __tablename__ = "delivery_attempt_configs"

    # ── Delivery reattempt charges ────────────────────────────────────────────
    max_delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # [{"attempt": 1, "fee": "10.00"}, ...]
    delivery_attempt_fees: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Return reattempt charges ──────────────────────────────────────────────
    max_return_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # [{"attempt": 1, "fee": "10.00"}, ...]
    return_attempt_fees: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DeliveryAttemptConfig "
            f"delivery={self.max_delivery_attempts} return={self.max_return_attempts}>"
        )
