"""Service Tiers admin API (v1)."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums.user import UserRole
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.rate_limit import SERVICE_TIER_READ_RATE_LIMIT, SERVICE_TIER_WRITE_RATE_LIMIT, limiter
from app.modules.service_tiers.constants import is_superfast_global_tier, is_superfast_tier_name
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier
from app.modules.service_tiers.service import ServiceTierService
from app.modules.service_tiers.v1.docs import (
    SERVICE_TIERS_CREATE,
    SERVICE_TIERS_DELETE,
    SERVICE_TIERS_EFFECTIVE_LIST,
    SERVICE_TIERS_GET,
    SERVICE_TIERS_GLOBAL_LIST,
    SERVICE_TIERS_LIST,
    SERVICE_TIERS_ORG_OVERRIDE_UPSERT,
    SERVICE_TIERS_UPDATE,
)
from app.modules.service_tiers.v1.schemas import (
    OrgServiceTierOverrideUpsertRequest,
    ServiceTierCreateRequest,
    ServiceTierListResponse,
    ServiceTierResponse,
    ServiceTierUpdateRequest,
)

router = APIRouter()

ServiceTierServiceDep = Annotated[ServiceTierService, Depends(ServiceTierService.dep)]
ServiceTierAdminDep = Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN)]
ServiceTierAdminOrB2BReadDep = Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.CUSTOMER_B2B, UserRole.CUSTOMER_B2C)]


def _to_tier_response(
    tier: ServiceTier,
    *,
    is_override: bool = False,
    source_scope_type: ServiceTierScopeType | None = None,
    global_tier_id: str | None = None,
    permitted: bool | None = None,
    is_default: bool | None = None,
    plain_type: str | None = None,
    is_system_tier: bool | None = None,
    tier_name_locked: bool | None = None,
    permitted_locked: bool | None = None,
) -> ServiceTierResponse:
    st = tier.status if isinstance(tier.status, ServiceTierStatus) else ServiceTierStatus(str(tier.status))
    system_tier = is_system_tier if is_system_tier is not None else is_superfast_global_tier(tier)
    name_locked = tier_name_locked if tier_name_locked is not None else is_superfast_tier_name(tier.tier_name)
    return ServiceTierResponse(
        id=tier.id,
        created_at=tier.created_at,
        updated_at=tier.updated_at,
        version=tier.version,
        tier_name=tier.tier_name,
        description=tier.description,
        duration_days=tier.duration_days,
        error_margin_kg=tier.error_margin_kg,
        price_per_kg=float(tier.price_per_kg),
        price_per_package=float(tier.price_per_package),
        base_price=float(tier.base_price),
        available_for=ServiceTierAudience(tier.available_for),
        scope_type=ServiceTierScopeType(tier.scope_type),
        scope_org_id=tier.scope_org_id,
        color=tier.color,
        icon=tier.icon,
        status=st,
        is_override=is_override,
        source_scope_type=source_scope_type,
        global_tier_id=global_tier_id,
        permitted=permitted,
        is_default=is_default,
        plain_type=plain_type,
        is_system_tier=system_tier,
        tier_name_locked=name_locked,
        permitted_locked=permitted_locked if permitted_locked is not None else False,
    )


@router.get(
    "/effective-for-org/{org_id}",
    response_model=SuccessResponse[ServiceTierListResponse],
    **SERVICE_TIERS_EFFECTIVE_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_READ_RATE_LIMIT)
async def list_effective_service_tiers_for_org(
    request: Request,
    response: Response,
    org_id: UUID,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminOrB2BReadDep,
) -> dict:
    rows = await service.get_effective_tiers_with_source_for_org(str(org_id))
    responses = [
        _to_tier_response(
            row["tier"],
            is_override=bool(row.get("is_override")),
            source_scope_type=ServiceTierScopeType(row["source_scope_type"]) if row.get("source_scope_type") else None,
            global_tier_id=row.get("global_tier_id"),
            permitted=row.get("permitted"),
            is_default=row.get("is_default"),
            plain_type=row.get("plain_type"),
            is_system_tier=row.get("is_system_tier"),
            tier_name_locked=row.get("tier_name_locked"),
            permitted_locked=row.get("permitted_locked"),
        )
        for row in rows
    ]
    return ok(data=ServiceTierListResponse(items=responses, total=len(responses)))


@router.get(
    "/global",
    response_model=SuccessResponse[ServiceTierListResponse],
    **SERVICE_TIERS_GLOBAL_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_READ_RATE_LIMIT)
async def list_global_service_tier_catalog(
    request: Request,
    response: Response,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
    available_for: list[ServiceTierAudience] = Query(
        default=[],
        description="Filter by audience (multi-select): CUSTOMER_B2B | CUSTOMER_B2C | BOTH",
    ),
    status: list[ServiceTierStatus] = Query(default=[], description="Filter by status (multi-select): ACTIVE | INACTIVE"),
) -> dict:
    items = await service.list_tiers(
        scope_type=ServiceTierScopeType.GLOBAL,
        scope_org_id=None,
        available_for=available_for or None,
        status=status or None,
    )
    responses = [_to_tier_response(t) for t in items]
    return ok(data=ServiceTierListResponse(items=responses, total=len(responses)))


@router.put(
    "/orgs/{org_id}/overrides",
    response_model=SuccessResponse[ServiceTierResponse],
    **SERVICE_TIERS_ORG_OVERRIDE_UPSERT,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_WRITE_RATE_LIMIT)
async def upsert_org_service_tier_override(
    request: Request,
    response: Response,
    org_id: UUID,
    body: OrgServiceTierOverrideUpsertRequest,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    raw = body.model_dump(exclude_unset=True, exclude={"tier_name", "available_for", "version"})
    dec_keys = ("price_per_kg", "price_per_package", "base_price")
    patch: dict[str, object] = {}
    for k, v in raw.items():
        if k in dec_keys and v is not None:
            patch[k] = Decimal(str(v))
        else:
            patch[k] = v
    tier = await service.upsert_org_tier_override(
        organization_id=str(org_id),
        tier_name=body.tier_name,
        available_for=body.available_for,
        payload=patch,
        expected_version=body.version,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(data=_to_tier_response(tier, is_override=True, source_scope_type=ServiceTierScopeType.ORG))


@router.get(
    "",
    response_model=SuccessResponse[ServiceTierListResponse],
    **SERVICE_TIERS_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_READ_RATE_LIMIT)
async def list_service_tiers(
    request: Request,
    response: Response,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
    scope_type: ServiceTierScopeType | None = Query(default=None),
    scope_org_id: str | None = Query(default=None),
    available_for: list[ServiceTierAudience] = Query(
        default=[],
        description="Filter by audience (multi-select): CUSTOMER_B2B | CUSTOMER_B2C | BOTH",
    ),
    status: list[ServiceTierStatus] = Query(default=[], description="Filter by status (multi-select): ACTIVE | INACTIVE"),
    search: str | None = Query(None, max_length=100, description="Search by tier name (case-insensitive)"),
    min_price: float | None = Query(None, ge=0, description="Minimum price per package (GBP)"),
    max_price: float | None = Query(None, ge=0, description="Maximum price per package (GBP)"),
    min_days: int | None = Query(None, ge=1, description="Minimum duration in days"),
    max_days: int | None = Query(None, ge=1, description="Maximum duration in days"),
) -> dict:
    items = await service.list_tiers(
        scope_type=scope_type,
        scope_org_id=scope_org_id,
        available_for=available_for or None,
        status=status or None,
        search=search,
        min_price=min_price,
        max_price=max_price,
        min_days=min_days,
        max_days=max_days,
    )
    responses = [_to_tier_response(t) for t in items]
    return ok(data=ServiceTierListResponse(items=responses, total=len(responses)))


@router.get(
    "/",
    include_in_schema=False,
    response_model=SuccessResponse[ServiceTierListResponse],
)
async def list_service_tiers_slash_alias(
    request: Request,
    response: Response,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
    scope_type: ServiceTierScopeType | None = Query(default=None),
    scope_org_id: str | None = Query(default=None),
    available_for: list[ServiceTierAudience] = Query(default=[]),
    status: list[ServiceTierStatus] = Query(default=[]),
    search: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_days: int | None = None,
    max_days: int | None = None,
) -> dict:
    return await list_service_tiers(
        request=request,
        response=response,
        service=service,
        _user=_user,
        scope_type=scope_type,
        scope_org_id=scope_org_id,
        available_for=available_for,
        status=status,
        search=search,
        min_price=min_price,
        max_price=max_price,
        min_days=min_days,
        max_days=max_days,
    )


@router.post(
    "",
    response_model=SuccessResponse[ServiceTierResponse],
    status_code=status.HTTP_201_CREATED,
    **SERVICE_TIERS_CREATE,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_WRITE_RATE_LIMIT)
async def create_service_tier(
    request: Request,
    response: Response,
    body: ServiceTierCreateRequest,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    tier = await service.create_tier(
        tier_name=body.tier_name,
        description=body.description,
        duration_days=body.duration_days,
        error_margin_kg=body.error_margin_kg,
        price_per_kg=Decimal(str(body.price_per_kg)),
        price_per_package=Decimal(str(body.price_per_package)),
        base_price=Decimal(str(body.base_price)),
        available_for=body.available_for,
        scope_type=body.scope_type,
        scope_org_id=body.scope_org_id,
        color=body.color,
        icon=body.icon,
        status=body.status,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(data=_to_tier_response(tier))


@router.post(
    "/",
    include_in_schema=False,
    response_model=SuccessResponse[ServiceTierResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_service_tier_slash_alias(
    request: Request,
    response: Response,
    body: ServiceTierCreateRequest,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    return await create_service_tier(
        request=request,
        response=response,
        body=body,
        service=service,
        _user=_user,
    )


@router.get(
    "/{tier_id}",
    response_model=SuccessResponse[ServiceTierResponse],
    status_code=status.HTTP_200_OK,
    **SERVICE_TIERS_GET,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_READ_RATE_LIMIT)
async def get_service_tier(
    request: Request,
    response: Response,
    tier_id: UUID,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    tier = await service.get_tier(str(tier_id))
    return ok(data=_to_tier_response(tier))


@router.patch(
    "/{tier_id}",
    response_model=SuccessResponse[ServiceTierResponse],
    status_code=status.HTTP_200_OK,
    **SERVICE_TIERS_UPDATE,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_WRITE_RATE_LIMIT)
async def update_service_tier(
    request: Request,
    response: Response,
    tier_id: UUID,
    body: ServiceTierUpdateRequest,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    raw = body.model_dump(exclude_unset=True, exclude={"version"})
    dec_keys = ("price_per_kg", "price_per_package", "base_price")
    data: dict[str, object] = {}
    for k, v in raw.items():
        if k in dec_keys and v is not None:
            data[k] = Decimal(str(v))
        else:
            data[k] = v
    updated = await service.update_tier(
        tier_id=str(tier_id),
        data=data,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
        expected_version=body.version,
    )
    return ok(data=_to_tier_response(updated))


@router.delete(
    "/{tier_id}",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_200_OK,
    **SERVICE_TIERS_DELETE,  # type: ignore[arg-type]
)
@limiter.limit(SERVICE_TIER_WRITE_RATE_LIMIT)
async def delete_service_tier(
    request: Request,
    response: Response,
    tier_id: UUID,
    service: ServiceTierServiceDep,
    _user: ServiceTierAdminDep,
) -> dict:
    await service.delete_tier(
        tier_id=str(tier_id),
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(data={})
