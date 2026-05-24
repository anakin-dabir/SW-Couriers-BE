from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.functional_validators import BeforeValidator

if TYPE_CHECKING:
    from starlette.requests import Request

# Currency: always Decimal, 2 decimal places. Safe to compare and use in arithmetic with other Decimals.
# Performance: single quantize per value; security: no float; scalability: Decimal everywhere in domain.
_TWO_PLACES = Decimal("0.01")


def _quantize_currency(v: Any) -> Decimal:
    """Normalize input to Decimal quantized to 2 places. Return type is Decimal (comparison-safe)."""
    d = Decimal(v) if not isinstance(v, Decimal) else v
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def quantize_currency(value: Decimal | int | float | str) -> Decimal:
    """Round a currency amount to 2 decimal places (ROUND_HALF_UP). Returns Decimal — safe to compare and use in arithmetic with other Decimals."""
    d = Decimal(value) if not isinstance(value, Decimal) else value
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


# Pydantic: validates and coerces to Decimal(2dp). At runtime field values are plain Decimal — use anywhere Decimal is expected.
CurrencyAmount = Annotated[Decimal, BeforeValidator(_quantize_currency)]


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class TimestampSchema(BaseModel):
    created_at: datetime
    updated_at: datetime


class IDSchema(BaseModel):
    id: str


class UserSchema(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    id: str
    first_name: str | None = None
    last_name: str | None = None


class BaseResponseSchema(IDSchema, TimestampSchema, BaseSchema):
    version: int


class PaginationParams(BaseSchema):
    page: int = Field(default=1, ge=1, description="Page number (1-based)")
    size: int = Field(default=20, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class PaginatedResponse[T](BaseSchema):
    model_config = ConfigDict(extra="forbid")

    items: list[T]
    total: int
    page: int
    size: int
    pages: int
    current_url: str | None = Field(default=None, description="Full URL of the current page including all query parameters.")
    next_url: str | None = Field(default=None, description="URL of the next page. Null when on the last page.")

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        size: int,
        request: "Request | None" = None,
    ) -> Self:
        pages = (total + size - 1) // size if size > 0 else 0
        current_url: str | None = None
        next_url: str | None = None
        if request is not None:
            current_url = str(request.url)
            if page < pages:
                next_url = str(request.url.include_query_params(page=page + 1, size=size))
        return cls(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages,
            current_url=current_url,
            next_url=next_url,
        )


# API response envelope (success/error) — used as response_model= and in OpenAPI


class SuccessResponse[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    message: str | None = None
    data: T | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class MessageResponse(BaseModel):
    success: Literal[True] = True
    message: str


class TokenData(BaseSchema):
    """Token payload embedded in auth responses.

    ``access_token`` is always in the response body.
    ``refresh_token`` is included only for driver clients; web clients
    receive it via an HttpOnly cookie instead.
    """

    access_token: str
    access_token_expires_in: int
    refresh_token: str | None = None
    refresh_token_expires_in: int | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class AuthResponse[T](BaseModel):
    """Response envelope that carries tokens alongside optional data.

    Login   → AuthResponse[UserBrief]  (data = user, tokens)
    Refresh → AuthResponse[None]              (tokens only)
    """

    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    message: str | None = None
    data: T | None = None
    tokens: TokenData

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    details: list[dict[str, Any]] | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    message: str
    error: ErrorBody
