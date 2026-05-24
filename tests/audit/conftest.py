"""Fixtures specific to audit tests."""

from datetime import date, datetime
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.audit.models import AuditLog, AuditSavedView
from app.modules.user.models import User
from app.modules.organizations.models import Organization


@pytest_asyncio.fixture
async def admin_user(user_factory) -> User:
    """An active ADMIN user."""
    return await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)


@pytest_asyncio.fixture
async def admin_headers(admin_user: User) -> dict[str, str]:
    """Bearer headers for the admin user."""
    token, _ = create_access_token(
        user_id=admin_user.id,
        role=admin_user.role,
        client_type="ADMIN",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


@pytest_asyncio.fixture
async def super_admin_user(user_factory) -> User:
    """An active SUPER_ADMIN user."""
    return await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)


@pytest_asyncio.fixture
async def super_admin_headers(super_admin_user: User) -> dict[str, str]:
    """Bearer headers for the super admin user."""
    token, _ = create_access_token(
        user_id=super_admin_user.id,
        role=super_admin_user.role,
        client_type="ADMIN",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


@pytest_asyncio.fixture
async def b2b_user(user_factory, sample_org: Organization) -> User:
    """An active CUSTOMER_B2B user bound to sample_org."""
    return await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=sample_org.id,
    )


@pytest_asyncio.fixture
async def b2b_headers(b2b_user: User) -> dict[str, str]:
    """Bearer headers for B2B portal user."""
    token, _ = create_access_token(
        user_id=b2b_user.id,
        role=b2b_user.role,
        client_type="CUSTOMER_B2B",
        organization_id=b2b_user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2B",
    }


@pytest_asyncio.fixture
async def org_factory(db_session: AsyncSession):
    """Factory to create test Organization rows."""
    _counter = 0

    async def _create(**overrides) -> Organization:
        nonlocal _counter
        _counter += 1
        defaults = {
            "reference": f"AUDIT-ORG-{_counter:05d}",
            "trading_name": f"Audit Org {_counter}",
            "legal_entity_name": f"Audit Org {_counter} Ltd",
            "companies_house_number": f"CH{_counter:08d}",
            "vat_number": f"GB{_counter:09d}",
            "date_of_incorporation": date(2020, 1, 1),
            "industry": "OTHER",
            "company_size": "1-10 employees",
            "reg_address_line_1": "123 Business St",
            "reg_city": "London",
            "reg_postcode": "EC1A 1BB",
            "reg_country": "United Kingdom",
            "status": "ACTIVE",
        }
        defaults.update(overrides)
        org = Organization(**defaults)
        db_session.add(org)
        await db_session.flush()
        await db_session.refresh(org)
        return org

    return _create


@pytest_asyncio.fixture
async def sample_org(org_factory) -> Organization:
    """A single active organization for audit testing."""
    return await org_factory()


@pytest_asyncio.fixture
async def audit_log_factory(db_session: AsyncSession, admin_user: User):
    """Factory to create test AuditLog entries."""

    async def _create(organization_id: str, **overrides) -> AuditLog:
        defaults = {
            "user_id": admin_user.id,
            "user_role": "ADMIN",
            "action": "test.action",
            "category": "System",
            "event_type": "SYSTEM_CONFIG_CHANGED",
            "severity": "INFO",
            "entity_type": "test_entity",
            "entity_id": None,
            "organization_id": organization_id,
            "ip_address": "127.0.0.1",
            "user_agent": "Mozilla/5.0",
        }
        defaults.update(overrides)
        log = AuditLog(**defaults)
        db_session.add(log)
        await db_session.flush()
        await db_session.refresh(log)
        return log

    return _create


@pytest_asyncio.fixture
async def saved_view_factory(db_session: AsyncSession, admin_user: User):
    """Factory to create test AuditSavedView entries."""

    async def _create(**overrides) -> AuditSavedView:
        defaults = {
            "name": "My Saved View",
            "user_id": admin_user.id,
            "organization_id": None,
            "filters": {"category": ["System"]},
            "is_default": False,
        }
        defaults.update(overrides)
        view = AuditSavedView(**defaults)
        db_session.add(view)
        await db_session.flush()
        await db_session.refresh(view)
        return view

    return _create
