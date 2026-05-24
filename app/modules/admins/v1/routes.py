"""Admin management routes.

Endpoints:
  GET  /admins/stats                 — Admin statistics (total, active, inactive, suspended).
  GET  /admins                       — Paginated list (search, filter by status/date, sort).
  POST /admins                       — Create new admin + optional invite.
  POST /admins/{user_id}/invite      — Send invite to a draft admin.
  POST /admins/{user_id}/support-issue-password — Support password reset body ``new_password`` (RESET_ADMIN_PASSWORDS WRITE).
  GET  /admins/{user_id}             — Full admin detail + permissions.
  PATCH /admins/{user_id}            — Update profile (message-only response; GET for detail).
  PATCH /admins/{user_id}/permissions — Replace permission overrides (message-only; GET for detail).
  POST /admins/{user_id}/suspend     — Suspend active admin (requires ADMINS WRITE; not self).
  POST /admins/{user_id}/reactivate  — Reactivate suspended admin (requires ADMINS WRITE; not self).

Authorization:
  ``Resource.ADMINS`` at **READ** for list/get/stats; **WRITE** for create, update, invite,
  permissions, suspend, reactivate, delete, and support-issue-password. Suspend, delete,
  reactivate, and permission replacement cannot target your own account (enforced in the service).
"""

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from pydantic import EmailStr

from app.common.deps import Allowed, AuthUser
from app.common.enums import ROLE_TO_CLIENT_TYPE, ClientType, Job, UserRole, UserStatus, UserTitle
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ValidationError
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.common.utils import build_driver_set_password_link, build_email_link
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.modules.admins.models import Admin
from app.modules.admins.service import AdminService
from app.modules.admins.v1.docs import (
    CREATE_ADMIN,
    DELETE_ADMIN,
    GET_ADMIN,
    GET_ADMIN_STATS,
    LIST_ADMINS,
    REACTIVATE_ADMIN,
    SEND_ADMIN_INVITE,
    SUPPORT_ISSUE_ADMIN_PASSWORD,
    SUSPEND_ADMIN,
    UPDATE_ADMIN,
    UPDATE_ADMIN_PERMISSIONS,
)
from app.modules.admins.v1.schemas import (
    AdminListItemResponse,
    AdminPermissionEntry,
    AdminPostalPatchRequest,
    AdminResponse,
    AdminStatsResponse,
    AdminStatusChangeRequest,
    AssignedOrgItem,
    CreateAdminResponse,
    SendAdminInviteResponse,
    UpdateAdminPermissionsRequest,
)
from app.modules.auth.service import AuthService
from app.modules.auth.v1.schemas import SupportIssuePasswordRequest, SupportIssuePasswordResponse
from app.modules.user.v1.schemas import SendInviteResponse
from app.modules.permission.service import PermissionService

router = APIRouter()


def _admin_address_fields(admin: Admin) -> dict[str, str | None]:
    return {
        "address_line_1": admin.address_line_1,
        "address_line_2": admin.address_line_2,
        "city": admin.city,
        "state": admin.state,
        "postcode": admin.postcode,
        "country": admin.country,
    }


AdminServiceDep = Annotated[AdminService, Depends(AdminService.dep)]
PermissionServiceDep = Annotated[PermissionService, Depends(PermissionService.dep)]

_ANY_ADMIN = (UserRole.ADMIN, UserRole.SUPER_ADMIN)

AdminsReadDep = Annotated[
    AuthUser,
    Allowed(*_ANY_ADMIN, resource=Resource.ADMINS, level=PermissionLevel.READ),
]
AdminsWriteDep = Annotated[
    AuthUser,
    Allowed(*_ANY_ADMIN, resource=Resource.ADMINS, level=PermissionLevel.WRITE),
]
ResetAdminPasswordDep = Annotated[
    AuthUser,
    Allowed(*_ANY_ADMIN, resource=Resource.RESET_ADMIN_PASSWORDS, level=PermissionLevel.WRITE),
]
AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get(
    "/stats",
    response_model=SuccessResponse[AdminStatsResponse],
    **GET_ADMIN_STATS,
)
async def get_admin_stats(
    user: AdminsReadDep,
    admin_service: AdminServiceDep,
) -> dict:
    """Get admin statistics (total, active, inactive, suspended)."""
    stats = await admin_service.get_admin_stats()
    return ok(AdminStatsResponse(**stats))


# ── List ──────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[PaginatedResponse[AdminListItemResponse]],
    **LIST_ADMINS,
)
async def list_admins(
    request: Request,
    user: AdminsReadDep,
    admin_service: AdminServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[
        str | None,
        Query(description="Search by admin_ref, name, email, phone, position, or postal fields"),
    ] = None,
    status: Annotated[UserStatus | None, Query(description="Filter by account status")] = None,
    sort: Annotated[str, Query(description="newest | oldest | name_asc | name_desc")] = "newest",
    date_from: Annotated[date | None, Query(description="Filter by created_at >= date (ISO format)")] = None,
    date_to: Annotated[date | None, Query(description="Filter by created_at <= date (ISO format)")] = None,
) -> dict:
    """List admin users with pagination, search, status filter, and date range."""
    admins, total = await admin_service.list_admins(
        page=page,
        size=size,
        search=search,
        status=status,
        sort=sort,
        date_from=date_from,
        date_to=date_to,
    )

    user_ids = [usr.id for adm, usr in admins]
    assignments = await admin_service.get_account_assignments(user_ids)

    items = [
        AdminListItemResponse(
            id=usr.id,
            admin_ref=adm.admin_ref,
            title=usr.title,
            first_name=usr.first_name,
            last_name=usr.last_name,
            full_name=usr.full_name,
            email=usr.email,
            phone=usr.phone,
            position_role=usr.position_role,
            **_admin_address_fields(adm),
            role=usr.role,
            status=usr.status,
            last_login=usr.last_login,
            created_at=usr.created_at,
            assigned_accounts=[
                AssignedOrgItem(**org) for org in assignments.get(usr.id, [])
            ],
        )
        for adm, usr in admins
    ]

    paginated = PaginatedResponse[AdminListItemResponse].create(
        items=items, total=total, page=page, size=size, request=request
    )
    return ok(paginated)


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=SuccessResponse[CreateAdminResponse],
    status_code=status.HTTP_201_CREATED,
    **CREATE_ADMIN,
)
async def create_admin(
    request: Request,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
    first_name: Annotated[str, Form(min_length=1, max_length=100)],
    last_name: Annotated[str, Form(min_length=1, max_length=100)],
    email: Annotated[EmailStr, Form()],
    address_line_1: Annotated[str, Form(min_length=1, max_length=255)],
    city: Annotated[str, Form(min_length=1, max_length=100)],
    state: Annotated[str, Form(min_length=1, max_length=100)],
    postcode: Annotated[str, Form(min_length=1, max_length=20)],
    title: Annotated[str | None, Form(description="MR | MRS | MS | DR | PROF")] = None,
    phone: Annotated[str | None, Form(max_length=50)] = None,
    position_role: Annotated[str | None, Form(max_length=150)] = None,
    address_line_2: Annotated[str | None, Form(max_length=255)] = None,
    country: Annotated[str | None, Form(max_length=100)] = None,
    permissions: Annotated[
        str | None,
        Form(description='JSON array: [{"resource": "DRIVERS", "level": "WRITE"}, ...]'),
    ] = None,
    send_invite: Annotated[bool, Form()] = True,
    profile_photo: Annotated[
        UploadFile | None,
        File(description="Profile photo — JPEG or PNG, max 5 MB"),
    ] = None,
) -> dict:
    """Create a new admin user with permission assignments."""
    title_value: str | None = None
    if title is not None:
        try:
            title_value = UserTitle(title).value
        except ValueError:
            valid = ", ".join(t.value for t in UserTitle)
            raise ValidationError(f"Invalid title '{title}'. Must be one of: {valid}") from None

    resolved_permissions: dict[Resource, PermissionLevel] = {}
    if permissions:
        try:
            perm_list = json.loads(permissions)
            entries = [AdminPermissionEntry(**p) for p in perm_list]
            resolved_permissions = {Resource(p.resource): PermissionLevel[p.level] for p in entries}
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            raise ValidationError(f"Invalid permissions value: {exc}") from exc

    user_id, invite_id, raw_token = await admin_service.create_admin(
        email=email,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        title=title_value,
        position_role=position_role,
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        postcode=postcode,
        country=country,
        permissions=resolved_permissions,
        inviter=user,
        send_invite=send_invite,
    )

    photo_upload_failed = False
    if profile_photo is not None:
        try:
            await admin_service.upload_profile_photo(
                user_id,
                profile_photo,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
        except Exception:
            photo_upload_failed = True

    message = "Admin created as draft. Send the invite when ready."

    if send_invite and raw_token:
        invite_link = build_email_link(ClientType.ADMIN, "accept-invite", raw_token)
        if invite_link:
            job = await enqueue(
                Job.SEND_INVITE_EMAIL,
                invite_id,
                email,
                first_name,
                invite_link,
                expires_days=AuthService.INVITE_EXPIRE_DAYS,
            )
            message = (
                "Admin created and invite email is being sent."
                if job is not None
                else "Admin created. Arq worker unavailable; email will be sent when worker starts."
            )
        else:
            message = "Admin created. Set LINK_BASE_URL_ADMIN to enable invite emails."
    elif send_invite:
        message = "Admin created and invite email is being sent."

    return ok(
        data=CreateAdminResponse(
            user_id=user_id,
            email=email,
            invite_id=invite_id,
            status="PENDING_VERIFICATION",
            photo_upload_failed=photo_upload_failed,
        ),
        message=message,
    )


# ── Send invite ───────────────────────────────────────────────────────────────


@router.post(
    "/activation-link-requests/{request_id}/resend-invite",
    response_model=SuccessResponse[SendInviteResponse],
    status_code=status.HTTP_201_CREATED,
)
async def resend_activation_link_request_invite(
    request_id: str,
    user: AdminsWriteDep,
    auth_service: AuthServiceDep,
) -> dict:
    """Handle one shared activation-link request by sending a fresh invite once.

    Drivers receive the dedicated set-password deep link; everyone else gets the
    standard accept-invite landing-page link.
    """
    result = await auth_service.resend_invite_for_activation_link_request(user, request_id)

    if result.throttled:
        return ok(
            data=SendInviteResponse(invite_id=result.public_invite_id, email=result.user.email),
            message="Invite created. Email is being sent.",
        )

    if result.user.role == UserRole.DRIVER:
        link = build_driver_set_password_link(
            token=result.raw_token or "", email=result.user.email
        )
        if link:
            job = await enqueue(
                Job.SEND_DRIVER_ACTIVATION_EMAIL,
                result.public_invite_id,
                result.user.email,
                (result.user.first_name or "").strip() or "there",
                link,
                expires_days=settings.DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS,
                priority=QueuePriority.HIGH,
            )
            message = (
                "Invite email is being sent."
                if job is not None
                else "Invite created. Arq worker unavailable; email will be sent when worker starts."
            )
        else:
            message = "Invite created. Set LINK_BASE_URL_DRIVER to enable driver activation emails."
    else:
        client_type = ROLE_TO_CLIENT_TYPE.get(result.user.role, ClientType.CUSTOMER_B2C)
        invite_link = build_email_link(client_type, "accept-invite", result.raw_token or "")
        if invite_link:
            job = await enqueue(
                Job.SEND_INVITE_EMAIL,
                result.public_invite_id,
                result.user.email,
                result.user.first_name,
                invite_link,
                expires_days=AuthService.INVITE_EXPIRE_DAYS,
            )
            message = (
                "Invite email is being sent."
                if job is not None
                else "Invite created. Arq worker unavailable; email will be sent when worker starts."
            )
        else:
            message = "Invite created. Configure the matching invite link base URL to enable invite emails."

    return ok(
        data=SendInviteResponse(invite_id=result.public_invite_id, email=result.user.email),
        message=message,
    )


@router.post(
    "/{user_id}/invite",
    response_model=SuccessResponse[SendAdminInviteResponse],
    status_code=status.HTTP_201_CREATED,
    **SEND_ADMIN_INVITE,
)
async def send_admin_invite(
    request: Request,
    user_id: str,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
) -> dict:
    """Send an invite email to a draft admin (status=PENDING_VERIFICATION)."""
    invite_id, raw_token, email, first_name = await admin_service.send_invite(
        user_id=user_id,
        inviter=user,
    )

    invite_link = ""
    message = "Invite created. Email is being sent."
    if raw_token:
        invite_link = build_email_link(ClientType.ADMIN, "accept-invite", raw_token)
        if invite_link:
            job = await enqueue(
                Job.SEND_INVITE_EMAIL,
                invite_id,
                email,
                first_name,
                invite_link,
                expires_days=AuthService.INVITE_EXPIRE_DAYS,
            )
            message = (
                "Invite email is being sent."
                if job is not None
                else "Invite created. Arq worker unavailable; email will be sent when worker starts."
            )
        else:
            message = "Invite created. Set LINK_BASE_URL_ADMIN to enable invite emails."

    return ok(
        data=SendAdminInviteResponse(invite_id=invite_id, email=email),
        message=message,
    )


@router.post(
    "/{user_id}/support-issue-password",
    response_model=SuccessResponse[SupportIssuePasswordResponse],
    **SUPPORT_ISSUE_ADMIN_PASSWORD,
)
async def support_issue_admin_password(
    user_id: str,
    admin: ResetAdminPasswordDep,
    body: SupportIssuePasswordRequest,
    auth_service: AuthServiceDep,
) -> dict:
    uid, email = await auth_service.support_issue_temporary_password(
        actor=admin,
        target_user_id=user_id,
        new_password=body.new_password,
        flow="admin_staff",
    )
    return ok(
        data=SupportIssuePasswordResponse(user_id=uid, email=email),
        message="Password reset. The user was signed out of all sessions.",
    )


# ── Get single ────────────────────────────────────────────────────────────────


@router.get(
    "/{user_id}",
    response_model=SuccessResponse[AdminResponse],
    **GET_ADMIN,
)
async def get_admin(
    user_id: str,
    user: AdminsReadDep,
    admin_service: AdminServiceDep,
    perm_service: PermissionServiceDep,
) -> dict:
    adm, target = await admin_service.get_admin(user_id)

    summary = await perm_service.get_user_permission_summary(target)
    permissions = [
        AdminPermissionEntry(resource=r, level=p["level"])
        for r, p in summary.items()
        if PermissionLevel[p["level"]] != PermissionLevel.NONE
    ]

    return ok(
        AdminResponse(
            id=target.id,
            admin_ref=adm.admin_ref,
            title=target.title,
            first_name=target.first_name,
            last_name=target.last_name,
            full_name=target.full_name,
            email=target.email,
            phone=target.phone,
            position_role=target.position_role,
            **_admin_address_fields(adm),
            role=target.role,
            status=target.status,
            last_login=target.last_login,
            profile_photo_url=admin_service.get_profile_photo_url(target.avatar_url),
            permissions=permissions,
            created_at=target.created_at,
            updated_at=target.updated_at,
            version=target.version,
        )
    )


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch(
    "/{user_id}",
    response_model=SuccessResponse[dict],
    **UPDATE_ADMIN,
)
async def update_admin(
    user_id: str,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
    first_name: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    last_name: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    title: Annotated[str | None, Form(description="MR | MRS | MS | DR | PROF")] = None,
    phone: Annotated[str | None, Form(max_length=50)] = None,
    position_role: Annotated[str | None, Form(max_length=150)] = None,
    address_line_1: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    address_line_2: Annotated[str | None, Form(max_length=255)] = None,
    city: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    state: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    postcode: Annotated[str | None, Form(min_length=1, max_length=20)] = None,
    country: Annotated[str | None, Form(max_length=100)] = None,
    profile_photo: Annotated[
        UploadFile | None,
        File(description="Profile photo — JPEG or PNG, max 5 MB"),
    ] = None,
) -> dict:
    title_value: str | None = None
    if title is not None:
        try:
            title_value = UserTitle(title).value
        except ValueError:
            valid = ", ".join(t.value for t in UserTitle)
            raise ValidationError(f"Invalid title '{title}'. Must be one of: {valid}") from None

    postal = AdminPostalPatchRequest(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        postcode=postcode,
        country=country,
    )

    adm, target = await admin_service.update_admin(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        title=title_value,
        position_role=position_role,
        address_line_1=postal.address_line_1,
        address_line_2=postal.address_line_2,
        city=postal.city,
        state=postal.state,
        postcode=postal.postcode,
        country=postal.country,
        permissions=None,
        updated_by_user_id=user.id,
        updated_by_user_role=user.role,
    )

    photo_upload_failed = False
    if profile_photo is not None:
        try:
            await admin_service.upload_profile_photo(
                target.id,
                profile_photo,
                audit_user_id=user.id,
                audit_user_role=user.role,
            )
        except Exception:
            photo_upload_failed = True

    return ok(
        message=(
            "Admin updated successfully."
            if not photo_upload_failed
            else "Admin updated, but profile photo upload failed."
        ),
    )


@router.patch(
    "/{user_id}/permissions",
    response_model=SuccessResponse[dict],
    **UPDATE_ADMIN_PERMISSIONS,
)
async def update_admin_permissions(
    user_id: str,
    data: UpdateAdminPermissionsRequest,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
) -> dict:
    await admin_service.update_admin_permissions(
        user_id=user_id,
        permissions=data.resolved_permissions(),
        updated_by_user_id=user.id,
        updated_by_user_role=user.role,
    )

    return ok(message="Admin permissions updated successfully.")


# ── Suspend ───────────────────────────────────────────────────────────────────


@router.post(
    "/{user_id}/suspend",
    response_model=SuccessResponse[AdminResponse],
    **SUSPEND_ADMIN,
)
async def suspend_admin(
    user_id: str,
    data: AdminStatusChangeRequest,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
    perm_service: PermissionServiceDep,
) -> dict:
    """Suspend an active admin account. Requires ``ADMINS`` WRITE; cannot suspend yourself."""
    adm, target = await admin_service.suspend_admin(
        user_id=user_id,
        reason=data.reason,
        suspended_by_user_id=user.id,
        suspended_by_user_role=user.role,
    )

    summary = await perm_service.get_user_permission_summary(target)
    permissions = [
        AdminPermissionEntry(resource=r, level=p["level"])
        for r, p in summary.items()
        if PermissionLevel[p["level"]] != PermissionLevel.NONE
    ]

    return ok(
        AdminResponse(
            id=target.id,
            admin_ref=adm.admin_ref,
            title=target.title,
            first_name=target.first_name,
            last_name=target.last_name,
            full_name=target.full_name,
            email=target.email,
            phone=target.phone,
            position_role=target.position_role,
            **_admin_address_fields(adm),
            role=target.role,
            status=target.status,
            last_login=target.last_login,
            profile_photo_url=admin_service.get_profile_photo_url(target.avatar_url),
            permissions=permissions,
            created_at=target.created_at,
            updated_at=target.updated_at,
            version=target.version,
        ),
        message="Admin account suspended.",
    )


# ── Reactivate ────────────────────────────────────────────────────────────────


@router.post(
    "/{user_id}/reactivate",
    response_model=SuccessResponse[AdminResponse],
    **REACTIVATE_ADMIN,
)
async def reactivate_admin(
    user_id: str,
    data: AdminStatusChangeRequest,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
    perm_service: PermissionServiceDep,
) -> dict:
    """Reactivate a suspended admin account. Requires ``ADMINS`` WRITE; cannot reactivate yourself."""
    adm, target = await admin_service.reactivate_admin(
        user_id=user_id,
        reason=data.reason,
        reactivated_by_user_id=user.id,
        reactivated_by_user_role=user.role,
    )

    summary = await perm_service.get_user_permission_summary(target)
    permissions = [
        AdminPermissionEntry(resource=r, level=p["level"])
        for r, p in summary.items()
        if PermissionLevel[p["level"]] != PermissionLevel.NONE
    ]

    return ok(
        AdminResponse(
            id=target.id,
            admin_ref=adm.admin_ref,
            title=target.title,
            first_name=target.first_name,
            last_name=target.last_name,
            full_name=target.full_name,
            email=target.email,
            phone=target.phone,
            position_role=target.position_role,
            **_admin_address_fields(adm),
            role=target.role,
            status=target.status,
            last_login=target.last_login,
            profile_photo_url=admin_service.get_profile_photo_url(target.avatar_url),
            permissions=permissions,
            created_at=target.created_at,
            updated_at=target.updated_at,
            version=target.version,
        ),
        message="Admin account reactivated.",
    )


@router.delete(
    "/{user_id}",
    response_model=SuccessResponse[dict],
    **DELETE_ADMIN,
)
async def delete_admin(
    user_id: str,
    user: AdminsWriteDep,
    admin_service: AdminServiceDep,
) -> dict:
    """Hard-delete an admin. Requires ``ADMINS`` WRITE; cannot delete yourself."""
    await admin_service.delete_admin(
        user_id=user_id,
        deleted_by_user_id=user.id,
        deleted_by_user_role=user.role,
    )
    return ok(message="Admin deleted successfully.")
