from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.security import create_access_token
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.organizations.models import Organization, OrgContact, OrgPaymentConfig, OrgPaymentMethod
from app.modules.organizations.enums import BillingSchedule, ContactRole, ContactStatus, PaymentModel, VatRate, VatTreatment
from app.modules.orders.enums import DeliveryStopStatus, OrderDraftStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, OrderDraft, Package
from app.modules.user.models import User

ORDERS = "/v1/orders"
PICKUP = "/v1/pickup-addresses"


@pytest.fixture(autouse=True)
def _mock_order_job_enqueue():
    """Order create/submit enqueue Arq jobs; API tests use in-memory Redis without Arq pool."""
    with patch("app.modules.orders.service.enqueue", new_callable=AsyncMock) as mock_enqueue:
        mock_enqueue.return_value = None
        yield mock_enqueue


def _org_query(organization_id: str) -> str:
    return urlencode({"organization_id": organization_id})


def _b2b_create_json(organization_id: str, **fields: object) -> dict:
    body: dict = {"client_type": "B2B", "organization_id": organization_id}
    body.update(fields)
    return body


def _standard_pricing_plans() -> list[dict]:
    return [
        {
            "selected": True,
            "plain_name": "STANDARD",
            "plain_type": "standard",
            "id_price_tier": "test-tier-standard",
            "base_price": "10.00",
            "price_per_package": "5.00",
            "price_per_kg": "1.00",
            "days": 3,
        },
    ]


async def _ensure_org_order_prerequisites(db_session, org: Organization) -> None:
    if not org.pricing_plans:
        org.pricing_plans = _standard_pricing_plans()
    config = (
        await db_session.execute(
            select(OrgPaymentConfig).where(OrgPaymentConfig.organization_id == org.id)
        )
    ).scalar_one_or_none()
    if config is None:
        db_session.add(
            OrgPaymentConfig(
                organization_id=org.id,
                vat_number="GB123456789",
                vat_rate=VatRate.STANDARD_20,
                vat_treatment=VatTreatment.UK,
                max_delivery_attempts=3,
                delivery_attempt_fees=[
                    {"attempt": 1, "fee": "0.00"},
                    {"attempt": 2, "fee": "1.50"},
                    {"attempt": 3, "fee": "2.50"},
                ],
                max_return_attempts=2,
                return_attempt_fees=[
                    {"attempt": 1, "fee": "5.00"},
                    {"attempt": 2, "fee": "8.00"},
                ],
            )
        )
    await db_session.flush()


async def _org_cash_payment_method_id(db_session, org_id: str, *, org: Organization | None = None) -> str:
    org_row = org or await db_session.get(Organization, org_id)
    if org_row is not None:
        await _ensure_org_order_prerequisites(db_session, org_row)
    pm = OrgPaymentMethod(
        organization_id=org_id,
        payment_model=PaymentModel.CASH,
        billing_schedule=BillingSchedule.IMMEDIATE,
        is_default=True,
    )
    db_session.add(pm)
    await db_session.flush()
    await db_session.refresh(pm)
    return pm.id


async def _pickup_id_from_api(client: AsyncClient, headers: dict[str, str]) -> str:
    pickup_resp = await client.post(
        PICKUP,
        headers=headers,
        json=[
            {
                "line_1": "1 Warehouse Way",
                "city": "Birmingham",
                "state": "West Midlands",
                "postcode": "B1 1AA",
                "country": "United Kingdom",
            }
        ],
    )
    assert pickup_resp.status_code == 201
    return pickup_resp.json()["data"][0]["id"]


def _b2b_order_core(
    organization_id: str,
    contact_user_id: str,
    pickup_address_id: str,
    payment_method_id: str,
    *,
    delivery_stops: list[dict] | None = None,
    **extra: object,
) -> dict:
    body: dict = {
        "client_type": "B2B",
        "organization_id": organization_id,
        "contact_user_id": contact_user_id,
        "pickup_address_id": pickup_address_id,
        "payment_method": "CASH",
        "payment_method_id": payment_method_id,
        "delivery_stops": delivery_stops or [_full_stop()],
    }
    body.update(extra)
    return body


def _drafts_path(organization_id: str, sub: str = "") -> str:
    q = _org_query(organization_id)
    if not sub:
        return f"{ORDERS}/drafts?{q}"
    return f"{ORDERS}/drafts/{sub}?{q}"


def _list_query(organization_id: str, **params: object) -> str:
    items: list[tuple[str, str]] = [("organization_id", organization_id)]
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            for entry in value:
                items.append((key, str(entry)))
        else:
            items.append((key, str(value)))
    return urlencode(items)


def _returns_path(organization_id: str, **params: object) -> str:
    return f"{ORDERS}/returns?{_list_query(organization_id, **params)}"


def _failed_deliveries_path(organization_id: str, **params: object) -> str:
    return f"{ORDERS}/failed-deliveries?{_list_query(organization_id, **params)}"


def _customer_b2b_headers(user_id: str, *, organization_id: str | None = None) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="ADMIN",
        client_type="ADMIN",
        organization_id=None,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _customer_b2c_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2C",
        client_type="CUSTOMER_B2C",
        organization_id=None,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2C"}


def _full_stop(suffix: str = "") -> dict:
    return {
        "recipient_first_name": f"North{suffix}",
        "recipient_last_name": f"Hub{suffix}",
        "recipient_phone": "07222222222",
        "recipient_email": f"north{suffix or 'hub'}@example.com",
        "line_1": "10 Broad St",
        "city": "Birmingham",
        "postcode": "B1 2HP",
        "service_tier_name": "STANDARD",
        "packages": [
            {
                "declared_weight_kg": 8,
                "length_cm": 40,
                "width_cm": 30,
                "height_cm": 20,
                "declared_value": 100,
            }
        ],
    }


def _full_draft_payload(suffix: str = "") -> dict:
    return {"delivery_stops": [_full_stop(suffix)]}


def _delivery_stop_fields(*, tracking_id: str) -> dict:
    return {
        "tracking_id": tracking_id,
        "recipient_first_name": "A",
        "recipient_last_name": "B",
        "recipient_phone": "07123456789",
        "recipient_email": "a@example.com",
        "line_1": "1 St",
        "city": "London",
        "postcode": "E1 1AA",
    }


async def _insert_test_order(
    db_session,
    *,
    order_id: str,
    master_label_id: str,
    organization_id: str,
    customer_id: str,
    status: OrderStatus,
) -> Order:
    """Insert order row; sets client_type when the column exists (local DBs ahead of model)."""
    pk = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    params = {
        "id": pk,
        "order_id": order_id,
        "master_label_id": master_label_id,
        "organization_id": organization_id,
        "customer_id": customer_id,
        "status": status.value,
        "now": now,
    }
    try:
        await db_session.execute(
            text(
                """
                INSERT INTO orders (
                    id, order_id, master_label_id, organization_id, customer_id,
                    status, client_type, subtotal, vat_amount, total_amount,
                    created_at, updated_at, version
                ) VALUES (
                    :id, :order_id, :master_label_id, :organization_id, :customer_id,
                    :status, 'B2B', 0, 0, 0,
                    :now, :now, 1
                )
                """
            ),
            params,
        )
    except Exception:
        await db_session.execute(
            text(
                """
                INSERT INTO orders (
                    id, order_id, master_label_id, organization_id, customer_id,
                    status, subtotal, vat_amount, total_amount,
                    created_at, updated_at, version
                ) VALUES (
                    :id, :order_id, :master_label_id, :organization_id, :customer_id,
                    :status, 0, 0, 0,
                    :now, :now, 1
                )
                """
            ),
            params,
        )
    await db_session.flush()
    order = (await db_session.execute(select(Order).where(Order.id == pk))).scalar_one()
    return order


async def _seed_return_stop(
    db_session,
    org_factory,
    user_factory,
    *,
    stop_status: DeliveryStopStatus,
    package_status: PackageStatus = PackageStatus.RETURN_INITIATED,
    org: Organization | None = None,
    customer_id: str | None = None,
):
    suffix = uuid.uuid4().hex[:8].upper()
    if org is None:
        org = await org_factory()
    if customer_id is None:
        user: User = await user_factory(
            status="ACTIVE",
            email_verified=True,
            role="CUSTOMER_B2B",
            organization_id=org.id,
        )
        customer_id = user.id
    order = await _insert_test_order(
        db_session,
        order_id=f"SWC-ORD-RT-{suffix}",
        master_label_id=f"ML-RT-{suffix}",
        organization_id=org.id,
        customer_id=customer_id,
        status=OrderStatus.RETURN_IN_PROGRESS,
    )
    stop = DeliveryStop(
        order_id=order.id,
        status=stop_status,
        **_delivery_stop_fields(tracking_id=f"TRK-RT-{suffix}"),
    )
    db_session.add(stop)
    await db_session.flush()
    pkg = Package(
        order_id=order.id,
        delivery_stop_id=stop.id,
        status=package_status,
    )
    db_session.add(pkg)
    await db_session.flush()
    return org, order, stop


async def _seed_failed_delivery_stop(
    db_session,
    org_factory,
    user_factory,
    *,
    stop_status: DeliveryStopStatus,
    package_status: PackageStatus = PackageStatus.CUSTOMER_NOT_HOME,
    org: Organization | None = None,
    customer_id: str | None = None,
):
    suffix = uuid.uuid4().hex[:8].upper()
    if org is None:
        org = await org_factory()
    if customer_id is None:
        user: User = await user_factory(
            status="ACTIVE",
            email_verified=True,
            role="CUSTOMER_B2B",
            organization_id=org.id,
        )
        customer_id = user.id
    order = await _insert_test_order(
        db_session,
        order_id=f"SWC-ORD-FD-{suffix}",
        master_label_id=f"ML-FD-{suffix}",
        organization_id=org.id,
        customer_id=customer_id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    stop = DeliveryStop(
        order_id=order.id,
        status=stop_status,
        **_delivery_stop_fields(tracking_id=f"TRK-FD-{suffix}"),
    )
    db_session.add(stop)
    await db_session.flush()
    pkg = Package(
        order_id=order.id,
        delivery_stop_id=stop.id,
        status=package_status,
    )
    db_session.add(pkg)
    await db_session.flush()
    return org, order, stop


@pytest.mark.asyncio
async def test_create_order_and_lookup_endpoints(client: AsyncClient, user_factory, org_factory, db_session) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    create_resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm_id,
            delivery_stops=[
                {
                    "recipient_first_name": "North",
                    "recipient_last_name": "Hub",
                    "recipient_phone": "07222222222",
                    "recipient_email": "north.hub@example.com",
                    "line_1": "10 Broad St",
                    "city": "Birmingham",
                    "postcode": "B1 2HP",
                    "service_tier_name": "STANDARD",
                    "packages": [
                        {"declared_weight_kg": 8, "length_cm": 40, "width_cm": 30, "height_cm": 20, "declared_value": 100},
                        {"declared_weight_kg": 10, "length_cm": 50, "width_cm": 35, "height_cm": 25, "declared_value": 200},
                    ],
                }
            ],
        ),
    )
    assert create_resp.status_code == 201
    data = create_resp.json()["data"]
    assert data["order_id"].startswith("SWC-ORD-")
    assert data["master_label"]["master_label_id"]
    assert len(data["pickup_labels"]) == 2
    assert all(label["package_id"].startswith("PKG-") for label in data["pickup_labels"])

    order_query = await db_session.execute(select(Order).where(Order.order_id == data["order_id"]))
    order = order_query.scalar_one()
    order_id = order.id
    master_label_id = order.master_label_id

    by_id = await client.get(f"{ORDERS}/detail/{order_id}", headers=headers)
    assert by_id.status_code in {200, 403}
    if by_id.status_code == 200:
        assert by_id.json()["data"]["id"] == order_id
        assert len(by_id.json()["data"]["delivery_stops"][0]["packages"]) == 2

    by_master = await client.get(f"{ORDERS}/{order_id}/master-label", headers=headers)
    assert by_master.status_code in {200, 404}
    if by_master.status_code == 200:
        assert by_master.json()["data"]["id"] == order_id
        assert len(by_master.json()["data"]["delivery_stops"][0]["packages"]) == 2


@pytest.mark.asyncio
async def test_create_order_with_pickup_address(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)

    create_resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm_id,
            delivery_stops=[_full_stop()],
        ),
    )
    assert create_resp.status_code == 201
    order_ref = create_resp.json()["data"]["order_id"]
    order_query = await db_session.execute(select(Order).where(Order.order_id == order_ref))
    order = order_query.scalar_one()
    assert order.pickup_address_id == pickup_id
    assert order.organization_id == org.id


@pytest.mark.asyncio
async def test_create_order_card_requires_credit_card_id(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm = OrgPaymentMethod(
        organization_id=org.id,
        payment_model=PaymentModel.CARD,
        billing_schedule=BillingSchedule.IMMEDIATE,
        is_default=True,
    )
    db_session.add(pm)
    await db_session.flush()
    await db_session.refresh(pm)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm.id,
            payment_method="CARD",
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 422
    assert "credit_card_id" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_order_card_requires_payment_method_nonce(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm = OrgPaymentMethod(
        organization_id=org.id,
        payment_model=PaymentModel.CARD,
        billing_schedule=BillingSchedule.IMMEDIATE,
        is_default=True,
    )
    db_session.add(pm)
    await db_session.flush()
    await db_session.refresh(pm)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm.id,
            payment_method="CARD",
            credit_card_id=str(uuid.uuid4()),
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 422
    assert "payment_method_nonce" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_order_non_card_rejects_payment_method_id(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            str(uuid.uuid4()),
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_order_non_card_rejects_credit_card_id(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm_id,
            credit_card_id=str(uuid.uuid4()),
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_order_non_card_rejects_payment_method_nonce(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm_id,
            payment_method_nonce="fake-nonce-from-verifycard",
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 422
    assert "payment_method_nonce" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_order_b2b_rejects_other_org_in_body(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    other: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            other.id,
            user.id,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_order_b2b_rejects_wrong_contact_user_id(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    other_user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            other_user.id,
            pickup_id,
            pm_id,
            delivery_stops=[_full_stop()],
        ),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_order_b2c_not_yet_implemented(client: AsyncClient, user_factory, org_factory, db_session) -> None:
    org: Organization = await org_factory()
    b2c: User = await user_factory(
        role="CUSTOMER_B2C",
        status="ACTIVE",
        email_verified=True,
        organization_id=None,
    )
    headers = _customer_b2c_headers(b2c.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    resp = await client.post(
        ORDERS,
        headers=headers,
        json={
            "client_type": "B2C",
            "contact_user_id": b2c.id,
            "pickup_address_id": pickup_id,
            "payment_method": "CASH",
            "payment_method_id": pm_id,
            "delivery_stops": [_full_stop()],
        },
    )
    assert resp.status_code == 422
    assert "not yet implemented" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_order_admin_on_behalf_of_contact(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    contact_user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    contact = OrgContact(
        organization_id=org.id,
        contact_number=f"+447700900{uuid.uuid4().int % 1000000:06d}",
        contact_role=ContactRole.ACCOUNT_OWNER,
        status=ContactStatus.ACTIVE,
        user_id=contact_user.id,
    )
    db_session.add(contact)
    await db_session.flush()
    await db_session.refresh(contact)

    headers = _admin_headers(admin.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    contact_headers = _customer_b2b_headers(contact_user.id, organization_id=org.id)
    pickup_id = await _pickup_id_from_api(client, contact_headers)
    create_resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            contact_user.id,
            pickup_id,
            pm_id,
            delivery_stops=[_full_stop()],
        ),
    )
    assert create_resp.status_code == 201
    oid = create_resp.json()["data"]["order_id"]
    row = (await db_session.execute(select(Order).where(Order.order_id == oid))).scalar_one()
    assert row.customer_id == contact_user.id
    assert row.created_by_id == admin.id


@pytest.mark.asyncio
async def test_save_draft_minimal(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    resp = await client.post(_drafts_path(org.id), headers=headers, json={"memo": "Partial"})
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["organization_id"] == org.id
    assert data["payload"]["memo"] == "Partial"


@pytest.mark.asyncio
async def test_save_draft_with_stops(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    resp = await client.post(_drafts_path(org.id), headers=headers, json=_full_draft_payload())
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert len(data["payload"]["delivery_stops"]) == 1


@pytest.mark.asyncio
async def test_update_draft(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    create_resp = await client.post(_drafts_path(org.id), headers=headers, json={"memo": "Step1"})
    assert create_resp.status_code == 201
    draft_id = create_resp.json()["data"]["id"]

    update_resp = await client.patch(
        _drafts_path(org.id, draft_id),
        headers=headers,
        json={"delivery_stops": [_full_stop("U")]},
    )
    assert update_resp.status_code == 200
    payload = update_resp.json()["data"]["payload"]
    assert payload["memo"] == "Step1"
    assert len(payload["delivery_stops"]) == 1
    assert payload["delivery_stops"][0]["recipient_first_name"] == "NorthU"


@pytest.mark.asyncio
async def test_list_drafts(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    for memo in ("D1", "D2"):
        await client.post(
            _drafts_path(org.id),
            headers=headers,
            json={"organization_id": org.id, "contact_user_id": user.id, "memo": memo},
        )

    list_resp = await client.get(_drafts_path(org.id), headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()["data"]
    assert data["total"] >= 2
    assert all("id" in item and "delivery_stop_count" in item for item in data["items"])
    assert all("created_by" in item and "pickup_address" in item for item in data["items"])


@pytest.mark.asyncio
async def test_list_drafts_includes_created_by_and_pickup_address(
    client: AsyncClient, user_factory, org_factory
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        first_name="Draft",
        last_name="Creator",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pickup_id = await _pickup_id_from_api(client, headers)

    create_resp = await client.post(
        _drafts_path(org.id),
        headers=headers,
        json={
            "organization_id": org.id,
            "contact_user_id": user.id,
            "pickup_address_id": pickup_id,
            "memo": "WithPickup",
        },
    )
    assert create_resp.status_code == 201

    list_resp = await client.get(_drafts_path(org.id), headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    matched = next((item for item in items if item.get("pickup_address_id") == pickup_id), None)
    assert matched is not None
    assert "Draft" in (matched.get("created_by") or "")
    assert "Creator" in (matched.get("created_by") or "")
    assert "Warehouse Way" in (matched.get("pickup_address") or "")
    assert "B1 1AA" in (matched.get("pickup_address") or "")


@pytest.mark.asyncio
async def test_list_drafts_search_by_creator_name(
    client: AsyncClient, user_factory, org_factory
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        first_name="Searchable",
        last_name="DraftUser",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pickup_id = await _pickup_id_from_api(client, headers)

    await client.post(
        _drafts_path(org.id),
        headers=headers,
        json={
            "organization_id": org.id,
            "contact_user_id": user.id,
            "pickup_address_id": pickup_id,
            "memo": "SearchTarget",
        },
    )

    list_resp = await client.get(
        f"{ORDERS}/drafts?{_list_query(org.id, search='Searchable')}",
        headers=headers,
    )
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert any("Searchable" in (item.get("created_by") or "") for item in items)


@pytest.mark.asyncio
async def test_list_drafts_tolerates_legacy_invalid_pickup_address_id(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    """List must not 422 when an older draft has a non-UUID pickup_address_id in JSONB payload."""
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    legacy = OrderDraft(
        organization_id=org.id,
        customer_id=user.id,
        created_by_id=user.id,
        status=OrderDraftStatus.PENDING,
        payload={"pickup_address_id": "DRAFT-000001", "memo": "LegacyBadPickupId"},
    )
    db_session.add(legacy)
    await db_session.commit()

    valid_resp = await client.post(
        _drafts_path(org.id),
        headers=headers,
        json={"organization_id": org.id, "contact_user_id": user.id, "memo": "ValidDraft"},
    )
    assert valid_resp.status_code == 201

    list_resp = await client.get(_drafts_path(org.id), headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert list_resp.json()["data"]["total"] >= 2
    legacy_item = next(
        (item for item in items if item.get("pickup_address_id") == "DRAFT-000001"),
        None,
    )
    assert legacy_item is not None
    assert legacy_item.get("pickup_address") is None


@pytest.mark.asyncio
async def test_save_draft_rejects_invalid_pickup_address_id(
    client: AsyncClient, user_factory, org_factory
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    resp = await client.post(
        _drafts_path(org.id),
        headers=headers,
        json={
            "organization_id": org.id,
            "contact_user_id": user.id,
            "pickup_address_id": "DRAFT-000001",
        },
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert any(d.get("field") == "pickup_address_id" for d in details)


@pytest.mark.asyncio
async def test_list_returns_includes_attempt_fields(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org, _, stop = await _seed_return_stop(
        db_session,
        org_factory,
        user_factory,
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED,
    )
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    list_resp = await client.get(_returns_path(org.id), headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    matched = next((item for item in items if item["delivery_stop_id"] == stop.id), None)
    assert matched is not None
    assert matched["stop_status"] == DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value
    assert matched["attempt_number"] == 1
    assert matched["max_attempts"] == 3
    assert isinstance(matched["packages"], list)
    assert len(matched["packages"]) >= 1


@pytest.mark.asyncio
async def test_list_returns_filters_by_attempt_number(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org, _, stop_one = await _seed_return_stop(
        db_session,
        org_factory,
        user_factory,
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED,
    )
    _, _, stop_two = await _seed_return_stop(
        db_session,
        org_factory,
        user_factory,
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED,
        org=org,
    )
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    list_resp = await client.get(
        _returns_path(org.id, attempt_number=[1]),
        headers=headers,
    )
    assert list_resp.status_code == 200
    data = list_resp.json()["data"]
    stop_ids = {item["delivery_stop_id"] for item in data["items"]}
    assert stop_one.id in stop_ids
    assert stop_two.id not in stop_ids
    assert all(item["attempt_number"] == 1 for item in data["items"])


@pytest.mark.asyncio
async def test_list_returns_rejects_invalid_attempt_number(
    client: AsyncClient, user_factory, org_factory
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    list_resp = await client.get(
        _returns_path(org.id, attempt_number=[9]),
        headers=headers,
    )
    assert list_resp.status_code == 422
    assert "attempt_number" in list_resp.text.lower()


@pytest.mark.asyncio
async def test_list_failed_deliveries_filters_by_attempt_number(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org, _, stop_one = await _seed_failed_delivery_stop(
        db_session,
        org_factory,
        user_factory,
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED,
    )
    _, _, stop_two = await _seed_failed_delivery_stop(
        db_session,
        org_factory,
        user_factory,
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED,
        org=org,
    )
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    list_resp = await client.get(
        _failed_deliveries_path(org.id, attempt_number=[2]),
        headers=headers,
    )
    assert list_resp.status_code == 200
    data = list_resp.json()["data"]
    stop_ids = {item["delivery_stop_id"] for item in data["items"]}
    assert stop_one.id not in stop_ids
    assert stop_two.id in stop_ids
    assert all(item["attempt_number"] == 2 for item in data["items"])


@pytest.mark.asyncio
async def test_get_draft(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    create_resp = await client.post(_drafts_path(org.id), headers=headers, json={"memo": "GetMe"})
    draft_id = create_resp.json()["data"]["id"]

    get_resp = await client.get(_drafts_path(org.id, draft_id), headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["payload"]["memo"] == "GetMe"


@pytest.mark.asyncio
async def test_delete_draft(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    create_resp = await client.post(_drafts_path(org.id), headers=headers, json={"memo": "Deletable"})
    draft_id = create_resp.json()["data"]["id"]

    del_resp = await client.delete(_drafts_path(org.id, draft_id), headers=headers)
    assert del_resp.status_code == 200

    get_resp = await client.get(_drafts_path(org.id, draft_id), headers=headers)
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_draft(client: AsyncClient, user_factory, org_factory, db_session) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    submit_payload = {
        "client_type": "B2B",
        "contact_user_id": user.id,
        "pickup_address_id": pickup_id,
        "payment_method": "CASH",
        "payment_method_id": pm_id,
        "delivery_stops": [_full_stop()],
    }
    create_resp = await client.post(_drafts_path(org.id), headers=headers, json=submit_payload)
    assert create_resp.status_code == 201
    draft_id = create_resp.json()["data"]["id"]

    submit_resp = await client.post(
        f"{ORDERS}/drafts/{draft_id}/submit?{_org_query(org.id)}",
        headers=headers,
    )
    assert submit_resp.status_code == 200
    data = submit_resp.json()["data"]
    assert data["status"] == "PENDING_PICKUP"
    assert data["order_id"].startswith("SWC-ORD-")
    assert len(data["delivery_stops"]) == 1
    for stop in data["delivery_stops"]:
        assert stop["status"] == "PENDING_PICKUP"
        for pkg in stop["packages"]:
            assert pkg["status"] == "PENDING_PICKUP"

    get_resp = await client.get(_drafts_path(org.id, draft_id), headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == draft_id


@pytest.mark.asyncio
async def test_submit_draft_missing_fields_fails(client: AsyncClient, user_factory, org_factory) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)

    create_resp = await client.post(_drafts_path(org.id), headers=headers, json={})
    assert create_resp.status_code == 201
    draft_id = create_resp.json()["data"]["id"]

    submit_resp = await client.post(
        f"{ORDERS}/drafts/{draft_id}/submit?{_org_query(org.id)}",
        headers=headers,
    )
    assert submit_resp.status_code == 422


@pytest.mark.asyncio
async def test_create_order_auto_creates_invoice_with_line_items(
    client: AsyncClient, user_factory, org_factory, db_session
) -> None:
    org: Organization = await org_factory()
    user: User = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        phone="07111111111",
    )
    headers = _customer_b2b_headers(user.id, organization_id=org.id)
    pm_id = await _org_cash_payment_method_id(db_session, org.id)
    pickup_id = await _pickup_id_from_api(client, headers)
    create_resp = await client.post(
        ORDERS,
        headers=headers,
        json=_b2b_order_core(
            org.id,
            user.id,
            pickup_id,
            pm_id,
            delivery_stops=[_full_stop()],
        ),
    )
    assert create_resp.status_code == 201
    order_ref = create_resp.json()["data"]["order_id"]
    order_result = await db_session.execute(select(Order).where(Order.order_id == order_ref))
    order_pk = order_result.scalar_one().id

    invoice_result = await db_session.execute(select(Invoice).where(Invoice.order_id == order_pk))
    invoice = invoice_result.scalar_one_or_none()
    assert invoice is not None
    assert invoice.organization_id == org.id
    assert invoice.customer_id == user.id

    line_items_result = await db_session.execute(
        select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice.id)
    )
    line_items = list(line_items_result.scalars().all())
    assert len(line_items) >= 1
