"""Fixtures specific to payment method tests."""

from datetime import date
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import Organization
from app.modules.user.models import User


# ── Auth helpers ──────────────────────────────────────────────


def b2b_headers(user: User) -> dict[str, str]:
    """Bearer + client type headers for a B2B customer user."""
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="CUSTOMER_B2B",
        region_id=None,
        organization_id=user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2B",
    }


def b2c_headers(user: User) -> dict[str, str]:
    """Bearer + client type headers for a B2C customer user."""
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="CUSTOMER_B2C",
        region_id=None,
        organization_id=None,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2C",
    }


def admin_headers(user: User) -> dict[str, str]:
    """Bearer + client type headers for an admin user (dev endpoints)."""
    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="ADMIN",
        region_id=None,
        organization_id=None,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


# ── Factories ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def org_factory(db_session: AsyncSession):
    _counter = 0

    async def _create(**overrides) -> Organization:
        nonlocal _counter
        _counter += 1
        defaults = {
            "reference": f"SWC-PAY-{_counter:05d}",
            "trading_name": f"Pay Test Org {_counter}",
            "legal_entity_name": f"Pay Test Org {_counter} Ltd",
            "companies_house_number": f"PY{_counter:06d}",
            "vat_number": f"GB{_counter:09d}",
            "date_of_incorporation": date(2020, 1, 1),
            "industry": "OTHER",
            "company_size": "1-10 employees",
            "reg_address_line_1": "1 Payment Street",
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
    return await org_factory()


@pytest_asyncio.fixture
async def b2b_user(user_factory, sample_org: Organization) -> User:
    """Active B2B customer linked to sample_org."""
    return await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=sample_org.id,
    )


@pytest_asyncio.fixture
async def b2c_user(user_factory) -> User:
    """Active B2C customer."""
    return await user_factory(
        role="CUSTOMER_B2C",
        status="ACTIVE",
        email_verified=True,
    )


@pytest_asyncio.fixture
async def admin_user(user_factory) -> User:
    """Active admin user for dev endpoints."""
    return await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)


# ── Braintree mock helpers ─────────────────────────────────────


def make_braintree_customer_result(customer_id: str = "bt-cust-001") -> MagicMock:
    result = MagicMock()
    result.is_success = True
    result.customer = MagicMock()
    result.customer.id = customer_id
    return result


def make_braintree_pm_result(
    token: str = "bt-token-001",
    card_type: str = "Visa",
    last_4: str = "4242",
    exp_month: str = "12",
    exp_year: str = "2029",
) -> MagicMock:
    result = MagicMock()
    result.is_success = True
    result.payment_method = MagicMock()
    result.payment_method.token = token
    result.payment_method.card_type = card_type
    result.payment_method.last_4 = last_4
    result.payment_method.expiration_month = exp_month
    result.payment_method.expiration_year = exp_year
    return result


def make_braintree_failed_result(message: str = "Card declined") -> MagicMock:
    result = MagicMock()
    result.is_success = False
    result.message = message
    return result


def make_braintree_duplicate_card_result(code: str = "81763") -> MagicMock:
    err = MagicMock()
    err.code = code
    result = MagicMock()
    result.is_success = False
    result.message = "Duplicate card"
    result.errors = MagicMock()
    result.errors.deep_errors = [err]
    return result


def make_braintree_nonce_find_three_ds_ok() -> MagicMock:
    pmn = MagicMock()
    tds = MagicMock()
    tds.liability_shifted = True
    tds.liability_shift_possible = True
    pmn.three_d_secure_info = tds
    return pmn


def make_braintree_nonce_find_three_ds_fail(
    *,
    missing_info: bool = False,
    shifted: bool = False,
    possible: bool = True,
    status: str | None = None,
) -> MagicMock:
    pmn = MagicMock()
    if missing_info:
        pmn.three_d_secure_info = None
    else:
        tds = MagicMock()
        tds.liability_shifted = shifted
        tds.liability_shift_possible = possible
        if status is not None:
            tds.status = status
        pmn.three_d_secure_info = tds
    return pmn


def make_braintree_credit_card_result(
    token: str = "bt-cc-token-001",
    card_type: str = "Visa",
    last_4: str = "1111",
    exp_month: str = "12",
    exp_year: str = "2029",
) -> MagicMock:
    result = MagicMock()
    result.is_success = True
    result.credit_card = MagicMock()
    result.credit_card.token = token
    result.credit_card.card_type = card_type
    result.credit_card.last_4 = last_4
    result.credit_card.expiration_month = exp_month
    result.credit_card.expiration_year = exp_year
    return result


def make_braintree_tx_result(tx_id: str = "bt-tx-001", success: bool = True) -> MagicMock:
    result = MagicMock()
    result.is_success = success
    result.transaction = MagicMock()
    result.transaction.id = tx_id
    result.transaction.amount = "15.50"
    return result


def make_braintree_vault_nonce_create_result(
    *,
    nonce: str = "vault-forwarded-nonce-abc",
    bin_value: str | None = "411111",
) -> MagicMock:
    result = MagicMock()
    result.is_success = True
    pmn = MagicMock()
    pmn.nonce = nonce
    pmn.bin_data = None
    if bin_value is not None:
        pmn.details = {"bin": bin_value}
    else:
        pmn.details = None
    result.payment_method_nonce = pmn
    return result


# ── Payload helpers ────────────────────────────────────────────


def make_create_card_payload(**overrides) -> dict:
    defaults = {
        "nonce": "fake-nonce-from-hosted-fields",
        "cardholder_name": "Test User",
        "set_as_default": True,
    }
    defaults.update(overrides)
    return defaults


def make_dev_raw_card_payload(**overrides) -> dict:
    defaults = {
        "card_number": "4111111111111111",
        "expiry_month": 12,
        "expiry_year": 2029,
        "cvv": "123",
        "cardholder_name": "Dev Test User",
        "set_as_default": True,
    }
    defaults.update(overrides)
    return defaults
