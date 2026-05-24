"""Fixtures specific to organization tests."""

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_token
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.models import DocAccessToken, Organization
from app.modules.user.models import User


@pytest_asyncio.fixture
async def admin_user(user_factory) -> User:
    """An active ADMIN user."""
    return await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)


@pytest_asyncio.fixture
async def doc_access_token(admin_user: User, db_session: AsyncSession) -> str:
    """A valid 1-hour DocAccessToken for admin_user, inserted directly in the DB.

    Included in admin_headers so all document endpoint tests pass without needing
    to complete the OTP flow each time.
    """
    raw = secrets.token_hex(32)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    row = DocAccessToken(
        user_id=admin_user.id,
        token_hash=hash_token(raw),
        expires_at=expires_at,
        access_scope=DocAccessScope.ORG_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return raw


@pytest_asyncio.fixture
async def admin_headers(admin_user: User, doc_access_token: str) -> dict[str, str]:
    """Bearer + doc-access headers for the admin user.

    Includes X-Doc-Access-Token so document endpoints (which now require
    step-up auth) work without running the full OTP flow in every test.
    """
    token, _ = create_access_token(
        user_id=admin_user.id,
        role=admin_user.role,
        client_type="ADMIN",
        region_id=None,
        organization_id=None,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
        "X-Doc-Access-Token": doc_access_token,
    }


@pytest_asyncio.fixture
async def org_factory(db_session: AsyncSession):
    """Factory to create test Organization rows directly in the DB."""
    _counter = 0

    async def _create(**overrides) -> Organization:
        nonlocal _counter
        _counter += 1
        # Unique reference per row: tests use a shared DB with real commits, so a per-fixture
        # counter alone always restarts at SWC-ORG-00001 and collides across tests.
        ref_suffix = uuid.uuid4().hex[:16]
        defaults = {
            "reference": f"T{ref_suffix}"[:20],
            "trading_name": f"Test Org {_counter}",
            "legal_entity_name": f"Test Org {_counter} Limited",
            "companies_house_number": f"CH{ref_suffix[:8]}",
            "vat_number": f"GB{ref_suffix[:9]}",
            "date_of_incorporation": date(2020, 1, 1),
            "industry": "OTHER",
            "company_size": "1-10 employees",
            "reg_address_line_1": "1 Test Street",
            "reg_city": "London",
            "reg_postcode": "EC1A 1BB",
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
    """A single active organization for use in tests."""
    return await org_factory()


@pytest_asyncio.fixture
async def pricing_tier_ids(db_session: AsyncSession) -> list[str]:
    """Return the IDs of all service tiers seeded in the DB."""
    result = await db_session.execute(text("SELECT id::text FROM service_tier ORDER BY price_per_package ASC"))
    return [row[0] for row in result.fetchall()]


@pytest_asyncio.fixture
async def account_manager_users(user_factory) -> list[User]:
    """Three pre-seeded admin/super-admin users for account manager tests.

    Creates:
      - Alice Admin   (ADMIN)
      - Bob Super     (SUPER_ADMIN)
      - Carol Admin   (ADMIN)

    All are active and email-verified so they appear in the
    GET /v1/organizations/account-managers list endpoint.
    """
    alice = await user_factory(
        role="ADMIN",
        status="ACTIVE",
        email_verified=True,
        first_name="Alice",
        last_name="Admin",
        email="alice.admin.test@swcouriers.test",
    )
    bob = await user_factory(
        role="SUPER_ADMIN",
        status="ACTIVE",
        email_verified=True,
        first_name="Bob",
        last_name="Super",
        email="bob.super.test@swcouriers.test",
    )
    carol = await user_factory(
        role="ADMIN",
        status="ACTIVE",
        email_verified=True,
        first_name="Carol",
        last_name="Admin",
        email="carol.admin.test@swcouriers.test",
    )
    return [alice, bob, carol]
