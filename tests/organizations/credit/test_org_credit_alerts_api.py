from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit_alerts.enums import (
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertSeverity,
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.org_credit_alerts.models import OrgCreditAlert
from app.modules.organizations.models import Organization
from app.modules.user.models import User


async def _create_credit_account(
    db_session,
    org_id: str,
    *,
    credit_limit: Decimal = Decimal("10000.00"),
    used: Decimal = Decimal("0"),
    status: OrgCreditAccountStatus = OrgCreditAccountStatus.ACTIVE,
) -> OrgCreditAccount:
    acct = OrgCreditAccount(
        organization_id=org_id,
        status=status,
        credit_limit=credit_limit,
        used_credit=used,
    )
    db_session.add(acct)
    await db_session.flush()
    return acct


@pytest.mark.asyncio
async def test_get_alerts_summary_returns_zero_counts_when_no_alerts(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/alerts/summary",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()["data"]
    assert payload["active_alerts_count"] == 0
    assert payload["unacknowledged_alerts_count"] == 0
    assert payload["last_alert_triggered_at"] is None


@pytest.mark.asyncio
async def test_get_alert_config_returns_defaults_when_none_customised(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/alerts/config",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    types_returned = {i["alert_type"] for i in items}
    assert types_returned == {t.value for t in CreditAlertType}

    warning_cfg = next(i for i in items if i["alert_type"] == CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING.value)
    critical_cfg = next(i for i in items if i["alert_type"] == CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL.value)
    assert warning_cfg["enabled"] is True
    assert warning_cfg["threshold_pct"] == "75"
    assert critical_cfg["threshold_pct"] == "90"
    assert warning_cfg["auto_acknowledge"] is True


@pytest.mark.asyncio
async def test_patch_alert_config_rejects_invalid_thresholds(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/alerts/config",
        headers=admin_headers,
        json={
            "items": [
                {
                    "alert_type": CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING.value,
                    "enabled": True,
                    "threshold_pct": "0",
                    "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR.value,
                    "delivery_channel": CreditAlertDeliveryChannel.BOTH.value,
                    "auto_acknowledge": True,
                },
            ],
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_alert_config_upserts_and_returns_items(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    body = {
        "items": [
            {
                "alert_type": CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING.value,
                "enabled": True,
                "threshold_pct": "80",
                "cooldown_period": CreditAlertCooldownPeriod.SEVEN_HOURS.value,
                "delivery_channel": CreditAlertDeliveryChannel.EMAIL_ONLY.value,
                "auto_acknowledge": False,
            },
            {
                "alert_type": CreditAlertType.REVIEW_OVERDUE.value,
                "enabled": False,
                "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR.value,
                "delivery_channel": CreditAlertDeliveryChannel.IN_APP_ONLY.value,
                "auto_acknowledge": False,
            },
        ],
    }
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/alerts/config",
        headers=admin_headers,
        json=body,
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 2
    util = next(i for i in items if i["alert_type"] == CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING.value)
    assert util["threshold_pct"] == "80.00"
    assert util["cooldown_period"] == CreditAlertCooldownPeriod.SEVEN_HOURS.value
    assert util["delivery_channel"] == CreditAlertDeliveryChannel.EMAIL_ONLY.value
    assert util["auto_acknowledge"] is False


@pytest.mark.asyncio
async def test_list_active_alerts_returns_active_rows(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org.id)
    db_session.add(
        OrgCreditAlert(
            organization_id=org.id,
            alert_type=CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.ACTIVE,
            title="Utilisation Warning",
            summary="Utilisation reached 80%.",
            context={"utilisation_percent": 80.0},
            triggered_at=datetime.now(UTC),
        ),
    )
    await db_session.flush()

    r = await client.get(
        f"/v1/organizations/{org.id}/credit/alerts/active",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) == 1
    assert rows[0]["status"] == CreditAlertStatus.ACTIVE.value
    assert rows[0]["severity"] == CreditAlertSeverity.WARNING.value


@pytest.mark.asyncio
async def test_acknowledge_alert_sets_status_and_user(
    client: AsyncClient,
    admin_headers: dict[str, str],
    admin_user: User,
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    alert = OrgCreditAlert(
        organization_id=org.id,
        alert_type=CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL,
        severity=CreditAlertSeverity.CRITICAL,
        status=CreditAlertStatus.ACTIVE,
        title="Over-Limit Breach",
        summary="Balance exceeded limit.",
        triggered_at=datetime.now(UTC),
    )
    db_session.add(alert)
    await db_session.flush()

    r = await client.post(
        f"/v1/organizations/{org.id}/credit/alerts/{alert.id}/acknowledge",
        headers=admin_headers,
        json={"resolution_notes": "Contacted client."},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == CreditAlertStatus.ACKNOWLEDGED.value
    assert data["acknowledged_by"]["id"] == admin_user.id
    assert data["resolution_notes"] == "Contacted client."


@pytest.mark.asyncio
async def test_snooze_alert_sets_until_in_future(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    alert = OrgCreditAlert(
        organization_id=org.id,
        alert_type=CreditAlertType.REVIEW_OVERDUE,
        severity=CreditAlertSeverity.CRITICAL,
        status=CreditAlertStatus.ACTIVE,
        title="Review Overdue",
        summary="Overdue by 2 days.",
        triggered_at=datetime.now(UTC),
    )
    db_session.add(alert)
    await db_session.flush()

    r = await client.post(
        f"/v1/organizations/{org.id}/credit/alerts/{alert.id}/snooze",
        headers=admin_headers,
        json={"duration": "FOUR_HOURS"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == CreditAlertStatus.SNOOZED.value
    snoozed_until = datetime.fromisoformat(data["snoozed_until"])
    assert snoozed_until > datetime.now(UTC)


@pytest.mark.asyncio
async def test_get_alert_detail_returns_404_for_wrong_org(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org_a: Organization = await org_factory()
    org_b: Organization = await org_factory()
    alert = OrgCreditAlert(
        organization_id=org_a.id,
        alert_type=CreditAlertType.ACCOUNT_ON_HOLD,
        severity=CreditAlertSeverity.CRITICAL,
        status=CreditAlertStatus.ACTIVE,
        title="Account On Hold",
        summary="ON_HOLD.",
        triggered_at=datetime.now(UTC),
    )
    db_session.add(alert)
    await db_session.flush()

    r = await client.get(
        f"/v1/organizations/{org_b.id}/credit/alerts/{alert.id}",
        headers=admin_headers,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_alerts_history_filters_by_status(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    now = datetime.now(UTC)
    db_session.add_all([
        OrgCreditAlert(
            organization_id=org.id,
            alert_type=CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING,
            severity=CreditAlertSeverity.WARNING,
            status=CreditAlertStatus.ACKNOWLEDGED,
            title="Utilisation Warning",
            summary="70%",
            triggered_at=now - timedelta(hours=2),
            acknowledged_at=now - timedelta(hours=1),
        ),
        OrgCreditAlert(
            organization_id=org.id,
            alert_type=CreditAlertType.REVIEW_OVERDUE,
            severity=CreditAlertSeverity.CRITICAL,
            status=CreditAlertStatus.AUTO_ACKNOWLEDGED,
            title="Review Overdue",
            summary="1 day",
            triggered_at=now - timedelta(hours=3),
            acknowledged_at=now - timedelta(hours=3),
        ),
    ])
    await db_session.flush()

    r = await client.get(
        f"/v1/organizations/{org.id}/credit/alerts/history",
        headers=admin_headers,
        params={"statuses": [CreditAlertStatus.ACKNOWLEDGED.value]},
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["status"] == CreditAlertStatus.ACKNOWLEDGED.value
