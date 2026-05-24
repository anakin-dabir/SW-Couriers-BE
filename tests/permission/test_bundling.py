"""Tests for SYSTEM_DEFAULTS permission bundling."""

from __future__ import annotations

from app.common.enums.permission import PermissionLevel, Resource
from app.modules.permission.bundling import effective_permission_level, is_assignable_resource


class TestEffectivePermissionLevel:
    def test_system_defaults_write_grants_dropdown_write(self) -> None:
        perms = {
            Resource.SYSTEM_DEFAULTS: PermissionLevel.WRITE,
            Resource.DYNAMIC_CONFIGS: PermissionLevel.NONE,
        }
        assert (
            effective_permission_level(perms, Resource.DYNAMIC_CONFIGS, PermissionLevel.WRITE)
            == PermissionLevel.WRITE
        )

    def test_system_defaults_does_not_grant_dropdown_read(self) -> None:
        perms = {Resource.SYSTEM_DEFAULTS: PermissionLevel.WRITE}
        assert (
            effective_permission_level(perms, Resource.DYNAMIC_CONFIGS, PermissionLevel.READ)
            == PermissionLevel.NONE
        )

    def test_dynamic_configs_read_is_direct_only(self) -> None:
        perms = {
            Resource.SYSTEM_DEFAULTS: PermissionLevel.NONE,
            Resource.DYNAMIC_CONFIGS: PermissionLevel.READ,
        }
        assert (
            effective_permission_level(perms, Resource.DYNAMIC_CONFIGS, PermissionLevel.READ)
            == PermissionLevel.READ
        )


class TestAssignableResources:
    def test_status_automation_not_assignable(self) -> None:
        assert not is_assignable_resource(Resource.STATUS_AUTOMATION_RULES)

    def test_deprecated_finance_resources_not_assignable(self) -> None:
        assert not is_assignable_resource(Resource.INVOICES)
        assert not is_assignable_resource(Resource.ACCOUNT_STATEMENTS)

    def test_billing_assignable(self) -> None:
        assert is_assignable_resource(Resource.BILLING)

    def test_system_defaults_assignable(self) -> None:
        assert is_assignable_resource(Resource.SYSTEM_DEFAULTS)
