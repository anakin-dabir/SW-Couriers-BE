"""Planning v1 routes — root prefix ``/routes``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.planning.service import PlanningService
from app.modules.planning.v1.docs import ROUTE_MAP
from app.modules.planning.v1.schemas import RouteMapResponse

router = APIRouter()

PlanningServiceDep = Annotated[PlanningService, Depends(PlanningService.dep)]
RouteReaderDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.DRIVER,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.WAREHOUSE_STAFF,
        resource=Resource.ROUTE_PLANNING,
        level=PermissionLevel.READ,
    ),
]


@router.get(
    "/{route_id}/map",
    response_model=SuccessResponse[RouteMapResponse],
    **ROUTE_MAP,
)
async def get_route_map(
    route_id: str,
    user: RouteReaderDep,
    planning_service: PlanningServiceDep,
) -> dict:
    payload = await planning_service.get_route_map(
        route_id=route_id,
        viewer_user_id=user.id,
        viewer_role=user.role,
    )
    return ok(data=payload)
