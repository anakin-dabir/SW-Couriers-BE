"""Tests for the hardcoded default permission matrix.

Guards against accidentally leaving a resource out of a role's defaults.
"""

from typing import cast

from app.common.enums.permission import PermissionLevel, Resource
from app.common.enums.user import UserRole
from app.modules.permission.defaults import DEFAULT_PERMISSIONS, get_role_defaults


class TestDefaultPermissionMatrix:
    """Validate the completeness and correctness of DEFAULT_PERMISSIONS."""

    def test_all_roles_have_defaults(self) -> None:
        """Every UserRole must have an entry in DEFAULT_PERMISSIONS."""
        for role in UserRole:
            assert role in DEFAULT_PERMISSIONS, f"Missing defaults for role {role.value}"

    def test_all_resources_covered_per_role(self) -> None:
        """Every Resource must have a level for every role via ``get_role_defaults``.

        Role dicts in ``DEFAULT_PERMISSIONS`` may be partial (admin / super-admin
        only list resources on the admin UI matrix), so this guard goes through
        the public accessor which fills any gap with ``NONE``.
        """
        for role in UserRole:
            role_perms = get_role_defaults(role)
            for resource in Resource:
                assert resource in role_perms, f"Resource {resource.value} missing from {role.value} defaults"

    def test_admin_matrix_matches_ui(self) -> None:
        """Admin's WRITE resources match the admin permission UI matrix exactly.

        Logs (`AUDIT_LOG`, `ACCESS_LOGS`) are READ. ``DYNAMIC_CONFIGS`` is READ
        (write bundled under ``SYSTEM_DEFAULTS``). Deprecated ``STATUS_AUTOMATION_RULES``
        stays NONE. Every other resource not on the matrix is NONE.
        """
        write_resources = {
            Resource.VEHICLE_MANAGEMENT,
            Resource.DRIVERS,
            Resource.ORGANIZATIONS,
            Resource.CUSTOMERS,
            Resource.RESET_B2B_CLIENT_PASSWORDS,
            Resource.ADMINS,
            Resource.CREDIT_NOTES,
            Resource.ROUTE_PLANNING,
            Resource.RESET_ADMIN_PASSWORDS,
            Resource.HOLIDAYS,
            Resource.SUSPENSION_RULES,
            Resource.SYSTEM_DEFAULTS,
            Resource.SERVICE_TIERS,
            Resource.NOTIFICATIONS,
            Resource.QUICKBOOKS,
            Resource.DASHBOARD,
            Resource.BILLING,
            Resource.DOCUMENTS,
        }
        read_resources = {Resource.AUDIT_LOG, Resource.ACCESS_LOGS}
        config_read_only_resources = {Resource.DYNAMIC_CONFIGS}
        bundled_resources = {Resource.STATUS_AUTOMATION_RULES}

        admin_perms = get_role_defaults(UserRole.ADMIN)
        for resource in Resource:
            if resource in write_resources:
                assert admin_perms[resource] == PermissionLevel.WRITE, f"Admin should have WRITE on {resource.value}"
            elif resource in read_resources:
                assert admin_perms[resource] == PermissionLevel.READ, f"Admin should have READ on {resource.value}"
            elif resource in config_read_only_resources:
                assert admin_perms[resource] == PermissionLevel.READ, (
                    f"Admin should have READ on {resource.value}"
                )
            elif resource in bundled_resources:
                assert admin_perms[resource] == PermissionLevel.NONE, (
                    f"Admin should have NONE on bundled resource {resource.value}"
                )
            else:
                assert admin_perms[resource] == PermissionLevel.NONE, f"Admin should have NONE on {resource.value}"

    def test_super_admin_matrix_matches_ui_with_logs_writable(self) -> None:
        """Super-admin shares admin's matrix but gets WRITE on the log resources."""
        admin_perms = get_role_defaults(UserRole.ADMIN)
        super_admin_perms = get_role_defaults(UserRole.SUPER_ADMIN)
        for resource in Resource:
            if resource in (Resource.AUDIT_LOG, Resource.ACCESS_LOGS):
                assert super_admin_perms[resource] == PermissionLevel.WRITE, (
                    f"Super-admin should have WRITE on {resource.value}"
                )
            else:
                assert super_admin_perms[resource] == admin_perms[resource], (
                    f"Super-admin diverges from admin on {resource.value}"
                )

    def test_b2b_matrix_matches_ui(self) -> None:
        """CUSTOMER_B2B's permissions match the B2B portal navigation matrix exactly.

        Mirrors the frontend ``B2B_PORTAL_MODULES`` list — anything not on the
        matrix is NONE, except ``SYSTEM_DEFAULTS`` (org status automation API).
        """
        write_resources = {
            Resource.ORDERS,
            Resource.CARD_PAYMENT,
            Resource.REQUEST_CREDIT,
            Resource.CONTACTS,
            Resource.SYSTEM_DEFAULTS,
        }
        read_resources = {
            Resource.DASHBOARD,
            Resource.BILLING,
            Resource.NOTIFICATIONS,
            Resource.DOCUMENTS,
            Resource.ORG_PROFILE,
            Resource.AUDIT_LOG,
        }

        b2b_perms = get_role_defaults(UserRole.CUSTOMER_B2B)
        for resource in Resource:
            if resource in write_resources:
                assert b2b_perms[resource] == PermissionLevel.WRITE, (
                    f"CUSTOMER_B2B should have WRITE on {resource.value}"
                )
            elif resource in read_resources:
                assert b2b_perms[resource] == PermissionLevel.READ, (
                    f"CUSTOMER_B2B should have READ on {resource.value}"
                )
            else:
                assert b2b_perms[resource] == PermissionLevel.NONE, (
                    f"CUSTOMER_B2B should have NONE on {resource.value}"
                )

    def test_driver_has_minimal_access(self) -> None:
        """Driver should have READ on SHIPMENTS + SUPPORT_TICKETS, NONE on everything else."""
        driver_perms = get_role_defaults(UserRole.DRIVER)
        assert driver_perms[Resource.SHIPMENTS] == PermissionLevel.READ
        assert driver_perms[Resource.SUPPORT_TICKETS] == PermissionLevel.READ
        assert driver_perms[Resource.DASHBOARD] == PermissionLevel.NONE
        assert driver_perms[Resource.USERS] == PermissionLevel.NONE

    def test_get_role_defaults_unknown_role_returns_all_none(self) -> None:
        """Fallback for an unknown role should return NONE for all resources."""
        fallback = get_role_defaults(cast(UserRole, "NONEXISTENT_ROLE"))
        for resource in Resource:
            assert fallback[resource] == PermissionLevel.NONE

    def test_permission_level_ordering(self) -> None:
        """WRITE > READ > NONE for comparison logic."""
        assert PermissionLevel.WRITE > PermissionLevel.READ
        assert PermissionLevel.READ > PermissionLevel.NONE
        assert PermissionLevel.WRITE >= PermissionLevel.READ
