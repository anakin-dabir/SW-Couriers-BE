"""Holidays admin API (v1)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums.user import UserRole
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.modules.holidays.enums import HolidayAudience
from app.modules.holidays.service import HolidayService
from app.modules.holidays.v1.docs import (
    HOLIDAYS_COPY,
    HOLIDAYS_CREATE,
    HOLIDAYS_DELETE,
    HOLIDAYS_GET,
    HOLIDAYS_LIST,
    HOLIDAYS_UPDATE,
    HOLIDAYS_YEARS,
)
from app.modules.holidays.v1.schemas import (
    CopyHolidaysRequest,
    CopyHolidaysResponse,
    HolidayAllowedDriverInfo,
    HolidayCreateRequest,
    HolidayListResponse,
    HolidayResponse,
    HolidayUpdateRequest,
    HolidayYearSummary,
    HolidayYearSummaryListResponse,
)

router = APIRouter()

HolidayServiceDep = Annotated[HolidayService, Depends(HolidayService.dep)]

# Admin-only: treat holidays as configuration; require auth and enforce ADMIN role in handlers.
HolidayReadDep = Annotated[AuthUser, Allowed()]
HolidayWriteDep = Annotated[AuthUser, Allowed()]


def _to_holiday_response(holiday, driver_name_map: dict[str, str]) -> HolidayResponse:
    allowed_driver_ids = [row.driver_id for row in holiday.allowed_drivers]
    allowed_drivers = [
        HolidayAllowedDriverInfo(id=driver_id, name=driver_name_map.get(driver_id, "Unknown Driver"))
        for driver_id in allowed_driver_ids
    ]
    return HolidayResponse(
        id=holiday.id,
        name=holiday.name,
        start_date=holiday.start_date,
        end_date=holiday.end_date,
        audience=holiday.audience,  # type: ignore[arg-type]
        allow_shifts=holiday.allow_shifts,
        allowed_driver_ids=allowed_driver_ids,
        allowed_drivers=allowed_drivers,
    )


@router.get(
    "",
    response_model=SuccessResponse[HolidayListResponse],
    **HOLIDAYS_LIST,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_holidays(
    request: Request,
    response: Response,
    service: HolidayServiceDep,
    _user: HolidayReadDep,
    year: int | None = None,
    audience: HolidayAudience | None = None,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    items = await service.list_holidays(year=year, audience=audience)
    driver_ids = [row.driver_id for holiday in items for row in holiday.allowed_drivers]
    driver_name_map = await service.get_allowed_driver_name_map(driver_ids)
    responses: list[HolidayResponse] = [_to_holiday_response(h, driver_name_map) for h in items]
    return ok(data=HolidayListResponse(year=year, items=responses, total=len(responses)))


@router.get(
    "/",
    include_in_schema=False,
    response_model=SuccessResponse[HolidayListResponse],
)
async def list_holidays_slash_alias(
    request: Request,
    response: Response,
    service: HolidayServiceDep,
    _user: HolidayReadDep,
    year: int | None = None,
    audience: HolidayAudience | None = None,
) -> dict:
    return await list_holidays(
        request=request,
        response=response,
        service=service,
        _user=_user,
        year=year,
        audience=audience,
    )


@router.get(
    "/years",
    response_model=SuccessResponse[HolidayYearSummaryListResponse],
    status_code=status.HTTP_200_OK,
    **HOLIDAYS_YEARS,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_holiday_years(
    request: Request,
    response: Response,
    service: HolidayServiceDep,
    _user: HolidayReadDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    year_counts = await service.list_holiday_years()
    items = [HolidayYearSummary(year=year, holidays_count=count) for year, count in year_counts]
    return ok(data=HolidayYearSummaryListResponse(items=items, total=len(items)))


@router.post(
    "",
    response_model=SuccessResponse[HolidayResponse],
    status_code=status.HTTP_201_CREATED,
    **HOLIDAYS_CREATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_holiday(
    request: Request,
    response: Response,
    body: HolidayCreateRequest,
    service: HolidayServiceDep,
    _user: HolidayWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    holiday = await service.create_holiday(
        name=body.name,
        start_date=body.start_date,
        end_date=body.end_date,
        audience=body.audience,
        allow_shifts=body.allow_shifts,
        allowed_driver_ids=body.allowed_driver_ids,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    driver_ids = [row.driver_id for row in holiday.allowed_drivers]
    driver_name_map = await service.get_allowed_driver_name_map(driver_ids)
    payload = _to_holiday_response(holiday, driver_name_map)
    return ok(data=payload)


@router.post(
    "/",
    include_in_schema=False,
    response_model=SuccessResponse[HolidayResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_holiday_slash_alias(
    request: Request,
    response: Response,
    body: HolidayCreateRequest,
    service: HolidayServiceDep,
    _user: HolidayWriteDep,
) -> dict:
    return await create_holiday(
        request=request,
        response=response,
        body=body,
        service=service,
        _user=_user,
    )


@router.get(
    "/{holiday_id}",
    response_model=SuccessResponse[HolidayResponse],
    status_code=status.HTTP_200_OK,
    **HOLIDAYS_GET,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_holiday(
    request: Request,
    response: Response,
    holiday_id: str,
    service: HolidayServiceDep,
    _user: HolidayReadDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    holiday = await service.get_holiday(holiday_id)
    driver_ids = [row.driver_id for row in holiday.allowed_drivers]
    driver_name_map = await service.get_allowed_driver_name_map(driver_ids)
    payload = _to_holiday_response(holiday, driver_name_map)
    return ok(data=payload)


@router.patch(
    "/{holiday_id}",
    response_model=SuccessResponse[HolidayResponse],
    status_code=status.HTTP_200_OK,
    **HOLIDAYS_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_holiday(
    request: Request,
    response: Response,
    holiday_id: str,
    body: HolidayUpdateRequest,
    service: HolidayServiceDep,
    _user: HolidayWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    updated = await service.update_holiday(
        holiday_id=holiday_id,
        name=body.name,
        start_date=body.start_date,
        end_date=body.end_date,
        audience=body.audience,
        allow_shifts=body.allow_shifts,
        allowed_driver_ids=body.allowed_driver_ids,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    driver_ids = [row.driver_id for row in updated.allowed_drivers]
    driver_name_map = await service.get_allowed_driver_name_map(driver_ids)
    payload = _to_holiday_response(updated, driver_name_map)
    return ok(data=payload)


@router.delete(
    "/{holiday_id}",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_200_OK,
    **HOLIDAYS_DELETE,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_holiday(
    request: Request,
    response: Response,
    holiday_id: str,
    service: HolidayServiceDep,
    _user: HolidayWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    await service.delete_holiday(
        holiday_id=holiday_id,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(data={})


@router.post(
    "/copy",
    response_model=SuccessResponse[CopyHolidaysResponse],
    **HOLIDAYS_COPY,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def copy_holidays(
    request: Request,
    response: Response,
    body: CopyHolidaysRequest,
    service: HolidayServiceDep,
    _user: HolidayWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    copied = await service.copy_holidays(
        source_year=body.source_year,
        target_year=body.target_year,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    data = CopyHolidaysResponse(
        source_year=body.source_year,
        target_year=body.target_year,
        copied_count=copied,
    )
    return ok(data=data)
