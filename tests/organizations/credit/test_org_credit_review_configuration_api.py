from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.organizations.models import Organization
from app.modules.user.models import User


@pytest.mark.asyncio
async def test_patch_review_configuration_returns_422_when_body_incomplete(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/reviews/configuration",
        headers=admin_headers,
        json={"review_frequency": "QUARTERLY"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_review_configuration_updates_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    admin_user: User,
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    db_session.add(
        OrgCreditAccount(
            organization_id=org.id,
            status=OrgCreditAccountStatus.ACTIVE,
            credit_limit=Decimal("10000.00"),
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()
    eff = date.today()
    body = {
        "review_frequency": "QUARTERLY",
        "next_review_date": eff.isoformat(),
        "reminder_period": "SEVEN_DAYS",
        "reviewer_user_id": admin_user.id,
    }

    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/reviews/configuration",
        headers=admin_headers,
        json=body,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["message"] == "Review configuration updated."
    assert payload.get("data") is None
