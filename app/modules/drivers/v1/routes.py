"""Driver management routes — list, get, create, update, soft-delete.

All endpoints require authentication. List and get require Resource.DRIVERS READ;
create, update, and delete require Resource.DRIVERS WRITE.
"""

import json as _json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, Response, UploadFile

from app.common.deps import Allowed, AuthUser, DriverDocAccessDep, SessionDep
from app.common.enums import UserRole, UserStatus
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import NotFoundError, ValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, PaginatedResponse, SuccessResponse
from app.core.rate_limit import DOC_OTP_VERIFY_RATE_LIMIT, DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.core.security import generate_secure_password
from app.modules.auth.service import AuthService
from app.modules.auth.v1.schemas import SupportIssuePasswordRequest, SupportIssuePasswordResponse
from app.modules.drivers.enums import (
    CalendarEventSource,
    DriverAccountStatus,
    DriverCapacity,
    DriverDocumentKind,
    DriverDocumentStatus,
    DriverLiveStatus,
    DriverType,
    ShiftStatus,
    TimeOffType,
    TrafficViolationStatus,
    TrafficViolationType,
)
from app.modules.drivers.models import DriverDocument, DriverTrafficViolation
from app.modules.drivers.service import DriverService
from app.modules.planning.enums import RouteStatus, RouteType
from app.modules.drivers.v1.docs import (
    CREATE_DRIVER_TERMS,
    CREATE_DRIVER_DRAFT,
    GET_DRIVER_ACTIVITY_LOG,
    GET_DRIVER_ACTIVITY_LOG_DETAIL,
    CREATE_DRIVER_WITH_USER,
    SEND_DRIVER_DOC_OTP,
    SUPPORT_ISSUE_DRIVER_PASSWORD,
    VERIFY_DRIVER_DOC_OTP,
    DELETE_DRIVER,
    DELETE_DRIVER_DRAFT,
    DOCUMENT_DELETE,
    DOCUMENT_GET_FULL,
    DOCUMENT_UPDATE,
    DOCUMENTS_LIST,
    DOCUMENTS_MUTATE,
    DRAFT_DOCUMENT_DELETE,
    DRAFT_DOCUMENT_GET_FULL,
    DRAFT_DOCUMENT_UPDATE,
    DRAFT_DOCUMENTS_LIST,
    DRAFT_DOCUMENTS_MUTATE,
    GET_DRIVER_DRAFT,
    GET_DRIVER,
    GET_DRIVER_CONFIGURATION,
    GET_DRIVER_FULL,
    GET_DRIVER_KPIS,
    DRIVER_SCHEDULE_AVAILABILITY_CALENDAR,
    GET_ROUTE_SUMMARY,
    LIST_DRIVER_ROUTE_HISTORY,
    LIST_DRIVERS,
    LIST_DRIVER_DRAFTS,
    LIST_DRIVER_TERMS,
    LIST_ROUTE_TELEMATICS,
    PASSWORD_RESET_DRIVER,
    PASSWORD_RESET_DRIVER_REQUEST_BODY,
    PROFILE_PHOTO_DELETE,
    RESEND_DRIVER_CREDENTIALS,
    REACTIVATE_DRIVER,
    SUBMIT_DRIVER_DRAFT,
    SCHEDULE_GET,
    SCHEDULE_UPDATE,
    SCHEDULE_UPDATE_DAY,
    SHIFT_DELETE,
    SHIFT_GET_FULL,
    SHIFT_UPDATE,
    SHIFTS_LIST,
    SHIFTS_MUTATE,
    SUSPEND_DRIVER,
    TIME_OFF_DELETE,
    TIME_OFF_GET_FULL,
    TIME_OFF_LIST,
    TIME_OFF_MUTATE,
    TIME_OFF_UPDATE,
    TRAFFIC_VIOLATION_DELETE,
    TRAFFIC_VIOLATION_DELETE_PROOF,
    TRAFFIC_VIOLATION_GET_FULL,
    TRAFFIC_VIOLATION_ADD_PROOFS,
    TRAFFIC_VIOLATION_UPDATE,
    TRAFFIC_VIOLATIONS_LIST,
    TRAFFIC_VIOLATIONS_MUTATE,
    PATCH_DRIVER_CONFIGURATION,
    UPDATE_DRIVER,
    UPDATE_DRIVER_DRAFT,
    UPDATE_DRIVER_FORM,
    UPDATE_DRIVER_TERMS,
)
from app.modules.audit.repository import AuditRepository
from app.modules.drivers.activity_display import (
    activity_event_label,
    activity_user_type_badge,
    actor_email,
    audit_category_str,
    audit_event_type_str,
    parse_os_from_user_agent,
    redact_audit_json,
)
from app.modules.drivers.v1.schemas import (
    AdminDriverPasswordChangeRequest,
    DriverActivityLogDetailResponse,
    DriverActivityLogListItem,
    DriverActivityLogListResponse,
    DriverDetailResponse,
    DriverOperationalConfigurationResponse,
    DriverOperationalConfigurationUpdateRequest,
    DriverDocAccessTokenResponse,
    DriverDocOTPSendResponse,
    DriverDocumentCreateRequest,
    DriverDocumentResponse,
    DriverDocumentResult,
    DriverDocumentsListResponse,
    DriverFullProfileResponse,
    DriverDraftListResponse,
    DriverDraftListEntry,
    DriverDraftUpdateRequest,
    DriverDraftUpsertResponse,
    DriverKpis,
    DriverListEntry,
    DriverListResponse,
    OnboardDrivingLicenceDocumentMeta,
    DriverShiftEntry,
    DriverShiftListResponse,
    DriverCalendarResponse,
    DriverTimeOffEntry,
    DriverTimeOffListResponse,
    DriverUpdateRequest,
    DriverUserBrief,
    DriverWithUserCreateResponse,
    DriverTermsAndConditionsCreateRequest,
    DriverTermsAndConditionsListResponse,
    DriverTermsAndConditionsResponse,
    DriverTermsAndConditionsUpdateRequest,
    ReactivateDriverRequest,
    SuspendDriverRequest,
    TrafficViolationEntry,
    TrafficViolationListResponse,
    TrafficViolationProofEntry,
    TrafficViolationProofUploadResult,
    TrafficViolationUpsertResponse,
    WeeklyScheduleDay,
    WeeklyScheduleResponse,
    RouteEventEntry,
    RouteEventsResponse,
    RouteHistoryResponse,
    RouteHistoryRow,
    RouteProgress,
    RouteSummaryResponse,
    RouteStopSummary,
)
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.doc_access_service import DocAccessServiceDep
from app.modules.organizations.v1.schemas import DocOTPVerifyRequest
from app.storage.upload import validate_document


router = APIRouter()
# add-new-driver: at most one driving licence file; custom docs use POST /{driver_id}/documents.
MAX_ONBOARD_DRIVING_LICENCE_FILES = 1

DriverServiceDep = Annotated[DriverService, Depends(DriverService.dep)]
AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]

# RBAC: READ for list/get, WRITE for create/update/delete
DriverReadDep = Annotated[AuthUser, Allowed(resource=Resource.DRIVERS, level=PermissionLevel.READ)]
DriverWriteDep = Annotated[AuthUser, Allowed(resource=Resource.DRIVERS, level=PermissionLevel.WRITE)]

DriverSupportPasswordDep = Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.DRIVERS, level=PermissionLevel.WRITE)]

def _driver_draft_data_dict(driver) -> dict[str, object]:
    """Return `driver_drafts.draft_data` for this driver, or `{}` if there is no pivot row."""
    row = getattr(driver, "draft", None)
    if row is None:
        return {}
    return dict(getattr(row, "draft_data", None) or {})


def _draft_identity_strings_from_data(draft_data: dict[str, object]) -> tuple[str | None, str | None, str | None, str | None]:
    """Normalize identity fields from draft JSONB: trim strings; email lowercased."""

    def _one(key: str) -> str | None:
        v = draft_data.get(key)
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return str(v)

    em = _one("email")
    if em is not None:
        em = em.lower()
    return em, _one("first_name"), _one("last_name"), _one("phone")


def _merge_draft_identity_with_user(
    draft_data: dict[str, object],
    user,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Prefer normalized draft_data identity, then linked `users` row (draft list semantics)."""
    de, df, dl, dp = _draft_identity_strings_from_data(draft_data)
    return (
        de or getattr(user, "email", None),
        df or getattr(user, "first_name", None),
        dl or getattr(user, "last_name", None),
        dp or getattr(user, "phone", None),
    )


def _draft_overlay_str(
    draft_data: dict[str, object],
    *,
    overlay_jsonb: bool,
    key: str,
    column_val: str | None,
) -> str | None:
    """When overlaying JSONB for unlinked drafts, match detail hydration (non-empty wins)."""
    if not overlay_jsonb:
        return column_val
    v = draft_data.get(key)
    if v is None or v == "":
        return column_val
    return str(v)


def _coerce_driver_type_enum(raw: object | None) -> DriverType | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, DriverType):
        return raw
    try:
        return DriverType(str(raw))
    except ValueError:
        return None


def _coerce_max_stops_int(raw_ms: object | None, *, fallback: int | None) -> int | None:
    if raw_ms is None:
        return fallback
    if isinstance(raw_ms, bool):
        return fallback
    if isinstance(raw_ms, int):
        return raw_ms
    if isinstance(raw_ms, str):
        try:
            return int(raw_ms.strip())
        except ValueError:
            return fallback
    try:
        return int(str(raw_ms))
    except (TypeError, ValueError):
        return fallback


def _coerce_layover_cost_decimal(raw: object | None, *, fallback: Decimal) -> Decimal:
    if raw is None:
        return fallback
    if isinstance(raw, Decimal):
        d = raw
    else:
        try:
            d = Decimal(str(raw).strip())
        except Exception:
            return fallback
    if d < 0:
        return fallback
    return d


def _coerce_max_layover_nights_overlay(raw: object | None, *, fallback: int) -> int:
    if raw is None:
        return fallback
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, str):
        try:
            n = int(raw.strip())
        except ValueError:
            return fallback
    else:
        try:
            n = int(str(raw))
        except (TypeError, ValueError):
            return fallback
    return max(0, min(366, n))


def _overlay_layover_from_draft(
    draft_data: dict[str, object],
    *,
    overlay_draft_jsonb: bool,
    okay_column: bool,
    cost_column: Decimal,
    nights_column: int,
) -> tuple[bool, Decimal, int]:
    """For unlinked drafts, prefer operational layover keys from draft JSONB when present."""
    if not overlay_draft_jsonb:
        return okay_column, cost_column, nights_column
    ok_v = okay_column
    if "okay_with_layover" in draft_data:
        ok_v = bool(draft_data["okay_with_layover"])
    cost_v = cost_column
    if "layover_cost_per_night" in draft_data:
        cost_v = _coerce_layover_cost_decimal(draft_data.get("layover_cost_per_night"), fallback=cost_v)
    nights_v = nights_column
    if "max_layover_nights" in draft_data:
        nights_v = _coerce_max_layover_nights_overlay(draft_data.get("max_layover_nights"), fallback=nights_v)
    return ok_v, cost_v, nights_v


def _to_list_entry(driver) -> DriverListEntry:
    u = getattr(driver, "user", None)
    raw_capacities = list(getattr(driver, "capacities", None) or [])
    if not raw_capacities:
        capacities: list[DriverCapacity] = [DriverCapacity.VAN]
    else:
        capacities = [DriverCapacity(c) for c in raw_capacities]
    return DriverListEntry(
        id=driver.id,
        user_id=driver.user_id,
        driver_code=driver.driver_code,
        first_name=getattr(u, "first_name", ""),
        last_name=getattr(u, "last_name", ""),
        phone=getattr(u, "phone", None),
        capacities=capacities,
        account_status=driver.account_status,
        live_status=driver.live_status,
        safety_score=driver.safety_score,
        created_at=driver.created_at,
        updated_at=driver.updated_at,
        version=driver.version,
    )


def _to_draft_list_entry(driver) -> DriverDraftListEntry:
    u = getattr(driver, "user", None)
    d = getattr(driver, "draft", None)
    draft_data = _driver_draft_data_dict(driver)
    overlay_jsonb = driver.user_id is None
    raw_capacities = list(getattr(driver, "capacities", None) or [])
    if overlay_jsonb:
        capacities_from_json = draft_data.get("capacities")
        if isinstance(capacities_from_json, list) and capacities_from_json:
            capacities = [DriverCapacity(c) for c in capacities_from_json]
        elif raw_capacities:
            capacities = [DriverCapacity(c) for c in raw_capacities]
        else:
            capacities = [DriverCapacity.VAN]
    elif not raw_capacities:
        capacities = [DriverCapacity.VAN]
    else:
        capacities = [DriverCapacity(c) for c in raw_capacities]

    list_email, list_fn, list_ln, list_ph = _merge_draft_identity_with_user(draft_data, u)

    driver_type_raw = (
        (draft_data.get("driver_type") or driver.driver_type) if overlay_jsonb else driver.driver_type
    )
    driver_type_val = _coerce_driver_type_enum(driver_type_raw)

    return DriverDraftListEntry(
        id=driver.id,
        user_id=driver.user_id,
        driver_code=driver.driver_code,
        draft_id=getattr(d, "draft_id", None) if d is not None else None,
        draft_created_by=getattr(d, "created_by", None) if d is not None else None,
        draft_created_at=getattr(d, "created_at", None) if d is not None else None,
        draft_updated_at=getattr(d, "updated_at", None) if d is not None else None,
        is_submitted=bool(getattr(d, "is_submitted", False)) if d is not None else False,
        email=list_email,
        first_name=list_fn,
        last_name=list_ln,
        phone=list_ph,
        capacities=capacities,
        driver_type=driver_type_val,
        country=_draft_overlay_str(draft_data, overlay_jsonb=overlay_jsonb, key="country", column_val=driver.country),
        state=_draft_overlay_str(draft_data, overlay_jsonb=overlay_jsonb, key="state", column_val=driver.state),
        city=_draft_overlay_str(draft_data, overlay_jsonb=overlay_jsonb, key="city", column_val=driver.city),
        postcode=_draft_overlay_str(draft_data, overlay_jsonb=overlay_jsonb, key="postcode", column_val=driver.postcode),
        account_status=driver.account_status,
        live_status=driver.live_status,
        safety_score=driver.safety_score,
        created_at=driver.created_at,
        updated_at=driver.updated_at,
        version=driver.version,
    )


def _to_detail_response(driver, driver_service) -> DriverDetailResponse:
    draft_data = _driver_draft_data_dict(driver)
    # Until submit, profile + identity for drafts live in `driver_drafts.draft_data` (and may
    # not be mirrored on `drivers` columns). After link, use ORM only so JSONB cannot override.
    overlay_draft_jsonb = driver.user_id is None

    u = getattr(driver, "user", None)
    user_brief = None
    if u is not None:
        user_brief = DriverUserBrief(
            id=u.id,
            email=u.email,
            first_name=getattr(u, "first_name", None),
            last_name=getattr(u, "last_name", None),
            phone=getattr(u, "phone", None),
        )
    else:
        # Drafts have no linked user row; identity is stored in draft JSONB (same normalization as draft list).
        draft_email_norm, draft_fn, draft_ln, draft_ph = _draft_identity_strings_from_data(draft_data)
        if draft_email_norm or draft_fn or draft_ln or draft_ph:
            user_brief = DriverUserBrief(
                # There is no real `users.id` yet; use driver id as a stable client-side reference.
                id=driver.id,
                email=draft_email_norm,
                first_name=draft_fn,
                last_name=draft_ln,
                phone=draft_ph,
            )
    profile_photo_url = driver_service.get_profile_photo_url(driver.profile_photo_key)
    raw_capacities = list(getattr(driver, "capacities", None) or [])
    if overlay_draft_jsonb:
        capacities_from_json = draft_data.get("capacities")
        if isinstance(capacities_from_json, list) and capacities_from_json:
            capacities = [DriverCapacity(c) for c in capacities_from_json]
        elif raw_capacities:
            capacities = [DriverCapacity(c) for c in raw_capacities]
        else:
            capacities = [DriverCapacity.VAN]
    elif not raw_capacities:
        capacities = [DriverCapacity.VAN]
    else:
        capacities = [DriverCapacity(c) for c in raw_capacities]

    def _from_draft_str(key: str, column_val: str | None) -> str | None:
        return _draft_overlay_str(
            draft_data, overlay_jsonb=overlay_draft_jsonb, key=key, column_val=column_val
        )

    max_stops_val = driver.max_stops
    if overlay_draft_jsonb and "max_stops" in draft_data:
        max_stops_val = _coerce_max_stops_int(draft_data["max_stops"], fallback=driver.max_stops)
    if max_stops_val is None:
        max_stops_val = 30

    driver_type_raw = (
        (draft_data.get("driver_type") or driver.driver_type) if overlay_draft_jsonb else driver.driver_type
    )
    driver_type_val = _coerce_driver_type_enum(driver_type_raw)

    territory_tags_val = driver.territory_tags
    if overlay_draft_jsonb and "territory_tags" in draft_data:
        tg = draft_data["territory_tags"]
        if isinstance(tg, list):
            territory_tags_val = [str(x) for x in tg]

    notes_val = _from_draft_str("notes", driver.notes) if overlay_draft_jsonb else driver.notes

    lc_raw = getattr(driver, "layover_cost_per_night", None)
    layover_dec = Decimal(str(lc_raw)) if lc_raw is not None else Decimal("0")
    okay_lo = bool(getattr(driver, "okay_with_layover", False))
    max_lo_nights = int(getattr(driver, "max_layover_nights", 0) or 0)
    okay_lo, layover_dec, max_lo_nights = _overlay_layover_from_draft(
        draft_data,
        overlay_draft_jsonb=overlay_draft_jsonb,
        okay_column=okay_lo,
        cost_column=layover_dec,
        nights_column=max_lo_nights,
    )
    return DriverDetailResponse(
        id=driver.id,
        user_id=driver.user_id,
        driver_code=driver.driver_code,
        user=user_brief,
        depot_id=_from_draft_str("depot_id", driver.depot_id),
        vehicle_id=_from_draft_str("vehicle_id", driver.vehicle_id),
        address_line1=_from_draft_str("address_line1", driver.address_line1),
        address_line2=_from_draft_str("address_line2", driver.address_line2),
        city=_from_draft_str("city", driver.city),
        postcode=_from_draft_str("postcode", driver.postcode),
        capacities=capacities,
        driver_type=driver_type_val,
        country=_from_draft_str("country", driver.country),
        state=_from_draft_str("state", driver.state),
        license_number=_from_draft_str("license_number", driver.license_number),
        license_category=_from_draft_str("license_category", driver.license_category),
        max_stops=max_stops_val,
        territory_tags=territory_tags_val,
        account_status=driver.account_status,
        live_status=driver.live_status,
        safety_score=driver.safety_score,
        on_time_deliveries=driver.on_time_deliveries,
        notes=notes_val,
        okay_with_layover=okay_lo,
        layover_cost_per_night=layover_dec,
        max_layover_nights=max_lo_nights,
        profile_photo_url=profile_photo_url,
        created_at=driver.created_at,
        updated_at=driver.updated_at,
        version=driver.version,
    )


def _to_operational_configuration_response(driver) -> DriverOperationalConfigurationResponse:
    draft_data = _driver_draft_data_dict(driver)
    overlay_draft_jsonb = driver.user_id is None
    lc = getattr(driver, "layover_cost_per_night", None)
    cost = Decimal(str(lc)) if lc is not None else Decimal("0")
    okay_lo, cost, max_nights = _overlay_layover_from_draft(
        draft_data,
        overlay_draft_jsonb=overlay_draft_jsonb,
        okay_column=bool(getattr(driver, "okay_with_layover", False)),
        cost_column=cost,
        nights_column=int(getattr(driver, "max_layover_nights", 0) or 0),
    )
    return DriverOperationalConfigurationResponse(
        okay_with_layover=okay_lo,
        layover_cost_per_night=cost,
        max_layover_nights=max_nights,
    )


def _to_document_response(doc: DriverDocument, file_url: str | None, status: DriverDocumentStatus) -> DriverDocumentResponse:
    kind_enum = DriverDocumentKind(doc.kind)
    title = doc.title
    if title is None and kind_enum is not DriverDocumentKind.CUSTOM:
        title = kind_enum.to_display_title()
    return DriverDocumentResponse(
        id=doc.id,
        driver_id=doc.driver_id,
        document_type=kind_enum,
        title=title,
        file_url=file_url,
        expiry_date=doc.expiry_date,
        status=status,
    )


async def _detail_with_driver_documents(
    driver,
    driver_service: DriverService,
    *,
    presign_compliance_file_urls: bool = False,
) -> DriverDetailResponse:
    """Driver detail plus compliance documents.

    When ``presign_compliance_file_urls`` is False (e.g. ``GET /v1/drivers/{id}``), ``file_url`` is always
    None — use document APIs + OTP for downloads. Draft upsert/read endpoints pass True so admins get
    presigned URLs without the step-up header.
    """
    detail = _to_detail_response(driver, driver_service)
    docs = await driver_service.list_driver_documents(driver.id)
    doc_items: list[DriverDocumentResponse] = []
    for doc in docs:
        status = driver_service.compute_document_status(doc.expiry_date)
        file_url: str | None
        if presign_compliance_file_urls:
            file_url = driver_service.get_file_url(doc.file_key, content_type=doc.content_type)
        else:
            file_url = None
        doc_items.append(_to_document_response(doc, file_url=file_url, status=status))
    return detail.model_copy(update={"documents": DriverDocumentsListResponse(items=doc_items)})


async def _detail_without_documents(
    driver,
    driver_service: DriverService,
) -> DriverDetailResponse:
    """Driver detail without documents (for draft GET, to separate doc access)."""
    detail = _to_detail_response(driver, driver_service)
    return detail.model_copy(update={"documents": DriverDocumentsListResponse(items=[])})


def _to_proof_upload_result(
    result: dict[str, object],
    proofs_by_id: dict[str, TrafficViolationProofEntry],
) -> TrafficViolationProofUploadResult:
    """Map raw service proof upload result into typed response schema."""
    proof_id = result.get("proof_id")
    return TrafficViolationProofUploadResult(
        index=int(cast(int | str, result["index"])),
        filename=str(result["filename"]),
        status=cast(Literal["success", "failed"], str(result["status"])),
        error=(str(result["error"]) if result.get("error") else None),
        proof=(proofs_by_id.get(str(proof_id)) if proof_id else None),
    )


# ── Driver document access OTP (register before /{driver_id} routes) ───────────


@router.post(
    "/documents/otp/send",
    response_model=SuccessResponse[DriverDocOTPSendResponse],
    **SEND_DRIVER_DOC_OTP,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def send_driver_doc_otp(
    request: Request,
    response: Response,
    user: DriverWriteDep,
    session: SessionDep,
    service: DocAccessServiceDep,
) -> dict:
    """Request OTP for driver compliance document endpoints (email step-up).

    See OpenAPI **Request a driver document access OTP** for limits, scope (`DRIVER_DOCUMENTS`),
    and required `Resource.DRIVERS` WRITE permission.
    """
    from sqlalchemy import select as sa_select

    from app.modules.user.models import User

    stmt = sa_select(User.email, User.first_name, User.last_name).where(User.id == user.id)
    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        raise NotFoundError(resource="User account")

    user_email, first_name, last_name = row
    user_name = f"{first_name or ''} {last_name or ''}".strip() or user_email

    await service.send_otp(
        user_id=user.id,
        user_email=user_email,
        user_name=user_name,
        access_scope=DocAccessScope.DRIVER_DOCUMENTS,
    )
    return ok(DriverDocOTPSendResponse())


@router.post(
    "/documents/otp/verify",
    response_model=SuccessResponse[DriverDocAccessTokenResponse],
    **VERIFY_DRIVER_DOC_OTP,
)
@limiter.limit(DOC_OTP_VERIFY_RATE_LIMIT)
async def verify_driver_doc_otp(
    request: Request,
    response: Response,
    body: DocOTPVerifyRequest,
    user: DriverWriteDep,
    service: DocAccessServiceDep,
) -> dict:
    """Verify OTP and return a 1-hour token for `X-Driver-Doc-Access-Token`.

    Only OTPs created for **driver** document scope are accepted; organisation OTP codes are rejected.
    """
    result = await service.verify_otp(
        user_id=user.id,
        otp_code=body.otp,
        access_scope=DocAccessScope.DRIVER_DOCUMENTS,
    )
    raw = result["doc_access_token"]
    token_preview = raw[:8]
    return ok(
        DriverDocAccessTokenResponse(
            driver_doc_access_token=raw,
            expires_in=result["expires_in"],
            expires_at=result["expires_at"],
            message=(
                f"OTP verified. Use `X-Driver-Doc-Access-Token: {token_preview}...` "
                "on all `/v1/drivers/.../documents` compliance routes (list, get, upload, update, delete). Valid for 1 hour."
            ),
        )
    )


@router.get(
    "",
    response_model=SuccessResponse[DriverListResponse],
    **LIST_DRIVERS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_drivers(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    account_status: Annotated[
        list[DriverAccountStatus] | None,
        Query(description="Account status filter (multi-select)"),
    ] = None,
    live_status: Annotated[
        list[DriverLiveStatus] | None,
        Query(description="Live status filter (multi-select)"),
    ] = None,
    depot_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    order_by: Annotated[str | None, Query()] = "created_at",
    order_desc: Annotated[bool, Query()] = True,
) -> dict:
    """List drivers with pagination, search, filters, and KPIs."""
    account_status_values = [s.value for s in (account_status or [])]
    live_status_values = [s.value for s in (live_status or [])]
    items, total = await driver_service.list_drivers(
        page=page,
        size=size,
        account_status=account_status_values or None,
        live_status=live_status_values or None,
        depot_id=depot_id,
        search=search,
        order_by=order_by,
        order_desc=order_desc,
    )

    # KPIs — total_employed is the default-unfiltered list count (drafts/unlinked excluded), not affected by query filters.
    total_employed = await driver_service._driver_repo.count_drivers_default_admin_list_total()  # type: ignore[attr-defined]
    active_now = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.ACTIVE)  # type: ignore[attr-defined]
    suspended = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.SUSPENDED)  # type: ignore[attr-defined]
    pending_activation = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.PENDING_ACTIVATION)  # type: ignore[attr-defined]

    kpis = DriverKpis(
        total_employed=total_employed,
        active_now=active_now,
        suspended=suspended,
        pending_activation=pending_activation,
    )
    table = PaginatedResponse.create(
        items=[_to_list_entry(d) for d in items],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverListResponse(kpis=kpis, table=table))


@router.get(
    "/drafts",
    response_model=SuccessResponse[DriverDraftListResponse],
    **LIST_DRIVER_DRAFTS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_driver_drafts(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    depot_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    order_by: Annotated[str | None, Query()] = "created_at",
    order_desc: Annotated[bool, Query()] = True,
) -> dict:
    """List only drivers in DRAFT status."""
    items, total = await driver_service.list_driver_drafts(
        page=page,
        size=size,
        depot_id=depot_id,
        search=search,
        order_by=order_by,
        order_desc=order_desc,
    )
    table = PaginatedResponse.create(
        items=[_to_draft_list_entry(d) for d in items],
        total=total,
        page=page,
        size=size,
    )
    return ok(data=DriverDraftListResponse(table=table))


@router.get(
    "/drafts/{driver_id}",
    response_model=SuccessResponse[DriverDraftUpsertResponse],
    **GET_DRIVER_DRAFT,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_draft(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get driver draft snapshot by driver_id (works for submitted and non-submitted drafts)."""
    draft_row = await driver_service.get_driver_draft_row(driver_id)
    if draft_row is None:
        raise NotFoundError(resource="driver_draft", id=driver_id)
    driver = await driver_service.get_driver(driver_id)
    return ok(
        data=DriverDraftUpsertResponse(
            draft_id=draft_row.draft_id,
            driver=await _detail_with_driver_documents(
                driver,
                driver_service,
                presign_compliance_file_urls=False,
            ),
        )
    )


@router.post(
    "/drafts",
    response_model=SuccessResponse[DriverDraftUpsertResponse],
    status_code=201,
    **CREATE_DRIVER_DRAFT,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_driver_draft(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    email: Annotated[str | None, Form()] = None,
    first_name: Annotated[str | None, Form()] = None,
    last_name: Annotated[str | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    driver_type: Annotated[str | None, Form(description="DriverType enum value")] = None,
    address_line1: Annotated[str | None, Form()] = None,
    address_line2: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    state: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    postcode: Annotated[str | None, Form()] = None,
    latitude: Annotated[float | None, Form()] = None,
    longitude: Annotated[float | None, Form()] = None,
    depot_id: Annotated[str | None, Form()] = None,
    vehicle_id: Annotated[str | None, Form()] = None,
    license_number: Annotated[str | None, Form()] = None,
    license_category: Annotated[str | None, Form()] = None,
    max_stops: Annotated[int | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    okay_with_layover: Annotated[bool | None, Form()] = None,
    layover_cost_per_night: Annotated[str | None, Form()] = None,
    max_layover_nights: Annotated[int | None, Form()] = None,
    profile_photo: Annotated[
        UploadFile | None,
        File(description="Optional profile photo (JPEG/PNG, max 5 MB); stored via Cloudflare Images."),
    ] = None,
    document_uploads: list[UploadFile] | None = File(
        None,
        alias="documents",
        description="Optional driving licence document on draft save (max 1 file).",
    ),
    documents_metadata: str | None = Form(
        None,
        description=(
            "Optional JSON array for `documents` (index-aligned). "
            "When provided, only DRIVING_LICENCE is accepted."
        ),
    ),
) -> dict:
    """Create a driver draft (multipart; optional documents/profile photo)."""
    form = await request.form()
    indexed_capacity: dict[int, str] = {}
    for key, value in form.multi_items():
        match = re.fullmatch(r"capacity\[(\d+)\]", str(key))
        if match:
            indexed_capacity[int(match.group(1))] = str(value)
    capacities = [indexed_capacity[i] for i in sorted(indexed_capacity.keys())] if indexed_capacity else None
    if capacities is not None:
        capacities = list(dict.fromkeys(str(item) for item in capacities if item))

    has_any_field = any(
        value is not None
        for value in (
            email,
            first_name,
            last_name,
            phone,
            capacities,
            driver_type,
            address_line1,
            address_line2,
            country,
            state,
            city,
            postcode,
            latitude,
            longitude,
            depot_id,
            vehicle_id,
            license_number,
            license_category,
            max_stops,
            notes,
            okay_with_layover,
            layover_cost_per_night,
            max_layover_nights,
        )
    )
    has_file_mutation = profile_photo is not None or bool(document_uploads)
    if not has_any_field and not has_file_mutation:
        raise ValidationError("At least one field or file is required to save a draft")

    layover_dec: Decimal | None = None
    if layover_cost_per_night is not None:
        try:
            layover_dec = Decimal(str(layover_cost_per_night).strip())
        except Exception as exc:
            raise ValidationError("layover_cost_per_night must be a valid decimal amount") from exc
        if layover_dec < 0:
            raise ValidationError("layover_cost_per_night must be >= 0")
    if max_layover_nights is not None and (max_layover_nights < 0 or max_layover_nights > 366):
        raise ValidationError("max_layover_nights must be between 0 and 366 inclusive")

    driver = await driver_service.create_driver_draft(
        capacities=capacities,
        driver_type=driver_type,
        address_line1=address_line1,
        address_line2=address_line2,
        country=country,
        state=state,
        city=city,
        postcode=postcode,
        latitude=latitude,
        longitude=longitude,
        depot_id=depot_id,
        vehicle_id=vehicle_id,
        license_number=license_number,
        license_category=license_category,
        max_stops=max_stops,
        territory_tags=None,
        notes=notes,
        created_by=user.id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    if okay_with_layover is not None or layover_dec is not None or max_layover_nights is not None:
        await driver_service.update_driver(
            driver.id,
            expected_version=driver.version,
            audit_user_id=user.id,
            audit_user_role=user.role,
            okay_with_layover=okay_with_layover,
            layover_cost_per_night=layover_dec,
            max_layover_nights=max_layover_nights,
        )
    if profile_photo is not None:
        await driver_service.update_profile_photo(
            driver.id,
            profile_photo,
            audit_user_id=user.id,
            audit_user_role=user.role,
        )

    # Persist draft UI state (identity + form fields) into driver_drafts.draft_data JSONB.
    draft_payload: dict[str, object] = {}
    if email is not None:
        draft_payload["email"] = email.strip().lower()
    if first_name is not None:
        draft_payload["first_name"] = first_name
    if last_name is not None:
        draft_payload["last_name"] = last_name
    if phone is not None:
        draft_payload["phone"] = phone
    if capacities is not None:
        draft_payload["capacities"] = capacities
    if driver_type is not None:
        draft_payload["driver_type"] = driver_type
    if address_line1 is not None:
        draft_payload["address_line1"] = address_line1
    if address_line2 is not None:
        draft_payload["address_line2"] = address_line2
    if country is not None:
        draft_payload["country"] = country
    if state is not None:
        draft_payload["state"] = state
    if city is not None:
        draft_payload["city"] = city
    if postcode is not None:
        draft_payload["postcode"] = postcode
    if latitude is not None:
        draft_payload["latitude"] = latitude
    if longitude is not None:
        draft_payload["longitude"] = longitude
    if depot_id is not None:
        draft_payload["depot_id"] = depot_id
    if vehicle_id is not None:
        draft_payload["vehicle_id"] = vehicle_id
    if license_number is not None:
        draft_payload["license_number"] = license_number
    if license_category is not None:
        draft_payload["license_category"] = license_category
    if max_stops is not None:
        draft_payload["max_stops"] = max_stops
    if notes is not None:
        draft_payload["notes"] = notes
    if okay_with_layover is not None:
        draft_payload["okay_with_layover"] = okay_with_layover
    if layover_dec is not None:
        draft_payload["layover_cost_per_night"] = str(layover_dec)
    if max_layover_nights is not None:
        draft_payload["max_layover_nights"] = max_layover_nights
    if draft_payload:
        await driver_service.merge_driver_draft_data(driver_id=driver.id, incoming=draft_payload, created_by=user.id)

    docs = list(document_uploads or [])
    if docs and documents_metadata is None:
        raise ValidationError("documents_metadata is required when documents are provided")
    if (not docs) and documents_metadata is not None:
        raise ValidationError("documents must be provided when documents_metadata is set")
    if docs:
        if len(docs) > MAX_ONBOARD_DRIVING_LICENCE_FILES:
            raise ValidationError(f"At most {MAX_ONBOARD_DRIVING_LICENCE_FILES} driving licence document can be attached to draft")
        try:
            raw_meta = _json.loads(str(documents_metadata).strip())
        except _json.JSONDecodeError as err:
            raise ValidationError("documents_metadata must be valid JSON") from err
        if not isinstance(raw_meta, list) or len(raw_meta) != len(docs):
            raise ValidationError(f"documents_metadata length ({len(raw_meta) if isinstance(raw_meta, list) else 0}) must match documents count ({len(docs)})")
        parsed_meta: list[OnboardDrivingLicenceDocumentMeta] = []
        for idx, item in enumerate(raw_meta):
            try:
                parsed_meta.append(OnboardDrivingLicenceDocumentMeta.model_validate(item))
            except Exception as exc:
                raise ValidationError(f"Invalid documents_metadata at index {idx}: {exc}") from exc
        # Upsert optional driving licence on draft.
        existing_docs = await driver_service.list_driver_documents(driver.id)
        licence = next((d for d in existing_docs if d.kind == DriverDocumentKind.DRIVING_LICENCE.value), None)
        await validate_document(docs[0])
        if licence is None:
            await driver_service.create_driver_document(
                driver_id=driver.id,
                kind=DriverDocumentKind.DRIVING_LICENCE.value,
                title=None,
                expiry_date=parsed_meta[0].expiry_date,
                upload=docs[0],
                is_initial=True,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
        else:
            await driver_service.update_driver_document(
                document_id=licence.id,
                title=None,
                expiry_date=parsed_meta[0].expiry_date,
                upload=docs[0],
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
    driver = await driver_service.get_driver(driver.id)
    draft_row = await driver_service.get_driver_draft_row(driver.id)
    if draft_row is None:
        # Should not happen, but keep response stable.
        draft_row = await driver_service.ensure_driver_draft_row(driver_id=driver.id, created_by=user.id)
    return ok(
        data=DriverDraftUpsertResponse(
            draft_id=draft_row.draft_id,
            driver=await _detail_with_driver_documents(
                driver, driver_service, presign_compliance_file_urls=True
            ),
        ),
        message="Driver draft created",
    )


@router.patch(
    "/drafts/{driver_id}",
    response_model=SuccessResponse[DriverDraftUpsertResponse],
    **UPDATE_DRIVER_DRAFT,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_driver_draft(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    first_name: Annotated[str | None, Form()] = None,
    last_name: Annotated[str | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    email: Annotated[str | None, Form()] = None,
    driver_type: Annotated[str | None, Form(description="DriverType enum value")] = None,
    address_line1: Annotated[str | None, Form()] = None,
    address_line2: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    state: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    postcode: Annotated[str | None, Form()] = None,
    depot_id: Annotated[str | None, Form()] = None,
    vehicle_id: Annotated[str | None, Form()] = None,
    license_number: Annotated[str | None, Form()] = None,
    license_category: Annotated[str | None, Form()] = None,
    max_stops: Annotated[int | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    okay_with_layover: Annotated[bool | None, Form()] = None,
    layover_cost_per_night: Annotated[str | None, Form()] = None,
    max_layover_nights: Annotated[int | None, Form()] = None,
    expected_version: Annotated[int | None, Form()] = None,
    profile_photo: Annotated[
        UploadFile | None,
        File(description="Optional profile photo (JPEG/PNG, max 5 MB); stored via Cloudflare Images."),
    ] = None,
    document_uploads: list[UploadFile] | None = File(
        None,
        alias="documents",
        description="Optional driving licence document on draft update (max 1 file).",
    ),
    documents_metadata: str | None = Form(
        None,
        description=(
            "Optional JSON array for `documents` (index-aligned). "
            "When provided, only DRIVING_LICENCE is accepted."
        ),
    ),
) -> dict:
    """Update an existing driver draft (multipart/form-data)."""
    driver = await driver_service.get_driver(driver_id)
    if driver.account_status != DriverAccountStatus.DRAFT:
        raise ValidationError("Only DRAFT drivers can be updated via the drafts endpoint")
    if expected_version is None:
        raise ValidationError("expected_version is required for draft updates")

    form = await request.form()
    indexed_capacity: dict[int, str] = {}
    for key, value in form.multi_items():
        match = re.fullmatch(r"capacity\[(\d+)\]", str(key))
        if match:
            indexed_capacity[int(match.group(1))] = str(value)
    capacities_payload = [indexed_capacity[i] for i in sorted(indexed_capacity.keys())] if indexed_capacity else None
    if capacities_payload is not None:
        capacities_payload = list(dict.fromkeys(str(item) for item in capacities_payload if item))

    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "capacities": capacities_payload,
        "driver_type": driver_type,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "country": country,
        "state": state,
        "city": city,
        "postcode": postcode,
        "depot_id": depot_id,
        "vehicle_id": vehicle_id,
        "license_number": license_number,
        "license_category": license_category,
        "max_stops": max_stops,
        "notes": notes,
        "okay_with_layover": okay_with_layover,
        "max_layover_nights": max_layover_nights,
        "expected_version": expected_version,
    }
    if layover_cost_per_night is not None:
        try:
            payload["layover_cost_per_night"] = Decimal(str(layover_cost_per_night).strip())
        except Exception as exc:
            raise ValidationError("layover_cost_per_night must be a valid decimal amount") from exc
    payload = {k: v for k, v in payload.items() if v is not None}
    # Documents/profile-photo-only patches are valid; don't force a data-field mutation.
    mutation_payload = {k: v for k, v in payload.items() if k != "expected_version"}

    docs = list(document_uploads or [])
    has_file_mutation = profile_photo is not None or bool(docs)
    if not mutation_payload and not has_file_mutation:
        raise ValidationError("At least one field or file must be provided to update a draft")

    if mutation_payload:
        body = DriverDraftUpdateRequest.model_validate(payload)
        data = body.model_dump(exclude_unset=True)
        expected_version = data.pop("expected_version", None)
    else:
        data = {}

    # Map capacities enum to raw string list for service layer.
    if "capacities" in data and data["capacities"] is not None:
        data["capacities"] = [c.value for c in data["capacities"]]
    if "driver_type" in data and data["driver_type"] is not None:
        data["driver_type"] = data["driver_type"].value

    # Draft identity is stored in driver_drafts.draft_data (driver has no linked user_id yet).
    identity_data: dict[str, object] = {}
    for key in ("email", "first_name", "last_name", "phone"):
        if key in data:
            val = data.pop(key)
            if key == "email" and isinstance(val, str):
                identity_data[key] = val.strip().lower()
            else:
                identity_data[key] = val

    draft_incoming: dict[str, object] = {}
    draft_incoming.update(identity_data)
    draft_incoming.update(data)
    if isinstance(draft_incoming.get("layover_cost_per_night"), Decimal):
        draft_incoming["layover_cost_per_night"] = str(draft_incoming["layover_cost_per_night"])

    await driver_service.update_driver(
        driver_id,
        expected_version=expected_version,
        audit_user_id=user.id,
        audit_user_role=user.role,
        **data,
    )

    if draft_incoming:
        await driver_service.merge_driver_draft_data(
            driver_id=driver_id,
            incoming=draft_incoming,
            created_by=user.id,
        )
    if profile_photo is not None:
        await driver_service.update_profile_photo(
            driver_id,
            profile_photo,
            audit_user_id=user.id,
            audit_user_role=user.role,
        )

    if docs and documents_metadata is None:
        raise ValidationError("documents_metadata is required when documents are provided")
    if (not docs) and documents_metadata is not None:
        raise ValidationError("documents must be provided when documents_metadata is set")
    if docs:
        if len(docs) > MAX_ONBOARD_DRIVING_LICENCE_FILES:
            raise ValidationError(f"At most {MAX_ONBOARD_DRIVING_LICENCE_FILES} driving licence document can be attached to draft")
        try:
            raw_meta = _json.loads(str(documents_metadata).strip())
        except _json.JSONDecodeError as err:
            raise ValidationError("documents_metadata must be valid JSON") from err
        if not isinstance(raw_meta, list) or len(raw_meta) != len(docs):
            raise ValidationError(f"documents_metadata length ({len(raw_meta) if isinstance(raw_meta, list) else 0}) must match documents count ({len(docs)})")
        parsed_meta: list[OnboardDrivingLicenceDocumentMeta] = []
        for idx, item in enumerate(raw_meta):
            try:
                parsed_meta.append(OnboardDrivingLicenceDocumentMeta.model_validate(item))
            except Exception as exc:
                raise ValidationError(f"Invalid documents_metadata at index {idx}: {exc}") from exc
        existing_docs = await driver_service.list_driver_documents(driver_id)
        licence = next((d for d in existing_docs if d.kind == DriverDocumentKind.DRIVING_LICENCE.value), None)
        await validate_document(docs[0])
        if licence is None:
            await driver_service.create_driver_document(
                driver_id=driver_id,
                kind=DriverDocumentKind.DRIVING_LICENCE.value,
                title=None,
                expiry_date=parsed_meta[0].expiry_date,
                upload=docs[0],
                is_initial=True,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
        else:
            await driver_service.update_driver_document(
                document_id=licence.id,
                title=None,
                expiry_date=parsed_meta[0].expiry_date,
                upload=docs[0],
                audit_user_id=user.id,
                audit_user_role=user.role,
            )

    draft_row = await driver_service.ensure_driver_draft_row(driver_id=driver_id, created_by=user.id)
    driver = await driver_service.get_driver(driver_id)
    return ok(
        data=DriverDraftUpsertResponse(
            draft_id=draft_row.draft_id,
            driver=await _detail_with_driver_documents(
                driver, driver_service, presign_compliance_file_urls=True
            ),
        ),
        message="Driver draft updated",
    )


@router.post(
    "/{driver_id}/submit",
    response_model=SuccessResponse[DriverDraftUpsertResponse],
    status_code=200,
    **SUBMIT_DRIVER_DRAFT,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def submit_driver_draft(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    auth_service: AuthServiceDep,
    user: DriverWriteDep,
    email: Annotated[str, Form(description="Driver user email (login)")],
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    # Required driver fields for submit
    driver_type: Annotated[str, Form(description="DriverType enum value")],
    address_line1: Annotated[str, Form()],
    city: Annotated[str, Form()],
    postcode: Annotated[str, Form()],
    state: Annotated[str, Form()],
    okay_with_layover: Annotated[bool, Form(description="Whether the driver accepts layovers")],
    layover_cost_per_night: Annotated[
        str,
        Form(description="GBP per night (decimal string). Use 0 when not accepting layovers."),
    ],
    max_layover_nights: Annotated[int, Form(description="Maximum consecutive layover nights (0–366)")],
    # Optional fields
    address_line2: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    latitude: Annotated[float | None, Form()] = None,
    longitude: Annotated[float | None, Form()] = None,
    depot_id: Annotated[str | None, Form()] = None,
    vehicle_id: Annotated[str | None, Form()] = None,
    license_number: Annotated[str | None, Form()] = None,
    license_category: Annotated[str | None, Form()] = None,
    max_stops: Annotated[int, Form()] = 30,
    notes: Annotated[str | None, Form()] = None,
    # Optional driving licence upload
    document_uploads: list[UploadFile] | None = File(None, alias="documents"),
    documents_metadata: str | None = Form(None),
    expected_version: Annotated[int | None, Form()] = None,
) -> dict:
    """Submit a draft driver: enforce compulsory fields/docs, activate driver, and send activation email."""
    draft_row = await driver_service.get_driver_draft_row(driver_id)
    driver = await driver_service.get_driver(driver_id)
    if draft_row is not None and getattr(draft_row, "is_submitted", False):
        return ok(
            data=DriverDraftUpsertResponse(
                draft_id=draft_row.draft_id,
                driver=await _detail_with_driver_documents(
                    driver, driver_service, presign_compliance_file_urls=True
                ),
            ),
            message="Driver draft already submitted",
        )
    if driver.account_status != DriverAccountStatus.DRAFT:
        raise ValidationError("Only DRAFT drivers can be submitted")
    if expected_version is None:
        raise ValidationError("expected_version is required for draft submit")

    # Enforce required final fields (empty strings are still "present" as multipart form values).
    email_norm = email.strip().lower()
    if not email_norm:
        raise ValidationError("email is required")
    first_name_norm = first_name.strip()
    if not first_name_norm:
        raise ValidationError("first_name is required")
    last_name_norm = last_name.strip()
    if not last_name_norm:
        raise ValidationError("last_name is required")
    phone_norm = phone.strip()
    if not phone_norm:
        raise ValidationError("phone is required")
    address_line1_norm = address_line1.strip()
    if not address_line1_norm:
        raise ValidationError("address_line1 is required")
    state_norm = state.strip()
    if not state_norm:
        raise ValidationError("state is required")
    city_norm = city.strip()
    if not city_norm:
        raise ValidationError("city is required")
    postcode_norm = postcode.strip()
    if not postcode_norm:
        raise ValidationError("postcode is required")

    # Validate enums early for clearer 422s.
    try:
        driver_type_norm = DriverType(driver_type).value
    except Exception as exc:
        raise ValidationError("Invalid driver_type") from exc

    # Parse capacities sent as capacity[0], capacity[1], ...
    form = await request.form()
    indexed_capacity: dict[int, str] = {}
    for key, value in form.multi_items():
        match = re.fullmatch(r"capacity\[(\d+)\]", str(key))
        if match:
            indexed_capacity[int(match.group(1))] = str(value)
    if 0 not in indexed_capacity:
        raise ValidationError("capacities are required: provide capacity[0] (VAN/TRUCK)")
    capacities = [indexed_capacity[i] for i in sorted(indexed_capacity.keys())]
    capacities = list(dict.fromkeys(str(item) for item in capacities if item))
    if not capacities:
        raise ValidationError("capacities are required: provide capacity[0] (VAN/TRUCK)")
    try:
        capacities_norm = [DriverCapacity(c).value for c in capacities]
    except Exception as exc:
        raise ValidationError("Invalid capacity value") from exc

    try:
        layover_dec = Decimal(str(layover_cost_per_night).strip())
    except Exception as exc:
        raise ValidationError("layover_cost_per_night must be a valid decimal amount") from exc
    if layover_dec < 0:
        raise ValidationError("layover_cost_per_night must be >= 0")
    if max_layover_nights < 0 or max_layover_nights > 366:
        raise ValidationError("max_layover_nights must be between 0 and 366 inclusive")

    # Validate uploaded files first so malformed files fail early.
    docs = list(document_uploads or [])
    validated_documents: list[tuple[bytes, str, str]] = []
    for upload in docs:
        content, detected_type = await validate_document(upload)
        validated_documents.append((content, upload.filename or "document", detected_type))
    metadata_list: list[OnboardDrivingLicenceDocumentMeta] = []
    if docs and documents_metadata is None:
        raise ValidationError("documents_metadata is required when documents are provided")
    if (not docs) and documents_metadata is not None:
        raise ValidationError("documents must be provided when documents_metadata is set")
    if docs:
        if len(validated_documents) > MAX_ONBOARD_DRIVING_LICENCE_FILES:
            raise ValidationError(f"At most {MAX_ONBOARD_DRIVING_LICENCE_FILES} driving licence document is allowed on submit")
        try:
            raw_meta = _json.loads(str(documents_metadata).strip())
        except _json.JSONDecodeError as err:
            raise ValidationError("documents_metadata must be valid JSON") from err
        if not isinstance(raw_meta, list):
            raise ValidationError("documents_metadata must be a JSON array")
        if len(raw_meta) != len(validated_documents):
            raise ValidationError(f"documents_metadata length ({len(raw_meta)}) must match documents count ({len(validated_documents)})")
        for idx, item in enumerate(raw_meta):
            try:
                metadata_list.append(OnboardDrivingLicenceDocumentMeta.model_validate(item))
            except Exception as exc:
                raise ValidationError(f"Invalid documents_metadata at index {idx}: {exc}") from exc

    generated_password = generate_secure_password()

    # Atomic submit: wrap user creation + driver finalize + doc upsert in ONE transaction.
    # If optimistic locking fails (409 Conflict), the whole transaction rolls back,
    # so we do not persist orphan users or partial submit state.
    async with driver_service._driver_repo.session.begin_nested():  # type: ignore[attr-defined]
        created_user = await auth_service.create_user(
            email=email_norm,
            password=generated_password,
            first_name=first_name_norm,
            last_name=last_name_norm,
            phone=phone_norm,
            role=UserRole.DRIVER,
            status=UserStatus.PENDING_VERIFICATION,
            force_password_change=True,
            audit_user_id=user.id,
            audit_user_role=user.role,
        )

        await driver_service.submit_driver_draft(
            driver_id=driver_id,
            expected_version=expected_version,
            user_id=created_user.id,
            capacities=capacities_norm,
            driver_type=driver_type_norm,
            address_line1=address_line1_norm,
            address_line2=address_line2,
            country=country,
            state=state_norm,
            city=city_norm,
            postcode=postcode_norm,
            depot_id=depot_id,
            vehicle_id=vehicle_id,
            license_number=license_number,
            license_category=license_category,
            max_stops=max_stops,
            okay_with_layover=okay_with_layover,
            layover_cost_per_night=layover_dec,
            max_layover_nights=max_layover_nights,
            notes=notes,
            audit_user_id=user.id,
            audit_user_role=user.role,
        )

        if docs:
            existing = await driver_service.list_driver_documents(driver_id)
            licence = next((d for d in existing if d.kind == DriverDocumentKind.DRIVING_LICENCE.value), None)
            expiry = metadata_list[0].expiry_date
            upload = docs[0]
            if licence is None:
                await driver_service.create_driver_document(
                    driver_id=driver_id,
                    kind=DriverDocumentKind.DRIVING_LICENCE.value,
                    title=None,
                    expiry_date=expiry,
                    upload=upload,
                    is_initial=True,
                    audit_user_id=user.id,
                    audit_user_role=user.role,
                )
            else:
                await driver_service.update_driver_document(
                    document_id=licence.id,
                    title=None,
                    expiry_date=expiry,
                    upload=upload,
                    audit_user_id=user.id,
                    audit_user_role=user.role,
                )

    # Send activation email only after the DB transaction commits successfully.
    await auth_service.issue_driver_activation_email(inviter=user, target_user_id=created_user.id)

    draft_row = await driver_service.ensure_driver_draft_row(driver_id=driver_id, created_by=user.id)
    driver = await driver_service.get_driver(driver_id)
    return ok(
        data=DriverDraftUpsertResponse(
            draft_id=draft_row.draft_id,
            driver=await _detail_with_driver_documents(
                driver, driver_service, presign_compliance_file_urls=True
            ),
        ),
        message="Driver draft submitted",
    )


@router.post(
    "/{driver_id}/resend-credentials",
    response_model=MessageResponse,
    **RESEND_DRIVER_CREDENTIALS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def resend_driver_credentials(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    auth_service: AuthServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Admin: resend driver activation deep link email for drivers pending activation."""
    driver = await driver_service.get_driver(driver_id)
    if driver.account_status != DriverAccountStatus.PENDING_ACTIVATION:
        raise ValidationError("Driver must be in PENDING_ACTIVATION state")
    if not driver.user_id or getattr(driver, "user", None) is None:
        raise ValidationError("Driver account is missing linked user")

    await auth_service.issue_driver_activation_email(inviter=user, target_user_id=driver.user_id)
    return ok(message="Activation link resent.")


@router.post(
    "/{user_id}/support-issue-password",
    response_model=SuccessResponse[SupportIssuePasswordResponse],
    **SUPPORT_ISSUE_DRIVER_PASSWORD,
)
async def support_issue_admin_password(
    user_id: str,
    admin: DriverSupportPasswordDep,
    body: SupportIssuePasswordRequest,
    auth_service: AuthServiceDep,
) -> dict:
    uid, email = await auth_service.support_issue_temporary_password(
        actor=admin,
        target_user_id=user_id,
        new_password=body.new_password,
        flow="driver",
    )
    return ok(
        data=SupportIssuePasswordResponse(user_id=uid, email=email),
        message="Password reset. The user was signed out of all sessions.",
    )

@router.delete(
    "/drafts/{draft_id}",
    response_model=SuccessResponse[MessageResponse],
    **DELETE_DRIVER_DRAFT,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_driver_draft(
    request: Request,
    response: Response,
    draft_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Hard delete a non-submitted draft by business id (draft_id)."""
    await driver_service.delete_draft_by_draft_id(
        draft_id=draft_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=MessageResponse(message="Driver draft deleted"), message="Driver draft deleted")


@router.get(
    "/kpis",
    response_model=SuccessResponse[DriverKpis],
    **GET_DRIVER_KPIS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_kpis(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get driver KPIs / metrics (separate from list view)."""
    total_employed = await driver_service._driver_repo.count_drivers_default_admin_list_total()  # type: ignore[attr-defined]
    active_now = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.ACTIVE)  # type: ignore[attr-defined]
    suspended = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.SUSPENDED)  # type: ignore[attr-defined]
    pending_activation = await driver_service._driver_repo.count_by_account_status(DriverAccountStatus.PENDING_ACTIVATION)  # type: ignore[attr-defined]

    return ok(data=DriverKpis(total_employed=total_employed, active_now=active_now, suspended=suspended, pending_activation=pending_activation))


# ── Shifts (collection routes — must be defined BEFORE /{driver_id} to avoid path collision) ──


@router.get(
    "/shifts",
    response_model=SuccessResponse[DriverShiftListResponse],
    **SHIFTS_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_shifts(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    driver_id: Annotated[str | None, Query()] = None,
    depot_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> dict:
    """List driver shifts for calendar / depot scheduling."""
    shifts = await driver_service.list_shifts(
        driver_id=driver_id,
        depot_id=depot_id,
        date_from=date_from,
        date_to=date_to,
    )
    items = [
        DriverShiftEntry(
            id=shift.id,
            driver_id=shift.driver_id,
            date=shift.shift_date,
            start_time=shift.start_time.timetz(),
            end_time=shift.end_time.timetz(),
            status=shift.status,
        )
        for shift in shifts
    ]
    return ok(data=DriverShiftListResponse(items=items))


@router.post(
    "/shifts",
    response_model=SuccessResponse[DriverShiftEntry],
    status_code=201,
    **SHIFTS_MUTATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_shift(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    driver_id: Annotated[str, Form()],
    date_value: Annotated[date, Form(alias="date")],
    start_time: Annotated[time, Form()],
    end_time: Annotated[time, Form()],
    status: Annotated[str, Form()] = ShiftStatus.PLANNED.value,
) -> dict:
    """Create a new driver shift."""
    shift = await driver_service.create_shift(
        driver_id=driver_id,
        shift_date=date_value,
        start_time=start_time,
        end_time=end_time,
        shift_type="",
        status=status,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(
        data=DriverShiftEntry(
            id=shift.id,
            driver_id=shift.driver_id,
            date=shift.shift_date,
            start_time=shift.start_time.timetz(),
            end_time=shift.end_time.timetz(),
            status=shift.status,
        )
    )


@router.get(
    "/shifts/{shift_id}/full",
    response_model=SuccessResponse[DriverShiftEntry],
    **SHIFT_GET_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_shift_full(
    request: Request,
    response: Response,
    shift_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get a single driver shift."""
    shift = await driver_service.get_shift(shift_id)
    return ok(
        data=DriverShiftEntry(
            id=shift.id,
            driver_id=shift.driver_id,
            date=shift.shift_date,
            start_time=shift.start_time.timetz(),
            end_time=shift.end_time.timetz(),
            status=shift.status,
        )
    )


# ── Driver item routes ──────────────────────────────────────────────────────────


def _driver_activity_list_item(log) -> DriverActivityLogListItem:
    user = getattr(log, "user", None)
    return DriverActivityLogListItem(
        id=log.id,
        timestamp=log.created_at,
        event=activity_event_label(log),
        user_type=activity_user_type_badge(user_role=log.user_role, user_id=log.user_id, user=user),
        activity_performed_by=actor_email(log, user),
        ip_address=log.ip_address,
    )


@router.get(
    "/{driver_id}/activity-log",
    response_model=SuccessResponse[DriverActivityLogListResponse],
    **GET_DRIVER_ACTIVITY_LOG,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_driver_activity_log(
    request: Request,
    response: Response,
    driver_id: Annotated[str, Path(description="Driver profile id.")],
    driver_service: DriverServiceDep,
    session: SessionDep,
    _user: DriverReadDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-based).", examples=[1])] = 1,
    size: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Entries per page (default 50, max 100).",
            examples=[50],
        ),
    ] = 50,
    from_date: Annotated[
        datetime | None,
        Query(
            description="Inclusive lower bound on `timestamp` (ISO 8601 datetime).",
            examples=["2026-02-01T00:00:00Z"],
        ),
    ] = None,
    to_date: Annotated[
        datetime | None,
        Query(
            description="Inclusive upper bound on `timestamp` (ISO 8601 datetime).",
            examples=["2026-02-28T23:59:59Z"],
        ),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"],
        Query(description="Sort order by timestamp (`desc` = newest first).", examples=["desc"]),
    ] = "desc",
    search: Annotated[
        str | None,
        Query(
            description=(
                "Case-insensitive match against actor email, first/last name, action, reason, "
                "IP address, or audit reference."
            ),
            examples=["login"],
        ),
    ] = None,
) -> dict:
    """Paginated activity log for admin driver profile (table columns only)."""
    driver = await driver_service.get_driver(driver_id)
    driver_user_id = driver.user_id
    repo = AuditRepository(session)
    items, total = await repo.get_driver_activity_logs(
        driver_id=driver_id,
        driver_user_id=driver_user_id,
        page=page,
        size=size,
        search=search,
        from_date=from_date,
        to_date=to_date,
        sort_by=sort,
    )
    return ok(
        data=DriverActivityLogListResponse(
            items=[_driver_activity_list_item(log) for log in items],
            total=total,
            page=page,
            size=size,
        )
    )


@router.get(
    "/{driver_id}/activity-log/{audit_log_id}",
    response_model=SuccessResponse[DriverActivityLogDetailResponse],
    **GET_DRIVER_ACTIVITY_LOG_DETAIL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_activity_log_detail(
    request: Request,
    response: Response,
    driver_id: Annotated[str, Path(description="Driver profile id.")],
    audit_log_id: Annotated[
        str,
        Path(description="Audit log row id from the list endpoint `items[].id`."),
    ],
    driver_service: DriverServiceDep,
    session: SessionDep,
    _user: DriverReadDep,
) -> dict:
    """Single audit entry for row click; includes redacted old/new values."""
    driver = await driver_service.get_driver(driver_id)
    driver_user_id = driver.user_id
    repo = AuditRepository(session)
    log = await repo.get_driver_activity_log_by_id(
        driver_id=driver_id,
        driver_user_id=driver_user_id,
        audit_log_id=audit_log_id,
    )
    if log is None:
        raise NotFoundError(resource="audit_log", id=audit_log_id)

    user = getattr(log, "user", None)
    os_label = log.os or parse_os_from_user_agent(log.user_agent)
    detail = DriverActivityLogDetailResponse(
        id=log.id,
        timestamp=log.created_at,
        event=activity_event_label(log),
        user_type=activity_user_type_badge(user_role=log.user_role, user_id=log.user_id, user=user),
        activity_performed_by=actor_email(log, user),
        ip_address=log.ip_address,
        audit_ref=log.audit_ref,
        action=log.action,
        category=audit_category_str(log),
        event_type=audit_event_type_str(log),
        severity=log.severity,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        entity_ref=log.entity_ref,
        reason=log.reason,
        user_id=log.user_id,
        user_role=log.user_role,
        organization_id=log.organization_id,
        user_agent=log.user_agent,
        browser=log.browser,
        device=log.device,
        os=os_label,
        old_value=redact_audit_json(log.old_value),
        new_value=redact_audit_json(log.new_value),
    )
    return ok(data=detail)


@router.get(
    "/{driver_id}",
    response_model=SuccessResponse[DriverDetailResponse],
    **GET_DRIVER,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get driver by ID with profile info and documents (metadata only; no presigned file URLs)."""
    driver = await driver_service.get_driver(driver_id)
    detail = await _detail_with_driver_documents(driver, driver_service)
    return ok(data=detail)


@router.get(
    "/{driver_id}/configuration",
    response_model=SuccessResponse[DriverOperationalConfigurationResponse],
    **GET_DRIVER_CONFIGURATION,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_configuration(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Operational scheduling preferences (layovers)."""
    driver = await driver_service.get_driver(driver_id)
    return ok(data=_to_operational_configuration_response(driver))


@router.patch(
    "/{driver_id}/configuration",
    response_model=SuccessResponse[DriverOperationalConfigurationResponse],
    **PATCH_DRIVER_CONFIGURATION,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def patch_driver_configuration(
    request: Request,
    response: Response,
    driver_id: str,
    body: DriverOperationalConfigurationUpdateRequest,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Replace layover settings (admin Edit Configurations modal)."""
    expected = body.expected_version
    await driver_service.update_driver(
        driver_id,
        okay_with_layover=body.okay_with_layover,
        layover_cost_per_night=body.layover_cost_per_night,
        max_layover_nights=body.max_layover_nights,
        expected_version=expected,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver_id)
    return ok(data=_to_operational_configuration_response(driver))


@router.get(
    "/{driver_id}/full",
    response_model=SuccessResponse[DriverFullProfileResponse],
    **GET_DRIVER_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_full(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Full driver profile; compliance `documents[].file_url` is omitted (use document APIs + OTP). Traffic violation proofs include presigned URLs like the dedicated traffic-violation endpoints."""
    # Core driver
    driver = await driver_service.get_driver(driver_id)

    # Documents
    docs = await driver_service.list_driver_documents(driver_id)
    doc_items: list[DriverDocumentResponse] = []
    for doc in docs:
        status = driver_service.compute_document_status(doc.expiry_date)
        doc_items.append(_to_document_response(doc, file_url=None, status=status))
    documents = DriverDocumentsListResponse(items=doc_items)

    # Time off (includes all leave types, including sick leave)
    time_off_items, paid_leave_taken, unpaid_leave_taken = await driver_service.list_time_off(driver_id)
    time_off_entries = [
        DriverTimeOffEntry(
            id=entry.id,
            driver_id=entry.driver_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            type=TimeOffType(entry.type),
            days=entry.days,
            notes=entry.notes,
            is_paid=entry.is_paid,
        )
        for entry in time_off_items
    ]
    time_off = DriverTimeOffListResponse(
        items=time_off_entries,
        paid_leave_taken=paid_leave_taken,
        unpaid_leave_taken=unpaid_leave_taken,
    )

    # Weekly schedule
    schedule_rows, total_hours = await driver_service.get_weekly_schedule(driver_id)
    schedule_days = [
        WeeklyScheduleDay(
            day_of_week=row.day_of_week,
            is_active=row.is_active,
            start_time=row.start_time,
            end_time=row.end_time,
        )
        for row in schedule_rows
    ]
    schedule = WeeklyScheduleResponse(days=schedule_days, total_weekly_hours=total_hours)

    # Shifts (no date filter, full history)
    shifts = await driver_service.list_shifts(driver_id=driver_id, depot_id=None, date_from=None, date_to=None)
    shift_items = [
        DriverShiftEntry(
            id=shift.id,
            driver_id=shift.driver_id,
            date=shift.shift_date,
            start_time=shift.start_time.timetz(),
            end_time=shift.end_time.timetz(),
            status=shift.status,
        )
        for shift in shifts
    ]
    shifts_resp = DriverShiftListResponse(items=shift_items)

    # Traffic violations (first page with large page size)
    violations_items, violations_total = await driver_service.list_traffic_violations(driver_id, page=1, size=1000)
    violation_entries = [
        TrafficViolationEntry(
            id=v.id,
            driver_id=v.driver_id,
            occurred_at=v.occurred_at,
            violation_type=v.violation_type,
            amount=v.amount,
            status=v.status,
            notes=v.notes,
            proofs=[
                TrafficViolationProofEntry(
                    id=p.id,
                    url=driver_service.get_file_url(p.file_key),
                    content_type=p.content_type,
                    size_bytes=p.size_bytes,
                    created_at=p.created_at,
                )
                for p in list(getattr(v, "proofs", []) or [])
            ],
        )
        for v in violations_items
    ]
    traffic_violations = TrafficViolationListResponse(
        items=violation_entries,
        total=violations_total,
        page=1,
        size=1000,
    )

    full_profile = DriverFullProfileResponse(
        driver=_to_detail_response(driver, driver_service),
        documents=documents,
        time_off=time_off,
        schedule=schedule,
        shifts=shifts_resp,
        traffic_violations=traffic_violations,
    )

    return ok(data=full_profile)

    # @router.post(
    #     "/",
    #     response_model=SuccessResponse[DriverDetailResponse],
    #     status_code=201,
    #     **CREATE_DRIVER,
    # )
    # async def create_driver(
    #     body: DriverCreateRequest,
    #     driver_service: DriverServiceDep,
    #     user: DriverWriteDep,
    # ) -> dict:
    #     """Create a new driver linked to an existing user (one driver per user)."""
    #     driver = await driver_service.create_driver(
    #         user_id=body.user_id,
    #         first_name=body.first_name,
    #         last_name=body.last_name,
    #         phone=body.phone,
    #         email=body.email,
    #         capacity=body.capacity,
    #         driver_type=body.driver_type,
    #         address_line1=body.address_line1,
    #         address_line2=body.address_line2,
    #         city=body.city,
    #         postcode=body.postcode,
    #         latitude=body.latitude,
    #         longitude=body.longitude,
    #         depot_id=body.depot_id,
    #         vehicle_id=body.vehicle_id,
    #         license_number=body.license_number,
    #         license_category=body.license_category,
    #         max_stops=body.max_stops,
    #         territory_tags=body.territory_tags,
    #         account_status=body.account_status,
    #         live_status=body.live_status,
    #         notes=body.notes,
    #         audit_user_id=user.id,
    #         audit_user_role=user.role,
    #     )
    #     driver = await driver_service.get_driver(driver.id)
    #     return ok(data=_to_detail_response(driver, driver_service), message="Driver created")


@router.post(
    "/add-new-driver",
    response_model=SuccessResponse[DriverWithUserCreateResponse],
    status_code=201,
    **CREATE_DRIVER_WITH_USER,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_driver_with_user(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    auth_service: AuthServiceDep,
    user: DriverWriteDep,
    # Required identity fields
    driver_type: Annotated[str, Form(description="DriverType enum value")],
    # Address (required)
    address_line1: Annotated[str, Form()],
    city: Annotated[str, Form()],
    postcode: Annotated[str, Form()],
    # Canonical identity fields (required)
    email: Annotated[str, Form(description="Driver user email (login)")],
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    state: Annotated[str, Form()],
    # Operational configuration (required on onboarding — must precede optional Form fields)
    okay_with_layover: Annotated[bool, Form(description="Whether the driver accepts layovers")],
    layover_cost_per_night: Annotated[
        str,
        Form(description="GBP per night (decimal string, e.g. 85 or 85.00). Use 0 when not accepting layovers."),
    ],
    max_layover_nights: Annotated[int, Form(description="Maximum consecutive layover nights (0–366)")],
    # Optional contact/address fields
    address_line2: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    latitude: Annotated[float | None, Form()] = None,
    longitude: Annotated[float | None, Form()] = None,
    # Assignment
    depot_id: Annotated[str | None, Form()] = None,
    vehicle_id: Annotated[str | None, Form()] = None,
    # Licence details
    license_number: Annotated[str | None, Form()] = None,
    license_category: Annotated[str | None, Form()] = None,
    max_stops: Annotated[int, Form()] = 30,
    # Notes
    notes: Annotated[str | None, Form()] = None,
    profile_photo: Annotated[
        UploadFile | None,
        File(description="Optional profile photo (JPEG/PNG, max 5 MB); stored via Cloudflare Images."),
    ] = None,
    document_uploads: list[UploadFile] | None = File(
        None,
        alias="documents",
        description=(
            "Optional driving licence document (PDF/JPG/PNG/DOC/DOCX, max 10 MB). "
            "At most one file. Custom documents must be uploaded after creation via "
            "POST /v1/drivers/{driver_id}/documents."
        ),
    ),
    documents_metadata: str | None = Form(
        None,
        description=(
            "Optional JSON array with exactly one object when documents are provided, index-aligned with the file. "
            "Each element: { document_type (DRIVING_LICENCE), title (optional, canonical if omitted), "
            "expiry_date (required, YYYY-MM-DD) }"
        ),
    ),
) -> dict:
    """Create user (role=DRIVER), driver, optional profile photo, and required driving licence; send activation email."""
    generated_password = generate_secure_password()
    created_user = await auth_service.create_user(
        email=email,
        password=generated_password,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        role=UserRole.DRIVER,
        status=UserStatus.PENDING_VERIFICATION,
        audit_user_id=user.id,
        audit_user_role=user.role,
        force_password_change=True,
    )

    if not state:
        raise ValidationError("state is required")

    # Validate uploaded files first so malformed files fail early before metadata parsing.
    docs = list(document_uploads or [])
    validated_documents: list[tuple[bytes, str, str]] = []
    for upload in docs:
        content, detected_type = await validate_document(upload)
        validated_documents.append((content, upload.filename or "document", detected_type))
    if len(validated_documents) > MAX_ONBOARD_DRIVING_LICENCE_FILES:
        raise ValidationError(f"At most {MAX_ONBOARD_DRIVING_LICENCE_FILES} driving licence document is allowed on create")

    # Parse metadata as a strict, index-aligned list (driving licence only) when docs are sent.
    metadata_list: list[dict[str, object]] = []
    if docs and documents_metadata is None:
        raise ValidationError("documents_metadata is required when documents are provided")
    if (not docs) and documents_metadata is not None:
        raise ValidationError("documents must be provided when documents_metadata is set")
    if docs:
        try:
            raw_meta = _json.loads(str(documents_metadata).strip())
        except _json.JSONDecodeError as err:
            raise ValidationError("documents_metadata must be valid JSON") from err
        if not isinstance(raw_meta, list):
            raise ValidationError("documents_metadata must be a JSON array")
        if len(raw_meta) != len(validated_documents):
            raise ValidationError(f"documents_metadata length ({len(raw_meta)}) must match documents count ({len(validated_documents)})")
        for idx, item in enumerate(raw_meta):
            try:
                parsed = OnboardDrivingLicenceDocumentMeta.model_validate(item)
            except Exception as exc:
                raise ValidationError(f"Invalid documents_metadata at index {idx}: {exc}") from exc
            metadata_list.append(
                {
                    "document_type": parsed.document_type.value,
                    "title": parsed.title,
                    "expiry_date": parsed.expiry_date.isoformat(),
                }
            )

    # Support capacities sent as capacity[0], capacity[1], ... (indexed form keys).
    # We read them from the raw form payload so the handler doesn't need legacy `capacity`/`capacities` fields.
    form = await request.form()
    indexed_capacity: dict[int, str] = {}
    for key, value in form.multi_items():
        match = re.fullmatch(r"capacity\[(\d+)\]", str(key))
        if match:
            indexed_capacity[int(match.group(1))] = str(value)
    if 0 not in indexed_capacity:
        raise ValidationError("capacities are required: provide capacity[0] (VAN/TRUCK)")
    capacities = [indexed_capacity[i] for i in sorted(indexed_capacity.keys())]
    # Deduplicate while keeping order.
    capacities = list(dict.fromkeys(str(item) for item in capacities if item))

    try:
        layover_dec = Decimal(str(layover_cost_per_night).strip())
    except Exception as exc:
        raise ValidationError("layover_cost_per_night must be a valid decimal amount") from exc
    if layover_dec < 0:
        raise ValidationError("layover_cost_per_night must be >= 0")
    if max_layover_nights < 0 or max_layover_nights > 366:
        raise ValidationError("max_layover_nights must be between 0 and 366 inclusive")

    driver, document_results = await driver_service.create_driver_with_documents(
        user_id=created_user.id,
        capacities=capacities,
        driver_type=driver_type,
        address_line1=address_line1,
        address_line2=address_line2,
        country=country,
        state=state,
        city=city,
        postcode=postcode,
        latitude=latitude,
        longitude=longitude,
        depot_id=depot_id,
        vehicle_id=vehicle_id,
        license_number=license_number,
        license_category=license_category,
        max_stops=max_stops,
        territory_tags=None,
        notes=notes,
        documents=validated_documents,
        documents_metadata=metadata_list,
        profile_photo=profile_photo,
        okay_with_layover=okay_with_layover,
        layover_cost_per_night=layover_dec,
        max_layover_nights=max_layover_nights,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver.id)
    documents = [DriverDocumentResult(type=kind, status=status, error=error) for (kind, status, error) in document_results]
    await auth_service.issue_driver_activation_email(inviter=user, target_user_id=created_user.id)
    return ok(
        data=DriverWithUserCreateResponse(
            driver=_to_detail_response(driver, driver_service),
            documents=documents,
        ),
        message="Driver and user created; activation email sent to driver.",
    )


@router.get(
    "/terms-and-conditions/config",
    response_model=SuccessResponse[DriverTermsAndConditionsListResponse],
    **LIST_DRIVER_TERMS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_driver_terms_and_conditions(
    request: Request,
    response: Response,
    driver_service: DriverServiceDep,
    user: DriverReadDep,
) -> dict:
    rows = await driver_service.list_driver_terms()
    return ok(
        data=DriverTermsAndConditionsListResponse(
            items=[DriverTermsAndConditionsResponse.model_validate(row) for row in rows]
        )
    )


@router.post(
    "/terms-and-conditions/config",
    response_model=SuccessResponse[DriverTermsAndConditionsResponse],
    status_code=201,
    **CREATE_DRIVER_TERMS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_driver_terms_and_conditions(
    request: Request,
    response: Response,
    body: DriverTermsAndConditionsCreateRequest,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    payload = await driver_service.create_driver_terms(
        title=body.title.strip(),
        clauses=[
            {"clause_order": c.clause_order, "heading": c.heading.strip(), "body": c.body.strip()}
            for c in body.clauses
        ],
        effective_from=body.effective_from,
        is_active=body.is_active,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=DriverTermsAndConditionsResponse.model_validate(payload), message="Driver terms created")


@router.patch(
    "/terms-and-conditions/config/{terms_id}",
    response_model=SuccessResponse[DriverTermsAndConditionsResponse],
    **UPDATE_DRIVER_TERMS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_driver_terms_and_conditions(
    terms_id: str,
    request: Request,
    response: Response,
    body: DriverTermsAndConditionsUpdateRequest,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    payload = await driver_service.update_driver_terms(
        terms_id=terms_id,
        title=body.title.strip() if body.title is not None else None,
        clauses=(
            [{"clause_order": c.clause_order, "heading": c.heading.strip(), "body": c.body.strip()} for c in body.clauses]
            if body.clauses is not None
            else None
        ),
        effective_from=body.effective_from,
        is_active=body.is_active,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=DriverTermsAndConditionsResponse.model_validate(payload), message="Driver terms updated")


    # @router.post(
    #     "/onboarding",
    #     response_model=SuccessResponse[DriverDetailResponse],
    #     status_code=201,
    #     **CREATE_DRIVER_ONBOARDING,
    # )
    # async def create_driver_onboarding(
    #     driver_service: DriverServiceDep,
    #     user: DriverWriteDep,
    #     # Linked user identity
    #     user_id: str = Form(..., description="Existing user ID to link as driver"),
    #     # Identity
    #     first_name: str = Form(...),
    #     last_name: str = Form(...),
    #     phone: str = Form(...),
    #     email: str = Form(...),
    #     # Capacity / type
    #     capacity: str = Form(..., description="DriverCapacity enum value"),
    #     driver_type: str = Form(..., description="DriverType enum value"),
    #     # Address
    #     address_line1: str = Form(...),
    #     address_line2: str | None = Form(None),
    #     city: str = Form(...),
    #     postcode: str = Form(...),
    #     latitude: float | None = Form(None),
    #     longitude: float | None = Form(None),
    #     # Assignment
    #     depot_id: str | None = Form(None),
    #     vehicle_id: str | None = Form(None),
    #     # Licence details
    #     license_number: str | None = Form(None),
    #     license_category: str | None = Form(None),
    #     max_stops: int = Form(30),
    #     # Notes
    #     notes: str | None = Form(None),
    #     # Initial documents
    #     driving_licence_file: UploadFile | None = File(None),
    #     driving_licence_expiry_date: date | None = Form(None),
    #     cpc_certificate_file: UploadFile | None = File(None),
    #     cpc_certificate_expiry_date: date | None = Form(None),
    #     digital_tachograph_file: UploadFile | None = File(None),
    #     digital_tachograph_expiry_date: date | None = Form(None),
    #     custom_document_title: str | None = Form(None),
    #     custom_document_file: UploadFile | None = File(None),
    #     custom_document_expiry_date: date | None = Form(None),
    #     save_as_draft: bool = Form(False),
    #     submit_driver: bool = Form(False),
    # ) -> dict:
    #     """Create a driver profile together with initial compliance documents (multipart)."""
    #     driver, _ = await driver_service.create_driver_with_documents(
    #         user_id=user_id,
    #         first_name=first_name,
    #         last_name=last_name,
    #         phone=phone,
    #         email=email,
    #         capacity=capacity,
    #         driver_type=driver_type,
    #         address_line1=address_line1,
    #         address_line2=address_line2,
    #         city=city,
    #         postcode=postcode,
    #         latitude=latitude,
    #         longitude=longitude,
    #         depot_id=depot_id,
    #         vehicle_id=vehicle_id,
    #         license_number=license_number,
    #         license_category=license_category,
    #         max_stops=max_stops,
    #         territory_tags=None,
    #         notes=notes,
    #         save_as_draft=save_as_draft,
    #         submit_driver=submit_driver,
    #         driving_licence_file=driving_licence_file,
    #         driving_licence_expiry_date=driving_licence_expiry_date,
    #         cpc_certificate_file=cpc_certificate_file,
    #         cpc_certificate_expiry_date=cpc_certificate_expiry_date,
    #         digital_tachograph_file=digital_tachograph_file,
    #         digital_tachograph_expiry_date=digital_tachograph_expiry_date,
    #         custom_document_title=custom_document_title,
    #         custom_document_file=custom_document_file,
    #         custom_document_expiry_date=custom_document_expiry_date,
    #         audit_user_id=user.id,
    #         audit_user_role=user.role,
    #     )
    #     driver = await driver_service.get_driver(driver.id)
    #     return ok(data=_to_detail_response(driver, driver_service), message="Driver onboarded")


@router.get(
    "/{driver_id}/documents",
    response_model=SuccessResponse[DriverDocumentsListResponse],
    **DOCUMENTS_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_driver_documents(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """List compliance documents for a driver."""
    docs = await driver_service.list_driver_documents(driver_id)

    items: list[DriverDocumentResponse] = []
    for doc in docs:
        status = driver_service.compute_document_status(doc.expiry_date)
        file_url = driver_service.get_file_url(doc.file_key)
        items.append(_to_document_response(doc, file_url=file_url, status=status))

    return ok(data=DriverDocumentsListResponse(items=items))


def _document_create_form(
    document_type: Annotated[str, Form(description="Document type: DRIVING_LICENCE, CUSTOM")],
    title: Annotated[str | None, Form(description="Required when document_type is CUSTOM; for other types omit or use enum with spaces (e.g. DRIVING LICENCE)")] = None,
    expiry_date: Annotated[date | None, Form()] = None,
) -> DriverDocumentCreateRequest:
    return DriverDocumentCreateRequest.model_validate({"document_type": document_type, "title": title, "expiry_date": expiry_date})


@router.post(
    "/{driver_id}/documents",
    response_model=SuccessResponse[DriverDocumentResponse],
    status_code=201,
    **DOCUMENTS_MUTATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def upload_driver_document(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
    body: Annotated[DriverDocumentCreateRequest, Depends(_document_create_form)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Upload a new compliance document for a driver."""
    doc = await driver_service.create_driver_document(
        driver_id=driver_id,
        kind=body.document_type.value,
        title=body.title,
        expiry_date=body.expiry_date,
        upload=file,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.get(
    "/documents/{document_id}/full",
    response_model=SuccessResponse[DriverDocumentResponse],
    **DOCUMENT_GET_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_document_full(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """Get a single driver document with computed status."""
    doc = await driver_service.get_driver_document(document_id)
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.patch(
    "/documents/{document_id}",
    response_model=SuccessResponse[DriverDocumentResponse],
    **DOCUMENT_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_driver_document(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
    title: Annotated[str | None, Form()] = None,
    expiry_date: Annotated[date | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Update document metadata and/or replace the file. Returns updated document with file_url (preview) and auto-calculated expiry status (VALID, EXPIRING_SOON, EXPIRED)."""
    doc = await driver_service.update_driver_document(
        document_id=document_id,
        title=title,
        expiry_date=expiry_date,
        upload=file,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.delete(
    "/documents/{document_id}",
    response_model=SuccessResponse[dict],
    **DOCUMENT_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_driver_document(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """Delete a driver document."""
    await driver_service.delete_driver_document(
        document_id=document_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.get(
    "/drafts/{draft_id}/documents",
    response_model=SuccessResponse[DriverDocumentsListResponse],
    **DRAFT_DOCUMENTS_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_draft_driver_documents(
    request: Request,
    response: Response,
    draft_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """List compliance documents for a draft driver."""
    docs = await driver_service.list_driver_documents(draft_id)

    items: list[DriverDocumentResponse] = []
    for doc in docs:
        status = driver_service.compute_document_status(doc.expiry_date)
        file_url = driver_service.get_file_url(doc.file_key)
        items.append(_to_document_response(doc, file_url=file_url, status=status))

    return ok(data=DriverDocumentsListResponse(items=items))


@router.post(
    "/drafts/{draft_id}/documents",
    response_model=SuccessResponse[DriverDocumentResponse],
    status_code=201,
    **DRAFT_DOCUMENTS_MUTATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def upload_draft_driver_document(
    request: Request,
    response: Response,
    draft_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
    body: Annotated[DriverDocumentCreateRequest, Depends(_document_create_form)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Upload a new compliance document for a draft driver."""
    doc = await driver_service.create_driver_document(
        driver_id=draft_id,
        kind=body.document_type.value,
        title=body.title,
        expiry_date=body.expiry_date,
        upload=file,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.get(
    "/drafts/documents/{document_id}/full",
    response_model=SuccessResponse[DriverDocumentResponse],
    **DRAFT_DOCUMENT_GET_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_draft_driver_document_full(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """Get a single draft driver document with computed status."""
    doc = await driver_service.get_driver_document(document_id)
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.patch(
    "/drafts/documents/{document_id}",
    response_model=SuccessResponse[DriverDocumentResponse],
    **DRAFT_DOCUMENT_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_draft_driver_document(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
    title: Annotated[str | None, Form()] = None,
    expiry_date: Annotated[date | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Update document metadata and/or replace the file for a draft driver. Returns updated document with file_url (preview) and auto-calculated expiry status (VALID, EXPIRING_SOON, EXPIRED)."""
    doc = await driver_service.update_driver_document(
        document_id=document_id,
        title=title,
        expiry_date=expiry_date,
        upload=file,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    status = driver_service.compute_document_status(doc.expiry_date)
    return ok(data=_to_document_response(doc, file_url=driver_service.get_file_url(doc.file_key), status=status))


@router.delete(
    "/drafts/documents/{document_id}",
    response_model=SuccessResponse[dict],
    **DRAFT_DOCUMENT_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_draft_driver_document(
    request: Request,
    response: Response,
    document_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    _driver_doc_access: DriverDocAccessDep,
) -> dict:
    """Delete a draft driver document."""
    await driver_service.delete_driver_document(
        document_id=document_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.get(
    "/{driver_id}/time-off",
    response_model=SuccessResponse[DriverTimeOffListResponse],
    **TIME_OFF_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_time_off(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """List time off entries and KPI for a driver."""
    items, paid_leave_taken, unpaid_leave_taken = await driver_service.list_time_off(driver_id)
    entries = [
        DriverTimeOffEntry(
            id=entry.id,
            driver_id=entry.driver_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            type=TimeOffType(entry.type),
            days=entry.days,
            notes=entry.notes,
            is_paid=entry.is_paid,
        )
        for entry in items
    ]
    return ok(
        data=DriverTimeOffListResponse(
            items=entries,
            paid_leave_taken=paid_leave_taken,
            unpaid_leave_taken=unpaid_leave_taken,
        )
    )


@router.post(
    "/{driver_id}/time-off",
    response_model=SuccessResponse[DriverTimeOffEntry],
    status_code=201,
    **TIME_OFF_MUTATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_time_off(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    start_date: Annotated[date, Form()],
    end_date: Annotated[date, Form()],
    type: Annotated[str, Form()],
    notes: Annotated[str | None, Form()] = None,
    is_paid: Annotated[bool, Form()] = True,
) -> dict:
    """Create a time off entry for a driver."""
    entry = await driver_service.create_time_off(
        driver_id=driver_id,
        start_date=start_date,
        end_date=end_date,
        type=type,
        notes=notes,
        is_paid=is_paid,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(
        data=DriverTimeOffEntry(
            id=entry.id,
            driver_id=entry.driver_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            type=TimeOffType(entry.type),
            days=entry.days,
            notes=entry.notes,
            is_paid=entry.is_paid,
        )
    )


@router.get(
    "/time-off/{time_off_id}/full",
    response_model=SuccessResponse[DriverTimeOffEntry],
    **TIME_OFF_GET_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_time_off_full(
    request: Request,
    response: Response,
    time_off_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get a single time off entry."""
    entry = await driver_service.get_time_off(time_off_id)
    return ok(
        data=DriverTimeOffEntry(
            id=entry.id,
            driver_id=entry.driver_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            type=TimeOffType(entry.type),
            days=entry.days,
            notes=entry.notes,
            is_paid=entry.is_paid,
        )
    )


@router.patch(
    "/time-off/{time_off_id}",
    response_model=SuccessResponse[DriverTimeOffEntry],
    **TIME_OFF_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_time_off(
    request: Request,
    response: Response,
    time_off_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    start_date: Annotated[date | None, Form()] = None,
    end_date: Annotated[date | None, Form()] = None,
    type: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    is_paid: Annotated[bool | None, Form()] = None,
) -> dict:
    """Update a time off entry."""
    entry = await driver_service.update_time_off(
        time_off_id=time_off_id,
        start_date=start_date,
        end_date=end_date,
        type=type,
        notes=notes,
        is_paid=is_paid,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(
        data=DriverTimeOffEntry(
            id=entry.id,
            driver_id=entry.driver_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            type=TimeOffType(entry.type),
            days=entry.days,
            notes=entry.notes,
            is_paid=entry.is_paid,
        )
    )


@router.delete(
    "/time-off/{time_off_id}",
    response_model=SuccessResponse[dict],
    **TIME_OFF_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_time_off(
    request: Request,
    response: Response,
    time_off_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Delete a time off entry."""
    await driver_service.delete_time_off(
        time_off_id=time_off_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.get(
    "/{driver_id}/traffic-violations",
    response_model=SuccessResponse[TrafficViolationListResponse],
    **TRAFFIC_VIOLATIONS_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_traffic_violations(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
) -> dict:
    """List traffic violations for a driver."""
    items, total = await driver_service.list_traffic_violations(driver_id, page=page, size=size)
    def _map_proofs(v: DriverTrafficViolation) -> list[TrafficViolationProofEntry]:
        proofs = list(getattr(v, "proofs", []) or [])
        return [
            TrafficViolationProofEntry(
                id=p.id,
                url=driver_service.get_file_url(p.file_key),
                content_type=p.content_type,
                size_bytes=p.size_bytes,
                created_at=p.created_at,
            )
            for p in proofs
        ]
    entries = [
        TrafficViolationEntry(
            id=v.id,
            driver_id=v.driver_id,
            occurred_at=v.occurred_at,
            violation_type=v.violation_type,
            amount=v.amount,
            status=v.status,
            notes=v.notes,
            proofs=_map_proofs(v),
        )
        for v in items
    ]
    return ok(data=TrafficViolationListResponse(items=entries, total=total, page=page, size=size))


@router.post(
    "/{driver_id}/traffic-violations",
    response_model=SuccessResponse[TrafficViolationUpsertResponse],
    status_code=201,
    **TRAFFIC_VIOLATIONS_MUTATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_traffic_violation(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    violation_type: Annotated[TrafficViolationType, Form(description="TrafficViolationType enum value")],
    amount: Annotated[Decimal, Form()],
    date_value: Annotated[date, Form(alias="date")],
    time_value: Annotated[time, Form(alias="time")],
    status: Annotated[TrafficViolationStatus, Form(description="TrafficViolationStatus enum value")],
    notes: Annotated[str | None, Form()] = None,
    proofs: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Create a new traffic violation (ticket) for a driver."""
    occurred_at = datetime.combine(date_value, time_value, tzinfo=UTC)
    violation, proof_results = await driver_service.create_traffic_violation(
        driver_id=driver_id,
        occurred_at=occurred_at,
        violation_type=violation_type.value,
        amount=amount,
        status=status.value,
        notes=notes,
        proofs=proofs,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    violation = await driver_service.get_traffic_violation(violation.id)
    proof_entries = [
        TrafficViolationProofEntry(
            id=p.id,
            url=driver_service.get_file_url(p.file_key),
            content_type=p.content_type,
            size_bytes=p.size_bytes,
            created_at=p.created_at,
        )
        for p in list(getattr(violation, "proofs", []) or [])
    ]
    proofs_by_id = {p.id: p for p in proof_entries}
    return ok(
        data=TrafficViolationUpsertResponse(
            violation=TrafficViolationEntry(
                id=violation.id,
                driver_id=violation.driver_id,
                occurred_at=violation.occurred_at,
                violation_type=violation.violation_type,
                amount=violation.amount,
                status=violation.status,
                notes=violation.notes,
                proofs=proof_entries,
            ),
            proof_results=[
                _to_proof_upload_result(r, proofs_by_id)
                for r in proof_results
            ],
        ),
    )


@router.patch(
    "/traffic-violations/{violation_id}",
    response_model=SuccessResponse[TrafficViolationUpsertResponse],
    **TRAFFIC_VIOLATION_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_traffic_violation(
    request: Request,
    response: Response,
    violation_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    violation_type: Annotated[TrafficViolationType | None, Form(description="TrafficViolationType enum value")] = None,
    amount: Annotated[Decimal | None, Form()] = None,
    date_value: Annotated[date | None, Form(alias="date")] = None,
    time_value: Annotated[time | None, Form(alias="time")] = None,
    status: Annotated[TrafficViolationStatus | None, Form(description="TrafficViolationStatus enum value")] = None,
    notes: Annotated[str | None, Form()] = None,
    proofs: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Update a traffic violation (multipart/form-data; PATCH semantics)."""
    if (date_value is None) != (time_value is None):
        raise ValidationError("Both date and time must be provided together when updating occurred_at")
    occurred_at = datetime.combine(date_value, time_value, tzinfo=UTC) if (date_value is not None and time_value is not None) else None

    violation, proof_results = await driver_service.update_traffic_violation(
        violation_id=violation_id,
        occurred_at=occurred_at,
        violation_type=violation_type.value if violation_type is not None else None,
        amount=amount,
        status=status.value if status is not None else None,
        notes=notes,
        proofs=proofs,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    violation = await driver_service.get_traffic_violation(violation.id)
    proof_entries = [
        TrafficViolationProofEntry(
            id=p.id,
            url=driver_service.get_file_url(p.file_key),
            content_type=p.content_type,
            size_bytes=p.size_bytes,
            created_at=p.created_at,
        )
        for p in list(getattr(violation, "proofs", []) or [])
    ]
    proofs_by_id = {p.id: p for p in proof_entries}
    return ok(
        data=TrafficViolationUpsertResponse(
            violation=TrafficViolationEntry(
                id=violation.id,
                driver_id=violation.driver_id,
                occurred_at=violation.occurred_at,
                violation_type=violation.violation_type,
                amount=violation.amount,
                status=violation.status,
                notes=violation.notes,
                proofs=proof_entries,
            ),
            proof_results=[
                _to_proof_upload_result(r, proofs_by_id)
                for r in proof_results
            ],
        )
    )


@router.get(
    "/traffic-violations/{violation_id}/full",
    response_model=SuccessResponse[TrafficViolationEntry],
    **TRAFFIC_VIOLATION_GET_FULL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_traffic_violation_full(
    request: Request,
    response: Response,
    violation_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get a single traffic violation."""
    violation = await driver_service.get_traffic_violation(violation_id)
    return ok(
        data=TrafficViolationEntry(
            id=violation.id,
            driver_id=violation.driver_id,
            occurred_at=violation.occurred_at,
            violation_type=violation.violation_type,
            amount=violation.amount,
            status=violation.status,
            notes=violation.notes,
            proofs=[
                TrafficViolationProofEntry(
                    id=p.id,
                    url=driver_service.get_file_url(p.file_key),
                    content_type=p.content_type,
                    size_bytes=p.size_bytes,
                    created_at=p.created_at,
                )
                for p in list(getattr(violation, "proofs", []) or [])
            ],
        )
    )


@router.post(
    "/traffic-violations/{violation_id}/proofs",
    response_model=SuccessResponse[TrafficViolationUpsertResponse],
    status_code=201,
    **TRAFFIC_VIOLATION_ADD_PROOFS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def add_traffic_violation_proofs(
    request: Request,
    response: Response,
    violation_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    proofs: Annotated[list[UploadFile], File()],
) -> dict:
    """Add one or more proof files to an existing traffic violation."""
    proof_results = await driver_service.add_traffic_violation_proofs(
        violation_id=violation_id,
        proofs=proofs,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    violation = await driver_service.get_traffic_violation(violation_id)
    proofs_entries = [
        TrafficViolationProofEntry(
            id=p.id,
            url=driver_service.get_file_url(p.file_key),
            content_type=p.content_type,
            size_bytes=p.size_bytes,
            created_at=p.created_at,
        )
        for p in list(getattr(violation, "proofs", []) or [])
    ]
    proofs_by_id = {p.id: p for p in proofs_entries}
    return ok(
        data=TrafficViolationUpsertResponse(
            violation=TrafficViolationEntry(
                id=violation.id,
                driver_id=violation.driver_id,
                occurred_at=violation.occurred_at,
                violation_type=violation.violation_type,
                amount=violation.amount,
                status=violation.status,
                notes=violation.notes,
                proofs=proofs_entries,
            ),
            proof_results=[
                _to_proof_upload_result(r, proofs_by_id)
                for r in proof_results
            ],
        ),
    )


@router.delete(
    "/traffic-violations/proofs/{proof_id}",
    response_model=SuccessResponse[dict],
    **TRAFFIC_VIOLATION_DELETE_PROOF,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_traffic_violation_proof(
    request: Request,
    response: Response,
    proof_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Delete a single proof file from a traffic violation."""
    await driver_service.delete_traffic_violation_proof(
        proof_id=proof_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.delete(
    "/traffic-violations/{violation_id}",
    response_model=SuccessResponse[dict],
    **TRAFFIC_VIOLATION_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_traffic_violation(
    request: Request,
    response: Response,
    violation_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Delete a traffic violation."""
    await driver_service.delete_traffic_violation(
        violation_id=violation_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.patch(
    "/shifts/{shift_id}",
    response_model=SuccessResponse[DriverShiftEntry],
    **SHIFT_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_shift(
    request: Request,
    response: Response,
    shift_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    date_value: Annotated[date | None, Form(alias="date")] = None,
    start_time: Annotated[time | None, Form()] = None,
    end_time: Annotated[time | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
) -> dict:
    """Update an existing driver shift."""
    shift = await driver_service.update_shift(
        shift_id=shift_id,
        shift_date=date_value,
        start_time=start_time,
        end_time=end_time,
        status=status,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(
        data=DriverShiftEntry(
            id=shift.id,
            driver_id=shift.driver_id,
            date=shift.shift_date,
            start_time=shift.start_time.timetz(),
            end_time=shift.end_time.timetz(),
            status=shift.status,
        )
    )


@router.delete(
    "/shifts/{shift_id}",
    response_model=SuccessResponse[dict],
    **SHIFT_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_shift(
    request: Request,
    response: Response,
    shift_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Delete a driver shift."""
    await driver_service.delete_shift(
        shift_id=shift_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data={})


@router.patch(
    "/{driver_id}/form",
    response_model=SuccessResponse[DriverDetailResponse],
    **UPDATE_DRIVER_FORM,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_driver_form(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    first_name: Annotated[str | None, Form()] = None,
    last_name: Annotated[str | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    email: Annotated[str | None, Form()] = None,
    driver_type: Annotated[str | None, Form()] = None,
    address_line1: Annotated[str | None, Form()] = None,
    address_line2: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    state: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    postcode: Annotated[str | None, Form()] = None,
    depot_id: Annotated[str | None, Form()] = None,
    vehicle_id: Annotated[str | None, Form()] = None,
    license_number: Annotated[str | None, Form()] = None,
    license_category: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    account_status: Annotated[str | None, Form()] = None,
    live_status: Annotated[str | None, Form()] = None,
    max_stops: Annotated[int | None, Form()] = None,
    okay_with_layover: Annotated[bool | None, Form()] = None,
    layover_cost_per_night: Annotated[str | None, Form()] = None,
    max_layover_nights: Annotated[int | None, Form()] = None,
    expected_version: Annotated[int | None, Form()] = None,
    profile_photo: Annotated[UploadFile | None, File()] = None,
    driving_licence_file: Annotated[UploadFile | None, File()] = None,
    driving_licence_expiry_date: Annotated[date | None, Form()] = None,
    # Back-compat with onboarding-style licence upload fields:
    documents: Annotated[list[UploadFile] | None, File(alias="documents")] = None,
    documents_metadata: Annotated[str | None, Form()] = None,
) -> dict:
    """Update driver using multipart/form-data; supports optional profile photo and driving licence upsert."""
    form = await request.form()
    profile_photo_updated = False
    driving_licence_action: str | None = None

    # Support capacities sent as capacity[0], capacity[1], ... plus repeated capacities keys.
    indexed_capacity: dict[int, str] = {}
    plain_capacities: list[str] = []
    for key, value in form.multi_items():
        key_s = str(key)
        value_s = str(value)
        match = re.fullmatch(r"capacity\[(\d+)\]", key_s)
        if match:
            indexed_capacity[int(match.group(1))] = value_s
            continue
        if key_s in {"capacities", "capacities[]"}:
            plain_capacities.append(value_s)
    capacities_payload: list[str] | None = None
    if indexed_capacity:
        capacities_payload = [indexed_capacity[i] for i in sorted(indexed_capacity.keys())]
    elif plain_capacities:
        capacities_payload = plain_capacities
    if capacities_payload is not None:
        capacities_payload = list(dict.fromkeys(str(item) for item in capacities_payload if item))

    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "capacities": capacities_payload,
        "driver_type": driver_type,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "country": country,
        "state": state,
        "city": city,
        "postcode": postcode,
        "depot_id": depot_id,
        "vehicle_id": vehicle_id,
        "license_number": license_number,
        "license_category": license_category,
        "max_stops": max_stops,
        "account_status": account_status,
        "live_status": live_status,
        "notes": notes,
        "expected_version": expected_version,
    }
    if okay_with_layover is not None:
        payload["okay_with_layover"] = okay_with_layover
    if layover_cost_per_night is not None:
        try:
            payload["layover_cost_per_night"] = Decimal(str(layover_cost_per_night).strip())
        except Exception as exc:
            raise ValidationError("layover_cost_per_night must be a valid decimal amount") from exc
    if max_layover_nights is not None:
        payload["max_layover_nights"] = max_layover_nights
    payload = {k: v for k, v in payload.items() if v is not None}
    if payload:
        body = DriverUpdateRequest.model_validate(payload)
        data = body.model_dump(exclude_unset=True)
        expected = data.pop("expected_version", None)
        await driver_service.update_driver(
            driver_id,
            expected_version=expected,
            audit_user_id=user.id,
            audit_user_role=user.role,
            **data,
        )

    if profile_photo is not None:
        await driver_service.update_profile_photo(
            driver_id,
            profile_photo,
            audit_user_id=user.id,
            audit_user_role=user.role,
        )
        profile_photo_updated = True

    if driving_licence_file is not None:
        if driving_licence_expiry_date is None:
            raise ValidationError("driving_licence_expiry_date is required when driving_licence_file is provided")
        docs = await driver_service.list_driver_documents(driver_id)
        licence = next((d for d in docs if d.kind == DriverDocumentKind.DRIVING_LICENCE.value), None)
        if licence is None:
            await driver_service.create_driver_document(
                driver_id=driver_id,
                kind=DriverDocumentKind.DRIVING_LICENCE.value,
                title=None,
                expiry_date=driving_licence_expiry_date,
                upload=driving_licence_file,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
            driving_licence_action = "created"
        else:
            await driver_service.update_driver_document(
                document_id=licence.id,
                title=None,
                expiry_date=driving_licence_expiry_date,
                upload=driving_licence_file,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
            driving_licence_action = "updated"

    # Alternative input shape: documents + documents_metadata (same as onboarding).
    # Supports exactly one document, and document_type must be DRIVING_LICENCE.
    if driving_licence_file is None and documents:
        if documents_metadata is None:
            raise ValidationError("documents_metadata is required when documents are provided")
        if len(documents) != 1:
            raise ValidationError("Exactly 1 document must be provided in documents[] for update")
        try:
            raw_meta = _json.loads(str(documents_metadata).strip())
        except _json.JSONDecodeError as err:
            raise ValidationError("documents_metadata must be valid JSON") from err
        if not isinstance(raw_meta, list) or len(raw_meta) != 1:
            raise ValidationError("documents_metadata must be a JSON array with exactly 1 object for update")
        try:
            parsed = OnboardDrivingLicenceDocumentMeta.model_validate(raw_meta[0])
        except Exception as exc:
            raise ValidationError(f"Invalid documents_metadata: {exc}") from exc

        upload = documents[0]
        # Validate file first (same as onboarding)
        await validate_document(upload)

        docs = await driver_service.list_driver_documents(driver_id)
        licence = next((d for d in docs if d.kind == DriverDocumentKind.DRIVING_LICENCE.value), None)
        if licence is None:
            await driver_service.create_driver_document(
                driver_id=driver_id,
                kind=DriverDocumentKind.DRIVING_LICENCE.value,
                title=None,
                expiry_date=parsed.expiry_date,
                upload=upload,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
            driving_licence_action = "created"
        else:
            await driver_service.update_driver_document(
                document_id=licence.id,
                title=None,
                expiry_date=parsed.expiry_date,
                upload=upload,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
            driving_licence_action = "updated"

    driver = await driver_service.get_driver(driver_id)
    parts: list[str] = []
    if profile_photo_updated:
        parts.append("profile photo updated")
    if driving_licence_action == "created":
        parts.append("driving licence uploaded")
    elif driving_licence_action == "updated":
        parts.append("driving licence replaced")
    message = "; ".join(parts) if parts else "Driver updated"
    return ok(data=_to_detail_response(driver, driver_service), message=message)


@router.delete(
    "/{driver_id}/profile-photo",
    response_model=SuccessResponse[DriverDetailResponse],
    **PROFILE_PHOTO_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_driver_profile_photo(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Remove a driver's profile photo (admin). Idempotent when no photo exists."""
    await driver_service.remove_profile_photo(
        driver_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver_id)
    return ok(data=_to_detail_response(driver, driver_service), message="Profile photo removed")


@router.patch(
    "/{driver_id}",
    response_model=SuccessResponse[DriverDetailResponse],
    **UPDATE_DRIVER,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_driver(
    request: Request,
    response: Response,
    driver_id: str,
    body: DriverUpdateRequest,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Update driver by ID (partial). Use expected_version for optimistic locking."""
    data = body.model_dump(exclude_unset=True)
    expected_version = data.pop("expected_version", None)
    driver = await driver_service.update_driver(
        driver_id,
        expected_version=expected_version,
        audit_user_id=user.id,
        audit_user_role=user.role,
        **data,
    )
    driver = await driver_service.get_driver(driver.id)
    return ok(data=_to_detail_response(driver, driver_service))


@router.delete(
    "/{driver_id}",
    response_model=SuccessResponse[DriverDetailResponse],
    **DELETE_DRIVER,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_driver(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Hard-delete driver and linked user, plus best-effort storage cleanup."""
    driver_snapshot = await driver_service.get_driver(driver_id)
    await driver_service.hard_delete_driver(
        driver_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(
        data=_to_detail_response(driver_snapshot, driver_service),
        message="Driver deleted",
    )


@router.post(
    "/{driver_id}/suspend",
    response_model=SuccessResponse[DriverDetailResponse],
    **SUSPEND_DRIVER,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def suspend_driver(
    request: Request,
    response: Response,
    driver_id: str,
    body: SuspendDriverRequest,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Suspend a driver account with a reason."""
    driver = await driver_service.suspend_driver(
        driver_id,
        reason=body.reason,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver.id)
    return ok(data=_to_detail_response(driver, driver_service))


@router.post(
    "/{driver_id}/reactivate",
    response_model=SuccessResponse[DriverDetailResponse],
    **REACTIVATE_DRIVER,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def reactivate_driver(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
    body: ReactivateDriverRequest | None = None,
) -> dict:
    """Reactivate a suspended driver account."""
    driver = await driver_service.reactivate_driver(
        driver_id,
        reason=body.reason if body is not None else None,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    driver = await driver_service.get_driver(driver.id)
    return ok(data=_to_detail_response(driver, driver_service))


@router.post(
    "/{driver_id}/password-reset",
    response_model=MessageResponse,
    **PASSWORD_RESET_DRIVER,
    **PASSWORD_RESET_DRIVER_REQUEST_BODY,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def request_driver_password_reset(
    request: Request,
    response: Response,
    driver_id: str,
    body: AdminDriverPasswordChangeRequest,
    driver_service: DriverServiceDep,
    auth_service: AuthServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Admin-only: directly change the driver's account password (no current password required)."""
    driver = await driver_service.get_driver(driver_id)
    await auth_service.set_password_admin(
        user_id=driver.user_id,
        new_password=body.new_password,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(message="Driver password changed successfully. Driver must log in with the new password.")


@router.get(
    "/{driver_id}/schedule",
    response_model=SuccessResponse[WeeklyScheduleResponse],
    **SCHEDULE_GET,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_weekly_schedule(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    """Get full weekly work schedule for a driver."""
    rows, total_hours = await driver_service.get_weekly_schedule(driver_id)
    days = [
        WeeklyScheduleDay(
            day_of_week=row.day_of_week,
            is_active=row.is_active,
            start_time=row.start_time,
            end_time=row.end_time,
        )
        for row in rows
    ]
    return ok(data=WeeklyScheduleResponse(days=days, total_weekly_hours=total_hours))


@router.put(
    "/{driver_id}/schedule",
    response_model=SuccessResponse[WeeklyScheduleResponse],
    **SCHEDULE_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def bulk_update_weekly_schedule(
    request: Request,
    response: Response,
    driver_id: str,
    body: WeeklyScheduleResponse,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Bulk update weekly schedule for a driver."""
    days_payload = [(d.day_of_week, d.is_active, d.start_time, d.end_time) for d in body.days]
    await driver_service.bulk_update_weekly_schedule(
        driver_id=driver_id,
        days=days_payload,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    rows, total_hours = await driver_service.get_weekly_schedule(driver_id)
    days = [
        WeeklyScheduleDay(
            day_of_week=row.day_of_week,
            is_active=row.is_active,
            start_time=row.start_time,
            end_time=row.end_time,
        )
        for row in rows
    ]
    return ok(data=WeeklyScheduleResponse(days=days, total_weekly_hours=total_hours))


@router.patch(
    "/{driver_id}/schedule/{day_of_week}",
    response_model=SuccessResponse[WeeklyScheduleResponse],
    **SCHEDULE_UPDATE_DAY,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_schedule_day(
    request: Request,
    response: Response,
    driver_id: str,
    day_of_week: int,
    body: WeeklyScheduleDay,
    driver_service: DriverServiceDep,
    user: DriverWriteDep,
) -> dict:
    """Update a single day in the weekly schedule."""
    await driver_service.update_schedule_day(
        driver_id=driver_id,
        day_of_week=day_of_week,
        is_active=body.is_active,
        start_time=body.start_time,
        end_time=body.end_time,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    rows, total_hours = await driver_service.get_weekly_schedule(driver_id)
    days = [
        WeeklyScheduleDay(
            day_of_week=row.day_of_week,
            is_active=row.is_active,
            start_time=row.start_time,
            end_time=row.end_time,
        )
        for row in rows
    ]
    return ok(data=WeeklyScheduleResponse(days=days, total_weekly_hours=total_hours))


@router.get(
    "/{driver_id}/schedule-availability/calendar",
    response_model=SuccessResponse[DriverCalendarResponse],
    **DRIVER_SCHEDULE_AVAILABILITY_CALENDAR,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_driver_schedule_availability_calendar(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    event_source: Annotated[
        list[CalendarEventSource] | None,
        Query(description="Calendar event source filter (multi-select)"),
    ] = None,
    shift_status: Annotated[
        list[ShiftStatus] | None,
        Query(description="Shift status filter (multi-select)"),
    ] = None,
    time_off_type: Annotated[
        list[TimeOffType] | None,
        Query(description="Time-off type filter (multi-select)"),
    ] = None,
    route_type: Annotated[
        list[RouteType] | None,
        Query(description="Route type filter (multi-select)"),
    ] = None,
    route_status: Annotated[
        list[RouteStatus] | None,
        Query(description="Route status filter (multi-select)"),
    ] = None,
) -> dict:
    payload = await driver_service.get_schedule_availability_calendar(
        driver_id=driver_id,
        from_date=from_date,
        to_date=to_date,
        event_source=[s.value for s in (event_source or [])] or None,
        shift_status=[s.value for s in (shift_status or [])] or None,
        time_off_type=[t.value for t in (time_off_type or [])] or None,
        route_type=[t.value for t in (route_type or [])] or None,
        route_status=[s.value for s in (route_status or [])] or None,
    )
    return ok(data=DriverCalendarResponse(**payload))


@router.get(
    "/{driver_id}/route-history",
    response_model=SuccessResponse[RouteHistoryResponse],
    **LIST_DRIVER_ROUTE_HISTORY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_driver_route_history(
    request: Request,
    response: Response,
    driver_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    type: Annotated[
        list[RouteType] | None,
        Query(description="Route type filter (multi-select)"),
    ] = None,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str | None, Query()] = "date",
    sort_desc: Annotated[bool, Query()] = True,
) -> dict:
    rows, total = await driver_service.list_driver_routes_history(
        driver_id=driver_id,
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
    "/routes/{route_id}/summary",
    response_model=SuccessResponse[RouteSummaryResponse],
    **GET_ROUTE_SUMMARY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_route_summary(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
) -> dict:
    payload = await driver_service.get_route_summary_payload(route_id)
    return ok(data=RouteSummaryResponse(**payload))


@router.get(
    "/routes/{route_id}/telematics",
    response_model=SuccessResponse[RouteEventsResponse],
    **LIST_ROUTE_TELEMATICS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_route_telematics(
    request: Request,
    response: Response,
    route_id: str,
    driver_service: DriverServiceDep,
    _user: DriverReadDep,
    event_type: Annotated[list[str] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
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
