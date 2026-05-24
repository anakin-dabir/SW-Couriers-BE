from __future__ import annotations

from typing import Self

from pydantic import Field, RootModel, field_validator, model_validator

from app.common.schemas import BaseSchema, IDSchema, TimestampSchema


class PickupAddressCreate(BaseSchema):
    same_as_registered_address: bool = False
    same_as_trading_address: bool = False
    label: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=50, description="Phone for the pickup site contact")
    line_1: str | None = Field(default=None, min_length=1, max_length=255)
    line_2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, min_length=2, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="From map pin; send together with longitude",
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="From map pin; send together with latitude",
    )
    is_default: bool = False

    @field_validator("postcode")
    @classmethod
    def strip_postcode(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()

    @model_validator(mode="after")
    def flags_and_manual_address(self) -> Self:
        if self.same_as_registered_address and self.same_as_trading_address:
            raise ValueError("Cannot set both same_as_registered_address and same_as_trading_address")
        if self.same_as_registered_address or self.same_as_trading_address:
            if self.latitude is None and self.longitude is None:
                return self
            if self.latitude is None or self.longitude is None:
                raise ValueError("latitude and longitude must both be set when sending map coordinates")
            return self
        for name, val, label in (
            ("line_1", self.line_1, "line_1"),
            ("city", self.city, "city"),
            ("postcode", self.postcode, "postcode"),
            ("country", self.country, "country"),
            ("state", self.state, "state"),
        ):
            if val is None or (isinstance(val, str) and not val.strip()):
                raise ValueError(
                    f"{label} is required when not using same as organisation registered or trading address",
                )
        if self.latitude is None and self.longitude is None:
            return self
        if self.latitude is None or self.longitude is None:
            raise ValueError("latitude and longitude must both be set when sending map coordinates")
        return self


class CreatePickupAddressesRequest(RootModel[list[PickupAddressCreate]]):
    @model_validator(mode="after")
    def at_least_one(self) -> Self:
        if len(self.root) < 1:
            raise ValueError("At least one pickup address is required")
        return self


class PickupAddressUpdate(BaseSchema):
    label: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=50)
    line_1: str | None = Field(default=None, min_length=1, max_length=255)
    line_2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, min_length=2, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="From map pin; send together with longitude",
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="From map pin; send together with latitude",
    )
    is_default: bool | None = None

    @field_validator("postcode")
    @classmethod
    def strip_postcode(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()

    @model_validator(mode="after")
    def lat_lng_both_or_neither(self) -> Self:
        if self.latitude is None and self.longitude is None:
            return self
        if self.latitude is None or self.longitude is None:
            raise ValueError("latitude and longitude must both be set when sending map coordinates")
        return self

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class GeocodeAddressRequest(BaseSchema):
    query: str | None = Field(default=None, max_length=500, description="Full address in one string (preferred if you have it)")
    line_1: str | None = Field(default=None, max_length=255)
    line_2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def query_or_structured(self) -> Self:
        if self.query and self.query.strip():
            return self
        if self.line_1 and self.city and self.postcode:
            return self
        raise ValueError("Provide either `query` or at least `line_1`, `city`, and `postcode`")


class GeocodeResultResponse(BaseSchema):
    latitude: float
    longitude: float
    formatted_address: str
    place_id: str | None = None
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None


class PickupAddressResponse(IDSchema, TimestampSchema, BaseSchema):
    organization_id: str | None = None
    user_id: str | None = None
    label: str | None = None
    contact_phone: str | None = None
    line_1: str
    line_2: str | None = None
    city: str
    state: str | None = None
    postcode: str
    country: str
    latitude: float | None = None
    longitude: float | None = None
    is_default: bool
    created_by_user_id: str | None = None
