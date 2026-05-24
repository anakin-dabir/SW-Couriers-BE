"""B2B org-scoped payment card routes must match JWT organization_id."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.enums import ContactRole, ContactStatus
from app.modules.organizations.models import OrgContact, Organization
from app.modules.user.models import User
from tests.payments.conftest import make_braintree_customer_result
from tests.payments.test_payment_methods_api import _mock_gateway
from tests.organizations.test_org_profile_acl import _b2b_headers, _make_contact

ORGS = "/v1/organizations"


@pytest.mark.asyncio
async def test_b2b_cannot_access_other_org_braintree_token(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    org_a = await org_factory()
    org_b = await org_factory()
    owner, _ = await _make_contact(
        db_session,
        user_factory,
        org_a,
        contact_role=ContactRole.ACCOUNT_OWNER.value,
        is_primary=True,
    )

    resp = await client.get(
        f"{ORGS}/{org_b.id}/payment-methods/cards/braintree-client-token",
        headers=_b2b_headers(owner),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_b2b_can_access_own_org_braintree_token(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
    org_factory,
) -> None:
    org_a = await org_factory()
    owner, _ = await _make_contact(
        db_session,
        user_factory,
        org_a,
        contact_role=ContactRole.ACCOUNT_OWNER.value,
        is_primary=True,
    )

    mock_patch, gw = _mock_gateway()
    gw.client_token.generate.return_value = "sandbox_org_scoped_token"

    with mock_patch:
        resp = await client.get(
            f"{ORGS}/{org_a.id}/payment-methods/cards/braintree-client-token",
            headers=_b2b_headers(owner),
        )

    assert resp.status_code == 200
    assert resp.json()["data"]["client_token"] == "sandbox_org_scoped_token"
