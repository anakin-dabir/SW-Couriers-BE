"""Unit tests for dashboard org scope resolution."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.common.deps import AuthUser
from app.common.enums import ClientType, UserRole
from app.modules.dashboard.access import resolve_dashboard_organization_id


def _user(*, role: UserRole, organization_id: str | None = None) -> AuthUser:
    return AuthUser(
        id="user-1",
        role=str(role),
        client_type=ClientType.ADMIN if role in (UserRole.ADMIN, UserRole.SUPER_ADMIN) else ClientType.CUSTOMER_B2B,
        jti="test-jti-dashboard-access",
        organization_id=organization_id,
    )


def test_admin_may_request_global_or_specific_org() -> None:
    admin = _user(role=UserRole.ADMIN)
    assert resolve_dashboard_organization_id(admin, None) is None
    assert resolve_dashboard_organization_id(admin, "org-abc") == "org-abc"


def test_b2b_is_forced_to_own_org() -> None:
    b2b = _user(role=UserRole.CUSTOMER_B2B, organization_id="org-mine")
    assert resolve_dashboard_organization_id(b2b, None) == "org-mine"
    assert resolve_dashboard_organization_id(b2b, "org-mine") == "org-mine"


def test_b2b_cannot_request_other_org() -> None:
    b2b = _user(role=UserRole.CUSTOMER_B2B, organization_id="org-mine")
    with pytest.raises(HTTPException) as exc:
        resolve_dashboard_organization_id(b2b, "org-other")
    assert exc.value.status_code == 403


def test_b2b_without_org_context_is_forbidden() -> None:
    b2b = _user(role=UserRole.CUSTOMER_B2B, organization_id=None)
    with pytest.raises(HTTPException) as exc:
        resolve_dashboard_organization_id(b2b, None)
    assert exc.value.status_code == 403
