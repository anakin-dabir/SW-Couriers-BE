"""Pydantic schemas for Crew v1 API."""

from __future__ import annotations

from pydantic import Field

from app.common.schemas import BaseSchema, PaginatedResponse, PaginationParams


class EligibleDriverParams(PaginationParams):
    """Query params for ``GET /v1/crews/eligible-drivers``."""

    search: str | None = Field(
        default=None,
        description="Partial match on first name, last name, or email (case-insensitive).",
    )


class EligibleRouteParams(PaginationParams):
    """Query params for ``GET /v1/crews/eligible-routes``."""

    search: str | None = Field(
        default=None,
        description="Partial match on route_code (case-insensitive).",
    )


class EligibleDriverItem(BaseSchema):
    """A driver-role user that is ACTIVE and has no currently open crew."""

    id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class EligibleRouteItem(BaseSchema):
    """A non-completed route with no currently open crew assignment."""

    id: str
    route_code: str
    status: str = Field(description="Route status (DRAFT / ASSIGNED / ACTIVE).")
    route_type: str | None = None
    plan_id: str | None = None


class EligibleDriverListResponse(PaginatedResponse[EligibleDriverItem]):
    """Paginated list of drivers eligible for a new crew."""


class EligibleRouteListResponse(PaginatedResponse[EligibleRouteItem]):
    """Paginated list of routes eligible for a crew assignment."""
