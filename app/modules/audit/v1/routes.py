import csv
import io
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import Allowed, AuditCtxDep, AuthUser, CurrentUserDep, get_db_session
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ForbiddenError
from app.common.exceptions import ValidationError
from app.modules.audit.helpers import audit_actor_label
from app.modules.audit.query_service import AuditQueryServiceDep
from app.modules.audit.service import (
    AuditService,
    compute_integrity_hash,
    integrity_payload,
)
from app.modules.audit.v1.schemas import (
    ActivityTrendResponse,
    ActivityTrendPoint,
    AuditCategory,
    AuditEventType,
    AuditLogDetail,
    AuditLogEntry,
    AuditLogListResponse,
    AuditLogSummary,
    ChangeHistoryResponse,
    ChangeHistoryEntry,
    ComparisonRequest,
    ComparisonResponse,
    ComparisonResultEntry,
    DataAccessHeatmapEntry,
    DataAccessHeatmapResponse,
    DataAccessSummaryEntry,
    DataAccessSummaryResponse,
    FieldHistoryEntry,
    FieldHistoryPoint,
    FieldHistoryResponse,
    IntegrityVerification,
    RelatedAuditEvent,
    SavedViewCreate,
    SavedViewResponse,
)
from app.common.response import ok
from app.common.schemas import SuccessResponse

router = APIRouter()

AUDIT_EXPORT_MAX_ROWS = 100_000
AUDIT_EXPORT_DEFAULT_DAYS = 90

AuditOrgReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_B2B,
        resource=Resource.AUDIT_LOG,
        level=PermissionLevel.READ,
    ),
]
AuditSavedViewsReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_B2B,
        resource=Resource.AUDIT_LOG,
        level=PermissionLevel.READ,
    ),
]


def _assert_audit_org_scope(user: AuthUser, organization_id: str) -> None:
    """B2B users can only access their own organization audit scope."""
    if user.role != UserRole.CUSTOMER_B2B:
        return
    if not user.organization_id or str(user.organization_id) != str(organization_id):
        raise ForbiddenError("You do not have access to this organisation.")


def _resolve_export_date_range(
    from_date: datetime | None,
    to_date: datetime | None,
    *,
    default_days: int = AUDIT_EXPORT_DEFAULT_DAYS,
) -> tuple[datetime, datetime]:
    """Default to the last N days when both bounds are omitted."""
    now = datetime.now(UTC)
    if from_date is None and to_date is None:
        return now - timedelta(days=default_days), now
    resolved_to = to_date if to_date is not None else now
    resolved_from = (
        from_date if from_date is not None else resolved_to - timedelta(days=default_days)
    )
    return resolved_from, resolved_to


@router.get(
    "/{organization_id}/audit-logs/summary",
    response_model=SuccessResponse[AuditLogSummary],
)
async def get_audit_summary(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Get summarized audit stats for the dashboard cards."""
    _assert_audit_org_scope(user, organization_id)
    stats = await svc.get_summary_stats(organization_id)
    return ok(data=stats)


def _device(v: str | None) -> str:
    return v if v and v.strip().lower() not in ("unknown", "other", "") else "Unknown"


def _browser(v: str | None) -> str:
    return v if v and v.strip().lower() not in ("unknown", "other", "") else "Unknown"


def _enum_text(value: object | None) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, str):
        return value
    return ""


def _coerce_audit_category(
    value: object | None,
    *,
    default: AuditCategory = AuditCategory.SYSTEM,
) -> AuditCategory:
    if isinstance(value, AuditCategory):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return AuditCategory(normalized)
        except ValueError:
            member = AuditCategory.__members__.get(normalized.upper())
            if member is not None:
                return member
    return default


def _coerce_audit_event_type(
    value: object | None,
    *,
    default: AuditEventType = AuditEventType.SYSTEM_CONFIG_CHANGED,
) -> AuditEventType:
    if isinstance(value, AuditEventType):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return AuditEventType(normalized)
        except ValueError:
            member = AuditEventType.__members__.get(normalized.upper())
            if member is not None:
                return member
    return default


def get_display_category(category: str | None, event_type: str | None) -> str:
    """Consolidated display category mapping logic used across audit routes."""
    cat = str(category or "").upper()
    et = str(event_type or "").upper()
    
    # Priority 1: Match by specific event type keywords
    if "BOOKING" in et: return "Booking"
    if "DELIVERY" in et or "POD" in et or "SHIPMENT" in et: return "Delivery"
    if "LOGIN" in et or "LOGOUT" in et or "SESSION" in et: return "Login"
    if "PAYMENT" in et or "TRANSACTION" in et: return "Payment"
    if "INVOICE" in et: return "Invoice"
    
    # Priority 2: Match by broad category enums
    if cat == "CREDIT": return "Credit"
    if cat in ("ACCOUNT", "CONTACT"): return "Account"
    if cat == "ORDER": return "Booking"
    if cat == "BILLING": return "Invoice"
    if cat in ("ACCESS", "SECURITY"): return "Login"
    
    # Priority 3: Keywords for System-recorded events that should be Account
    if "ACCOUNT" in et or "CONTACT" in et: return "Account"
    
    return "System"


@router.get(
    "/{organization_id}/audit-logs",
    response_model=SuccessResponse[AuditLogListResponse],
)
async def get_audit_logs(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    category: list[str] | None = Query(None, description="Event category filter"),
    event_type: list[str] | None = Query(None, description="Event type filter"),
    severity: list[str] | None = Query(None, description="Event severity filter"),
    actor: str | None = Query(None, enum=["Admin", "Client"], description="Search for logs by actor role"),
    browser: list[str] | None = Query(None, description="Filter logs by browser"),
    search: str | None = Query(None, description="Global search across IP, browser, action, reason, and actor email"),
    from_date: datetime | None = Query(None, description="Start date (ISO format)"),
    to_date: datetime | None = Query(None, description="End date (ISO format)"),
    sort_by: str = Query("desc", enum=["asc", "desc"], description="Sort direction by timestamp"),
    ui_category: list[str] | None = Query(None, description="Simplified UI category filter (Booking, Login, etc.)"),
):
    """Get paginated activity logs for the organization."""
    _assert_audit_org_scope(user, organization_id)
    items, total = await svc.get_organization_logs(
        organization_id, 
        page=page, 
        size=size, 
        category=category, 
        event_type=event_type,
        severity=severity,
        actor=actor,
        browser=browser,
        search=search,
        from_date=from_date,
        to_date=to_date,
        sort_by=sort_by,
        ui_category=ui_category,
    )
    
    # Simple OS version parsing
    def parse_os(ua_str: str | None) -> str:
        if not ua_str:
            return "Unknown"
        ua = ua_str.lower()
        if "windows nt 10.0" in ua:
            return "Windows 11" if "chrome" in ua else "Windows 10" # Very rough, but matches common UAs
        if "windows nt 6.3" in ua: return "Windows 8.1"
        if "windows nt 6.1" in ua: return "Windows 7"
        if "mac os x" in ua: return "macOS"
        if "iphone" in ua: return "iOS"
        if "android" in ua: return "Android"
        if "debian" in ua: return "Debian"
        if "fedora" in ua: return "Fedora"
        if "ubuntu" in ua: return "Ubuntu"
        if "linux" in ua: return "Linux"
        return "Other"

    processed_items = []
    for log in items:
        # Resolve category/event_type strings for lookup and strongly typed enums for payload.
        c_str = _enum_text(log.category)
        et_str = _enum_text(log.event_type)
        entry_category = _coerce_audit_category(log.category, default=AuditCategory.SYSTEM)
        entry_event_type = _coerce_audit_event_type(
            log.event_type,
            default=AuditEventType.SYSTEM_CONFIG_CHANGED,
        )

        processed_items.append(
            AuditLogEntry(
                id=log.id,
                created_at=log.created_at,
                os=log.os or parse_os(log.user_agent),
                browser=_browser(log.browser),
                device=_device(log.device),
                email=log.user.email if log.user else "System",
                actor=audit_actor_label(log.user_role),
                category=entry_category,
                event_type=entry_event_type,
                display_category=get_display_category(c_str, et_str),
                severity=log.severity,
                audit_ref=log.audit_ref,
                entity_ref=log.entity_ref,
                event=log.reason or log.action.replace(".", " ").replace("_", " ").capitalize(),
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                ip_address=log.ip_address,
            )
        )

    return ok(data=AuditLogListResponse(
        items=processed_items,
        total=total,
        page=page,
        size=size,
    ))


@router.get(
    "/{organization_id}/audit-logs/data-access",
    response_model=SuccessResponse[AuditLogListResponse],
)
async def get_data_access_logs(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    event_type: list[str] | None = Query(None, description="Access event type filter"),
    actor: str | None = Query(None, enum=["Admin", "Client"], description="Actor role filter"),
    search: str | None = Query(None, description="Search across IP, resource, and actor"),
    from_date: datetime | None = Query(None, description="Start date"),
    to_date: datetime | None = Query(None, description="End date"),
):
    """Get paginated data access logs specifically."""
    _assert_audit_org_scope(user, organization_id)
    # Force Access category
    items, total = await svc.get_organization_logs(
        organization_id,
        page=page,
        size=size,
        category=["Access"],
        event_type=event_type,
        actor=actor,
        search=search,
        from_date=from_date,
        to_date=to_date,
        sort_by="desc",
    )

    processed_items = []
    for log in items:
        # For data access logs, the 'resource' often comes from entity_ref or details.
        resource = log.entity_ref or f"{log.entity_type} {log.entity_id}" if log.entity_id else log.action
        category_value = _coerce_audit_category(log.category, default=AuditCategory.ACCESS)
        event_type_value = _coerce_audit_event_type(
            log.event_type,
            default=AuditEventType.CLIENT_PROFILE_VIEWED,
        )
        
        processed_items.append(
            AuditLogEntry(
                id=log.id,
                created_at=log.created_at,
                os=log.os or "Other",
                browser=_browser(log.browser),
                device=_device(log.device),
                email=log.user.email if log.user else "System",
                actor=audit_actor_label(log.user_role),
                category=category_value,
                event_type=event_type_value,
                display_category=get_display_category(
                    _enum_text(log.category),
                    _enum_text(log.event_type),
                ),
                severity=log.severity,
                audit_ref=log.audit_ref,
                entity_ref=log.entity_ref,
                event=log.reason or log.action.replace("_", " ").capitalize(),
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                ip_address=log.ip_address,
                resource=resource,
                duration="< 1 min",  # Defaulting as we don't track duration yet.
            )
        )

    return ok(data=AuditLogListResponse(
        items=processed_items,
        total=total,
        page=page,
        size=size,
    ))


@router.get(
    "/{organization_id}/audit-logs/data-access/summary",
    response_model=SuccessResponse[DataAccessSummaryResponse],
)
async def get_data_access_summary(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Get summarized frequency of data access by admin (last 30d)."""
    _assert_audit_org_scope(user, organization_id)
    items = await svc.get_data_access_summary(organization_id)
    parsed_items = [DataAccessSummaryEntry.model_validate(item) for item in items]
    return ok(data=DataAccessSummaryResponse(items=parsed_items))


@router.get(
    "/{organization_id}/audit-logs/data-access/heatmap",
    response_model=SuccessResponse[DataAccessHeatmapResponse],
)
async def get_data_access_heatmap(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Get frequency heatmap (dow vs hour) for data access logs."""
    _assert_audit_org_scope(user, organization_id)
    items = await svc.get_data_access_heatmap(organization_id)
    parsed_items = [DataAccessHeatmapEntry.model_validate(item) for item in items]
    return ok(data=DataAccessHeatmapResponse(items=parsed_items))


@router.get(
    "/{organization_id}/audit-logs/change-history",
    response_model=SuccessResponse[ChangeHistoryResponse],
)
async def get_change_history(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
    page: int = 1,
    size: int = 50,
    search: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    category: list[str] = Query(None),
    entity_type: list[str] = Query(None),
    action_type: list[str] = Query(None),
    actor: str | None = None,
):
    """Get summarized history of data modifications (non-access events)."""
    _assert_audit_org_scope(user, organization_id)
    total, items = await svc.get_change_history(
        organization_id=organization_id,
        page=page,
        size=size,
        search=search,
        from_date=from_date,
        to_date=to_date,
        category=category,
        entity_type=entity_type,
        action_type=action_type,
        actor=actor,
    )
    parsed_items = [ChangeHistoryEntry.model_validate(item) for item in items]
    return ok(data=ChangeHistoryResponse(items=parsed_items, total=total, page=page, size=size))


@router.post(
    "/{organization_id}/audit-logs/compare",
    response_model=SuccessResponse[ComparisonResponse],
)
async def compare_points_in_time(
    organization_id: str,
    data: ComparisonRequest,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Compare the state of specific fields at two different points in time."""
    _assert_audit_org_scope(user, organization_id)
    results = await svc.get_point_in_time_comparison(
        organization_id=organization_id,
        snapshot_a=data.snapshot_a,
        snapshot_b=data.snapshot_b,
        fields=data.fields
    )
    parsed_results = [ComparisonResultEntry.model_validate(item) for item in results]
    return ok(data=ComparisonResponse(items=parsed_results))


@router.get(
    "/{organization_id}/audit-logs/field-history/{field}",
    response_model=SuccessResponse[FieldHistoryResponse],
)
async def get_field_history(
    organization_id: str,
    field: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    event_type: list[str] | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
):
    """Get paginated history of changes for a specific field plus a monthly trend."""
    _assert_audit_org_scope(user, organization_id)
    items, total = await svc.get_field_history(
        organization_id,
        field,
        page=page,
        size=size,
        search=search,
        event_type=event_type,
        from_date=from_date,
        to_date=to_date,
    )
    trend = await svc.get_field_history_trend(
        organization_id,
        field,
        from_date=from_date,
        to_date=to_date,
    )
    parsed_items = [FieldHistoryEntry.model_validate(item) for item in items]
    parsed_points = [FieldHistoryPoint.model_validate(p) for p in trend]
    return ok(
        data=FieldHistoryResponse(
            items=parsed_items,
            total=total,
            page=page,
            size=size,
            points=parsed_points,
        )
    )


@router.get(
    "/{organization_id}/audit-logs/trend",
    response_model=SuccessResponse[ActivityTrendResponse],
)
async def get_audit_trend(
    organization_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Get activity trend data for the chart (last 30 days)."""
    _assert_audit_org_scope(user, organization_id)
    points = await svc.get_audit_trend(organization_id)
    parsed_points = [ActivityTrendPoint.model_validate(point) for point in points]
    return ok(data=ActivityTrendResponse(points=parsed_points))


def _build_detail(log) -> AuditLogDetail:
    """Convert an ``AuditLog`` ORM row into the full ``AuditLogDetail`` payload."""
    c_str = _enum_text(log.category)
    et_str = _enum_text(log.event_type)
    category = _coerce_audit_category(log.category, default=AuditCategory.SYSTEM)
    event_type = _coerce_audit_event_type(
        log.event_type, default=AuditEventType.SYSTEM_CONFIG_CHANGED
    )
    return AuditLogDetail(
        id=log.id,
        created_at=log.created_at,
        os=log.os or "Unknown",
        browser=_browser(log.browser),
        device=_device(log.device),
        email=log.user.email if log.user else "System",
        actor=audit_actor_label(log.user_role),
        category=category,
        event_type=event_type,
        display_category=get_display_category(c_str, et_str),
        severity=log.severity,
        audit_ref=log.audit_ref,
        entity_ref=log.entity_ref,
        event=log.reason or log.action.replace(".", " ").replace("_", " ").capitalize(),
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        ip_address=log.ip_address,
        action=log.action,
        reason=log.reason,
        user_agent=log.user_agent,
        old_value=log.old_value,
        new_value=log.new_value,
        user_id=log.user_id,
        organization_id=log.organization_id,
        session_id=log.session_id,
        correlation_id=log.correlation_id,
        integrity_hash=log.integrity_hash,
        prev_hash=log.prev_hash,
    )


def _build_related(log) -> RelatedAuditEvent:
    return RelatedAuditEvent(
        id=log.id,
        audit_ref=log.audit_ref,
        created_at=log.created_at,
        event_type=_enum_text(log.event_type) or None,
        severity=log.severity,
        event=log.reason or log.action.replace(".", " ").replace("_", " ").capitalize(),
        actor=audit_actor_label(log.user_role),
        email=log.user.email if log.user else None,
    )


@router.get(
    "/{organization_id}/audit-logs/export",
    response_class=StreamingResponse,
)
async def export_audit_logs(
    organization_id: str,
    user: AuditOrgReadDep,
    request: Request,
    ctx: AuditCtxDep,
    svc: AuditQueryServiceDep,
    category: list[str] | None = Query(None),
    event_type: list[str] | None = Query(None),
    severity: list[str] | None = Query(None),
    actor: str | None = Query(None, enum=["Admin", "Client"]),
    search: str | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Stream the filtered audit log as CSV. Also writes an ``AUDIT_LOG_EXPORTED`` audit row."""
    _assert_audit_org_scope(user, organization_id)
    export_from, export_to = _resolve_export_date_range(from_date, to_date)

    audit_service = AuditService(session, request=request)
    await audit_service.log(
        action="audit_log.exported",
        entity_type="organization",
        entity_id=organization_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        session_id=ctx.session_id,
        correlation_id=ctx.correlation_id,
        organization_id=organization_id,
        category=AuditCategory.ACCESS,
        event_type=AuditEventType.AUDIT_LOG_EXPORTED,
        severity="NOTICE",
        reason=f"Audit log export by {ctx.user_role}",
        new_value={
            "filters": {
                "category": category,
                "event_type": event_type,
                "severity": severity,
                "actor": actor,
                "search": search,
                "from_date": export_from.isoformat(),
                "to_date": export_to.isoformat(),
            }
        },
    )

    async def csv_stream():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "audit_ref",
            "created_at",
            "category",
            "event_type",
            "severity",
            "actor",
            "email",
            "entity_type",
            "entity_ref",
            "entity_id",
            "action",
            "reason",
            "ip_address",
            "browser",
            "os",
            "device",
            "session_id",
            "correlation_id",
            "integrity_hash",
        ])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        row_count = 0
        async for log in svc.iter_logs_for_export(
            organization_id,
            category=category,
            event_type=event_type,
            severity=severity,
            actor=actor,
            search=search,
            from_date=export_from,
            to_date=export_to,
        ):
            row_count += 1
            if row_count > AUDIT_EXPORT_MAX_ROWS:
                raise ValidationError(
                    f"Export exceeds the maximum of {AUDIT_EXPORT_MAX_ROWS:,} rows. "
                    "Narrow your date range or filters and try again."
                )
            writer.writerow([
                log.audit_ref or "",
                log.created_at.isoformat() if log.created_at else "",
                _enum_text(log.category),
                _enum_text(log.event_type),
                log.severity,
                audit_actor_label(log.user_role),
                (log.user.email if log.user else "") if hasattr(log, "user") else "",
                log.entity_type or "",
                log.entity_ref or "",
                log.entity_id or "",
                log.action,
                log.reason or "",
                log.ip_address or "",
                log.browser or "",
                log.os or "",
                log.device or "",
                log.session_id or "",
                log.correlation_id or "",
                log.integrity_hash or "",
            ])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    filename = f"audit-log-{organization_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{organization_id}/audit-logs/{audit_log_id}/related",
    response_model=SuccessResponse[list[RelatedAuditEvent]],
)
async def get_related_audit_events(
    organization_id: str,
    audit_log_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Return audit rows that share this row's correlation_id (same HTTP request)."""
    _assert_audit_org_scope(user, organization_id)
    log = await svc.get_log_by_id(organization_id, audit_log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    if not log.correlation_id:
        return ok(data=[])
    related = await svc.get_related_events(
        organization_id, log.correlation_id, exclude_id=audit_log_id
    )
    return ok(data=[_build_related(item) for item in related])


@router.get(
    "/{organization_id}/audit-logs/{audit_log_id}/verify",
    response_model=SuccessResponse[IntegrityVerification],
)
async def verify_audit_integrity(
    organization_id: str,
    audit_log_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Recompute the SHA-256 hash for an audit row and compare to the stored value."""
    _assert_audit_org_scope(user, organization_id)
    log = await svc.get_log_by_id(organization_id, audit_log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Audit log not found")

    if not log.integrity_hash:
        return ok(data=IntegrityVerification(
            id=log.id,
            audit_ref=log.audit_ref,
            ok=False,
            expected=None,
            found=None,
            reason="No integrity hash recorded (legacy row).",
        ))

    expected = compute_integrity_hash(log.prev_hash, integrity_payload(log))
    return ok(data=IntegrityVerification(
        id=log.id,
        audit_ref=log.audit_ref,
        ok=(expected == log.integrity_hash),
        expected=expected,
        found=log.integrity_hash,
        reason=None if expected == log.integrity_hash else "Hash mismatch.",
    ))


@router.get(
    "/{organization_id}/audit-logs/{audit_log_id}",
    response_model=SuccessResponse[AuditLogDetail],
)
async def get_audit_log_detail(
    organization_id: str,
    audit_log_id: str,
    user: AuditOrgReadDep,
    svc: AuditQueryServiceDep,
):
    """Get the full audit log row (incl. before/after, raw user agent, integrity)."""
    _assert_audit_org_scope(user, organization_id)
    log = await svc.get_log_by_id(organization_id, audit_log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return ok(data=_build_detail(log))


@router.get(
    "/audit-logs/saved-views",
    response_model=SuccessResponse[list[SavedViewResponse]],
)
async def get_saved_views(
    _user: AuditSavedViewsReadDep,
    user: CurrentUserDep,
    svc: AuditQueryServiceDep,
):
    """List all saved filter views for the logged-in user."""
    views = await svc.get_saved_views(user_id=user.id)
    return ok(data=views)


@router.post(
    "/audit-logs/saved-views",
    response_model=SuccessResponse[SavedViewResponse],
    status_code=201,
)
async def create_saved_view(
    data: SavedViewCreate,
    _user: AuditSavedViewsReadDep,
    user: CurrentUserDep,
    svc: AuditQueryServiceDep,
):
    """Save a new filter configuration as a global user view."""
    # organization_id is now optional and defaulted to None for global user views
    view = await svc.create_saved_view(user.id, data.model_dump())
    return ok(data=view)


@router.delete(
    "/audit-logs/saved-views/{view_id}",
)
async def delete_saved_view(
    view_id: str,
    _user: AuditSavedViewsReadDep,
    user: CurrentUserDep,
    svc: AuditQueryServiceDep,
):
    """Delete a saved filter view."""
    success = await svc.delete_saved_view(view_id, user_id=user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Saved view not found or access denied")
    return {"success": True}
