"""Driver self-service routes for mobile app profile management."""

import calendar
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.exceptions import ValidationError
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.common.utils import get_client_ip
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.service import DriverService
from app.modules.drivers.v1.docs import (
    SELF_ABOVE_70_MPH_REPORT,
    SELF_ACCEPT_ONBOARDING_CONSENTS,
    SELF_ACTIVE_DRIVING_MAP,
    SELF_AVERAGE_ROUTE_SPEED,
    SELF_AVERAGE_SPEED_REPORT,
    SELF_DELETE_PROFILE_PHOTO,
    SELF_DELIVERY_DETAIL,
    SELF_GET_CURRENT_TERMS,
    SELF_GET_ONBOARDING_STATUS,
    SELF_GET_PROFILE,
    SELF_HOME_SUMMARY,
    SELF_IMPORTANT_DELIVERY_NOTE,
    SELF_REPORTS_ABOVE_70_MPH,
    SELF_REPORTS_SHARP_BRAKES,
    SELF_ROUTE_ACTION,
    SELF_ROUTE_STOPS,
    SELF_ROUTE_SUMMARY,
    SELF_ROUTE_TELEMATICS,
    SELF_ROUTE_TODAY,
    SELF_ROUTES_ASSIGNED,
    SELF_ROUTES_BOARD,
    SELF_ROUTES_LIST,
    SELF_SET_MAP_PREFERENCE,
    SELF_SHARP_BRAKE_REPORT,
    SELF_STOP_ACTION,
    SELF_STOP_PACKAGES,
    SELF_TELEMETRY_BATCH,
    SELF_UPDATE_PROFILE,
    SELF_UPLOAD_PROFILE_PHOTO,
    SELF_WORK_SCHEDULE_DAY,
    SELF_WORK_SCHEDULE_WEEKLY,
)
from app.modules.drivers.v1.schemas import (
    DriverAbove70MphReportResponse,
    DriverActiveDrivingMapResponse,
    DriverAssignedRouteRow,
    DriverAssignedRoutesResponse,
    DriverAverageRouteSpeedResponse,
    DriverAverageSpeedReportResponse,
    DriverAverageSpeedReportRow,
    DriverCurrentRouteData,
    DriverCurrentRouteResponse,
    DriverDeliveryDetailResponse,
    DriverHomeSummaryResponse,
    DriverImportantDeliveryNoteResponse,
    DriverRouteActionResponse,
    DriverRoutesBoardResponse,
    DriverRoutesBoardRow,
    DriverRouteStopEntry,
    DriverRouteStopsResponse,
    DriverSelfMapPreferenceRequest,
    DriverSelfOnboardingConsentsRequest,
    DriverSelfOnboardingStatusResponse,
    DriverSelfProfileResponse,
    DriverSelfProfileUpdateRequest,
    DriverSelfTermsResponse,
    DriverSharpBrakeReportResponse,
    DriverStopActionRequest,
    DriverStopActionResponse,
    DriverStopPackageEntry,
    DriverStopPackagesResponse,
    DriverTelemetryBatchRequest,
    DriverTelemetryBatchResponse,
    RouteEventEntry,
    RouteEventsResponse,
    RouteHistoryResponse,
    RouteHistoryRow,
    RouteSummaryResponse,
    WorkScheduleDayDetailResponse,
    WorkScheduleDayEntry,
    WorkScheduleMonthlyResponse,
    WorkScheduleRouteInfo,
    WorkScheduleWeeklyResponse,
)
from app.modules.planning.enums import RouteStatus, RouteStopStatus, RouteType

router = APIRouter(prefix="/me")

DriverServiceDep = Annotated[DriverService, Depends(DriverService.dep)]
DriverSelfDep = Annotated[AuthUser, Allowed(UserRole.DRIVER)]

DriverReportPeriodLiteral = Literal["today", "yesterday", "this_week", "last_week", "last_month"]


def _resolve_driver_report_window(
    *,
    period: DriverReportPeriodLiteral | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    return DriverService.resolve_report_date_range(
        period=period,
        start_date=start_date,
        end_date=end_date,
        today=datetime.now(UTC).date(),
    )


def _merge_self_device_installation_id(
    *,
    query: str | None,
    header: str | None,
    body: str | None = None,
) -> str | None:
    raw = (body or header or query or "").strip()
    return raw or None


def _to_self_profile_response(driver, driver_service: DriverService) -> DriverSelfProfileResponse:
    user = getattr(driver, "user", None)
    return DriverSelfProfileResponse(
        id=driver.id,
        user_id=driver.user_id,
        driver_code=driver.driver_code,
        first_name=getattr(user, "first_name", ""),
        last_name=getattr(user, "last_name", ""),
        email=getattr(user, "email", ""),
        phone=getattr(user, "phone", None),
        profile_photo_url=driver_service.get_profile_photo_url(driver.profile_photo_key),
        requires_password_change=getattr(user, "force_password_change", False),
        terms_accepted_at=getattr(driver, "terms_accepted_at", None),
        location_consent_at=getattr(driver, "location_consent_at", None),
        map_preference=getattr(driver, "map_preference", None),
        version=driver.version,
    )


@router.get(
    "",
    response_model=SuccessResponse[DriverSelfProfileResponse],
    **SELF_GET_PROFILE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_profile(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    """Get authenticated driver's own profile."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    return ok(data=_to_self_profile_response(driver, driver_service))


@router.patch(
    "",
    response_model=SuccessResponse[DriverSelfProfileResponse],
    **SELF_UPDATE_PROFILE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_my_profile(
    request: Request,
    response: Response,
    body: DriverSelfProfileUpdateRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    """Update authenticated driver's own profile fields (name and phone only; email is not updatable)."""
    driver = await driver_service.update_driver_self_profile(
        user_id=user.id,
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        expected_version=body.expected_version,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver.id)
    return ok(data=_to_self_profile_response(driver, driver_service))


@router.get(
    "/terms-and-conditions/current",
    response_model=SuccessResponse[DriverSelfTermsResponse],
    **SELF_GET_CURRENT_TERMS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_current_terms_and_conditions(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    payload = cast(dict[str, Any], await driver_service.get_current_driver_terms())
    return ok(data=DriverSelfTermsResponse(**payload))


@router.get(
    "/onboarding-status",
    response_model=SuccessResponse[DriverSelfOnboardingStatusResponse],
    **SELF_GET_ONBOARDING_STATUS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_onboarding_status(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    device_installation_id: Annotated[
        str | None,
        Query(
            max_length=128,
            description=(
                "Optional opaque per-install identifier (8–128 characters when provided, after trim). "
                "When both this query parameter and ``X-Device-Installation-Id`` are sent, the header value "
                "takes precedence for the effective id. Omitted or blank → device-aware checks are skipped "
                "and ``requires_terms_reacceptance`` follows profile vs active terms hash only."
            ),
            openapi_examples={
                "per_install_uuid": {
                    "summary": "Typical opaque id",
                    "description": "UUID or other stable string generated once per app install.",
                    "value": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                },
            },
        ),
    ] = None,
    x_device_installation_id: Annotated[
        str | None,
        Header(
            alias="X-Device-Installation-Id",
            description=(
                "Optional same semantics as ``device_installation_id`` query param. "
                "Wins over the query parameter when both are non-empty (after trim)."
            ),
        ),
    ] = None,
) -> dict:
    merged = _merge_self_device_installation_id(
        query=device_installation_id,
        header=x_device_installation_id,
    )
    payload = cast(
        dict[str, Any],
        await driver_service.get_driver_self_onboarding_status(user_id=user.id, device_installation_id=merged),
    )
    return ok(data=DriverSelfOnboardingStatusResponse(**payload))


@router.post(
    "/onboarding-consents",
    response_model=SuccessResponse[DriverSelfOnboardingStatusResponse],
    **SELF_ACCEPT_ONBOARDING_CONSENTS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def accept_my_onboarding_consents(
    request: Request,
    response: Response,
    body: DriverSelfOnboardingConsentsRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    x_device_installation_id: Annotated[
        str | None,
        Header(
            alias="X-Device-Installation-Id",
            description=(
                "Optional opaque per-install id when ``device_installation_id`` is not set in the JSON body. "
                "If both JSON ``device_installation_id`` and this header are non-empty after trim, the JSON body wins."
            ),
        ),
    ] = None,
) -> dict:
    device_info: dict[str, str] = {}
    if body.device_platform:
        device_info["platform"] = body.device_platform
    if body.device_model:
        device_info["model"] = body.device_model
    if body.app_version:
        device_info["app_version"] = body.app_version

    merged_device = _merge_self_device_installation_id(
        query=None,
        header=x_device_installation_id,
        body=body.device_installation_id,
    )

    await driver_service.accept_driver_self_onboarding_consents(
        user_id=user.id,
        audit_user_id=user.id,
        audit_user_role=user.role,
        consent_context={
            "client_ip": get_client_ip(request),
            "user_agent": request.headers.get("user-agent"),
            "client_type": request.headers.get("x-client-type"),
            "device_info": device_info if device_info else None,
            "device_installation_id": merged_device,
        },
    )
    payload = cast(
        dict[str, Any],
        await driver_service.get_driver_self_onboarding_status(user_id=user.id, device_installation_id=merged_device),
    )
    return ok(data=DriverSelfOnboardingStatusResponse(**payload))


@router.patch(
    "/map-preference",
    response_model=SuccessResponse[DriverSelfOnboardingStatusResponse],
    **SELF_SET_MAP_PREFERENCE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def set_my_map_preference(
    request: Request,
    response: Response,
    body: DriverSelfMapPreferenceRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    await driver_service.set_driver_self_map_preference(
        user_id=user.id,
        map_preference=body.map_preference,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    payload = cast(dict[str, Any], await driver_service.get_driver_self_onboarding_status(user_id=user.id))
    return ok(data=DriverSelfOnboardingStatusResponse(**payload))


@router.post(
    "/photo",
    response_model=SuccessResponse[DriverSelfProfileResponse],
    **SELF_UPLOAD_PROFILE_PHOTO,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def upload_my_profile_photo(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    photo: Annotated[UploadFile, File()],
) -> dict:
    """Upload or replace authenticated driver's profile photo."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    updated = await driver_service.update_profile_photo(
        driver.id,
        photo,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    updated = await driver_service.get_driver(updated.id)
    return ok(data=_to_self_profile_response(updated, driver_service))


@router.delete(
    "/photo",
    response_model=SuccessResponse[dict],
    **SELF_DELETE_PROFILE_PHOTO,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_my_profile_photo(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    """Remove authenticated driver's profile photo."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    await driver_service.remove_profile_photo(
        driver.id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.get(
    "/home/summary",
    response_model=SuccessResponse[DriverHomeSummaryResponse],
    **SELF_HOME_SUMMARY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_home_summary(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    period: Annotated[
        DriverReportPeriodLiteral | None,
        Query(description="KPI window preset (alternative to start_date/end_date)"),
    ] = None,
    start_date: Annotated[date | None, Query(description="Start date (inclusive), YYYY-MM-DD")] = None,
    end_date: Annotated[date | None, Query(description="End date (inclusive), YYYY-MM-DD")] = None,
) -> dict:
    effective_period = period
    if effective_period is None and start_date is None and end_date is None:
        effective_period = "today"
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_driver_home_summary(
            driver_id=driver.id,
            period=effective_period,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    return ok(data=DriverHomeSummaryResponse(**payload))


@router.get(
    "/routes",
    response_model=SuccessResponse[RouteHistoryResponse],
    **SELF_ROUTES_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_routes(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    type: Annotated[list[RouteType] | None, Query(description="Route type filter (multi-select)")] = None,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str | None, Query()] = "date",
    sort_desc: Annotated[bool, Query()] = True,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_routes_history(
        driver_id=driver.id,
        page=page,
        size=size,
        route_type=[t.value for t in (type or [])] or None,
        search=search,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
    table = PaginatedResponse.create(
        items=[RouteHistoryRow(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=RouteHistoryResponse(table=table))


@router.get(
    "/routes/board",
    response_model=SuccessResponse[DriverRoutesBoardResponse],
    **SELF_ROUTES_BOARD,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_routes_board(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    tab: Annotated[
        Literal["upcoming", "past"],
        Query(description="Upcoming: ASSIGNED + ACTIVE. Past: COMPLETED."),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    type: Annotated[list[RouteType] | None, Query(description="Route type filter (multi-select)")] = None,
    search: Annotated[
        str | None,
        Query(
            description="Case-insensitive substring on route_code or vehicle registration_number (e.g. RT-763, AB12CDE).",
        ),
    ] = None,
    sort: Annotated[
        Literal["newest_first", "oldest_first"] | None,
        Query(
            description=(
                "Sort by plan service_date. Defaults: upcoming=oldest_first (soonest day first), "
                "past=newest_first (most recent day first). ACTIVE still ranks before ASSIGNED when service_date ties (upcoming only)."
            ),
        ),
    ] = None,
) -> dict:
    """All Routes tab: upcoming (open work) vs past (completed)."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_routes_board_tab(
        driver_id=driver.id,
        tab=tab,
        page=page,
        size=size,
        route_type=[t.value for t in (type or [])] or None,
        search=search,
        sort=sort,
    )
    table = PaginatedResponse.create(
        items=[DriverRoutesBoardRow(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverRoutesBoardResponse(table=table))


@router.get(
    "/routes/today",
    response_model=SuccessResponse[DriverCurrentRouteData],
    **SELF_ROUTE_TODAY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_today_route(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    service_date: Annotated[
        date | None,
        Query(
            description=(
                "Override ``RoutePlan.service_date`` (depot-local calendar day). "
                "When omitted, the server uses **today in the driver's depot timezone** "
                "(see ``Depot.timezone``; fallback if no depot: Europe/London)."
            ),
        ),
    ] = None,
) -> dict:
    """ASSIGNED or ACTIVE route for the plan's service day (default: depot-local today)."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = await driver_service.get_driver_today_route_dashboard_payload(
        driver_id=driver.id,
        explicit_service_date=service_date,
    )
    inner = DriverCurrentRouteResponse(**payload) if payload else None
    return ok(data=DriverCurrentRouteData(current_route=inner))


@router.get(
    "/routes/assigned",
    response_model=SuccessResponse[DriverAssignedRoutesResponse],
    **SELF_ROUTES_ASSIGNED,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_assigned_routes(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """All ASSIGNED routes (any service date), soonest service date first."""
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_assigned_routes_payload(
        driver_id=driver.id,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(
        items=[DriverAssignedRouteRow(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverAssignedRoutesResponse(table=table))


@router.get(
    "/reports/average-speed",
    response_model=SuccessResponse[DriverAverageSpeedReportResponse],
    **SELF_AVERAGE_SPEED_REPORT,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_average_speed_report(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    period: Annotated[
        DriverReportPeriodLiteral | None,
        Query(description="Optional preset window (alternative to start_date/end_date)"),
    ] = None,
    start_date: Annotated[date | None, Query(description="Start date (inclusive), YYYY-MM-DD")] = None,
    end_date: Annotated[date | None, Query(description="End date (inclusive), YYYY-MM-DD")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    window_start, window_end = _resolve_driver_report_window(
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_average_speed_report(
        driver_id=driver.id,
        start_date=window_start,
        end_date=window_end,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(
        items=[DriverAverageSpeedReportRow(**cast(dict[str, Any], r)) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverAverageSpeedReportResponse(table=table))


@router.get(
    "/reports/above-70-mph",
    response_model=SuccessResponse[DriverAbove70MphReportResponse],
    **SELF_REPORTS_ABOVE_70_MPH,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_reports_above_70_mph(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    period: Annotated[
        DriverReportPeriodLiteral | None,
        Query(description="Optional preset window (alternative to start_date/end_date)"),
    ] = None,
    start_date: Annotated[date | None, Query(description="Start date (inclusive), YYYY-MM-DD")] = None,
    end_date: Annotated[date | None, Query(description="End date (inclusive), YYYY-MM-DD")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    window_start, window_end = _resolve_driver_report_window(
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_above_70_mph_report(
        driver_id=driver.id,
        start_date=window_start,
        end_date=window_end,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(
        items=[RouteEventEntry(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverAbove70MphReportResponse(table=table))


@router.get(
    "/reports/sharp-brakes",
    response_model=SuccessResponse[DriverSharpBrakeReportResponse],
    **SELF_REPORTS_SHARP_BRAKES,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_reports_sharp_brakes(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    period: Annotated[
        DriverReportPeriodLiteral | None,
        Query(description="Optional preset window (alternative to start_date/end_date)"),
    ] = None,
    start_date: Annotated[date | None, Query(description="Start date (inclusive), YYYY-MM-DD")] = None,
    end_date: Annotated[date | None, Query(description="End date (inclusive), YYYY-MM-DD")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    window_start, window_end = _resolve_driver_report_window(
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.list_driver_sharp_brake_report(
        driver_id=driver.id,
        start_date=window_start,
        end_date=window_end,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(
        items=[RouteEventEntry(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverSharpBrakeReportResponse(table=table))


@router.get(
    "/routes/{route_id}/summary",
    response_model=SuccessResponse[RouteSummaryResponse],
    **SELF_ROUTE_SUMMARY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_route_summary(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    await driver_service.ensure_route_owned_by_driver(route_id=route_id, driver_id=driver.id)
    payload = await driver_service.get_route_summary_payload(route_id)
    return ok(data=RouteSummaryResponse(**payload))


@router.get(
    "/routes/{route_id}/telematics",
    response_model=SuccessResponse[RouteEventsResponse],
    **SELF_ROUTE_TELEMATICS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_route_telematics(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    event_type: Annotated[list[str] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    await driver_service.ensure_route_owned_by_driver(route_id=route_id, driver_id=driver.id)
    rows, total = await driver_service.list_route_events_payload(
        route_id=route_id,
        event_type=event_type,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(
        items=[RouteEventEntry(**r) for r in rows],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=RouteEventsResponse(table=table))


@router.get(
    "/routes/{route_id}/reports/above-70-mph",
    response_model=SuccessResponse[DriverAbove70MphReportResponse],
    **SELF_ABOVE_70_MPH_REPORT,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_above_70_mph_report(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.get_above_70_mph_report(
        route_id=route_id,
        driver_id=driver.id,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(items=[RouteEventEntry(**r) for r in rows], total=total, page=page, size=size)
    return ok(data=DriverAbove70MphReportResponse(table=table))


@router.get(
    "/routes/{route_id}/reports/sharp-brakes",
    response_model=SuccessResponse[DriverSharpBrakeReportResponse],
    **SELF_SHARP_BRAKE_REPORT,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_sharp_brake_report(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows, total = await driver_service.get_sharp_brake_report(
        route_id=route_id,
        driver_id=driver.id,
        page=page,
        size=size,
    )
    table = PaginatedResponse.create(items=[RouteEventEntry(**r) for r in rows], total=total, page=page, size=size)
    return ok(data=DriverSharpBrakeReportResponse(table=table))


@router.get(
    "/routes/{route_id}/average-speed",
    response_model=SuccessResponse[DriverAverageRouteSpeedResponse],
    **SELF_AVERAGE_ROUTE_SPEED,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_average_route_speed(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = await driver_service.get_average_route_speed_payload(route_id=route_id, driver_id=driver.id)
    return ok(data=payload)


@router.get(
    "/routes/{route_id}/active-driving-map",
    response_model=SuccessResponse[DriverActiveDrivingMapResponse],
    **SELF_ACTIVE_DRIVING_MAP,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_active_driving_map(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = await driver_service.get_active_driving_map_payload(route_id=route_id, driver_id=driver.id)
    return ok(data=payload)


@router.get(
    "/routes/{route_id}/stops",
    response_model=SuccessResponse[DriverRouteStopsResponse],
    **SELF_ROUTE_STOPS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_route_stops(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    rows = cast(list[dict[str, Any]], await driver_service.list_route_stops_for_driver(route_id=route_id, driver_id=driver.id))
    return ok(data=DriverRouteStopsResponse(items=[DriverRouteStopEntry(**row) for row in rows]))


@router.get(
    "/routes/{route_id}/stops/{stop_id}/delivery-detail",
    response_model=SuccessResponse[DriverDeliveryDetailResponse],
    **SELF_DELIVERY_DETAIL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_delivery_detail(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = await driver_service.get_delivery_detail_payload(route_id=route_id, stop_id=stop_id, driver_id=driver.id)
    return ok(data=payload)


@router.get(
    "/routes/{route_id}/stops/{stop_id}/important-note",
    response_model=SuccessResponse[DriverImportantDeliveryNoteResponse],
    **SELF_IMPORTANT_DELIVERY_NOTE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_important_delivery_note(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = await driver_service.get_important_delivery_note(route_id=route_id, stop_id=stop_id, driver_id=driver.id)
    return ok(data=payload)


@router.get(
    "/routes/{route_id}/stops/{stop_id}/packages",
    response_model=SuccessResponse[DriverStopPackagesResponse],
    **SELF_STOP_PACKAGES,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_my_stop_packages(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.list_stop_packages_for_driver(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        ),
    )
    return ok(
        data=DriverStopPackagesResponse(
            route_id=route_id,
            stop_id=stop_id,
            tracking_id=payload.get("tracking_id"),
            items=[DriverStopPackageEntry(**row) for row in cast(list[dict[str, Any]], payload.get("items", []))],
        )
    )


@router.post(
    "/routes/{route_id}/start",
    response_model=SuccessResponse[DriverRouteActionResponse],
    **SELF_ROUTE_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def start_my_route(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    route = await driver_service.driver_set_route_status(
        route_id=route_id,
        driver_id=driver.id,
        status=RouteStatus.ACTIVE,
        event_type="ROUTE_STARTED",
        metadata={"at": datetime.now(UTC).isoformat()},
    )
    return ok(data=DriverRouteActionResponse(route_id=route.id, status=route.status, message="Route started"))


@router.post(
    "/routes/{route_id}/pause",
    response_model=SuccessResponse[DriverRouteActionResponse],
    **SELF_ROUTE_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def pause_my_route(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    route = await driver_service.driver_set_route_status(
        route_id=route_id,
        driver_id=driver.id,
        status=RouteStatus.ACTIVE,
        event_type="ROUTE_PAUSED",
        metadata={"at": datetime.now(UTC).isoformat()},
    )
    return ok(data=DriverRouteActionResponse(route_id=route.id, status=route.status, message="Route paused"))


@router.post(
    "/routes/{route_id}/resume",
    response_model=SuccessResponse[DriverRouteActionResponse],
    **SELF_ROUTE_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def resume_my_route(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    route = await driver_service.driver_set_route_status(
        route_id=route_id,
        driver_id=driver.id,
        status=RouteStatus.ACTIVE,
        event_type="ROUTE_RESUMED",
        metadata={"at": datetime.now(UTC).isoformat()},
    )
    return ok(data=DriverRouteActionResponse(route_id=route.id, status=route.status, message="Route resumed"))


@router.post(
    "/routes/{route_id}/finish",
    response_model=SuccessResponse[DriverRouteActionResponse],
    **SELF_ROUTE_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def finish_my_route(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    route = await driver_service.driver_set_route_status(
        route_id=route_id,
        driver_id=driver.id,
        status=RouteStatus.COMPLETED,
        event_type="ROUTE_COMPLETED",
        metadata={"at": datetime.now(UTC).isoformat()},
    )
    if driver.account_status == DriverAccountStatus.PENDING_ACTIVATION:
        await driver_service.activate_driver_on_login(user_id=user.id)
    return ok(data=DriverRouteActionResponse(route_id=route.id, status=route.status, message="Route finished"))


@router.post(
    "/stops/{stop_id}/arrive",
    response_model=SuccessResponse[DriverStopActionResponse],
    **SELF_STOP_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def arrive_my_stop(
    request: Request,
    response: Response,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    body: DriverStopActionRequest,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    stop = await driver_service.driver_update_stop_status(
        stop_id=stop_id,
        driver_id=driver.id,
        status=RouteStopStatus.ARRIVED,
        notes=body.notes,
    )
    return ok(data=DriverStopActionResponse(stop_id=stop.id, status=stop.status, message="Stop marked as arrived"))


@router.post(
    "/stops/{stop_id}/complete",
    response_model=SuccessResponse[DriverStopActionResponse],
    **SELF_STOP_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def complete_my_stop(
    request: Request,
    response: Response,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    body: DriverStopActionRequest,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    stop = await driver_service.driver_update_stop_status(
        stop_id=stop_id,
        driver_id=driver.id,
        status=RouteStopStatus.COMPLETED,
        notes=body.notes,
    )
    return ok(data=DriverStopActionResponse(stop_id=stop.id, status=stop.status, message="Stop marked as completed"))


@router.post(
    "/stops/{stop_id}/fail",
    response_model=SuccessResponse[DriverStopActionResponse],
    **SELF_STOP_ACTION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def fail_my_stop(
    request: Request,
    response: Response,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    body: DriverStopActionRequest,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    stop = await driver_service.driver_update_stop_status(
        stop_id=stop_id,
        driver_id=driver.id,
        status=RouteStopStatus.FAILED,
        notes=body.notes,
    )
    return ok(data=DriverStopActionResponse(stop_id=stop.id, status=stop.status, message="Stop marked as failed"))


@router.post(
    "/telemetry/batch",
    response_model=SuccessResponse[DriverTelemetryBatchResponse],
    **SELF_TELEMETRY_BATCH,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def ingest_my_telemetry_batch(
    request: Request,
    response: Response,
    body: DriverTelemetryBatchRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    accepted = await driver_service.ingest_driver_telematics_batch(
        driver_id=driver.id,
        items=[item.model_dump(exclude_unset=True) for item in body.items],
    )
    return ok(data=DriverTelemetryBatchResponse(accepted=accepted))


def _build_schedule_day(raw: dict) -> WorkScheduleDayEntry:
    route_raw = raw.get("route")
    route = WorkScheduleRouteInfo(**route_raw) if route_raw else None
    return WorkScheduleDayEntry(
        date=raw["date"],
        day_type=raw["day_type"],
        shift_hours=raw.get("shift_hours"),
        shift_status=raw.get("shift_status"),
        time_off_type=raw.get("time_off_type"),
        time_off_is_paid=raw.get("time_off_is_paid"),
        holiday_name=raw.get("holiday_name"),
        route=route,
    )


@router.get(
    "/work-schedule",
    response_model=SuccessResponse[WorkScheduleWeeklyResponse | WorkScheduleMonthlyResponse],
    **SELF_WORK_SCHEDULE_WEEKLY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_work_schedule(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    view: Annotated[Literal["weekly", "monthly"], Query(description="Calendar view mode")] = "weekly",
    start_date: Annotated[date | None, Query(description="Week start date (weekly view)")] = None,
    end_date: Annotated[date | None, Query(description="Week end date (weekly view)")] = None,
    month: Annotated[str | None, Query(description="Month as YYYY-MM (monthly view)")] = None,
) -> dict:
    from app.common.exceptions import ValidationError

    driver = await driver_service.get_driver_by_user_id(user.id)

    if view == "weekly":
        if start_date is None or end_date is None:
            raise ValidationError("start_date and end_date are required for weekly view")
        if end_date < start_date:
            raise ValidationError("start_date must be before end_date")
        from_date, to_date = start_date, end_date
        raw_days = await driver_service.get_driver_work_schedule(
            driver_id=driver.id, from_date=from_date, to_date=to_date
        )
        return ok(
            data=WorkScheduleWeeklyResponse(
                start_date=from_date,
                end_date=to_date,
                days=[_build_schedule_day(d) for d in raw_days],
            )
        )

    # monthly view
    if month is None:
        raise ValidationError("month is required for monthly view (format: YYYY-MM)")
    year, mon = int(month[:4]), int(month[5:7])
    _, last_day = calendar.monthrange(year, mon)
    from_date = date(year, mon, 1)
    to_date = date(year, mon, last_day)
    raw_days = await driver_service.get_driver_work_schedule(
        driver_id=driver.id, from_date=from_date, to_date=to_date
    )
    return ok(
        data=WorkScheduleMonthlyResponse(
            month=month[:7],
            days=[_build_schedule_day(d) for d in raw_days],
        )
    )


@router.get(
    "/work-schedule/day",
    response_model=SuccessResponse[WorkScheduleDayDetailResponse],
    **SELF_WORK_SCHEDULE_DAY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_my_work_schedule_day(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    date: Annotated[date, Query(description="Calendar date (YYYY-MM-DD)")],
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    raw_days = await driver_service.get_driver_work_schedule(
        driver_id=driver.id, from_date=date, to_date=date
    )
    raw = raw_days[0]
    route_raw = raw.get("route")
    route = WorkScheduleRouteInfo(**route_raw) if route_raw else None
    return ok(
        data=WorkScheduleDayDetailResponse(
            date=raw["date"],
            day_type=raw["day_type"],
            shift_hours=raw.get("shift_hours"),
            shift_status=raw.get("shift_status"),
            time_off_type=raw.get("time_off_type"),
            time_off_is_paid=raw.get("time_off_is_paid"),
            holiday_name=raw.get("holiday_name"),
            vehicle=route_raw["vehicle_registration"] if route_raw else None,
            route=route,
        )
    )
