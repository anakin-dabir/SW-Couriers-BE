"""Auth service — business logic for authentication and token management.

Handles: registration, login (with lockout), token refresh (with rotation),
logout, password changes, and audit logging for all auth events.

This layer calls repositories — never touches the DB directly.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple
from uuid import uuid4

import jwt.exceptions as jwt_exceptions
import structlog
from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import (
    INVITE_SEND_BASE_COOLDOWN_SECONDS,
    INVITE_SEND_MAX_DAILY_REQUESTS,
    LOCKOUT_DURATION_MINUTES,
    MAX_ACTIVE_SESSIONS_PER_USER,
    MAX_LOGIN_ATTEMPTS,
)
from app.common.deps import AuthUser
from app.common.enums import ROLE_TO_CLIENT_TYPE, ClientType, Job, LogEvent, UserRole, UserStatus
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.common.schemas import TokenData
from app.common.service import BaseService
from app.common.session_display import (
    session_device_label,
    session_ip_location_label,
    session_ua_breakdown,
)
from app.common.utils import (
    blacklist_token,
    build_driver_set_password_link,
    build_email_link,
    get_client_ip,
    is_user_suspended,
    mark_session_revoked,
    mask_email,
    mask_ip_address,
    validate_link,
    verify_client_type_for_role,
)
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.core.redis import get_redis
from app.core.security import (
    PASSWORD_RESET_EXPIRE_MINUTES,
    PASSWORD_RESET_OTP_LENGTH,
    PASSWORD_RESET_SESSION_MINUTES,
    TokenType,
    check_needs_rehash,
    create_access_token,
    create_email_verification_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.modules.admins.repository import AdminRepository
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.auth.enums import ActivationLinkRequestStatus
from app.modules.auth.models import Invite
from app.modules.auth.repository import ActivationLinkRequestRepository, InviteRepository, RefreshTokenRepository, SessionRepository
from app.modules.auth.v1.schemas import (
    ActiveSessionsResponse,
    AdminMeProfile,
    DriverMeProfile,
    LoginServiceResponse,
    OrgContactMeProfile,
    RegisterRequest,
    RegisterResponse,
    SessionDevice,
    UserBrief,
)
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.repository import DriverRepository
from app.modules.notifications.enums import NotificationEvent, NotificationType
from app.modules.notifications.repository import NotificationRepository
from app.modules.organizations.enums import OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import DocAccessTokenRepository, OrganizationRepository, OrgContactRepository
from app.modules.permission.service import PermissionService
from app.modules.user.models import User
from app.modules.user.repository import UserRepository

logger = structlog.get_logger()

_INVITE_SEND_PREFIX = "invite_send:"


class CreateInviteResult(NamedTuple):
    throttled: bool
    invite: Invite | None
    raw_token: str | None
    user: User
    public_invite_id: str


class DriverActivationEmailResult(NamedTuple):
    sent: bool
    invite_id: str
    user: User


_INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"

_DUMMY_HASH = hash_password("dummy-password-timing-equalization")


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _signed_avatar_url(image_key: str | None) -> str | None:
    if not image_key:
        return None
    try:
        from app.storage.cloudflare_images import get_images_client

        return get_images_client().generate_signed_url(image_key, expiry_seconds=3600)
    except Exception:
        logger.warning("auth.me.avatar_signing_failed", image_key=image_key)
        return None

_REFRESH_REVOKED_REPLAY_WINDOW_SEC = 60
_REFRESH_REVOKED_REPLAY_MAX = 24

async def _enforce_revoked_refresh_replay_throttle(user_id: str, token_hash: str) -> None:
    if settings.is_test:
        return
    try:
        redis = get_redis()
    except RuntimeError:
        return
    key = f"auth:rt_revoked_replay:{user_id}:{token_hash[:32]}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, _REFRESH_REVOKED_REPLAY_WINDOW_SEC)
    if n > _REFRESH_REVOKED_REPLAY_MAX:
        raise RateLimitError(
            "Too many invalid refresh attempts. Please try again later.",
            retry_after=_REFRESH_REVOKED_REPLAY_WINDOW_SEC,
        )


class AuthService(BaseService):
    """Authentication and authorization service."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self.user_repo = UserRepository(session)
        self.token_repo = RefreshTokenRepository(session)
        self._doc_access_token_repo = DocAccessTokenRepository(session)
        self.session_repo = SessionRepository(session)
        self.invite_repo = InviteRepository(session)
        self.activation_link_request_repo = ActivationLinkRequestRepository(session)
        self._audit = AuditService(session)
        self._driver_repo = DriverRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._notif_repo = NotificationRepository(session)
        self._perm_service = PermissionService(session, request=request)
        self._ip_address = get_client_ip(request) if request else None
        self._user_agent = request.headers.get("user-agent") if request else None

    async def _enforce_invite_scope_for_target_org(
        self,
        inviter: AuthUser,
        target_organization_id: str | None,
        invited_user_id: str,
        *,
        organization_id: str | None,
    ) -> None:
        role = inviter.role if isinstance(inviter.role, str) else str(inviter.role)
        if role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            return

        def norm(v: str | None) -> str:
            if v is None:
                return ""
            return str(v).strip()

        io = norm(inviter.organization_id)
        oid = norm(organization_id)
        to = norm(target_organization_id)
        if io == oid == to:
            return

        await self._log_audit(
            action="auth.invite_scope_denied",
            entity_id=invited_user_id,
            user_id=inviter.id,
            user_role=role,
            new_value={
                "inviter_organization_id": inviter.organization_id,
                "organization_id": organization_id,
                "target_organization_id": target_organization_id,
                "invited_user_id": invited_user_id,
            },
            severity="WARNING",
            category=AuditCategory.ACCESS,
            event_type=AuditEventType.PERMISSION_DENIED,
        )
        logger.warning(
            "auth.invite_scope_denied",
            inviter_id=inviter.id,
            invited_user_id=invited_user_id,
            inviter_organization_id=inviter.organization_id,
            organization_id=organization_id,
            target_organization_id=target_organization_id,
        )
        raise ForbiddenError("You cannot send an invite to this user")

    async def _log_audit(
        self,
        action: str,
        entity_type: str = "user",
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.SECURITY,
        event_type: AuditEventType | str = AuditEventType.SYSTEM_CONFIG_CHANGED,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.log(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=old_value,
            new_value=new_value,
            ip_address=self._ip_address,
            user_agent=self._user_agent,
            reason=reason,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    # ── Registration ─────────────────────────────

    async def register(self, data: RegisterRequest) -> RegisterResponse:
        """Register a new customer account.

        - If email already exists, returns same success shape to avoid enumeration.
        - Otherwise creates user with pending_verification status.
        """
        email_normalized = data.email.strip().lower()

        fake_response = RegisterResponse(
            id=str(uuid4()),
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
            role=data.role,
            status=UserStatus.INACTIVE,
        )

        if await self.user_repo.email_exists(email_normalized):
            logger.info(LogEvent.REGISTRATION_DUPLICATE_EMAIL_IGNORED, email=mask_email(email_normalized))
            return fake_response

        try:
            async with self.user_repo.session.begin_nested():
                user = await self.user_repo.create(
                    {
                        "email": email_normalized,
                        "password_hash": hash_password(data.password),
                        "first_name": data.first_name,
                        "last_name": data.last_name,
                        "phone": data.phone,
                        "role": data.role,
                        "status": UserStatus.INACTIVE,
                    }
                )
        except IntegrityError:
            logger.info(LogEvent.REGISTRATION_DUPLICATE_EMAIL_IGNORED, email=mask_email(email_normalized))
            return fake_response

        verification_token = create_email_verification_token(user.id)
        await self._send_verification_email(user, verification_token)

        await self._log_audit(
            "auth.register",
            entity_id=user.id,
            user_id=user.id,
            user_role=user.role,
            new_value={"email": mask_email(user.email), "role": user.role},
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED,
            severity="NOTICE",
        )
        logger.info(LogEvent.USER_REGISTERED, user_id=user.id, role=user.role)

        return RegisterResponse(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=user.role,
            status=user.status,
        )

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        phone: str | None,
        role: UserRole,
        status: UserStatus = UserStatus.ACTIVE,
        force_password_change: bool = False,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> User:
        """Create a user account (admin-only for staff roles).

        This method is intended for admin-created accounts (drivers, warehouse staff, etc.).
        Customer roles (CUSTOMER_B2C, CUSTOMER_B2B) should use register() for self-registration.

        Authorization:
        - Staff roles (ADMIN, DRIVER, WAREHOUSE_STAFF): Only ADMIN can create these.
        - Customer roles (CUSTOMER_B2C, CUSTOMER_B2B): Use register() instead.

        The user is created with the specified status (default: ACTIVE, no email verification needed).

        Raises:
            ForbiddenError: If attempting to create a staff role without ADMIN privileges,
                          or if attempting to create a customer role (use register() instead).
            ValidationError: If password does not meet strength requirements.
            ConflictError: If email already exists.
        """
        from app.common.exceptions import ForbiddenError

        # Define which roles are staff (admin-created) vs customer (self-registered)
        _staff_roles: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.DRIVER, UserRole.WAREHOUSE_STAFF})
        _customer_roles: frozenset[UserRole] = frozenset({UserRole.CUSTOMER_B2C, UserRole.CUSTOMER_B2B})

        # Customer roles must use register() for self-registration
        if role in _customer_roles:
            customer_role_names = ", ".join(sorted(r.value for r in _customer_roles))
            raise ForbiddenError(f"Customer roles ({customer_role_names}) must use the register() endpoint for self-registration")

        # Staff roles require ADMIN privileges
        if role in _staff_roles:
            # Normalize audit_user_role string to UserRole enum for type-safe comparison
            try:
                audit_role_enum = UserRole(audit_user_role) if audit_user_role else None
            except ValueError:
                audit_role_enum = None

            if audit_role_enum not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
                staff_role_names = ", ".join(sorted(r.value for r in _staff_roles))
                raise ForbiddenError(f"Only administrators can create users with staff roles ({staff_role_names})")

        # Validate password strength (same as register())
        from app.common.validators import validate_password_strength

        try:
            validate_password_strength(password)
        except ValueError as exc:
            from app.common.exceptions import ValidationError

            raise ValidationError(str(exc)) from exc

        email_normalized = email.strip().lower()

        if await self.user_repo.email_exists(email_normalized):
            raise ConflictError("Email already registered")

        user = await self.user_repo.create(
            {
                "email": email_normalized,
                "password_hash": hash_password(password),
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "role": role.value,  # Store as string in DB (SQLAlchemy enum column handles conversion)
                "status": status.value,  # Store as string in DB
                "force_password_change": force_password_change,
            }
        )

        await self._log_audit(
            "auth.user_created",
            entity_id=user.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"email": mask_email(user.email), "role": user.role, "status": user.status},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED,
        )
        logger.info("user_created", user_id=user.id, role=user.role, created_by=audit_user_id)
        return user

    # ── Login ────────────────────────────────────

    async def login(
        self,
        email: str,
        password: str,
        client_type: ClientType,
    ) -> LoginServiceResponse:
        """Authenticate with email + password.

        client_type binds issued tokens to the requesting app (X-Client-Type).
        Returns LoginServiceResponse with JWT tokens on success.
        Uses generic error message to avoid user enumeration.
        Enforces account lockout after MAX_LOGIN_ATTEMPTS failures.
        """
        user = await self.user_repo.find_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

        if user.locked_until and user.locked_until > datetime.now(UTC):
            logger.warning(
                LogEvent.LOGIN_REJECTED_LOCKED,
                user_id=user.id,
                locked_until=user.locked_until.isoformat(),
            )
            raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

        from app.modules.client_inactivity.service import ClientInactivityService

        inactivity_reactivatable = ClientInactivityService.is_inactivity_reactivatable(
            status=user.status.value,
            inactive_reason=user.inactive_reason,
        )

        if user.status != UserStatus.ACTIVE and not inactivity_reactivatable:
            if user.status == UserStatus.SUSPENDED:
                logger.warning(LogEvent.LOGIN_REJECTED_SUSPENDED, user_id=user.id)
                raise AuthenticationError("Your account has been suspended. Please contact support.")
            elif user.status == UserStatus.INACTIVE:
                logger.warning(LogEvent.LOGIN_REJECTED_INACTIVE, user_id=user.id)
            elif user.status == UserStatus.PENDING_VERIFICATION:
                logger.warning(LogEvent.LOGIN_REJECTED_PENDING_VERIFICATION, user_id=user.id)
                raise AuthenticationError("Please Activate your account by clicking the link in the email we sent you.")
            else:
                logger.warning(LogEvent.LOGIN_REJECTED_INACTIVE, user_id=user.id, user_status=str(user.status))
            raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)
        await self._assert_b2b_portal_access(user=user, client_type=client_type)

        verify_client_type_for_role(client_type, user.role)

        if not verify_password(password, user.password_hash):
            await self._handle_failed_login(user)
            raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

        if user.failed_login_attempts > 0:
            await self.user_repo.reset_failed_attempts(user.id)

        if check_needs_rehash(user.password_hash):
            new_hash = hash_password(password)
            await self.user_repo.update_by_id(user.id, {"password_hash": new_hash})
            logger.info(LogEvent.PASSWORD_REHASHED, user_id=user.id)

        if inactivity_reactivatable:
            inactivity_service = ClientInactivityService(self._session, self._request)
            await inactivity_service.reactivate_user_on_login(user_id=user.id)

        if client_type == ClientType.DRIVER:
            from app.modules.drivers.service import DriverService

            driver_service = DriverService(self._session, self._request)
            try:
                driver = await driver_service.get_driver_by_user_id(user.id)
            except NotFoundError:
                raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

            allowed_for_login = {
                DriverAccountStatus.ACTIVE,
                DriverAccountStatus.PENDING_ACTIVATION,
            }
            if driver.account_status not in allowed_for_login:
                raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

            if driver.account_status == DriverAccountStatus.PENDING_ACTIVATION:
                await driver_service.activate_driver_on_login(user_id=user.id)

        response = await self._issue_tokens(user, client_type)

        await self.user_repo.update_last_login(user.id)

        await self._log_audit(
            "auth.login_success",
            entity_id=user.id,
            user_id=user.id,
            user_role=user.role,
            category=AuditCategory.ACCESS,
            event_type=AuditEventType.LOGIN_SUCCESS,
            severity="INFO",
        )
        return response

    async def _handle_failed_login(self, user: User) -> None:
        """Increment failed attempts and lock account if threshold reached."""
        new_count = await self.user_repo.increment_failed_attempts(user.id)

        await self._log_audit(
            "auth.login_failed",
            entity_id=user.id,
            user_id=user.id,
            user_role=user.role,
            new_value={"failed_login_attempts": new_count},
            severity="WARNING",
            category=AuditCategory.ACCESS,
            event_type=AuditEventType.LOGIN_FAILED,
        )

        if new_count >= MAX_LOGIN_ATTEMPTS:
            locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            await self.user_repo.lock_account(user.id, locked_until)
            await self._log_audit(
                "auth.account_locked",
                entity_id=user.id,
                user_id=user.id,
                user_role=user.role,
                new_value={"locked_until": locked_until.isoformat()},
                reason=f"Exceeded {MAX_LOGIN_ATTEMPTS} failed login attempts",
                severity="CRITICAL",
                category=AuditCategory.SECURITY,
                event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
            )
            logger.warning(
                LogEvent.ACCOUNT_LOCKED,
                user_id=user.id,
                locked_until=locked_until.isoformat(),
            )

    # Email verification

    async def _send_verification_email(self, user: User, token: str) -> None:
        """Enqueue the email verification link via Arq (HIGH priority).

        Link base URL is chosen by user role (LINK_BASE_URL_<CLIENT_TYPE> or VERIFICATION_LINK_BASE_URL).
        """
        client_type = ROLE_TO_CLIENT_TYPE.get(user.role, ClientType.CUSTOMER_B2C)
        link = build_email_link(client_type, "verify-email", token)
        token_fingerprint = token[:8] + "..." if len(token) > 8 else "[redacted]"
        logger.info(
            LogEvent.VERIFICATION_EMAIL_PLACEHOLDER,
            user_id=user.id,
            email=mask_email(user.email),
            verification_base_url=link.split("?")[0] if link else None,
            token_fingerprint=token_fingerprint,
        )
        if link:
            validate_link(link)
            job = await enqueue(
                Job.SEND_VERIFICATION_EMAIL,
                to_email=user.email,
                first_name=user.first_name,
                verification_link=link,
                priority=QueuePriority.HIGH,
            )
            if job is None:
                logger.warning(
                    LogEvent.VERIFICATION_EMAIL_PLACEHOLDER,
                    user_id=user.id,
                    email=mask_email(user.email),
                    enqueue_failed=True,
                )

    async def verify_email(self, token: str) -> None:
        """Verify a user's email using the one-time token. Marks email verified and sets status active."""
        try:
            user_id = decode_token(token, TokenType.EMAIL_VERIFICATION)["sub"]
        except jwt_exceptions.PyJWTError:
            raise AuthenticationError("Invalid or expired verification link") from None

        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("Invalid or expired verification link")

        if user.email_verified:
            logger.info(LogEvent.VERIFY_EMAIL_ALREADY_DONE, user_id=user.id)
            return

        await self.user_repo.verify_email(user_id)
        await self._log_audit(
            "auth.email_verified",
            entity_id=user_id,
            user_id=user_id,
            user_role=user.role,
            old_value={"status": user.status, "email_verified": False},
            new_value={"status": UserStatus.ACTIVE, "email_verified": True},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_ACTIVATED,
        )
        logger.info(LogEvent.EMAIL_VERIFIED, user_id=user_id)

    # Profile

    async def get_me(self, auth_user: "AuthUser") -> UserBrief:
        """Return current user identity plus small role-specific profile context."""

        user = await self.user_repo.get_by_id(auth_user.id)
        if user is None:
            raise AuthenticationError("User not found")
        if user.status != UserStatus.ACTIVE:
            raise AuthenticationError("User account is not active")
        await self._assert_b2b_portal_access(user=user, client_type=auth_user.client_type)

        contact_role: str | None = None
        profile_type: str | None = None
        avatar_key = user.avatar_url
        driver_profile: DriverMeProfile | None = None
        org_contact_profile: OrgContactMeProfile | None = None
        admin_profile: AdminMeProfile | None = None

        if user.role == UserRole.DRIVER:
            driver = await DriverRepository(self._session).find_by_user_id(user.id)
            if driver is not None:
                profile_type = UserRole.DRIVER.value
                avatar_key = avatar_key or driver.profile_photo_key
                driver_profile = DriverMeProfile(
                    id=driver.id,
                    driver_code=driver.driver_code,
                    terms_accepted_at=driver.terms_accepted_at,
                    location_consent_at=driver.location_consent_at,
                )

        elif user.role == UserRole.CUSTOMER_B2B and auth_user.organization_id:
            contact = await OrgContactRepository(self._session).get_active_contact_for_user(auth_user.organization_id, user.id)
            if contact is not None:
                contact_role = str(_enum_value(contact.contact_role))
                profile_type = UserRole.CUSTOMER_B2B.value
                org_contact_profile = OrgContactMeProfile(
                    id=contact.id,
                    contact_role=contact_role,
                    status=str(_enum_value(contact.status)),
                    is_primary=contact.is_primary,
                )

        elif user.role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            admin = await AdminRepository(self._session).find_by_user_id(user.id)
            if admin is not None:
                profile_type = user.role.value
                admin_profile = AdminMeProfile(
                    id=admin.id,
                    admin_ref=admin.admin_ref,
                    title=str(_enum_value(user.title)) if user.title is not None else None,
                    position_role=user.position_role,
                    address_line_1=admin.address_line_1,
                    address_line_2=admin.address_line_2,
                    city=admin.city,
                    state=admin.state,
                    postcode=admin.postcode,
                    country=admin.country,
                )

        return UserBrief(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=user.role,
            organization_id=user.organization_id,
            phone=user.phone,
            avatar_url=_signed_avatar_url(avatar_key),
            contact_role=contact_role,
            region_id=user.region_id,
            requires_password_change=user.force_password_change,
            profile_type=profile_type,
            driver=driver_profile,
            org_contact=org_contact_profile,
            admin=admin_profile,
            created_at=user.created_at,
        )

    # Token Refresh

    async def refresh_tokens(
        self,
        raw_refresh_token: str,
        client_type: ClientType | None = None,
    ) -> TokenData:
        """Rotate refresh token: revoke old, issue new pair.

        Replay of an already-revoked refresh token is rejected without revoking
        other sessions. A lost race to rotate the same token (concurrent refresh)
        is rejected the same way. Revoked-token replay is Redis-throttled per user
        and token fingerprint to limit abuse.
        If client_type is provided, it must match the user's role.
        """
        try:
            payload = decode_token(raw_refresh_token, TokenType.REFRESH)
        except jwt_exceptions.PyJWTError:
            raise AuthenticationError("Invalid refresh token") from None

        user_id: str = payload["sub"]
        old_hash = hash_token(raw_refresh_token)

        if await is_user_suspended(user_id):
            raise AuthenticationError("Your account has been suspended. Please contact support.")

        token_record = await self.token_repo.find_by_hash(old_hash)
        if token_record is None:
            raise AuthenticationError("Refresh token not found")

        if token_record.revoked:
            await _enforce_revoked_refresh_replay_throttle(user_id, old_hash)
            await self._log_audit(
                "auth.replay_detected",
                entity_id=user_id,
                user_id=user_id,
                new_value={"kind": "revoked_refresh_replayed", "session_id": token_record.session_id},
                reason="Revoked refresh token presented again; other sessions unchanged",
                severity="WARNING",
                category=AuditCategory.SECURITY,
                event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
            )
            logger.warning(
                LogEvent.REFRESH_TOKEN_REPLAY_DETECTED,
                user_id=user_id,
                kind="revoked_refresh_replayed",
            )
            raise AuthenticationError("Refresh token is no longer valid. Please log in again.")

        now = datetime.now(UTC)
        if token_record.expires_at < now:
            await self.token_repo.revoke(old_hash)
            raise AuthenticationError("Refresh token has expired")

        revoked_ok = await self.token_repo.revoke_if_active(old_hash)
        if not revoked_ok:
            await self._log_audit(
                "auth.replay_detected",
                entity_id=user_id,
                user_id=user_id,
                new_value={"kind": "rotation_race_lost", "session_id": token_record.session_id},
                reason="Refresh token already consumed by another request; other sessions unchanged",
                severity="NOTICE",
                category=AuditCategory.SECURITY,
                event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
            )
            logger.info(
                LogEvent.REFRESH_TOKEN_REPLAY_DETECTED,
                user_id=user_id,
                kind="rotation_race_lost",
            )
            raise AuthenticationError(
                "Refresh token was already used. If you opened multiple tabs, use the latest session or log in again."
            )

        if client_type is None:
            raise AuthenticationError("Missing client type; send X-Client-Type header")

        token_aud = payload.get("aud")
        if not token_aud:
            raise AuthenticationError("Refresh token format is outdated; please log in again")
        if token_aud != client_type.value:
            raise AuthenticationError("Refresh token was issued for a different client. Use the correct app or portal.")

        user = await self.user_repo.get_by_id(user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("User account is not active")
        await self._assert_b2b_portal_access(user=user, client_type=client_type)

        verify_client_type_for_role(client_type, user.role)

        # Logical device session id for UX + immediate per-request revocation.
        sid = token_record.session_id
        if sid is None:
            sess = await self.session_repo.create_session(
                user_id=user_id,
                user_agent=self._user_agent,
                ip_address=self._ip_address,
            )
            sid = sess.session_id
            await self.token_repo.attach_session_id_if_missing(old_hash, sid)

        active_count = await self.token_repo.count_active_for_user(user_id)
        if active_count >= MAX_ACTIVE_SESSIONS_PER_USER:
            await self.token_repo.revoke_oldest_keeping(user_id, MAX_ACTIVE_SESSIONS_PER_USER - 1)

        ct = client_type.value
        access_token, access_jti = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type=ct,
            region_id=user.region_id,
            organization_id=user.organization_id,
            sid=sid,
            sv=user.session_sv,
        )
        raw_token, new_hash, expires_at = create_refresh_token(user.id, ct)

        await self.token_repo.create(
            user_id=user.id,
            token_hash=new_hash,
            expires_at=expires_at,
            access_jti=access_jti,
            user_agent=self._user_agent,
            ip_address=self._ip_address,
            session_id=sid,
        )

        # Minimal audit: detect UA/IP changes for this logical session.
        from sqlalchemy import select

        from app.modules.auth.models import Session as SessionModel

        prev_meta_stmt = select(SessionModel.user_agent, SessionModel.ip_address).where(SessionModel.session_id == sid)
        prev_meta = await self._session.execute(prev_meta_stmt)
        prev_user_agent, prev_ip_address = prev_meta.one_or_none() or (None, None)
        user_agent_changed = prev_user_agent != self._user_agent
        ip_address_changed = prev_ip_address != self._ip_address

        await self.session_repo.touch_session(
            session_id=sid,
            user_agent=self._user_agent,
            ip_address=self._ip_address,
        )

        if user_agent_changed or ip_address_changed:
            await self._log_audit(
                "auth.session_device_changed",
                entity_type="session",
                entity_id=sid,
                user_id=user.id,
                new_value={
                    "user_agent_changed": user_agent_changed,
                    "ip_address_changed": ip_address_changed,
                },
            )

        await self._log_audit(
            "auth.session_refreshed",
            entity_type="session",
            entity_id=sid,
            user_id=user.id,
        )

        logger.info(LogEvent.TOKENS_REFRESHED, user_id=user.id)

        return TokenData(
            access_token=access_token,
            access_token_expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_token=raw_token,
            refresh_token_expires_in=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

    # Session management (access-token based)

    async def list_sessions(self, user: "AuthUser") -> ActiveSessionsResponse:
        current_sid = user.sid
        sessions = await self.session_repo.list_active_sessions(user_id=user.id)

        items: list[SessionDevice] = []
        for s in sessions:
            br, os_fam, dev_fam, is_mob, is_tab, is_pc = session_ua_breakdown(s.user_agent)
            items.append(
                SessionDevice(
                    session_id=s.session_id,
                    device_label=session_device_label(s.user_agent),
                    browser_family=br,
                    os_family=os_fam,
                    device_family=dev_fam,
                    is_mobile=is_mob,
                    is_tablet=is_tab,
                    is_pc=is_pc,
                    user_agent=s.user_agent,
                    ip_address=mask_ip_address(s.ip_address),
                    location_label=session_ip_location_label(s.ip_address),
                    last_seen_at=s.last_seen_at,
                    inactivity_expires_at=s.inactivity_expires_at,
                    current=current_sid is not None and s.session_id == current_sid,
                )
            )
        return ActiveSessionsResponse(items=items)

    async def logout_other_sessions(self, user: "AuthUser") -> int:
        token_ref = await self.token_repo.find_active_by_access_jti(user.id, user.jti)
        if token_ref is not None and token_ref.session_id is not None:
            keep_session_id = str(token_ref.session_id).strip()
        elif user.sid and await self.session_repo.session_is_active_for_user(user.id, user.sid):
            keep_session_id = str(user.sid).strip()
        else:
            raise AuthenticationError("Session identifier missing; please log in again")

        revoked_session_ids = await self.session_repo.revoke_sessions_except(
            user_id=user.id,
            keep_session_id=keep_session_id,
        )

        if revoked_session_ids:
            access_jtis = await self.token_repo.get_active_access_jtis_for_sessions(revoked_session_ids)
            for jti in access_jtis:
                await self._blacklist_access_jti(jti)

            await self.token_repo.revoke_refresh_tokens_for_sessions(revoked_session_ids)

            ttl_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
            for sid in revoked_session_ids:
                await mark_session_revoked(sid, ttl_seconds)

        if revoked_session_ids:
            await self._log_audit(
                "auth.sessions_revoked_other",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                new_value={
                    "revoked_session_ids": revoked_session_ids,
                },
                severity="NOTICE",
                category=AuditCategory.SECURITY,
                event_type=AuditEventType.ACCOUNT_UPDATED,
            )

        return len(revoked_session_ids)

    async def _assert_b2b_portal_access(self, *, user: User, client_type: ClientType) -> None:
        if client_type != ClientType.CUSTOMER_B2B:
            return
        role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
        if role_val != UserRole.CUSTOMER_B2B.value:
            return
        if not user.organization_id:
            return

        org = await self._session.get(Organization, user.organization_id)
        if org is None:
            return
        if org.status == OrganizationStatus.SUSPENDED:
            logger.warning(LogEvent.LOGIN_REJECTED_SUSPENDED, user_id=user.id, organization_id=org.id)
            raise AuthenticationError(_INVALID_CREDENTIALS_MESSAGE)

    async def logout_session(self, user: "AuthUser", session_id: str) -> int:
        access_jtis = await self.token_repo.get_active_access_jtis_for_sessions([session_id])
        for jti in access_jtis:
            await self._blacklist_access_jti(jti)

        await self.token_repo.revoke_refresh_tokens_for_sessions([session_id])
        revoked = await self.session_repo.revoke_session(user_id=user.id, session_id=session_id)

        ttl_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        await mark_session_revoked(session_id, ttl_seconds)

        await self._log_audit(
            "auth.session_revoked",
            entity_type="session",
            entity_id=session_id,
            user_id=user.id,
        )

        return 1 if revoked else 0

    # Logout

    async def logout(self, raw_refresh_token: str) -> None:
        """Single-session logout — always succeeds.

        1. Find the refresh token record → read its paired access_jti.
        2. Blacklist the access JTI in Redis for its remaining lifetime.
        3. Revoke the refresh token.
        Silently succeeds if the token is missing, already revoked, or expired.
        """
        token_hash = hash_token(raw_refresh_token)
        token_ref = await self.token_repo.find_by_hash(token_hash)

        if token_ref and token_ref.access_jti:
            await self._blacklist_access_jti(token_ref.access_jti)

        await self.token_repo.revoke(token_hash)

        logout_user_id = token_ref.user_id if token_ref else None
        await self._log_audit(
            "auth.logout",
            entity_type="session",
            entity_id=token_ref.id if token_ref else None,
            user_id=logout_user_id,
            category=AuditCategory.ACCESS,
            event_type=AuditEventType.LOGOUT_SUCCESS,
        )
        logger.info(LogEvent.USER_LOGGED_OUT, user_id=logout_user_id)

    async def logout_all(self, raw_refresh_token: str) -> int:
        """Logout everywhere — blacklist all access JTIs + revoke all refresh tokens.

        Uses the refresh token to identify the user (no access token required).
        Returns number of refresh sessions terminated.
        """
        token_hash = hash_token(raw_refresh_token)
        token_ref = await self.token_repo.find_by_hash(token_hash)
        if not token_ref:
            return 0

        user_id = token_ref.user_id

        # Race-safe logout-all: bump server-side session generation/version so
        # existing access JWTs carrying `sv` are rejected immediately.
        await self.user_repo.increment_session_sv(user_id)

        sessions_active = await self.session_repo.list_active_sessions(user_id=user_id)
        session_ids_active = [s.session_id for s in sessions_active]

        # Defense-in-depth for tokens that may not carry `sv` (backcompat).
        jtis = await self.token_repo.get_active_access_jtis(user_id)
        for jti in jtis:
            await self._blacklist_access_jti(jti)

        count = await self.token_repo.revoke_all_for_user(user_id)

        for sid in session_ids_active:
            await self.session_repo.revoke_session(user_id=user_id, session_id=sid)

        ttl_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        for sid in session_ids_active:
            await mark_session_revoked(sid, ttl_seconds)
        await self._log_audit(
            "auth.logout_all",
            entity_id=user_id,
            user_id=user_id,
            new_value={
                "sessions_revoked": len(session_ids_active),
                "refresh_tokens_revoked": count,
                "access_tokens_blacklisted": len(jtis),
            },
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
        )
        logger.info(LogEvent.USER_LOGGED_OUT_ALL, user_id=user_id, sessions=count)
        return count

    # Password Change

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change password for an authenticated user.

        - Fetches user from DB by id
        - Verifies current password
        - Hashes new password
        - Revokes all refresh tokens (force re-login)
        """
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("User not found")

        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")

        new_hash = hash_password(new_password)
        now_utc = datetime.now(UTC)
        await self.user_repo.update_by_id(
            user.id,
            {
                "password_hash": new_hash,
                "password_changed_at": now_utc,
                "force_password_change": False,
            },
        )

        await self.token_repo.revoke_all_for_user(user.id)
        await self._doc_access_token_repo.revoke_all_active_for_user(user.id)
        await self._log_audit(
            "auth.password_changed",
            entity_id=user.id,
            user_id=user.id,
            user_role=user.role,
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.PASSWORD_CHANGED,
        )
        logger.info(LogEvent.PASSWORD_CHANGED, user_id=user.id)

    async def set_password_admin(
        self,
        *,
        user_id: str,
        new_password: str,
        force_password_change: bool = False,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Change a user's password directly as an administrator (no current password).

        Intended for admin driver management flows where an ADMIN user updates a DRIVER's password.
        Enforces password strength and revokes all existing refresh tokens.
        """
        from app.common.exceptions import ForbiddenError, ValidationError

        # Only ADMIN can perform direct password changes.
        try:
            audit_role_enum = UserRole(audit_user_role) if audit_user_role else None
        except ValueError:
            audit_role_enum = None
        if audit_role_enum != UserRole.ADMIN:
            raise ForbiddenError("Only administrators can change driver passwords directly")

        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("User not found")

        # For now we scope this helper to DRIVER accounts only.
        if user.role != UserRole.DRIVER:
            raise ForbiddenError("This endpoint can only change passwords for DRIVER users")

        # Reuse global password strength validation.
        from app.common.validators import validate_password_strength

        try:
            validate_password_strength(new_password)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        new_hash = hash_password(new_password)
        now_utc = datetime.now(UTC)
        await self.user_repo.update_by_id(
            user.id,
            {
                "password_hash": new_hash,
                "password_changed_at": now_utc,
                "force_password_change": force_password_change,
            },
        )

        # Revoke all sessions for this user so they must log in with the new password.
        await self.token_repo.revoke_all_for_user(user.id)
        await self._doc_access_token_repo.revoke_all_active_for_user(user.id)
        await self._log_audit(
            "auth.password_changed_admin",
            entity_id=user.id,
            user_id=audit_user_id or user.id,
            user_role=audit_user_role or user.role,
            new_value={"password_changed_at": now_utc.isoformat()},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.PASSWORD_CHANGED,
        )
        logger.info(LogEvent.PASSWORD_CHANGED, user_id=user.id)

    async def _invalidate_all_sessions_for_user(self, user_id: str) -> None:
        await self.user_repo.increment_session_sv(user_id)
        sessions_active = await self.session_repo.list_active_sessions(user_id=user_id)
        session_ids_active = [s.session_id for s in sessions_active]
        jtis = await self.token_repo.get_active_access_jtis(user_id)
        for jti in jtis:
            await self._blacklist_access_jti(jti)
        await self.token_repo.revoke_all_for_user(user_id)
        for sid in session_ids_active:
            await self.session_repo.revoke_session(user_id=user_id, session_id=sid)
        ttl_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        for sid in session_ids_active:
            await mark_session_revoked(sid, ttl_seconds)
        await self._doc_access_token_repo.revoke_all_active_for_user(user_id)

    async def support_issue_temporary_password(
        self,
        *,
        actor: AuthUser,
        target_user_id: str,
        new_password: str,
        flow: Literal["admin_staff", "org_contact"],
        organization_id: str | None = None,
    ) -> tuple[str, str]:
        if target_user_id == actor.id:
            raise ForbiddenError("You cannot issue a support password for your own account")

        user = await self.user_repo.get_by_id(target_user_id)
        if user is None:
            raise NotFoundError(resource="user", id=target_user_id)

        if verify_password(new_password, user.password_hash):
            raise ValidationError("New password must be different from the user's current password")

        actor_role = actor.role if isinstance(actor.role, str) else str(actor.role)

        new_hash = hash_password(new_password)
        now_utc = datetime.now(UTC)
        await self.user_repo.update_by_id(
            user.id,
            {
                "password_hash": new_hash,
                "password_changed_at": now_utc,
                "force_password_change": True,
                "status": UserStatus.ACTIVE,
            },
        )
        await self._invalidate_all_sessions_for_user(user.id)

        audit_extra: dict[str, object] = {
            "target_email": mask_email(user.email),
            "flow": flow,
        }
        if organization_id is not None:
            audit_extra["organization_id"] = organization_id

        await self._log_audit(
            "auth.support_password_issued",
            entity_id=user.id,
            user_id=actor.id,
            user_role=actor_role,
            new_value=audit_extra,
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.PASSWORD_CHANGED,
        )
        logger.info(
            "auth.support_password_issued",
            actor_id=actor.id,
            target_user_id=user.id,
            flow=flow,
        )

        job = await enqueue(
            Job.SEND_SUPPORT_ISSUED_PASSWORD_EMAIL,
            to_email=user.email,
            first_name=(user.first_name or "").strip() or None,
            temporary_password=new_password,
            priority=QueuePriority.HIGH,
        )
        if job is None:
            logger.warning(LogEvent.SUPPORT_ISSUED_PASSWORD_EMAIL_FAILED, target_user_id=user.id)
        else:
            logger.info(LogEvent.SUPPORT_ISSUED_PASSWORD_EMAIL_SENT, target_user_id=user.id)

        return user.id, user.email

    # Password Reset

    _PWD_RESET_PREFIX = "pwd_reset:"
    _PWD_RESET_OTP_PREFIX = "pwd_reset_otp:"
    _PWD_RESET_SESS_PREFIX = "pwd_reset_sess:"
    _PWD_RESET_USER_SESS = "pwd_reset_user_session:"

    async def _revoke_password_reset_session(self, user_id: str) -> None:
        from app.core.redis import get_redis

        redis = get_redis()
        pointer = await redis.get(f"{self._PWD_RESET_USER_SESS}{user_id}")
        if not pointer:
            return
        if isinstance(pointer, (bytes, bytearray)):
            pointer = pointer.decode()
        h = str(pointer)
        await redis.delete(f"{self._PWD_RESET_SESS_PREFIX}{h}")
        await redis.delete(f"{self._PWD_RESET_USER_SESS}{user_id}")

    async def _store_password_reset_otp(self, user_id: str, otp: str) -> None:
        from app.core.redis import get_redis

        iat = int(datetime.now(UTC).timestamp())
        value = f"{hash_token(otp)}:{iat}"
        redis = get_redis()
        await redis.set(
            f"{self._PWD_RESET_OTP_PREFIX}{user_id}",
            value,
            ex=PASSWORD_RESET_EXPIRE_MINUTES * 60,
        )

    async def _try_consume_password_reset_otp(self, user_id: str, otp: str) -> datetime | None:
        from app.core.redis import get_redis

        redis = get_redis()
        key = f"{self._PWD_RESET_OTP_PREFIX}{user_id}"
        raw = await redis.get(key)
        if not raw:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        h, sep, iat_s = str(raw).partition(":")
        if not sep or h != hash_token(otp):
            return None
        await redis.delete(key)
        return datetime.fromtimestamp(int(iat_s), tz=UTC)

    async def _enforce_password_reset_throttle(self, email_hash: str) -> None:
        """Enforce exponential cooldown + daily cap for password reset requests.

        Two Redis keys per email (hashed), single prefix:
          pwd_reset:{hash}     — INCR counter, TTL 24h. Tracks daily usage (max 5).
          pwd_reset:{hash}:cd  — cooldown lock, exponential TTL (30s * 2^n).

        Raises RateLimitError (429) when either limit is hit.
        """
        from app.common.constants import PWD_RESET_BASE_COOLDOWN_SECONDS, PWD_RESET_MAX_DAILY_REQUESTS
        from app.common.exceptions import RateLimitError
        from app.core.redis import get_redis

        redis = get_redis()
        counter_key = f"{self._PWD_RESET_PREFIX}{email_hash}"
        cooldown_key = f"{counter_key}:cd"

        # Active cooldown from previous request
        cooldown_ttl = await redis.ttl(cooldown_key)
        if cooldown_ttl > 0:
            raise RateLimitError(
                f"Please wait {cooldown_ttl} seconds before requesting another password reset.",
                retry_after=cooldown_ttl,
            )

        # Daily cap
        current = await redis.get(counter_key)
        count = int(current) if current else 0
        if count >= PWD_RESET_MAX_DAILY_REQUESTS:
            key_ttl = await redis.ttl(counter_key)
            raise RateLimitError(
                "Maximum password reset requests reached for today. Please try again tomorrow.",
                retry_after=max(key_ttl, 0),
            )

        # Increment counter (24h TTL on first use)
        new_count = await redis.incr(counter_key)
        if new_count == 1:
            await redis.expire(counter_key, 86400)

        # Exponential cooldown: 30s, 60s, 120s, 240s, 480s
        cooldown = PWD_RESET_BASE_COOLDOWN_SECONDS * (2 ** (new_count - 1))
        await redis.set(cooldown_key, "1", ex=cooldown)

    async def request_invite_link_reminder(self, email: str) -> None:
        email_normalized = email.strip().lower()
        user = await self.user_repo.find_by_email(email_normalized)
        if user is None:
            logger.info(
                LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                reason="unknown_email",
                email=mask_email(email_normalized),
            )
            return

        if user.status != UserStatus.PENDING_VERIFICATION:
            logger.info(
                LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                reason="not_pending_activation",
                user_id=user.id,
            )
            return

        pending_request = await self.activation_link_request_repo.get_pending_for_user(user.id)
        if pending_request is not None:
            logger.info(
                LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                reason="pending_request_exists",
                user_id=user.id,
                activation_link_request_id=pending_request.id,
            )
            return

        role = UserRole(str(user.role))
        recipient_ids: list[str] = []
        org_id_for_notification: str | None = None
        context_extra: dict[str, object] = {}

        if role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            recipient_ids = await self._perm_service.list_active_admin_recipient_ids_for_resource(
                resource=Resource.ADMINS,
                min_level=PermissionLevel.WRITE,
            )
            context_extra["invited_role"] = "ADMIN"

        elif role == UserRole.DRIVER:
            driver = await self._driver_repo.find_by_user_id(user.id)
            recipient_ids = await self._perm_service.list_active_admin_recipient_ids_for_resource(
                resource=Resource.DRIVERS,
                min_level=PermissionLevel.WRITE,
            )
            context_extra["invited_role"] = "DRIVER"
            if driver is not None:
                context_extra["driver_id"] = driver.id

        elif role == UserRole.CUSTOMER_B2B:
            if not user.organization_id:
                logger.info(
                    LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                    reason="b2b_no_organization",
                    user_id=user.id,
                )
                return
            org = await self._org_repo.get_by_id(user.organization_id)
            if org is None:
                logger.info(
                    LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                    reason="b2b_org_missing",
                    user_id=user.id,
                )
                return
            recipient_ids = []
            for uid in (org.account_manager_user_id, org.secondary_account_manager_user_id, org.additional_account_manager_user_id):
                if uid:
                    recipient_ids.append(str(uid))
            recipient_ids = list(dict.fromkeys(recipient_ids))
            org_id_for_notification = user.organization_id
            context_extra["invited_role"] = "CUSTOMER_B2B"
            context_extra["organization_id"] = user.organization_id
        else:
            logger.info(
                LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                reason="unsupported_role",
                user_id=user.id,
                role=role.value,
            )
            return

        if not recipient_ids:
            logger.warning(
                "auth.invite_link_reminder_no_recipients",
                user_id=user.id,
                role=role.value,
            )
            return

        activation_link_request, created = await self.activation_link_request_repo.get_or_create_pending(user.id)
        if not created:
            logger.info(
                LogEvent.ACTIVATION_LINK_REMINDER_SKIPPED,
                reason="pending_request_exists",
                user_id=user.id,
                activation_link_request_id=activation_link_request.id,
            )
            return

        display_name = f"{user.first_name} {user.last_name}".strip()
        if not display_name:
            display_name = user.email
        body = f"{display_name} requested a new account activation link after the previous link expired."
        event = NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value
        ntype = NotificationType.ADMIN_INTERNAL.value
        base_ctx: dict[str, object] = {
            "activation_link_request_id": activation_link_request.id,
            "requester_user_id": user.id,
            "requester_role": role.value,
            "requester_name": display_name,
            **context_extra,
        }
        unique_recipient_ids = list(dict.fromkeys(recipient_ids))
        for rid in unique_recipient_ids:
            await self._notif_repo.create_notification(
                recipient_id=rid,
                organization_id=org_id_for_notification,
                event=event,
                notification_type=ntype,
                subject=None,
                body=body,
                context_json=base_ctx,
            )
        logger.info(
            LogEvent.ACTIVATION_LINK_REMINDER_QUEUED,
            user_id=user.id,
            role=role.value,
            recipient_count=len(unique_recipient_ids),
        )

    async def request_password_reset(self, email: str, client_type: ClientType) -> None:
        email_normalized = email.strip().lower()
        user = await self.user_repo.find_by_email(email_normalized)
        allowed_client = ROLE_TO_CLIENT_TYPE.get(user.role, ClientType.CUSTOMER_B2C) if user else None
        if user is None or allowed_client != client_type:
            logger.info(LogEvent.PASSWORD_RESET_REQUESTED_UNKNOWN_EMAIL, email=mask_email(email_normalized))
            raise ValidationError("Invalid email provided, please provide a valid email address.")
        email_hash = hash_token(email_normalized)
        await self._enforce_password_reset_throttle(email_hash)
        await self._revoke_password_reset_session(user.id)
        reset_otp = "".join(
            secrets.choice("0123456789") for _ in range(PASSWORD_RESET_OTP_LENGTH)
        )

        await self._log_audit(
            "auth.password_reset_requested",
            entity_id=user.id,
            user_id=user.id,
            user_role=user.role,
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.PASSWORD_CHANGED,
        )
        logger.info(
            LogEvent.PASSWORD_RESET_EMAIL_PLACEHOLDER,
            user_id=user.id,
            email=mask_email(user.email),
        )
        await self._store_password_reset_otp(user.id, reset_otp)
        job = await enqueue(
            Job.SEND_PASSWORD_RESET_EMAIL,
            to_email=user.email,
            first_name=user.first_name,
            reset_otp=reset_otp,
            expires_minutes=PASSWORD_RESET_EXPIRE_MINUTES,
            priority=QueuePriority.HIGH,
        )
        if job is None:
            logger.warning(
                LogEvent.PASSWORD_RESET_EMAIL_PLACEHOLDER,
                user_id=user.id,
                email=mask_email(user.email),
                enqueue_failed=True,
            )

    async def verify_password_reset_otp(
        self,
        email: str,
        otp: str,
        client_type: ClientType,
    ) -> dict:
        email_normalized = email.strip().lower()
        user = await self.user_repo.find_by_email(email_normalized)
        allowed_client = ROLE_TO_CLIENT_TYPE.get(user.role, ClientType.CUSTOMER_B2C) if user else None
        if user is None or allowed_client != client_type:
            raise AuthenticationError("Invalid or expired verification code")
        if await self._try_consume_password_reset_otp(user.id, otp) is None:
            raise AuthenticationError("Invalid or expired verification code")

        from app.core.redis import get_redis

        await self._revoke_password_reset_session(user.id)
        raw_token = secrets.token_hex(32)
        h = hash_token(raw_token)
        iat = int(datetime.now(UTC).timestamp())
        ttl_sec = PASSWORD_RESET_SESSION_MINUTES * 60
        redis = get_redis()
        await redis.set(
            f"{self._PWD_RESET_SESS_PREFIX}{h}",
            f"{user.id}:{iat}",
            ex=ttl_sec,
        )
        await redis.set(
            f"{self._PWD_RESET_USER_SESS}{user.id}",
            h,
            ex=ttl_sec,
        )
        expires_at = datetime.now(UTC) + timedelta(minutes=PASSWORD_RESET_SESSION_MINUTES)
        return {
            "password_reset_token": raw_token,
            "expires_in": ttl_sec,
            "expires_at": expires_at,
        }

    async def confirm_password_reset(
        self,
        new_password: str,
        *,
        password_reset_token: str,
        client_type: ClientType,
    ) -> None:
        from app.core.redis import get_redis

        h = hash_token(password_reset_token.strip())
        key = f"{self._PWD_RESET_SESS_PREFIX}{h}"
        redis = get_redis()
        raw = await redis.get(key)
        if not raw:
            raise AuthenticationError("Invalid or expired reset session")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        part = str(raw).split(":", 1)
        if len(part) != 2:
            raise AuthenticationError("Invalid or expired reset session")
        user_id, iat_s = part[0], part[1]
        try:
            token_issued_at = datetime.fromtimestamp(int(iat_s), tz=UTC)
        except (TypeError, ValueError):
            raise AuthenticationError("Invalid or expired reset session") from None

        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("Invalid or expired reset session")

        allowed_client = ROLE_TO_CLIENT_TYPE.get(user.role, ClientType.CUSTOMER_B2C)
        if allowed_client != client_type:
            raise AuthenticationError("Invalid or expired reset session")

        new_hash = hash_password(new_password)
        now_utc = datetime.now(UTC)
        updated = await self.user_repo.atomic_password_reset(
            user_id=user_id,
            new_hash=new_hash,
            now=now_utc,
            token_issued_at=token_issued_at,
        )
        if not updated:
            raise AuthenticationError("Invalid or expired reset session")

        await redis.delete(key)
        await redis.delete(f"{self._PWD_RESET_USER_SESS}{user_id}")

        await self.token_repo.revoke_all_for_user(user_id)
        await self._doc_access_token_repo.revoke_all_active_for_user(user_id)

        await self._log_audit(
            "auth.password_reset_confirmed",
            entity_id=user_id,
            user_id=user_id,
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.PASSWORD_CHANGED,
        )
        logger.info(LogEvent.PASSWORD_RESET_CONFIRMED, user_id=user_id)

    # Invites (flow B: user exists first, invite = set password / activate)

    INVITE_EXPIRE_DAYS = 7

    async def _invite_send_throttle_should_suppress(self, target_user_id: str) -> bool:
        try:
            redis = get_redis()
        except RuntimeError:
            return False
        counter_key = f"{_INVITE_SEND_PREFIX}{target_user_id}"
        cooldown_key = f"{counter_key}:cd"
        cooldown_ttl = await redis.ttl(cooldown_key)
        if cooldown_ttl > 0:
            return True
        current = await redis.get(counter_key)
        count = int(current) if current else 0
        return count >= INVITE_SEND_MAX_DAILY_REQUESTS

    async def _invite_send_throttle_consume(self, target_user_id: str) -> None:
        try:
            redis = get_redis()
        except RuntimeError:
            return
        counter_key = f"{_INVITE_SEND_PREFIX}{target_user_id}"
        cooldown_key = f"{counter_key}:cd"
        new_count = await redis.incr(counter_key)
        if new_count == 1:
            await redis.expire(counter_key, 86400)
        cooldown = INVITE_SEND_BASE_COOLDOWN_SECONDS * (2 ** (new_count - 1))
        await redis.set(cooldown_key, "1", ex=int(cooldown))

    async def create_invite(
        self,
        inviter: AuthUser,
        user_id: str,
        expires_days: int | None = None,
        *,
        organization_id: str | None = None,
    ) -> CreateInviteResult:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(resource="user", id=user_id)

        if user.email_verified:
            raise ConflictError("User has already accepted their invite")

        await self._enforce_invite_scope_for_target_org(
            inviter,
            user.organization_id,
            user.id,
            organization_id=organization_id,
        )

        if await self._invite_send_throttle_should_suppress(user_id):
            latest = await self.invite_repo.find_latest_invite_id_for_user(user_id)
            public_id = latest if latest is not None else "00000000-0000-0000-0000-000000000000"
            logger.info(
                LogEvent.INVITE_SEND_THROTTLED,
                invited_user_id=user_id,
                inviter_id=inviter.id,
            )
            return CreateInviteResult(True, None, None, user, public_id)

        await self._invite_send_throttle_consume(user_id)

        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        expiry = expires_days or self.INVITE_EXPIRE_DAYS
        expires_at = datetime.now(UTC) + timedelta(days=expiry)

        invite = await self.invite_repo.create(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=inviter.id,
        )

        await self._log_audit(
            "auth.invite_created",
            entity_id=invite.id,
            user_id=inviter.id,
            new_value={"invited_user_id": user_id, "email": mask_email(user.email)},
            severity="NOTICE",
        )
        logger.info(LogEvent.INVITE_CREATED, invite_id=invite.id, user_id=user_id, email=mask_email(user.email))

        return CreateInviteResult(False, invite, raw_token, user, invite.id)

    async def resend_invite_for_activation_link_request(
        self,
        inviter: AuthUser,
        request_id: str,
    ) -> CreateInviteResult:
        request = await self.activation_link_request_repo.get_by_id_for_update(request_id)
        if request is None:
            raise NotFoundError(resource="activation_link_request", id=request_id)
        if request.status != ActivationLinkRequestStatus.PENDING:
            raise ConflictError("Activation link request has already been handled")

        target = await self.user_repo.get_by_id(request.requester_user_id)
        if target is None:
            raise NotFoundError(resource="user", id=request.requester_user_id)

        if target.role == UserRole.DRIVER:
            invite, raw_token, user = await self._create_driver_activation_invite_row(
                target_user_id=request.requester_user_id,
                invited_by_user_id=inviter.id,
                inviter=inviter,
            )
            result = CreateInviteResult(
                throttled=False,
                invite=invite,
                raw_token=raw_token,
                user=user,
                public_invite_id=invite.id,
            )
        else:
            result = await self.create_invite(inviter, request.requester_user_id)

        resolved = await self.activation_link_request_repo.resolve_pending_by_id(
            request.id,
            resolved_by_user_id=inviter.id,
            resolved_invite_id=result.public_invite_id,
        )
        if not resolved:
            raise ConflictError("Activation link request has already been handled")
        return result

    async def _get_pending_invite_with_user(self, token: str) -> tuple[Invite, User]:
        token_hash = hash_token(token)
        invite = await self.invite_repo.find_pending_by_token_hash(token_hash)
        if invite is None:
            raise AuthenticationError("Invalid or expired invite link")
        if invite.expires_at < datetime.now(UTC):
            raise AuthenticationError("Invite link has expired")
        user = invite.user
        if user is None:
            raise AuthenticationError("Invalid or expired invite link")
        return invite, user

    async def validate_invite(self, token: str) -> dict:
        invite, user = await self._get_pending_invite_with_user(token)

        await self._log_audit(
            "auth.invite_validated",
            entity_id=invite.id,
            user_id=user.id,
            new_value={"email": mask_email(user.email)},
            severity="INFO",
        )
        logger.info("auth.invite_validated", invite_id=invite.id, user_id=user.id)

        return {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "role": user.role,
        }

    async def complete_invite_activation(
        self,
        token: str,
        password: str,
        *,
        allowed_roles: frozenset[str] | None = None,
    ) -> User:
        """Set password and activate user after a valid pending invite token."""
        token_hash = hash_token(token)
        invite = await self.invite_repo.find_pending_by_token_hash(token_hash)
        if invite is None:
            raise AuthenticationError("Invalid or expired invite link")

        user = invite.user
        if user is None:
            raise AuthenticationError("Invalid invite")

        role_val = user.role if isinstance(user.role, str) else user.role.value
        if allowed_roles is not None and role_val not in allowed_roles:
            raise AuthenticationError("Invalid or expired invite link")

        claimed = await self.invite_repo.mark_used_and_invalidate_sibling_invites(invite.id, invite.user_id)
        if not claimed:
            raise ConflictError("Invite has already been accepted")

        now_utc = datetime.now(UTC)
        await self.user_repo.update_by_id(
            invite.user_id,
            {
                "password_hash": hash_password(password),
                "password_changed_at": now_utc,
                "status": UserStatus.ACTIVE,
                "email_verified": True,
                "force_password_change": False,
            },
        )
        await self.token_repo.revoke_all_for_user(invite.user_id)

        is_driver_activation = allowed_roles == frozenset({UserRole.DRIVER.value})
        activation_event = (
            AuditEventType.DRIVER_ACTIVATION_COMPLETED
            if is_driver_activation
            else AuditEventType.ACCOUNT_ACTIVATED
        )
        activation_action = (
            "auth.driver_activation_completed"
            if is_driver_activation
            else "auth.invite_activated"
        )
        await self._log_audit(
            activation_action,
            entity_id=user.id,
            user_id=user.id,
            new_value={"email": mask_email(user.email), "role": user.role},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=activation_event,
        )
        logger.info("Account activated via invite", user_id=user.id, email=mask_email(user.email))

        # Role-specific profile activation. Each branch is idempotent — re-running
        # the same invite (which is prevented above) or a missing profile row is a
        # silent no-op rather than an error.
        if role_val == UserRole.DRIVER.value:
            from app.modules.drivers.service import DriverService

            try:
                await DriverService(self._session, self._request).activate_driver_on_login(user_id=invite.user_id)
            except Exception:
                logger.exception("driver.profile_activation_failed", user_id=invite.user_id)
        elif role_val == UserRole.CUSTOMER_B2B.value:
            from app.modules.organizations.repository import OrgContactRepository

            try:
                activated = await OrgContactRepository(self._session).activate_pending_for_user(invite.user_id)
                if activated > 0:
                    logger.info("org_contact.activated_via_invite", user_id=invite.user_id, count=activated)
            except Exception:
                logger.exception("org_contact.activation_failed", user_id=invite.user_id)

        updated_user = await self.user_repo.get_by_id(invite.user_id)
        assert updated_user is not None
        return updated_user

    async def validate_driver_activation_token(self, token: str) -> dict[str, object]:
        """Classify activation token for driver mobile (no auth). Does not consume the token."""
        raw = (token or "").strip()
        if not raw:
            return {"valid": False, "reason": "INVALID"}
        token_hash = hash_token(raw)
        invite = await self.invite_repo.find_by_token_hash_with_user(token_hash)
        if invite is None:
            return {"valid": False, "reason": "INVALID"}
        user = invite.user
        if user is None:
            return {"valid": False, "reason": "INVALID"}
        role_val = user.role if isinstance(user.role, str) else user.role.value
        if role_val != UserRole.DRIVER.value:
            return {"valid": False, "reason": "INVALID"}
        if invite.used_at is not None:
            return {"valid": False, "reason": "INVALID"}
        if user.email_verified:
            return {"valid": False, "reason": "ALREADY_ACTIVATED"}
        if invite.expires_at <= datetime.now(UTC):
            return {"valid": False, "reason": "EXPIRED"}
        return {
            "valid": True,
            "reason": None,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "expires_at": invite.expires_at.isoformat(),
        }

    async def complete_driver_activation(self, token: str, password: str) -> User:
        """Set password for a pending driver invite (driver role only)."""
        return await self.complete_invite_activation(
            token,
            password,
            allowed_roles=frozenset({UserRole.DRIVER.value}),
        )

    async def _create_driver_activation_invite_row(
        self,
        *,
        target_user_id: str,
        invited_by_user_id: str | None,
        inviter: AuthUser | None,
    ) -> tuple[Invite, str, User]:
        from app.modules.drivers.enums import DriverAccountStatus
        from app.modules.drivers.repository import DriverRepository

        user = await self.user_repo.get_by_id(target_user_id)
        if user is None:
            raise NotFoundError(resource="user", id=target_user_id)
        if user.role != UserRole.DRIVER:
            raise ValidationError("Not a driver account")
        if user.email_verified:
            raise ConflictError("This account has already completed activation")

        driver_repo = DriverRepository(self._session)
        driver = await driver_repo.find_by_user_id(user.id)
        if driver is None or driver.account_status != DriverAccountStatus.PENDING_ACTIVATION:
            raise ValidationError("Driver is not awaiting activation")

        if inviter is not None:
            await self._enforce_invite_scope_for_target_org(
                inviter,
                user.organization_id,
                user.id,
                organization_id=None,
            )

        await self.invite_repo.invalidate_pending_invites_for_user(user.id)
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        days = settings.DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS
        expires_at = datetime.now(UTC) + timedelta(days=days)
        invite = await self.invite_repo.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=invited_by_user_id,
        )
        invite_event = (
            AuditEventType.PUBLIC_DRIVER_ACTIVATION_RESEND_ACCEPTED
            if inviter is None
            else AuditEventType.DRIVER_ACTIVATION_INVITE_ISSUED
        )
        invite_action = (
            "auth.public_driver_activation_resend"
            if inviter is None
            else "auth.driver_activation_invite_created"
        )
        await self._log_audit(
            invite_action,
            entity_id=invite.id,
            user_id=user.id if inviter is None else inviter.id,
            user_role=inviter.role if inviter else None,
            new_value={"target_user_id": user.id, "email": mask_email(user.email)},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=invite_event,
        )
        logger.info(
            "auth.driver_activation_invite_created",
            invite_id=invite.id,
            target_user_id=user.id,
            email=mask_email(user.email),
        )
        return invite, raw_token, user

    async def issue_driver_activation_email(
        self, *, inviter: AuthUser, target_user_id: str
    ) -> DriverActivationEmailResult:
        """Create a fresh invite and enqueue the driver set-password email.

        Returns sent=False when LINK_BASE_URL_DRIVER is unset or the worker is unavailable.
        """
        invite, raw_token, user = await self._create_driver_activation_invite_row(
            target_user_id=target_user_id,
            invited_by_user_id=inviter.id,
            inviter=inviter,
        )
        link = build_driver_set_password_link(token=raw_token, email=user.email)
        if not link:
            logger.warning(
                "driver.activation.email_skipped_missing_link_base",
                user_id=user.id,
                email=mask_email(user.email),
            )
            return DriverActivationEmailResult(sent=False, invite_id=invite.id, user=user)
        job = await enqueue(
            Job.SEND_DRIVER_ACTIVATION_EMAIL,
            invite.id,
            user.email,
            (user.first_name or "").strip() or "there",
            link,
            expires_days=settings.DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS,
            priority=QueuePriority.HIGH,
        )
        return DriverActivationEmailResult(sent=job is not None, invite_id=invite.id, user=user)

    async def resend_driver_activation_public(self, email: str) -> None:
        """Public resend for expired links — rate-limited; silent when email is not eligible."""
        from hashlib import sha256

        from app.modules.drivers.enums import DriverAccountStatus
        from app.modules.drivers.repository import DriverRepository

        normalized = email.strip().lower()
        if not normalized:
            return

        try:
            redis = get_redis()
        except RuntimeError:
            redis = None
        if redis is not None:
            key = f"driver:act:resend:{sha256(normalized.encode()).hexdigest()[:48]}"
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, 3600)
            if n > settings.DRIVER_ACTIVATION_RESEND_MAX_PER_HOUR:
                raise RateLimitError(
                    "Too many resend requests for this email. Please try again later.",
                    retry_after=3600,
                )

        user = await self.user_repo.find_by_email(normalized)
        if user is None or user.role != UserRole.DRIVER or user.email_verified:
            return

        driver_repo = DriverRepository(self._session)
        driver = await driver_repo.find_by_user_id(user.id)
        if driver is None or driver.account_status != DriverAccountStatus.PENDING_ACTIVATION:
            return

        invite, raw_token, u = await self._create_driver_activation_invite_row(
            target_user_id=user.id,
            invited_by_user_id=None,
            inviter=None,
        )
        link = build_driver_set_password_link(token=raw_token, email=u.email)
        if not link:
            logger.warning(
                "driver.activation.resend_skipped_missing_link_base",
                user_id=u.id,
                email=mask_email(u.email),
            )
            return
        await enqueue(
            Job.SEND_DRIVER_ACTIVATION_EMAIL,
            invite.id,
            u.email,
            (u.first_name or "").strip() or "there",
            link,
            expires_days=settings.DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS,
            priority=QueuePriority.HIGH,
        )

    # Internal helpers

    @staticmethod
    async def _blacklist_access_jti(jti: str) -> None:
        # Access tokens live for JWT_ACCESS_TOKEN_EXPIRE_MINUTES max.
        # Blacklist for the full TTL since we don't know the exact exp from the JTI alone.
        ttl = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        await blacklist_token(jti, ttl)

    async def _issue_tokens(
        self,
        user: User,
        client_type: ClientType,
    ) -> LoginServiceResponse:
        """Issue access + refresh token pair bound to client_type (X-Client-Type)."""
        active_count = await self.token_repo.count_active_for_user(user.id)
        if active_count >= MAX_ACTIVE_SESSIONS_PER_USER:
            await self.token_repo.revoke_oldest_keeping(user.id, MAX_ACTIVE_SESSIONS_PER_USER - 1)

        ct = client_type.value
        # Create a stable logical device session (sid) for UX + immediate revocation.
        sess = await self.session_repo.create_session(user_id=user.id, user_agent=self._user_agent, ip_address=self._ip_address)

        access_token, access_jti = create_access_token(
            user_id=user.id,
            role=user.role,
            client_type=ct,
            region_id=user.region_id,
            organization_id=user.organization_id,
            sid=sess.session_id,
            sv=user.session_sv,
        )
        raw_refresh, token_hash, expires_at = create_refresh_token(user.id, ct)

        await self.token_repo.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            access_jti=access_jti,
            user_agent=self._user_agent,
            ip_address=self._ip_address,
            session_id=sess.session_id,
        )

        # Touch session to ensure last_seen_at/inactivity metadata are set.
        await self.session_repo.touch_session(
            session_id=sess.session_id,
            user_agent=self._user_agent,
            ip_address=self._ip_address,
        )

        await self._log_audit(
            "auth.session_created",
            entity_type="session",
            entity_id=sess.session_id,
            user_id=user.id,
        )

        logger.info(LogEvent.USER_LOGGED_IN, user_id=user.id, role=user.role)

        return LoginServiceResponse(
            tokens=TokenData(
                access_token=access_token,
                access_token_expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                refresh_token=raw_refresh,
                refresh_token_expires_in=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            ),
            user=UserBrief(
                id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                role=user.role,
                organization_id=user.organization_id,
                region_id=user.region_id,
                requires_password_change=user.force_password_change,
                created_at=user.created_at,
            ),
        )
