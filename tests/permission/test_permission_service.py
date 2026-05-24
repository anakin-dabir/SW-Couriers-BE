"""Tests for PermissionService — resolution, enforcement, and admin operations."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.common.enums.user import UserRole
from app.common.exceptions import ForbiddenError, NotFoundError
from app.modules.permission.defaults import get_role_defaults
from app.modules.permission.repository import PermissionRepository
from app.modules.permission.service import PermissionService
from app.modules.user.models import User


def _build_service(session: AsyncSession) -> PermissionService:
    return PermissionService(session)


class TestResolvePermissions:
    """Test permission resolution (defaults + overrides merge)."""

    @pytest.mark.asyncio
    async def test_returns_role_defaults_when_no_overrides(self, db_session: AsyncSession, user_factory) -> None:
        """A user with no overrides should get pure role defaults."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        permissions = await service.resolve_permissions(user)

        defaults = get_role_defaults(UserRole.WAREHOUSE_STAFF)
        for resource in Resource:
            assert permissions[resource] == defaults[resource], f"Mismatch on {resource.value}"

    @pytest.mark.asyncio
    async def test_override_takes_precedence(self, db_session: AsyncSession, user_factory) -> None:
        """An override should replace the role default for that resource."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(
            target_user_id=user.id,
            resource=Resource.USERS,
            level=PermissionLevel.READ,
            granted_by=user.id,
        )

        permissions = await service.resolve_permissions(user)
        assert permissions[Resource.USERS] == PermissionLevel.READ

    @pytest.mark.asyncio
    async def test_non_overridden_resources_keep_defaults(self, db_session: AsyncSession, user_factory) -> None:
        """Resources without overrides should still use role defaults."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(
            target_user_id=user.id,
            resource=Resource.USERS,
            level=PermissionLevel.READ,
            granted_by=user.id,
        )

        permissions = await service.resolve_permissions(user)
        defaults = get_role_defaults(UserRole.WAREHOUSE_STAFF)
        assert permissions[Resource.DASHBOARD] == defaults[Resource.DASHBOARD]
        assert permissions[Resource.SHIPMENTS] == defaults[Resource.SHIPMENTS]


class TestCheckPermission:
    """Test the enforcement method that raises ForbiddenError."""

    @pytest.mark.asyncio
    async def test_passes_when_level_sufficient(self, db_session: AsyncSession, user_factory) -> None:
        """No exception when user meets the required level."""
        user: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        await service.check_permission(user, Resource.SHIPMENTS, PermissionLevel.WRITE)

    @pytest.mark.asyncio
    async def test_write_implies_read(self, db_session: AsyncSession, user_factory) -> None:
        """WRITE permission should satisfy a READ requirement."""
        user: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        await service.check_permission(user, Resource.SHIPMENTS, PermissionLevel.READ)

    @pytest.mark.asyncio
    async def test_raises_forbidden_when_level_insufficient(self, db_session: AsyncSession, user_factory) -> None:
        """ForbiddenError when user lacks the required level."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        with pytest.raises(ForbiddenError):
            await service.check_permission(user, Resource.USERS, PermissionLevel.READ)

    @pytest.mark.asyncio
    async def test_raises_forbidden_read_vs_write(self, db_session: AsyncSession, user_factory) -> None:
        """READ permission should NOT satisfy a WRITE requirement."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        with pytest.raises(ForbiddenError):
            await service.check_permission(user, Resource.DASHBOARD, PermissionLevel.WRITE)


class TestSetPermission:
    """Test admin set/revoke/reset operations."""

    @pytest.mark.asyncio
    async def test_set_removes_override_when_matches_default(self, db_session: AsyncSession, user_factory) -> None:
        """Setting a permission to the role default should remove the override row."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.USERS, PermissionLevel.WRITE, user.id)

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert Resource.USERS in overrides

        defaults = get_role_defaults(UserRole.WAREHOUSE_STAFF)
        default_level = defaults[Resource.USERS]
        await service.set_permission(user.id, Resource.USERS, default_level, user.id)

        overrides = await repo.get_overrides_for_user(user.id)
        assert Resource.USERS not in overrides

    @pytest.mark.asyncio
    async def test_set_permission_nonexistent_user_raises_404(self, db_session: AsyncSession) -> None:
        """Setting permission for a non-existent user raises NotFoundError."""
        service = _build_service(db_session)
        with pytest.raises(NotFoundError):
            await service.set_permission(
                "00000000-0000-0000-0000-000000000000",
                Resource.DASHBOARD,
                PermissionLevel.READ,
                "admin-id",
            )

    @pytest.mark.asyncio
    async def test_reset_to_defaults(self, db_session: AsyncSession, user_factory) -> None:
        """Reset should remove all overrides."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.USERS, PermissionLevel.WRITE, user.id)
        await service.set_permission(user.id, Resource.BILLING, PermissionLevel.READ, user.id)

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert len(overrides) == 2

        await service.reset_to_defaults(user.id, user.id)

        overrides = await repo.get_overrides_for_user(user.id)
        assert len(overrides) == 0


class TestBulkSetPermissions:
    """Test bulk permission replacement."""

    @pytest.mark.asyncio
    async def test_bulk_set_replaces_all_overrides(self, db_session: AsyncSession, user_factory) -> None:
        """Bulk set should delete old overrides and insert new ones."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.USERS, PermissionLevel.READ, user.id)

        await service.bulk_set_permissions(
            user.id,
            {
                Resource.DASHBOARD: PermissionLevel.READ,
                Resource.BILLING: PermissionLevel.READ,
            },
            user.id,
        )

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert Resource.USERS not in overrides
        assert Resource.DASHBOARD in overrides
        assert Resource.BILLING in overrides

    @pytest.mark.asyncio
    async def test_bulk_set_none_override_revokes_access(self, db_session: AsyncSession, user_factory) -> None:
        """Setting a resource to NONE when role default is higher must store the override.

        Regression guard: a warehouse staff (SHIPMENTS default = WRITE) should
        lose access when admin bulk-sets SHIPMENTS to NONE.
        """
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.bulk_set_permissions(
            user.id,
            {Resource.SHIPMENTS: PermissionLevel.NONE},
            user.id,
        )

        permissions = await service.resolve_permissions(user)
        assert permissions[Resource.SHIPMENTS] == PermissionLevel.NONE

        with pytest.raises(ForbiddenError):
            await service.check_permission(user, Resource.SHIPMENTS, PermissionLevel.READ)


class TestPermissionSummary:
    """Test the admin-facing permission summary."""

    @pytest.mark.asyncio
    async def test_summary_shows_source(self, db_session: AsyncSession, user_factory) -> None:
        """Summary should distinguish role_default vs override."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.USERS, PermissionLevel.READ, user.id)

        summary = await service.get_user_permission_summary(user)

        assert summary[Resource.USERS.value]["source"] == "override"
        assert summary[Resource.USERS.value]["level"] == "READ"
        assert summary[Resource.DASHBOARD.value]["source"] == "role_default"
        assert summary[Resource.DASHBOARD.value]["level"] == "READ"

    @pytest.mark.asyncio
    async def test_summary_covers_all_resources(self, db_session: AsyncSession, user_factory) -> None:
        """Summary must include every Resource, even with no overrides."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)
        summary = await service.get_user_permission_summary(user)

        for resource in Resource:
            assert resource.value in summary, f"Missing {resource.value} from summary"


class TestUpsertIdempotency:
    """Setting the same permission twice must not crash or create duplicates."""

    @pytest.mark.asyncio
    async def test_set_same_permission_twice_is_idempotent(self, db_session: AsyncSession, user_factory) -> None:
        """Double-click scenario: admin saves the same value twice."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.DASHBOARD, PermissionLevel.READ, user.id)
        await service.set_permission(user.id, Resource.DASHBOARD, PermissionLevel.READ, user.id)

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert overrides[Resource.DASHBOARD] == PermissionLevel.READ

    @pytest.mark.asyncio
    async def test_upsert_updates_level_not_duplicate(self, db_session: AsyncSession, user_factory) -> None:
        """Changing level on an existing override should update, not create a second row."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.DASHBOARD, PermissionLevel.READ, user.id)
        await service.set_permission(user.id, Resource.DASHBOARD, PermissionLevel.WRITE, user.id)

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert overrides[Resource.DASHBOARD] == PermissionLevel.WRITE

        all_rows = await repo.get_all_for_user(user.id)
        dashboard_rows = [r for r in all_rows if r.resource == Resource.DASHBOARD.value]
        assert len(dashboard_rows) == 1


class TestBulkDuplicateResources:
    """Bulk set with duplicate resources in the request."""

    @pytest.mark.asyncio
    async def test_bulk_set_last_value_wins_for_duplicate_resource(self, db_session: AsyncSession, user_factory) -> None:
        """If frontend sends the same resource twice, last value should win (dict behavior)."""
        user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.bulk_set_permissions(
            user.id,
            {
                Resource.DASHBOARD: PermissionLevel.READ,
                Resource.DASHBOARD: PermissionLevel.WRITE,
            },
            user.id,
        )

        permissions = await service.resolve_permissions(user)
        assert permissions[Resource.DASHBOARD] == PermissionLevel.WRITE


class TestPermissionAfterReset:
    """Verify permission state is clean after reset operations."""

    @pytest.mark.asyncio
    async def test_resolve_after_reset_returns_pure_defaults(self, db_session: AsyncSession, user_factory) -> None:
        """After reset, resolve_permissions must return exactly the role defaults."""
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.set_permission(user.id, Resource.USERS, PermissionLevel.WRITE, user.id)
        await service.set_permission(user.id, Resource.BILLING, PermissionLevel.WRITE, user.id)
        await service.set_permission(user.id, Resource.SHIPMENTS, PermissionLevel.NONE, user.id)

        await service.reset_to_defaults(user.id, user.id)

        permissions = await service.resolve_permissions(user)
        defaults = get_role_defaults(UserRole.WAREHOUSE_STAFF)
        for resource in Resource:
            assert permissions[resource] == defaults[resource], f"After reset, {resource.value} should be {defaults[resource].name} " f"but got {permissions[resource].name}"

    @pytest.mark.asyncio
    async def test_reset_nonexistent_user_raises_404(self, db_session: AsyncSession) -> None:
        """Resetting permissions for a non-existent user raises NotFoundError."""
        service = _build_service(db_session)
        with pytest.raises(NotFoundError):
            await service.reset_to_defaults(
                "00000000-0000-0000-0000-000000000000",
                "admin-id",
            )


class TestSinglePermissionRevoke:
    """Test revoking access via set_permission with NONE level."""

    @pytest.mark.asyncio
    async def test_set_none_revokes_access_for_non_none_default(self, db_session: AsyncSession, user_factory) -> None:
        """Admin sets SHIPMENTS to NONE for warehouse staff (default=WRITE).

        The override must persist and block access.
        """
        user: User = await user_factory(role="WAREHOUSE_STAFF", status="ACTIVE", email_verified=True)
        service = _build_service(db_session)

        await service.check_permission(user, Resource.SHIPMENTS, PermissionLevel.WRITE)

        await service.set_permission(user.id, Resource.SHIPMENTS, PermissionLevel.NONE, user.id)

        with pytest.raises(ForbiddenError):
            await service.check_permission(user, Resource.SHIPMENTS, PermissionLevel.READ)

        repo = PermissionRepository(db_session)
        overrides = await repo.get_overrides_for_user(user.id)
        assert Resource.SHIPMENTS in overrides
        assert overrides[Resource.SHIPMENTS] == PermissionLevel.NONE
