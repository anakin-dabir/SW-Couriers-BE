"""Crew admin API (v1).

Eligibility lookups used by the dashboard when opening a crew or assigning a
crew to a route. Both endpoints are paginated and support a free-text ``search``
parameter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.crew.service import CrewService
from app.modules.crew.v1.docs import CREWS_ELIGIBLE_DRIVERS, CREWS_ELIGIBLE_ROUTES
from app.modules.crew.v1.schemas import (
    EligibleDriverItem,
    EligibleDriverListResponse,
    EligibleDriverParams,
    EligibleRouteItem,
    EligibleRouteListResponse,
    EligibleRouteParams,
)

router = APIRouter()

CrewServiceDep = Annotated[CrewService, Depends(CrewService.dep)]

DriversReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.DRIVERS, level=PermissionLevel.READ),
]
RoutePlanningReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ROUTE_PLANNING, level=PermissionLevel.READ),
]


@router.get(
    "/eligible-drivers",
    response_model=SuccessResponse[EligibleDriverListResponse],
    **CREWS_ELIGIBLE_DRIVERS,
)
async def list_eligible_drivers(
    request: Request,
    _caller: DriversReadDep,
    svc: CrewServiceDep,
    params: Annotated[EligibleDriverParams, Query()],
) -> dict:
    drivers, total = await svc.list_active_drivers_without_crew(
        page=params.page,
        size=params.size,
        search=params.search,
    )
    items = [
        EligibleDriverItem(
            id=d.id,
            first_name=getattr(d, "first_name", None),
            last_name=getattr(d, "last_name", None),
            email=getattr(d, "email", None),
        )
        for d in drivers
    ]
    return ok(
        EligibleDriverListResponse.create(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            request=request,
        )
    )


@router.get(
    "/eligible-routes",
    response_model=SuccessResponse[EligibleRouteListResponse],
    **CREWS_ELIGIBLE_ROUTES,
)
async def list_eligible_routes(
    request: Request,
    _caller: RoutePlanningReadDep,
    svc: CrewServiceDep,
    params: Annotated[EligibleRouteParams, Query()],
) -> dict:
    routes, total = await svc.list_assignable_routes(
        page=params.page,
        size=params.size,
        search=params.search,
    )
    items = [
        EligibleRouteItem(
            id=r.id,
            route_code=r.route_code,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            route_type=r.route_type.value if hasattr(r.route_type, "value") else (str(r.route_type) if r.route_type else None),
            plan_id=r.plan_id,
        )
        for r in routes
    ]
    return ok(
        EligibleRouteListResponse.create(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            request=request,
        )
    )
