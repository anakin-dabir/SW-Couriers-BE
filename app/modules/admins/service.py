"""Admin management service.

Handles the full Admin Management screen:
  - Create admin (invite wizard)
  - Send invite to draft admin
  - List admins (paginated, search, filter)
  - Get single admin detail
  - Update admin profile + permissions
  - Suspend / Reactivate admin

Authorization notes:
  Routes enforce ``Resource.ADMINS`` at READ or WRITE as appropriate. Suspend, reactivate, and
  delete require WRITE; self-suspend, self-delete, self-reactivate, and self permission updates
  are rejected in this service.
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import structlog
from fastapi import Request, UploadFile
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import func

from app.common.deps import AuthUser
from app.common.enums import UserRole, UserStatus
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.utils import mark_user_suspended, mask_email, unmark_user_suspended
from app.core.config import settings
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.auth.service import AuthService
from app.modules.admins.models import Admin
from app.modules.admins.repository import AdminRepository
from app.modules.organizations.repository import OrganizationRepository
from app.modules.permission.service import PermissionService
from app.modules.user.models import User
from app.modules.user.repository import UserRepository
from app.storage.upload import delete_image

logger = structlog.get_logger()

_ADMIN_ROLES = {UserRole.ADMIN, UserRole.SUPER_ADMIN}
_DEFAULT_ADMIN_COUNTRY = "United Kingdom"


class AdminService(BaseService):
    """Business logic for creating and managing admin users."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._user_repo = UserRepository(session)
        self._admin_repo = AdminRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._auth_service = AuthService(session, request)
        self._perm_service = PermissionService(session, request)
        self._audit = AuditService(session)

    @staticmethod
    def _enforce_quickbooks_write_permission(
        permissions: dict[Resource, PermissionLevel],
    ) -> dict[Resource, PermissionLevel]:
        normalized = dict(permissions)
        current = normalized.get(Resource.QUICKBOOKS, PermissionLevel.NONE)
        if current < PermissionLevel.WRITE:
            normalized[Resource.QUICKBOOKS] = PermissionLevel.WRITE
        return normalized

    # ── Profile photo ────────────────────────────────────────────────────────

    def get_profile_photo_url(self, avatar_url: str | None, *, expiry_seconds: int = 3600) -> str | None:
        """Generate a signed Cloudflare Images URL from the stored image key.

        Returns None if avatar_url is not set or signing is not configured.
        """
        if not avatar_url:
            return None
        try:
            from app.storage.cloudflare_images import get_images_client
            client = get_images_client()
            return client.generate_signed_url(avatar_url, expiry_seconds=expiry_seconds)
        except Exception:
            logger.warning("admin.profile_photo.signing_failed", avatar_url=avatar_url)
            return None

    async def upload_profile_photo(
        self,
        user_id: str,
        upload: UploadFile,
        *,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> str:
        """Upload a profile photo for an admin user via Cloudflare Images.

        Returns the Cloudflare image ID, which is stored in User.avatar_url.
        """
        allowed = {"image/jpeg", "image/png"}
        content_type = (upload.content_type or "").lower()
        if content_type not in allowed:
            raise ValidationError("Unsupported image type; only JPEG and PNG are allowed")

        data = await upload.read()
        max_bytes = 5 * 1024 * 1024
        if len(data) > max_bytes:
            raise ValidationError("File too large (max 5MB)")

        from app.storage.cloudflare_images import get_images_client
        client = get_images_client()
        result = await client.upload_image(
            BytesIO(data),
            filename=upload.filename or "admin-profile-photo",
            require_signed_urls=True,
            metadata={"kind": "admins_profile_photo", "user_id": user_id},
        )

        await self._user_repo.update_by_id(user_id, {"avatar_url": result.id})

        await self._audit.log(
            action="admin.profile_photo.uploaded",
            entity_type="user",
            entity_id=user_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"profile_photo_key": result.id},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        return result.id

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_admin(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: str | None,
        title: str | None,
        position_role: str | None,
        address_line_1: str,
        address_line_2: str | None,
        city: str,
        state: str,
        postcode: str,
        country: str | None,
        permissions: dict[Resource, PermissionLevel],
        inviter: AuthUser,
        send_invite: bool,
    ) -> tuple[str, str | None, str | None]:
        """Create an admin user, apply permission overrides, and optionally send invite.

        Returns:
            (user_id, invite_id | None, raw_token | None)
            invite_id and raw_token are None when send_invite=False (draft mode).

        Raises:
            ConflictError: Email already registered.
        """
        from app.core.security import generate_secure_password

        placeholder_password = generate_secure_password()

        user = await self._auth_service.create_user(
            email=email,
            password=placeholder_password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            role=UserRole.ADMIN,
            status=UserStatus.PENDING_VERIFICATION,
            force_password_change=False,
            audit_user_id=inviter.id,
            audit_user_role=inviter.role,
        )

        if title is not None or position_role is not None:
            update_data: dict = {}
            if title is not None:
                update_data["title"] = title
            if position_role is not None:
                update_data["position_role"] = position_role
            await self._user_repo.update_by_id(user.id, update_data)

        effective_permissions = self._enforce_quickbooks_write_permission(permissions)
        if effective_permissions:
            await self._perm_service.bulk_set_permissions(
                target_user_id=user.id,
                permissions=effective_permissions,
                granted_by=inviter.id,
            )

        country_norm = (country or "").strip() or _DEFAULT_ADMIN_COUNTRY
        await self._admin_repo.create(
            {
                "user_id": user.id,
                "address_line_1": address_line_1,
                "address_line_2": address_line_2,
                "city": city,
                "state": state,
                "postcode": postcode,
                "country": country_norm,
            }
        )

        invite_id: str | None = None
        raw_token: str | None = None
        if send_invite:
            r = await self._auth_service.create_invite(
                inviter,
                user.id,
            )
            invite_id = r.public_invite_id
            raw_token = r.raw_token

        await self._audit.log(
            action="admin.created",
            entity_type="user",
            entity_id=user.id,
            user_id=inviter.id,
            user_role=inviter.role,
            new_value={
                "email": mask_email(user.email),
                "title": title,
                "position_role": position_role,
                "send_invite": send_invite,
                "permission_overrides": len(permissions),
            },
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ROLE_ASSIGNED,
        )

        return user.id, invite_id, raw_token

    # ── Send invite for an existing draft admin ───────────────────────────────

    async def send_invite(
        self,
        *,
        user_id: str,
        inviter: AuthUser,
    ) -> tuple[str, str, str, str]:
        """Send an invite to a draft admin (status=PENDING_VERIFICATION).

        Returns:
            (invite_id, raw_token, email, first_name)
        """
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(resource="user", id=user_id)

        if user.role not in _ADMIN_ROLES:
            raise ConflictError("User is not an admin")

        if user.status != UserStatus.PENDING_VERIFICATION:
            raise ConflictError(
                f"Admin must be in {UserStatus.PENDING_VERIFICATION} status to send an invite; "
                f"current status: {user.status}"
            )

        r = await self._auth_service.create_invite(
            inviter,
            user_id,
        )
        return r.public_invite_id, r.raw_token or "", user.email, user.first_name

    # ── Stats ──────────────────────────────────────────────────────────────────

    async def get_admin_stats(self) -> dict[str, int]:
        """Get admin count statistics (total, active, inactive, suspended, pending_activation).

        Returns single dict with counts.
        """
        stmt = select(
            func.count().label("total"),
            func.count().filter(User.status == UserStatus.ACTIVE).label("active"),
            func.count().filter(User.status == UserStatus.INACTIVE).label("inactive"),
            func.count().filter(User.status == UserStatus.SUSPENDED).label("suspended"),
            func.count().filter(User.status == UserStatus.PENDING_VERIFICATION).label("pending_activation"),
        ).where(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))

        row = (await self._session.execute(stmt)).one()
        return {
            "total": row.total,
            "active": row.active,
            "inactive": row.inactive,
            "suspended": row.suspended,
            "pending_activation": row.pending_activation,
        }

    # ── List ──────────────────────────────────────────────────────────────────

    async def list_admins(
        self,
        *,
        page: int,
        size: int,
        search: str | None,
        status: UserStatus | None,
        sort: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[tuple[Admin, User]], int]:
        """Paginated list of admin rows with joined users (search, status, date range, sort).

        search matches on first_name, last_name, email, admin_ref, phone, position_role,
        address_line_1, city, state, postcode, country (case-insensitive ilike).
        sort: 'newest' (default) | 'oldest' | 'name_asc' | 'name_desc'
        date_from/date_to: filter by user created_at date range

        Returns (Admin, User) pairs for each admins row.
        """
        base_filter = User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN])

        stmt = (
            select(Admin, User)
            .join(User, Admin.user_id == User.id)
            .where(base_filter)
        )
        count_stmt = (
            select(func.count())
            .select_from(Admin)
            .join(User, Admin.user_id == User.id)
            .where(base_filter)
        )

        if search:
            term = f"%{search.strip()}%"
            condition = or_(
                User.first_name.ilike(term),
                User.last_name.ilike(term),
                User.email.ilike(term),
                Admin.admin_ref.ilike(term),
                User.phone.ilike(term),
                User.position_role.ilike(term),
                Admin.address_line_1.ilike(term),
                Admin.city.ilike(term),
                Admin.state.ilike(term),
                Admin.postcode.ilike(term),
                Admin.country.ilike(term),
            )
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        if status is not None:
            stmt = stmt.where(User.status == status)
            count_stmt = count_stmt.where(User.status == status)

        if date_from is not None:
            date_from_start = datetime.combine(date_from, datetime.min.time())
            stmt = stmt.where(User.created_at >= date_from_start)
            count_stmt = count_stmt.where(User.created_at >= date_from_start)

        if date_to is not None:
            date_to_end = datetime.combine(date_to, datetime.max.time())
            stmt = stmt.where(User.created_at <= date_to_end)
            count_stmt = count_stmt.where(User.created_at <= date_to_end)

        if sort == "oldest":
            stmt = stmt.order_by(User.created_at.asc())
        elif sort == "name_asc":
            stmt = stmt.order_by(User.first_name.asc(), User.last_name.asc())
        elif sort == "name_desc":
            stmt = stmt.order_by(User.first_name.desc(), User.last_name.desc())
        else:
            stmt = stmt.order_by(User.created_at.desc())

        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)

        items_result = await self._session.execute(stmt)
        count_result = await self._session.execute(count_stmt)

        rows = items_result.all()
        return [(row[0], row[1]) for row in rows], count_result.scalar_one()

    # ── Account assignments ───────────────────────────────────────────────────

    async def get_account_assignments(self, user_ids: list[str]) -> dict[str, list[dict]]:
        """Return a mapping of user_id → orgs where they hold any account manager position."""
        return await self._org_repo.get_account_assignments_for_admins(user_ids)

    async def get_admin(self, user_id: str) -> tuple[Admin, User]:
        stmt = (
            select(Admin)
            .where(Admin.user_id == user_id)
            .options(joinedload(Admin.user))
        )
        result = await self._session.execute(stmt)
        admin_row = result.unique().scalar_one_or_none()
        if admin_row is None:
            raise NotFoundError(resource="admin", id=user_id)
        u = admin_row.user
        if u.role not in _ADMIN_ROLES:
            raise NotFoundError(resource="admin", id=user_id)
        return admin_row, u

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_admin(
        self,
        *,
        user_id: str,
        first_name: str | None,
        last_name: str | None,
        phone: str | None,
        title: str | None,
        position_role: str | None,
        address_line_1: str | None,
        address_line_2: str | None,
        city: str | None,
        state: str | None,
        postcode: str | None,
        country: str | None,
        permissions: dict[Resource, PermissionLevel] | None,
        updated_by_user_id: str,
        updated_by_user_role: str,
    ) -> tuple[Admin, User]:
        """Update admin profile fields and/or permission overrides.

        Raises:
            NotFoundError: Admin not found.
            ForbiddenError: Attempt to change own permission overrides.
        """
        admin_row, user = await self.get_admin(user_id)

        update_data: dict = {}
        if first_name is not None:
            update_data["first_name"] = first_name
        if last_name is not None:
            update_data["last_name"] = last_name
        if phone is not None:
            update_data["phone"] = phone
        if title is not None:
            update_data["title"] = title
        if position_role is not None:
            update_data["position_role"] = position_role

        if update_data:
            await self._user_repo.update_by_id(user_id, update_data)

        admin_updates: dict = {}
        if address_line_1 is not None:
            admin_updates["address_line_1"] = address_line_1
        if address_line_2 is not None:
            admin_updates["address_line_2"] = address_line_2
        if city is not None:
            admin_updates["city"] = city
        if state is not None:
            admin_updates["state"] = state
        if postcode is not None:
            admin_updates["postcode"] = postcode
        if country is not None:
            admin_updates["country"] = country

        if admin_updates:
            await self._admin_repo.update_by_id(admin_row.id, admin_updates)

        if permissions is not None and user_id == updated_by_user_id:
            raise ForbiddenError("You cannot update your own permissions")

        if permissions is not None:
            permissions = self._enforce_quickbooks_write_permission(permissions)
            await self._perm_service.bulk_set_permissions(
                target_user_id=user_id,
                permissions=permissions,
                granted_by=updated_by_user_id,
            )

        await self._audit.log(
            action="admin.updated",
            entity_type="user",
            entity_id=user_id,
            user_id=updated_by_user_id,
            user_role=updated_by_user_role,
            new_value={
                **{k: v for k, v in update_data.items() if k != "title"},
                **admin_updates,
                "permission_overrides": len(permissions) if permissions else None,
            },
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )

        return await self.get_admin(user_id)

    async def update_admin_permissions(
        self,
        *,
        user_id: str,
        permissions: dict[Resource, PermissionLevel],
        updated_by_user_id: str,
        updated_by_user_role: str,
    ) -> tuple[Admin, User]:
        """Replace all permission overrides for an admin user.

        Raises:
            ForbiddenError: Attempt to update own permissions.
            NotFoundError: Target is not an admin user.
        """
        if user_id == updated_by_user_id:
            raise ForbiddenError("You cannot update your own permissions")

        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.role not in _ADMIN_ROLES:
            raise NotFoundError(resource="admin", id=user_id)

        await self._perm_service.bulk_set_permissions(
            target_user_id=user_id,
            permissions=self._enforce_quickbooks_write_permission(permissions),
            granted_by=updated_by_user_id,
        )

        await self._audit.log(
            action="admin.permissions.updated",
            entity_type="user",
            entity_id=user_id,
            user_id=updated_by_user_id,
            user_role=updated_by_user_role,
            new_value={"permission_overrides": len(permissions)},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )

        return await self.get_admin(user_id)

    async def delete_admin(
        self,
        *,
        user_id: str,
        deleted_by_user_id: str,
        deleted_by_user_role: str,
    ) -> None:
        if user_id == deleted_by_user_id:
            raise ForbiddenError("You cannot delete your own account")

        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.role not in _ADMIN_ROLES:
            raise NotFoundError(resource="admin", id=user_id)

        await self._perm_service.reset_to_defaults(
            target_user_id=user_id,
            admin_user_id=deleted_by_user_id,
        )

        if user.avatar_url:
            await delete_image(user.avatar_url)

        admin_row = await self._admin_repo.find_by_user_id(user_id)
        if admin_row is not None:
            await self._admin_repo.hard_delete(admin_row.id)

        await self._user_repo.hard_delete(user_id)

        await self._audit.log(
            action="admin.deleted",
            entity_type="user",
            entity_id=user_id,
            user_id=deleted_by_user_id,
            user_role=deleted_by_user_role,
            old_value={
                "status": user.status.value if hasattr(user.status, "value") else user.status,
                "role": user.role.value if hasattr(user.role, "value") else user.role,
            },
            new_value={"deleted": True},
            severity="WARNING",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_DEACTIVATED,
        )

    # ── Suspend ───────────────────────────────────────────────────────────────

    async def suspend_admin(
        self,
        *,
        user_id: str,
        reason: str,
        suspended_by_user_id: str,
        suspended_by_user_role: str,
    ) -> tuple[Admin, User]:
        """Suspend an active admin account.

        Raises:
            NotFoundError: Admin not found.
            ConflictError: Admin is not in ACTIVE status.
            ForbiddenError: Attempt to suspend self.
        """
        if user_id == suspended_by_user_id:
            raise ForbiddenError("You cannot suspend your own account")

        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.role not in _ADMIN_ROLES:
            raise NotFoundError(resource="admin", id=user_id)

        if user.status != UserStatus.ACTIVE:
            raise ConflictError(
                f"Admin must be ACTIVE to suspend; current status: {user.status}"
            )

        await self._user_repo.update_by_id(user_id, {"status": UserStatus.SUSPENDED})
        await mark_user_suspended(
            user_id,
            ttl_seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

        await self._audit.log(
            action="admin.suspended",
            entity_type="user",
            entity_id=user_id,
            user_id=suspended_by_user_id,
            user_role=suspended_by_user_role,
            old_value={"status": UserStatus.ACTIVE},
            new_value={"status": UserStatus.SUSPENDED},
            reason=reason,
            severity="WARNING",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
        )

        return await self.get_admin(user_id)

    async def reactivate_admin(
        self,
        *,
        user_id: str,
        reason: str,
        reactivated_by_user_id: str,
        reactivated_by_user_role: str,
    ) -> tuple[Admin, User]:
        """Reactivate a suspended admin account.

        Raises:
            NotFoundError: Admin not found.
            ConflictError: Admin is not SUSPENDED.
            ForbiddenError: Attempt to reactivate self.
        """
        if user_id == reactivated_by_user_id:
            raise ForbiddenError("You cannot reactivate your own account")

        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.role not in _ADMIN_ROLES:
            raise NotFoundError(resource="admin", id=user_id)

        if user.status != UserStatus.SUSPENDED:
            raise ConflictError(
                f"Admin must be SUSPENDED to reactivate; current status: {user.status}"
            )

        await self._user_repo.update_by_id(user_id, {"status": UserStatus.ACTIVE})
        await unmark_user_suspended(user_id)

        await self._audit.log(
            action="admin.reactivated",
            entity_type="user",
            entity_id=user_id,
            user_id=reactivated_by_user_id,
            user_role=reactivated_by_user_role,
            old_value={"status": UserStatus.SUSPENDED},
            new_value={"status": UserStatus.ACTIVE},
            reason=reason,
            severity="WARNING",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_REACTIVATED,
        )

        return await self.get_admin(user_id)
