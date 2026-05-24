"""Rich order / draft / failed / returned demo data for frontend API testing.


Single script entrypoint: bootstraps missing booking prerequisites on the billing demo org
(global STANDARD/EXPRESS tiers, pricing_plans, payment config, payment method, pickup), then
seeds orders and drafts. Does **not** create or clear billing invoices/payments.

Requires the billing demo org (``BILLING_DEMO_ORG_ID``) and a linked ``CUSTOMER_B2B`` user.


Only removes/replaces FE-tagged orders/drafts for the chosen org (billing rows untouched).

Usage:
  poetry run python scripts/seed_fe_order_scenarios.py seed
  poetry run python scripts/seed_fe_order_scenarios.py seed --organization-id 9491bf02-e7bf-413b-98a7-270999f90954
  poetry run python scripts/seed_fe_order_scenarios.py clear --organization-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.core.database import get_async_session
from app.common.enums.delivery import DeliveryServiceTier
from app.modules.orders.enums import ClientTypeEnum, DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, Order, OrderDraft, Package
from app.modules.orders.service import OrderService
from app.modules.orders.v1.schemas import DeliveryStopCreateItem, PackageCreateItem

from app.modules.payments.models import CreditCard
from app.modules.organizations.enums import PaymentModel
from app.modules.organizations.models import Organization, OrgPaymentMethod

from app.modules.pickup_addresses.models import PickupAddress
from app.modules.user.models import User
from scripts.fe_demo_lib import (
    BILLING_DEMO_ORG_ID,
    ORDER_PREFIX,
    SEED_TAG,
    clear_fe_demo_drafts,
    money,
    order_code,
    prepare_fe_order_booking_context,
    tracking_code,
)

FE_ORDER_SCENARIO_PREFIX = "FE-DEMO-ORD-SCN-"


def _default_service_tier_name(org: Organization) -> str:
    """Use an org pricing plan label so OrderService pricing resolves (not hard-coded STANDARD)."""
    plans = org.pricing_plans or []
    for plan in plans:
        if plan.get("selected") or plan.get("is_default"):
            name = (plan.get("plain_name") or plan.get("plain_type") or "").strip()
            if name:
                return name
    if plans:
        name = (plans[0].get("plain_name") or plans[0].get("plain_type") or "").strip()
        if name:
            return name
    return DeliveryServiceTier.STANDARD.value


RECIPIENTS = [
    ("Nora", "Singh", "12 Park Lane", "London", "W1K 1QB", 51.5078, -0.1517),
    ("Owen", "Baker", "8 Eaton Square", "London", "SW1W 9DJ", 51.4934, -0.1538),
    ("Paula", "Green", "30 Tavistock Square", "London", "WC1H 9HD", 51.5240, -0.1306),
    ("Quinn", "Hart", "44 Curzon Street", "London", "W1J 7UR", 51.5072, -0.1465),
    ("Rosa", "Meyer", "120 Holland Park Avenue", "London", "W11 4UA", 51.5078, -0.2086),
    ("Sam", "Olsen", "1 Cromwell Place", "London", "SW7 2JE", 51.4985, -0.1747),
    ("Tara", "Nguyen", "65 Cheyne Walk", "London", "SW3 5LR", 51.4836, -0.1746),
    ("Umar", "Shah", "10 Cornhill", "London", "EC3V 3LL", 51.5133, -0.0866),
]


SCENARIOS: list[dict] = [
    {"tag": "PENDING-PU", "order": OrderStatus.PENDING_PICKUP, "stop": DeliveryStopStatus.PENDING_PICKUP, "pkg": PackageStatus.PENDING_PICKUP},
    {"tag": "PU-SCHED", "order": OrderStatus.PICKUP_SCHEDULED, "stop": DeliveryStopStatus.PICKUP_SCHEDULED, "pkg": PackageStatus.PICKUP_SCHEDULED},
    {"tag": "WAREHOUSE", "order": OrderStatus.AT_WAREHOUSE, "stop": DeliveryStopStatus.AT_WAREHOUSE, "pkg": PackageStatus.AT_WAREHOUSE},
    {"tag": "SORTING", "order": OrderStatus.SORTING_IN_PROGRESS, "stop": DeliveryStopStatus.SORTING_IN_PROGRESS, "pkg": PackageStatus.SORTING_IN_PROGRESS},
    {"tag": "OUT-DEL", "order": OrderStatus.DELIVERY_IN_PROGRESS, "stop": DeliveryStopStatus.OUT_FOR_DELIVERY, "pkg": PackageStatus.OUT_FOR_DELIVERY},
    {"tag": "DELIVERED", "order": OrderStatus.DELIVERED, "stop": DeliveryStopStatus.DELIVERED, "pkg": PackageStatus.DELIVERED_TO_CUSTOMER},
    {"tag": "PARTIAL", "order": OrderStatus.PARTIALLY_DELIVERED, "stop": DeliveryStopStatus.PARTIALLY_DELIVERED, "pkg": PackageStatus.DELIVERED_TO_CUSTOMER},
    {"tag": "FAIL-ATT1", "order": OrderStatus.DELIVERY_IN_PROGRESS, "stop": DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED, "pkg": PackageStatus.CUSTOMER_NOT_HOME},
    {"tag": "FAIL-ATT2", "order": OrderStatus.FAILED, "stop": DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED, "pkg": PackageStatus.REFUSED_BY_CUSTOMER},
    {"tag": "FAILED", "order": OrderStatus.FAILED, "stop": DeliveryStopStatus.FAILED, "pkg": PackageStatus.REFUSED_BY_CUSTOMER},
    {"tag": "RET-INIT", "order": OrderStatus.RETURN_IN_PROGRESS, "stop": DeliveryStopStatus.RETURN_INITIATED, "pkg": PackageStatus.RETURN_INITIATED},
    {"tag": "RET-TRANS", "order": OrderStatus.RETURN_IN_TRANSIT, "stop": DeliveryStopStatus.RETURN_IN_TRANSIT, "pkg": PackageStatus.RETURN_IN_TRANSIT},
    {"tag": "RETURNED", "order": OrderStatus.RETURNED, "stop": DeliveryStopStatus.RETURNED, "pkg": PackageStatus.RETURNED},
    {"tag": "CANCELLED", "order": OrderStatus.CANCELLED, "stop": DeliveryStopStatus.CANCELLED, "pkg": PackageStatus.CANCELLED},
]


async def _clear_scenario_orders(organization_id: str) -> None:
    async with get_async_session() as session:
        await session.execute(
            delete(Order).where(
                Order.organization_id == organization_id,
                Order.order_id.ilike(f"{ORDER_PREFIX}%"),
                ~Order.order_id.ilike("FE-DEMO-ORD-DRV-%"),
            )
        )
        await session.execute(
            delete(Order).where(
                Order.organization_id == organization_id,
                Order.order_id.ilike(f"{FE_ORDER_SCENARIO_PREFIX}%"),
            )
        )
        await clear_fe_demo_drafts(session, organization_id)
        await session.commit()
        print(f"Cleared FE-DEMO orders and drafts for org {organization_id} (billing data untouched).")



async def _resolve_booking_context(
    session,
    organization_id: str,
) -> tuple[Organization, User, PickupAddress, OrgPaymentMethod]:
    return await prepare_fe_order_booking_context(session, organization_id)



async def _insert_scenario_order(
    session,
    *,
    org: Organization,
    customer: User,
    pickup: PickupAddress,
    idx: int,
    scenario: dict,
) -> Order:
    tag = scenario["tag"]
    fn, ln, line_1, city, postcode, lat, lng = RECIPIENTS[idx % len(RECIPIENTS)]
    order = Order(
        order_id=f"{FE_ORDER_SCENARIO_PREFIX}{tag}",
        master_label_id=f"ML-FE-SCN-{tag}",
        organization_id=org.id,
        customer_id=customer.id,
        pickup_address_id=pickup.id,
        requested_pickup_date=date.today() + timedelta(days=idx % 3),
        subtotal=money("38.00"),
        vat_amount=money("7.60"),
        total_amount=money("45.60"),
        status=scenario["order"].value if hasattr(scenario["order"], "value") else scenario["order"],
    )
    session.add(order)
    await session.flush()

    stop = DeliveryStop(
        order_id=order.id,
        tracking_id=tracking_code(f"SCN-{tag}", idx),
        recipient_first_name=fn,
        recipient_last_name=ln,
        recipient_phone=f"077009{900 + idx:05d}",
        recipient_email=f"{fn.lower()}.{ln.lower()}.fe.scn@example.com",
        line_1=line_1,
        city=city,
        postcode=postcode,
        latitude=lat,
        longitude=lng,
        service_tier=DeliveryServiceTier.STANDARD,
        signature_required=tag == "DELIVERED",
        safe_place_allowed=True,
        status=scenario["stop"].value if hasattr(scenario["stop"], "value") else scenario["stop"],
        scheduled_for=date.today() + timedelta(days=idx % 5),
    )
    session.add(stop)
    await session.flush()

    session.add(
        Package(
            order_id=order.id,
            delivery_stop_id=stop.id,
            length_cm=35,
            width_cm=25,
            height_cm=20,
            weight_kg=2.5,
            declared_weight_kg=2.7,
            declared_value=money("120.00"),
            status=scenario["pkg"].value if hasattr(scenario["pkg"], "value") else scenario["pkg"],
            is_damaged=tag == "FAILED",
            price_breakdown={"seed": SEED_TAG, "scenario": tag},
        )
    )
    await session.flush()
    return order


async def _seed_drafts(session, *, org: Organization, customer: User, pickup: PickupAddress, pm: OrgPaymentMethod) -> int:
    svc = OrderService(session)
    tier_name = _default_service_tier_name(org)
    drafts_spec = [
        ("DRAFT-FULL", True),
        ("DRAFT-PARTIAL", False),
        ("DRAFT-MULTI", True),
        ("DRAFT-ALT-TIER", True),
        ("DRAFT-EMPTY-STOPS", False),
    ]
    count = 0
    for tag, complete in drafts_spec:
        fn, ln, line_1, city, postcode, lat, lng = RECIPIENTS[count % len(RECIPIENTS)]
        payload: dict = {
            "seed": SEED_TAG,
            "scenario": tag,
            "client_type": "B2B",
            "organization_id": org.id,
            "contact_user_id": customer.id,
            "pickup_address_id": pickup.id,
            "payment_method": pm.payment_model.value if hasattr(pm.payment_model, "value") else str(pm.payment_model),
            "payment_method_id": str(pm.id),
            "requested_pickup_date": (date.today() + timedelta(days=count)).isoformat(),
        }
        if complete:
            payload["delivery_stops"] = [
                {
                    "recipient_first_name": fn,
                    "recipient_last_name": ln,
                    "recipient_phone": f"077009{600 + count:05d}",
                    "recipient_email": f"{fn.lower()}.draft@example.com",
                    "line_1": line_1,
                    "city": city,
                    "postcode": postcode,
                    "latitude": lat,
                    "longitude": lng,
                    "service_tier_name": tier_name,
                    "signature_required": False,
                    "safe_place_allowed": True,
                    "customer_note": f"Draft scenario {tag}",
                    "packages": [
                        {
                            "length_cm": 30,
                            "width_cm": 20,
                            "height_cm": 15,
                            "declared_weight_kg": "2.0",
                            "declared_value": "90.00",
                        }
                    ],
                }
            ]
            if tag == "DRAFT-MULTI":
                fn2, ln2, line_2, _city2, pc2, lat2, lng2 = RECIPIENTS[(count + 1) % len(RECIPIENTS)]
                payload["delivery_stops"].append(
                    {
                        "recipient_first_name": fn2,
                        "recipient_last_name": ln2,
                        "recipient_phone": "07700900666",
                        "line_1": line_2,
                        "city": "London",
                        "postcode": pc2,
                        "latitude": lat2,
                        "longitude": lng2,
                        "service_tier_name": tier_name,
                        "packages": [{"length_cm": 25, "width_cm": 20, "height_cm": 15, "declared_weight_kg": "1.5", "declared_value": "40.00"}],
                    }
                )
        await svc.save_draft(created_by_id=customer.id, payload=payload)
        count += 1
    return count


async def _seed_via_service(
    session,
    *,
    org: Organization,
    customer: User,
    pickup: PickupAddress,
    pm: OrgPaymentMethod,
    count: int = 4,
) -> int:
    payment_model = PaymentModel(pm.payment_model) if not isinstance(pm.payment_model, PaymentModel) else pm.payment_model
    credit_card_id: str | None = None
    if payment_model == PaymentModel.CARD:
        card = await session.scalar(
            select(CreditCard).where(CreditCard.organization_id == org.id).limit(1)
        )
        if card is None:
            print(
                f"Skipping OrderService bookings: org {org.id} uses CARD but has no credit_cards row. "
                "Lifecycle scenario orders are still inserted directly."
            )
            return 0
        credit_card_id = str(card.id)

    tier_name = _default_service_tier_name(org)
    svc = OrderService(session)
    created = 0
    for i in range(count):
        fn, ln, line_1, city, postcode, lat, lng = RECIPIENTS[i % len(RECIPIENTS)]
        order = await svc.create_order(
            client_type=ClientTypeEnum.B2B,
            organization_id=org.id,
            customer_id=customer.id,
            created_by_id=customer.id,
            pickup_address_id=pickup.id,
            requested_pickup_date=date.today() + timedelta(days=i),
            payment_method=payment_model,
            payment_method_id=str(pm.id),
            credit_card_id=credit_card_id,
            payment_method_nonce=None,
            delivery_stops=[
                DeliveryStopCreateItem(
                    recipient_first_name=fn,
                    recipient_last_name=ln,
                    recipient_phone=f"077009{700 + i:05d}",
                    recipient_email=f"{fn.lower()}.svc@example.com",
                    line_1=line_1,
                    city=city,
                    postcode=postcode,
                    latitude=lat,
                    longitude=lng,
                    service_tier_name=tier_name,
                    signature_required=False,
                    safe_place_allowed=True,
                    customer_note=f"Created via OrderService #{i + 1}",
                    packages=[
                        PackageCreateItem(
                            length_cm=32,
                            width_cm=22,
                            height_cm=18,
                            declared_weight_kg=Decimal("2.2"),
                            declared_value=Decimal("99.00"),
                        )
                    ],
                )
            ],
        )
        order.order_id = order_code("SVC", i + 1)
        created += 1
    return created


async def seed_scenarios(*, organization_id: str = BILLING_DEMO_ORG_ID) -> None:
    await _clear_scenario_orders(organization_id)

    async with get_async_session() as session:
        org, customer, pickup, pm = await _resolve_booking_context(session, organization_id)

        tier_for_api = _default_service_tier_name(org)
        allowed_tiers = {e.value for e in DeliveryServiceTier}
        if len(tier_for_api) <= 8 and tier_for_api.upper() in allowed_tiers:
            svc_count = await _seed_via_service(
                session,
                org=org,
                customer=customer,
                pickup=pickup,
                pm=pm,
                count=4,
            )
        else:
            svc_count = 0
            print(
                f"Skipping OrderService bookings (tier {tier_for_api!r} is not a short service tier). "
                "Lifecycle + draft rows are still seeded."
            )
        draft_count = await _seed_drafts(session, org=org, customer=customer, pickup=pickup, pm=pm)

        scenario_orders: list[Order] = []
        for idx, scenario in enumerate(SCENARIOS):
            scenario_orders.append(
                await _insert_scenario_order(
                    session,
                    org=org,
                    customer=customer,
                    pickup=pickup,
                    idx=idx,
                    scenario=scenario,
                )
            )

        await session.commit()

        print("=" * 72)
        print("FE order scenario seed complete (existing billing data left unchanged).")
        print(f"Organization    : {org.reference or org.id} ({org.id})")
        print(f"B2B customer    : {customer.email}  (role={customer.role.value})")
        print(f"Pickup address  : {pickup.label}")
        print(f"Via OrderService: {svc_count} orders ({ORDER_PREFIX}SVC-*)")
        print(f"Drafts (pending): {draft_count}")
        print(f"Lifecycle rows  : {len(scenario_orders)} ({FE_ORDER_SCENARIO_PREFIX}*)")
        print("Includes: drafts, failed (attempt 1/2/terminal), returned, delivered, cancelled, …")
        print("=" * 72)


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--organization-id",
        default=BILLING_DEMO_ORG_ID,
        help=f"Target organisation UUID (default: {BILLING_DEMO_ORG_ID})",
    )

    parser = argparse.ArgumentParser(description="Seed FE order/draft/failed/returned scenarios.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", parents=[common])
    sub.add_parser("clear", parents=[common])
    args = parser.parse_args()
    org_id = str(args.organization_id).strip()
    if args.cmd == "seed":
        asyncio.run(seed_scenarios(organization_id=org_id))
    else:
        asyncio.run(_clear_scenario_orders(org_id))


if __name__ == "__main__":
    main()
