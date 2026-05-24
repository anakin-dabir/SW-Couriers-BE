"""Team availability admin API (v1)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.common.deps import Allowed, AuthUser
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, limiter
from app.modules.drivers.enums import TimeOffType
from app.modules.team_availability.enums import TeamMemberType
from app.modules.team_availability.service import TeamAvailabilityService
from app.modules.team_availability.v1.docs import (
    TEAM_AVAILABILITY_CALENDAR,
    TEAM_AVAILABILITY_LEAVE_DETAIL,
    TEAM_AVAILABILITY_LEAVE_TYPES,
    TEAM_AVAILABILITY_MY_LEAVE_CREATE,
    TEAM_AVAILABILITY_MY_LEAVE_DELETE,
    TEAM_AVAILABILITY_MY_LEAVE_DETAIL,
    TEAM_AVAILABILITY_MY_LEAVE_LIST,
    TEAM_AVAILABILITY_MY_LEAVE_UPDATE,
    TEAM_AVAILABILITY_WHO_IS_OFF,
)
from app.modules.team_availability.v1.schemas import (
    LeaveTypeListResponse,
    LeaveTypeOption,
    MyLeaveCreateRequest,
    MyLeaveItem,
    MyLeaveListResponse,
    MyLeaveUpdateRequest,
    TeamCalendarResponse,
    TeamLeaveDetailResponse,
    WhoIsOffResponse,
)

router = APIRouter()

TeamAvailabilityServiceDep = Annotated[TeamAvailabilityService, Depends(TeamAvailabilityService.dep)]
TeamAvailabilityReadDep = Annotated[
    AuthUser,
    Allowed(resource=Resource.DRIVERS, level=PermissionLevel.READ),
]
TeamAvailabilitySettingsReadDep = Annotated[
    AuthUser,
    Allowed(resource=Resource.SETTINGS, level=PermissionLevel.READ),
]
TeamAvailabilitySettingsWriteDep = Annotated[
    AuthUser,
    Allowed(resource=Resource.SETTINGS, level=PermissionLevel.WRITE),
]


@router.get(
    "/leave-types",
    response_model=SuccessResponse[LeaveTypeListResponse],
    **TEAM_AVAILABILITY_LEAVE_TYPES,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_leave_types(
    request: Request,
    response: Response,
    service: TeamAvailabilityServiceDep,
    _user: TeamAvailabilityReadDep,
) -> dict:
    items = await service.list_leave_types()
    return ok(data=LeaveTypeListResponse(items=[LeaveTypeOption(**row) for row in items]))


@router.get(
    "/calendar",
    response_model=SuccessResponse[TeamCalendarResponse],
    **TEAM_AVAILABILITY_CALENDAR,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_team_calendar(
    request: Request,
    response: Response,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilityReadDep,
    from_date: Annotated[date, Query(description="Inclusive range start (ISO date)")],
    to_date: Annotated[date, Query(description="Inclusive range end (ISO date)")],
    time_off_type: Annotated[
        list[TimeOffType] | None,
        Query(description="Leave type filter (multi-select). Omit for all types."),
    ] = None,
    depot_id: Annotated[str | None, Query(description="Optional depot filter")] = None,
    driver_id: Annotated[
        list[str] | None,
        Query(description="Optional driver id filter (multi-select)"),
    ] = None,
    include_holidays: Annotated[
        bool,
        Query(description="Include company public holidays on the calendar"),
    ] = True,
    only_my_leaves: Annotated[
        bool,
        Query(
            description="When true, only leave for the authenticated user (driver and/or staff)."
        ),
    ] = False,
) -> dict:
    payload = await service.get_team_calendar(
        from_date=from_date,
        to_date=to_date,
        time_off_type=[t.value for t in (time_off_type or [])] or None,
        depot_id=depot_id,
        driver_ids=driver_id,
        include_holidays=include_holidays,
        only_my_leaves=only_my_leaves,
        current_user_id=user.id,
    )
    return ok(data=TeamCalendarResponse(**payload))


@router.get(
    "/who-is-off",
    response_model=SuccessResponse[WhoIsOffResponse],
    **TEAM_AVAILABILITY_WHO_IS_OFF,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_who_is_off(
    request: Request,
    response: Response,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilityReadDep,
    from_date: Annotated[date, Query(description="Inclusive range start")],
    to_date: Annotated[date, Query(description="Inclusive range end")],
    time_off_type: Annotated[
        list[TimeOffType] | None,
        Query(description="Leave type filter (multi-select)"),
    ] = None,
    depot_id: Annotated[str | None, Query(description="Optional depot filter")] = None,
    driver_id: Annotated[
        list[str] | None,
        Query(description="Optional driver id filter (multi-select)"),
    ] = None,
    only_my_leaves: Annotated[
        bool,
        Query(description="Restrict to authenticated user's leave (driver and/or staff)"),
    ] = False,
) -> dict:
    payload = await service.list_who_is_off(
        from_date=from_date,
        to_date=to_date,
        time_off_type=[t.value for t in (time_off_type or [])] or None,
        depot_id=depot_id,
        driver_ids=driver_id,
        only_my_leaves=only_my_leaves,
        current_user_id=user.id,
    )
    return ok(data=WhoIsOffResponse(**payload))


@router.get(
    "/time-off/{time_off_id}",
    response_model=SuccessResponse[TeamLeaveDetailResponse],
    **TEAM_AVAILABILITY_LEAVE_DETAIL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_leave_detail(
    request: Request,
    response: Response,
    time_off_id: str,
    service: TeamAvailabilityServiceDep,
    _user: TeamAvailabilityReadDep,
    member_type: Annotated[
        TeamMemberType,
        Query(description="DRIVER for fleet leave; STAFF for admin My Leaves"),
    ] = TeamMemberType.DRIVER,
) -> dict:
    payload = await service.get_leave_detail(time_off_id=time_off_id, member_type=member_type.value)
    return ok(data=TeamLeaveDetailResponse(**payload))


# ── My Leaves (admin / super-admin staff time off) ───────────────────────────


@router.get(
    "/my-leaves",
    response_model=SuccessResponse[MyLeaveListResponse],
    **TEAM_AVAILABILITY_MY_LEAVE_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_leaves(
    request: Request,
    response: Response,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilitySettingsReadDep,
) -> dict:
    payload = await service.list_my_leaves(user_id=user.id, role=user.role)
    return ok(
        data=MyLeaveListResponse(
            items=[MyLeaveItem(**row) for row in payload["items"]],
            paid_leave_taken=payload["paid_leave_taken"],
            unpaid_leave_taken=payload["unpaid_leave_taken"],
            total=payload["total"],
        )
    )


@router.post(
    "/my-leaves",
    response_model=SuccessResponse[MyLeaveItem],
    status_code=201,
    **TEAM_AVAILABILITY_MY_LEAVE_CREATE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def create_my_leave(
    request: Request,
    response: Response,
    body: MyLeaveCreateRequest,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilitySettingsWriteDep,
) -> dict:
    payload = await service.create_my_leave(
        user_id=user.id,
        role=user.role,
        start_date=body.start_date,
        end_date=body.end_date,
        leave_type=body.type.value,
        is_paid=body.is_paid,
        notes=body.notes,
    )
    return ok(data=MyLeaveItem(**payload))


@router.get(
    "/my-leaves/{time_off_id}",
    response_model=SuccessResponse[MyLeaveItem],
    **TEAM_AVAILABILITY_MY_LEAVE_DETAIL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_leave(
    request: Request,
    response: Response,
    time_off_id: str,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilitySettingsReadDep,
) -> dict:
    payload = await service.get_my_leave(user_id=user.id, role=user.role, time_off_id=time_off_id)
    return ok(data=MyLeaveItem(**payload))


@router.patch(
    "/my-leaves/{time_off_id}",
    response_model=SuccessResponse[MyLeaveItem],
    **TEAM_AVAILABILITY_MY_LEAVE_UPDATE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def update_my_leave(
    request: Request,
    response: Response,
    time_off_id: str,
    body: MyLeaveUpdateRequest,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilitySettingsWriteDep,
) -> dict:
    payload = await service.update_my_leave(
        user_id=user.id,
        role=user.role,
        time_off_id=time_off_id,
        start_date=body.start_date,
        end_date=body.end_date,
        leave_type=body.type.value if body.type is not None else None,
        is_paid=body.is_paid,
        notes=body.notes,
    )
    return ok(data=MyLeaveItem(**payload))


@router.delete(
    "/my-leaves/{time_off_id}",
    status_code=204,
    **TEAM_AVAILABILITY_MY_LEAVE_DELETE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def delete_my_leave(
    request: Request,
    response: Response,
    time_off_id: str,
    service: TeamAvailabilityServiceDep,
    user: TeamAvailabilitySettingsWriteDep,
) -> None:
    await service.delete_my_leave(user_id=user.id, role=user.role, time_off_id=time_off_id)
    response.status_code = 204
