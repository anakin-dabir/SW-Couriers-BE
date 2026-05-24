"""Create 32 demo orders via the real ``OrderService.create_order``.

Idempotent: deletes any prior demo orders (``order_id`` LIKE ``DEMO-PKU-ORD-%``) before recreating
the set. Each order targets one of the 8 seeded pickup addresses (4 orders per pickup address)
so a single pickup route can visit all 8 warehouses with a consistent shape.

Run::

    poetry run python scripts/seed_demo_orders.py

Depends on ``scripts/seed_demo_actors.py`` having been run first.

Why this script exists
======================
``OrderService.create_order`` is the canonical "real booking" path. It:

* validates the organisation is ``ACTIVE``,
* loads ``OrgPaymentConfig`` (VAT, attempt fees) and the matching ``OrgPaymentMethod`` row,
* prices the order via the org's ``pricing_plans`` JSON (tier name → ``base_price`` / ``price_per_kg`` / etc),
* writes ``orders`` + ``delivery_stops`` + ``packages`` rows in a nested transaction,
* records initial ``OrderEvent`` / ``DeliveryStopEvent`` / ``PackageEvent`` status transitions,
* enqueues status-automation evaluation jobs (no-op locally without arq workers),
* syncs a draft Invoice + line items via ``InvoiceService.sync_from_order``.

By going through this path the demo exercises every real prerequisite the system enforces in
production. Skipping ``payment_method=CARD`` keeps us off Braintree entirely (the seed uses the
org's pre-created ``CREDIT_ACCOUNT`` method).
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — runnable-script pattern (imports below sys.path bootstrap).

from sqlalchemy import delete, select

import app.models  # noqa: F401
from app.common.enums import ClientType
from app.core.database import get_async_session
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.orders.v1.schemas import DeliveryStopCreateItem, PackageCreateItem
from app.modules.organizations.enums import PaymentModel
from app.modules.organizations.models import OrgPaymentMethod
from scripts.seed_demo_actors import (
    DEMO_CUSTOMER_USER_ID,
    DEMO_ORG_ID,
    DEMO_ORG_PAYMENT_METHOD_ID,
    PICKUP_ADDRESSES,
)

DEMO_ORDER_PREFIX = "DEMO-PKU-ORD"
ORDERS_PER_PICKUP_ADDRESS = 4


RECIPIENTS: list[tuple[str, str, str, str, float, float]] = [
    ("Olivia", "Bennett", "12 Park Lane", "W1K 1QB", 51.5078, -0.1517),
    ("Liam", "Carter", "8 Eaton Square", "SW1W 9DJ", 51.4934, -0.1538),
    ("Sophie", "Patel", "30 Tavistock Square", "WC1H 9HD", 51.5240, -0.1306),
    ("Noah", "Edwards", "44 Curzon Street", "W1J 7UR", 51.5072, -0.1465),
    ("Mia", "Hughes", "120 Holland Park Avenue", "W11 4UA", 51.5078, -0.2086),
    ("Ethan", "Morgan", "1 Cromwell Place", "SW7 2JE", 51.4985, -0.1747),
    ("Ava", "Robinson", "65 Cheyne Walk", "SW3 5LR", 51.4836, -0.1746),
    ("Lucas", "Thompson", "10 Cornhill", "EC3V 3LL", 51.5133, -0.0866),
    ("Emily", "Watson", "15 Fenchurch Street", "EC3M 3BD", 51.5119, -0.0807),
    ("Aiden", "Walker", "1 Threadneedle Street", "EC2R 8AH", 51.5142, -0.0884),
    ("Isla", "Russell", "12 Long Acre", "WC2E 9LH", 51.5128, -0.1232),
    ("Harry", "Bell", "10 Russell Square", "WC1B 5EH", 51.5232, -0.1276),
    ("Ella", "Cooper", "23 Marylebone Road", "NW1 5JD", 51.5240, -0.1554),
    ("Oscar", "Fox", "55 Baker Street", "W1U 7EU", 51.5180, -0.1571),
    ("Lily", "Foster", "1 Portland Place", "W1B 1JZ", 51.5188, -0.1442),
    ("Jack", "Hill", "4 Bressenden Place", "SW1E 5DH", 51.4972, -0.1414),
    ("Grace", "Hunt", "20 Birdcage Walk", "SW1H 9JX", 51.5008, -0.1339),
    ("Theo", "Knight", "9 Lincoln's Inn Fields", "WC2A 3DA", 51.5165, -0.1167),
    ("Aria", "Mason", "10 Bedford Square", "WC1B 3RA", 51.5202, -0.1295),
    ("Henry", "Powell", "30 Bishopsgate", "EC2N 4AJ", 51.5152, -0.0817),
    ("Chloe", "Reid", "1 Crutched Friars", "EC3N 2HR", 51.5126, -0.0762),
    ("Jacob", "Saunders", "8 Devonshire Square", "EC2M 4PL", 51.5165, -0.0805),
    ("Layla", "Stevens", "1 Old Broad Street", "EC2N 1AR", 51.5152, -0.0853),
    ("Leo", "Turner", "2 Pancras Square", "N1C 4AG", 51.5365, -0.1255),
    ("Zoe", "Webb", "60 Cannon Street", "EC4N 6JP", 51.5125, -0.0930),
    ("Max", "Wood", "1 Carey Street", "WC2A 2JT", 51.5158, -0.1130),
    ("Ruby", "Young", "25 New Street", "EC2M 4NN", 51.5174, -0.0795),
    ("Felix", "Adams", "30 Old Bailey", "EC4M 7AU", 51.5152, -0.1009),
    ("Daisy", "Allen", "23 Throgmorton Street", "EC2N 2AP", 51.5151, -0.0876),
    ("Charlie", "Barnes", "11 Worship Street", "EC2A 2DT", 51.5232, -0.0856),
    ("Poppy", "Brooks", "2 Triton Square", "NW1 3AN", 51.5269, -0.1408),
    ("Toby", "Burton", "4 Greycoat Place", "SW1P 1SB", 51.4969, -0.1378),
]


async def _purge_prior_orders(session) -> None:
    await session.execute(delete(Order).where(Order.order_id.like(f"{DEMO_ORDER_PREFIX}-%")))
    await session.flush()


async def _ensure_payment_method(session) -> OrgPaymentMethod:
    """Confirm the org's CREDIT_ACCOUNT payment method exists and matches the demo id."""
    pm = await session.get(OrgPaymentMethod, DEMO_ORG_PAYMENT_METHOD_ID)
    if pm is None:
        raise SystemExit(
            "OrgPaymentMethod for the demo org is missing. Run scripts/seed_demo_actors.py first."
        )
    return pm


async def _create_order_for(
    *,
    svc: OrderService,
    pickup_address_id: str,
    recipient_index: int,
    sequence: int,
) -> Order:
    first, last, line_1, postcode, lat, lng = RECIPIENTS[recipient_index % len(RECIPIENTS)]
    tier_name = "EXPRESS" if sequence % 4 == 0 else "STANDARD"
    pkg_count = 1 + (sequence % 3)
    delivery_stop = DeliveryStopCreateItem(
        recipient_first_name=first,
        recipient_last_name=last,
        recipient_phone=f"077009{(50000 + sequence):05d}",
        recipient_email=f"{first.lower()}.{last.lower()}.{sequence}@example.com",
        line_1=line_1,
        city="London",
        postcode=postcode,
        latitude=lat,
        longitude=lng,
        service_tier_name=tier_name,
        signature_required=False,
        safe_place_allowed=False,
        customer_note=f"Please collect at the reception (demo order #{sequence}).",
        packages=[
            PackageCreateItem(
                length_cm=30 + (idx * 5),
                width_cm=20 + (idx * 3),
                height_cm=15 + (idx * 2),
                declared_weight_kg=2.0 + (idx * 1.5),
                declared_value=Decimal("75.00"),
            )
            for idx in range(pkg_count)
        ],
    )

    order = await svc.create_order(
        client_type=ClientType.CUSTOMER_B2B,
        organization_id=DEMO_ORG_ID,
        customer_id=DEMO_CUSTOMER_USER_ID,
        created_by_id=DEMO_CUSTOMER_USER_ID,
        pickup_address_id=pickup_address_id,
        requested_pickup_date=None,
        payment_method=PaymentModel.CREDIT_ACCOUNT,
        payment_method_id=DEMO_ORG_PAYMENT_METHOD_ID,
        credit_card_id=None,
        payment_method_nonce=None,
        delivery_stops=[delivery_stop],
    )
    order.order_id = f"{DEMO_ORDER_PREFIX}-{sequence:03d}"
    return order


async def _run() -> None:
    async with get_async_session() as session:
        await _purge_prior_orders(session)
        await _ensure_payment_method(session)
        svc = OrderService(session)

        recipient_idx = 0
        sequence = 0
        per_pickup: dict[str, int] = {}
        for pa in PICKUP_ADDRESSES:
            pa_id = str(pa["id"])
            per_pickup[pa_id] = 0
            for _ in range(ORDERS_PER_PICKUP_ADDRESS):
                sequence += 1
                await _create_order_for(
                    svc=svc,
                    pickup_address_id=pa_id,
                    recipient_index=recipient_idx,
                    sequence=sequence,
                )
                per_pickup[pa_id] += 1
                recipient_idx += 1
        await session.commit()

        count_stmt = select(Order).where(Order.order_id.like(f"{DEMO_ORDER_PREFIX}-%"))
        created = list((await session.execute(count_stmt)).scalars().all())

    print("=" * 72)
    print(f"Created {len(created)} demo orders ({ORDERS_PER_PICKUP_ADDRESS} per pickup address):")
    for pa in PICKUP_ADDRESSES:
        pa_id = str(pa["id"])
        print(f"  {pa_id}  {pa['label']:<24} → {per_pickup.get(pa_id, 0)} orders")
    print()
    print("Each order:")
    print("  * 1 delivery stop in central London")
    print("  * 1–3 packages, tiers rotated STANDARD/EXPRESS")
    print("  * CREDIT_ACCOUNT (no Braintree round-trip)")
    print("  * Invoice synced in draft via InvoiceService")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_run())
