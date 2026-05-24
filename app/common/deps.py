from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Optional, Protocol
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, File, Form, Header, Request, UploadFile
from pydantic import Json
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.common.enums import ROLE_TO_CLIENT_TYPE, ClientType, LogEvent, PermissionLevel, Resource, UserRole
from app.common.exceptions import AuthenticationError, ForbiddenError, ValidationError
from app.common.types import AuditContext
from app.common.utils import get_client_type_from_header, is_session_revoked, is_token_blacklisted, is_user_suspended
from app.core.database import get_db_session
from app.core.security import TokenType, decode_token
from app.storage.upload import (
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_IMAGE_TYPES,
    ALLOWED_ORG_DOCUMENT_TYPES,
    MAX_DOCUMENT_SIZE,
    MAX_IMAGE_SIZE,
    MAX_ORG_DOCUMENT_SIZE,
    MAX_DOCUMENT_SIZE,
    MAX_IMAGE_SIZE,
    read_and_validate,
)

logger = structlog.get_logger()

_bearer_scheme = HTTPBearer(auto_error=False)

BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class UserLike(Protocol):
    """Structural type for anything with id + role (AuthUser, User ORM, etc.)."""

    @property
    def id(self) -> str | Mapped[str]: ...
    @property
    def role(self) -> str | UserRole | Mapped[UserRole]: ...


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Lightweight identity extracted from JWT claims — no DB call."""

    id: str
    role: str
    client_type: ClientType
    jti: str
    sid: str | None = None
    sv: int | None = None
    organization_id: str | None = None


# Client type dependency


def _resolve_client_type(
    x_client_type: Annotated[
        str | None,
        Header(description="Client type: ADMIN, CUSTOMER_B2B, CUSTOMER_B2C, WAREHOUSE, or DRIVER"),
    ] = None,
) -> ClientType:
    # get_client_type_from_header raises AuthenticationError(401) on None/invalid,
    # which produces a 401 response — not a 422 RequestValidationError.
    return get_client_type_from_header(x_client_type)


ClientTypeDep = Annotated[ClientType, Depends(_resolve_client_type)]


# Core auth dependency (stateless)


async def _get_auth_user(
    request: Request,
    credentials: BearerDep,
    client_type: ClientTypeDep,
    session: SessionDep,
) -> AuthUser:
    if credentials is None:
        raise AuthenticationError("Missing authorization header")

    try:
        payload = decode_token(credentials.credentials, TokenType.ACCESS)
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Access token has expired") from None
    except jwt.InvalidTokenError as exc:
        logger.warning(LogEvent.JWT_DECODE_FAILED, error=str(exc))
        raise AuthenticationError("Invalid access token") from None

    jti = payload.get("jti")
    if not jti:
        raise AuthenticationError("Invalid token format")

    if await is_token_blacklisted(jti):
        raise AuthenticationError("Token has been revoked")

    # Per-request session revocation: only enforced when tokens include sid.
    raw_sid = payload.get("sid")
    if raw_sid is None or raw_sid == "":
        sid = None
    else:
        sid = str(raw_sid).strip()
    if sid and await is_session_revoked(sid):
        raise AuthenticationError("Session has been revoked")

    user_id = payload.get("sub")
    if user_id and await is_user_suspended(user_id):
        logger.info(LogEvent.USER_SUSPENDED_BLOCKED, user_id=user_id)
        raise AuthenticationError("Your account has been suspended. Please contact support.")

    token_aud = payload.get("aud")
    if not token_aud:
        raise AuthenticationError("Token format is outdated; please log in again")

    if token_aud != client_type:
        logger.warning(
            LogEvent.AUDIENCE_MISMATCH,
            user_id=payload.get("sub"),
            token_aud=token_aud,
            request_client=client_type,
        )
        raise AuthenticationError("Token was issued for a different client. Use the correct app or portal.")

    role = payload.get("role", "")
    # logout-all race safety: validate sv only when present (backcompat).
    sv = payload.get("sv")
    if sv is not None:
        try:
            sv_int = int(sv)
        except (TypeError, ValueError):
            raise AuthenticationError("Invalid token format") from None

        from sqlalchemy import select

        from app.modules.user.models import User

        stmt = select(User.session_sv).where(User.id == payload["sub"])
        result = await session.execute(stmt)
        current_sv = result.scalar_one_or_none()
        if current_sv is None:
            raise AuthenticationError("User not found")
        if sv_int != int(current_sv):
            raise AuthenticationError("Session has been revoked")
    expected_ct = ROLE_TO_CLIENT_TYPE.get(role)
    if expected_ct is not None and expected_ct != client_type:
        raise AuthenticationError("Role/client type mismatch")

    auth_user = AuthUser(
        id=payload["sub"],
        role=role,
        client_type=client_type,
        jti=jti,
        sid=sid,
        sv=int(sv) if sv is not None and str(sv).isdigit() else None,
        organization_id=payload.get("org_id") or None,
    )
    request.state.auth_user = auth_user
    request.state.token_payload = payload
    return auth_user


CurrentUserDep = Annotated[AuthUser, Depends(_get_auth_user)]


# Audit context dependency


def _build_audit_ctx(request: Request, user: CurrentUserDep) -> AuditContext:
    ctx = AuditContext.from_request(user, request)
    # Stash on request.state so AuditService can auto-pick session_id/correlation_id
    # for write sites that don't explicitly forward them (most legacy callers).
    request.state.audit_ctx = ctx
    return ctx


AuditCtxDep = Annotated[AuditContext, Depends(_build_audit_ctx)]


# Role-based access control


def require_role(*allowed_roles: UserRole) -> Callable:
    allowed_values = {r.value for r in allowed_roles}

    async def _checker(user: CurrentUserDep) -> AuthUser:
        if user.role not in allowed_values:
            logger.warning(
                LogEvent.RBAC_DENIED,
                user_id=user.id,
                user_role=user.role,
                required_roles=[r.value for r in allowed_roles],
            )
            raise ForbiddenError(f"This action requires one of: {', '.join(r.value for r in allowed_roles)}")
        return user

    return _checker


# Unified auth gate


@dataclass(frozen=True, slots=True)
class AllowedPolicy:
    """One branch of an OR-ed auth gate (see :func:`Allowed` ``policies`` kwarg).

    ``roles`` is the set of roles this branch applies to (must be non-empty).
    ``resource`` + ``level`` add an optional ACL check on top: when ``resource``
    is ``None`` the branch is role-only (no permission-table lookup).

    Branches are evaluated top-to-bottom; the first branch whose ``roles``
    contains the caller's role is the one used — so a user is only ever
    checked against one branch's ACL. If no branch matches the caller's role
    the request is rejected with a 403 listing every role permitted by any
    branch.
    """

    roles: tuple[UserRole, ...]
    resource: Resource | None = None
    level: PermissionLevel = PermissionLevel.READ


def Allowed(  # noqa: N802
    *roles: UserRole,
    resource: Resource | None = None,
    level: PermissionLevel = PermissionLevel.READ,
    policies: list[AllowedPolicy] | None = None,
) -> Any:
    """Single auth gate — pass roles, permissions, or both.

    Usage with Annotated (recommended for type hints)::

        # RBAC only
        user: Annotated[AuthUser, Allowed(UserRole.ADMIN)]
        user: Annotated[AuthUser, Allowed(UserRole.CUSTOMER_B2B, UserRole.CUSTOMER_B2C)]

        # ACL only (any authenticated user, checked against permission table)
        user: Annotated[AuthUser, Allowed(resource=Resource.USERS, level=PermissionLevel.READ)]

        # RBAC + ACL (role first, then permission)
        user: Annotated[AuthUser, Allowed(UserRole.ADMIN, resource=Resource.USERS, level=PermissionLevel.WRITE)]

        # Auth only (no role/permission check)
        user: Annotated[AuthUser, Allowed()]

        # Per-role policies — each user type checked against its own resource:
        user: Annotated[AuthUser, Allowed(policies=[
            AllowedPolicy(roles=(UserRole.ADMIN, UserRole.SUPER_ADMIN),
                          resource=Resource.BILLING, level=PermissionLevel.READ),
            AllowedPolicy(roles=(UserRole.CUSTOMER_B2B,),
                          resource=Resource.BILLING, level=PermissionLevel.READ),
            AllowedPolicy(roles=(UserRole.CUSTOMER_B2C,)),  # role-only
        ])]
    """

    if policies is not None:
        if roles or resource is not None:
            raise ValueError(
                "Allowed(): pass either flat `roles`/`resource`/`level` OR `policies`, not both.",
            )
        if not policies:
            raise ValueError("Allowed(): `policies` must contain at least one AllowedPolicy.")
        return _build_policies_dep(policies)

    if resource is not None:
        role_set = {r.value for r in roles} if roles else None

        async def _perm_checker(user: CurrentUserDep, session: SessionDep) -> AuthUser:
            if role_set and user.role not in role_set:
                logger.warning(
                    LogEvent.RBAC_DENIED,
                    user_id=user.id,
                    user_role=user.role,
                    required_roles=list(role_set),
                )
                raise ForbiddenError(f"This action requires one of: {', '.join(role_set)}")

            from app.modules.permission.service import PermissionService

            perm_service = PermissionService(session)
            await perm_service.check_permission(user, resource, level)
            return user

        return Depends(_perm_checker)

    if roles:
        return Depends(require_role(*roles))

    return Depends(_get_auth_user)


def _build_policies_dep(policies: list[AllowedPolicy]) -> Any:
    """Compile a list of :class:`AllowedPolicy` branches into a FastAPI dependency.

    First-branch-wins: the caller's role selects exactly one branch, whose ACL
    (if any) is then enforced.
    """
    all_allowed_roles: list[str] = []
    for p in policies:
        if not p.roles:
            raise ValueError("AllowedPolicy.roles must be non-empty.")
        for r in p.roles:
            if r.value not in all_allowed_roles:
                all_allowed_roles.append(r.value)

    # Precompute the role-set for each branch once.
    compiled: list[tuple[frozenset[str], Resource | None, PermissionLevel]] = [(frozenset(r.value for r in p.roles), p.resource, p.level) for p in policies]

    async def _policies_checker(user: CurrentUserDep, session: SessionDep) -> AuthUser:
        for role_set, branch_resource, branch_level in compiled:
            if user.role in role_set:
                if branch_resource is None:
                    return user
                from app.modules.permission.service import PermissionService

                perm_service = PermissionService(session)
                await perm_service.check_permission(user, branch_resource, branch_level)
                return user

        logger.warning(
            LogEvent.RBAC_DENIED,
            user_id=user.id,
            user_role=user.role,
            required_roles=all_allowed_roles,
        )
        raise ForbiddenError(f"This action requires one of: {', '.join(all_allowed_roles)}")

    return Depends(_policies_checker)


_ADMIN_PAYMENT_ROLES = frozenset({UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value})


def _payment_access_resource(role: str) -> Resource | None:
    """Map caller role to the permission resource for billing payment routes."""
    if role in _ADMIN_PAYMENT_ROLES:
        return Resource.BILLING
    if role == UserRole.CUSTOMER_B2B.value:
        return Resource.BILLING
    return None


def AllowedPaymentAccess(  # noqa: N802
    level: PermissionLevel = PermissionLevel.READ,
) -> Any:
    """Payment route ACL: ADMIN, SUPER_ADMIN, and CUSTOMER_B2B all require Resource.BILLING."""

    async def _checker(user: CurrentUserDep, session: SessionDep) -> AuthUser:
        resource = _payment_access_resource(user.role)
        if resource is None:
            logger.warning(
                LogEvent.RBAC_DENIED,
                user_id=user.id,
                user_role=user.role,
                required_roles=[UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value, UserRole.CUSTOMER_B2B.value],
            )
            raise ForbiddenError("This action requires ADMIN, SUPER_ADMIN, or CUSTOMER_B2B with the appropriate billing permission")

        from app.modules.permission.service import PermissionService

        perm_service = PermissionService(session)
        await perm_service.check_permission(user, resource, level)
        return user

    return Depends(_checker)


# Idempotency key enforcement


def require_idempotency_key(
    x_idempotency_key: Annotated[
        str,
        Header(alias="x-idempotency-key", description="Idempotency key for safe retries"),
    ],
) -> str:
    if len(x_idempotency_key) > 256:
        raise ValidationError("X-Idempotency-Key header is required for this endpoint (max 256 characters)")
    return x_idempotency_key


IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


# File validation

ValidatedFile = tuple[bytes, str, str]


@dataclass(frozen=True, slots=True)
class FileKind:
    allowed_types: set[str]
    max_size: int
    label: str
    formats: str


IMAGE = FileKind(
    allowed_types=ALLOWED_IMAGE_TYPES,
    max_size=MAX_IMAGE_SIZE,
    label="Image",
    formats="JPG/PNG/WebP",
)

DOCUMENT = FileKind(
    allowed_types=ALLOWED_DOCUMENT_TYPES,
    max_size=MAX_DOCUMENT_SIZE,
    label="Document",
    formats="PDF/JPG/PNG/DOC/DOCX",
)

ORG_FILE = FileKind(
    allowed_types=ALLOWED_ORG_DOCUMENT_TYPES | ALLOWED_IMAGE_TYPES,
    max_size=MAX_ORG_DOCUMENT_SIZE,
    label="File",
    formats="PDF/JPG/PNG/WebP/DOC/DOCX",
)


def validated_upload(
    kind: FileKind,
    *,
    field_name: str = "file",
    max_files: int = 1,
    max_size: int | None = None,
    optional: bool = False,
) -> Any:
    # Produces a Depends() for file upload with count + size enforcement.
    # max_files=1 → single UploadFile, returns ValidatedFile (or None if optional).
    # max_files>1 → list[UploadFile], returns list[ValidatedFile] (or [] if optional).
    # Use distinct field_name values when a route accepts multiple file fields.
    effective_max = max_size if max_size is not None else kind.max_size
    label = kind.label
    single = max_files == 1
    size_mb = effective_max / (1024 * 1024)
    desc = f"{label} ({kind.formats}, max {size_mb:g} MB)" if single else f"{label}s ({kind.formats}, max {size_mb:g} MB each, max {max_files} per request)"

    async def _dep(**kw: Any) -> Any:
        raw = kw.get(field_name)
        if single:
            if raw is None:
                if optional:
                    return None
                raise ValidationError(f"{label} is required")
            content, detected = await read_and_validate(
                raw,
                allowed_types=kind.allowed_types,
                max_size=effective_max,
                label=label,
            )
            return (content, raw.filename or label.lower(), detected)
        if not raw:
            if optional:
                return []
            raise ValidationError(f"At least one {label.lower()} is required")
        if len(raw) > max_files:
            raise ValidationError(f"Maximum {max_files} {label.lower()}(s) allowed per request")
        result: list[ValidatedFile] = []
        for f in raw:
            content, detected = await read_and_validate(
                f,
                allowed_types=kind.allowed_types,
                max_size=effective_max,
                label=label,
            )
            result.append((content, f.filename or label.lower(), detected))
        return result

    if single:
        ann = Annotated[Optional[UploadFile], File(description=desc)] if optional else Annotated[UploadFile, File()]
    else:
        ann = Annotated[Optional[list[UploadFile]], File(description=desc)] if optional else Annotated[list[UploadFile], File(description=desc)]
    default = None if optional else inspect.Parameter.empty

    setattr(
        _dep,
        "__signature__",
        inspect.Signature(
            [
                inspect.Parameter(field_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default, annotation=ann),
            ]
        ),
    )
    return Depends(_dep)


# Reusable form field type for JSON arrays of UUIDs (e.g. deleted_image_ids, deleted_document_ids)
DeletedUUIDList = Annotated[Json[list[UUID]] | None, Form(description="JSON array of UUIDs to delete")]


# ── Document step-up access ────────────────────────────────────────────────────


async def _require_doc_access(
    user: CurrentUserDep,
    session: SessionDep,
    x_doc_access_token: Annotated[
        str | None,
        Header(
            alias="x-doc-access-token",
            description=("Document access token obtained from " "POST /v1/organizations/documents/otp/verify. " "Required on all document management endpoints. Valid for 1 hour."),
        ),
    ] = None,
) -> None:
    """Validate the X-Doc-Access-Token header for document step-up authentication."""
    if not x_doc_access_token:
        raise AuthenticationError(
            "X-Doc-Access-Token header is required for document endpoints. "
            "Request an OTP via POST /v1/organizations/documents/otp/send, "
            "then verify it via POST /v1/organizations/documents/otp/verify "
            "to receive your token."
        )

    from app.modules.organizations.doc_access_service import DocAccessService

    from app.modules.organizations.doc_access_scope import DocAccessScope

    svc = DocAccessService(session)
    await svc.validate_token(token=x_doc_access_token, user_id=user.id, access_scope=DocAccessScope.ORG_DOCUMENTS)


DocAccessDep = Annotated[None, Depends(_require_doc_access)]


async def _require_driver_doc_access(
    user: CurrentUserDep,
    session: SessionDep,
    x_driver_doc_access_token: Annotated[
        str | None,
        Header(
            alias="x-driver-doc-access-token",
            description=(
                "Driver document access token from POST /v1/drivers/documents/otp/verify. "
                "Required on all driver compliance document routes (list, get, upload, update, delete). Valid for 1 hour."
            ),
        ),
    ] = None,
) -> None:
    """Validate the X-Driver-Doc-Access-Token header for driver document step-up authentication."""
    if not x_driver_doc_access_token:
        raise AuthenticationError(
            "X-Driver-Doc-Access-Token header is required for driver compliance document endpoints "
            "(list, get, upload, update, delete). "
            "Request an OTP via POST /v1/drivers/documents/otp/send, "
            "then verify it via POST /v1/drivers/documents/otp/verify "
            "to receive your token."
        )

    from app.modules.organizations.doc_access_scope import DocAccessScope
    from app.modules.organizations.doc_access_service import DocAccessService

    svc = DocAccessService(session)
    await svc.validate_token(
        token=x_driver_doc_access_token,
        user_id=user.id,
        access_scope=DocAccessScope.DRIVER_DOCUMENTS,
    )


DriverDocAccessDep = Annotated[None, Depends(_require_driver_doc_access)]
