from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.common.schemas import BaseSchema, BaseResponseSchema


class AttemptFeeEntry(BaseSchema):
    """Single attempt number and its fee charge."""

    attempt: int = Field(..., ge=1, description="Attempt number (1-based)")
    fee: Decimal = Field(..., ge=0, decimal_places=2, description="Fee in GBP")


class DeliveryAttemptConfigUpdate(BaseSchema):
    """Body for PUT /delivery-attempts — replaces the global config."""

    max_delivery_attempts: int | None = Field(None, ge=1, description="Derived from delivery_attempt_fees when omitted")
    delivery_attempt_fees: list[AttemptFeeEntry] = Field(
        ..., description="Fee per delivery attempt — length must match max_delivery_attempts"
    )
    max_return_attempts: int | None = Field(None, ge=1, description="Derived from return_attempt_fees when omitted")
    return_attempt_fees: list[AttemptFeeEntry] = Field(
        ..., description="Fee per return attempt — length must match max_return_attempts"
    )
    version: int | None = Field(
        None,
        ge=1,
        description="Optional optimistic lock version. If supplied and stale, update is rejected.",
    )

    @model_validator(mode="after")
    def derive_and_validate_fee_lengths(self) -> "DeliveryAttemptConfigUpdate":
        if self.max_delivery_attempts is None:
            self.max_delivery_attempts = len(self.delivery_attempt_fees)
        if self.max_return_attempts is None:
            self.max_return_attempts = len(self.return_attempt_fees)

        if len(self.delivery_attempt_fees) != self.max_delivery_attempts:
            raise ValueError(
                f"delivery_attempt_fees must have exactly {self.max_delivery_attempts} entries "
                f"(got {len(self.delivery_attempt_fees)})"
            )
        if len(self.return_attempt_fees) != self.max_return_attempts:
            raise ValueError(
                f"return_attempt_fees must have exactly {self.max_return_attempts} entries "
                f"(got {len(self.return_attempt_fees)})"
            )
        return self

    @model_validator(mode="after")
    def validate_attempt_numbers_sequential(self) -> "DeliveryAttemptConfigUpdate":
        for i, entry in enumerate(self.delivery_attempt_fees, start=1):
            if entry.attempt != i:
                raise ValueError(
                    f"delivery_attempt_fees[{i-1}].attempt must be {i}, got {entry.attempt}"
                )
        for i, entry in enumerate(self.return_attempt_fees, start=1):
            if entry.attempt != i:
                raise ValueError(
                    f"return_attempt_fees[{i-1}].attempt must be {i}, got {entry.attempt}"
                )
        return self


class DeliveryAttemptConfigResponse(BaseResponseSchema):
    max_delivery_attempts: int
    delivery_attempt_fees: list[AttemptFeeEntry] | None
    max_return_attempts: int
    return_attempt_fees: list[AttemptFeeEntry] | None


class DeliveryAttemptConfigPatch(BaseSchema):
    """Body for PATCH /delivery-attempts — partial global config updates."""

    max_delivery_attempts: int | None = Field(None, ge=1)
    delivery_attempt_fees: list[AttemptFeeEntry] | None = None
    max_return_attempts: int | None = Field(None, ge=1)
    return_attempt_fees: list[AttemptFeeEntry] | None = None
    version: int | None = Field(
        None,
        ge=1,
        description="Optional optimistic lock version. If supplied and stale, update is rejected.",
    )

    @model_validator(mode="after")
    def validate_not_empty(self) -> "DeliveryAttemptConfigPatch":
        if (
            self.max_delivery_attempts is None
            and self.delivery_attempt_fees is None
            and self.max_return_attempts is None
            and self.return_attempt_fees is None
        ):
            raise ValueError("At least one field must be provided.")
        return self
