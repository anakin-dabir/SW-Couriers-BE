"""Insert inbox rows for every active CUSTOMER_B2B user (B2B dashboard stream).

Uses ``NotificationType.B2B_CUSTOMER`` and the same events as the b2b_dashboard
preference tab. Body only; ``subject`` is null. ``organization_id`` is taken
from each user when set.

Run: poetry run python scripts/seed_b2b_customer_inbox_notifications.py
     poetry run python scripts/seed_b2b_customer_inbox_notifications.py --dry-run
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import app.models  # noqa: F401
from sqlalchemy import select

from app.common.enums.user import UserRole, UserStatus
from app.core.database import get_async_session
from app.modules.notifications.enums import (
    EVENT_DISPLAY_NAMES,
    NotificationEvent,
    NotificationType,
    events_for_notification_type,
)
from app.modules.notifications.models import Notification
from app.modules.user.models import User

_DEMO_BODIES: dict[NotificationEvent, str] = {
    NotificationEvent.BOOKING_CONFIRMATION: "Booking **SWC-BK-01402** confirmed — courier assigned for tomorrow 09:00.",
    NotificationEvent.PICKUP_SCHEDULED: "Pickup scheduled for **SWC-BK-01402** at 14 Brook Street, London.",
    NotificationEvent.OUT_FOR_DELIVERY: "**SWC-BK-01234** is out for delivery to EC1A.",
    NotificationEvent.DELIVERY_FAILED_FINAL: "Final delivery attempt failed for **SWC-BK-01189** — address incomplete.",
    NotificationEvent.INVOICE_GENERATED: "Invoice **INV-2026-00412** generated for your organisation.",
    NotificationEvent.INVOICE_OVERDUE: "Invoice **INV-2026-00391** is now overdue.",
    NotificationEvent.PAYMENT_RECEIVED: "Payment recorded for Invoice **INV-2026-00388**.",
    NotificationEvent.CREDIT_LIMIT_WARNING: "Credit utilisation reached **85%** of your approved limit.",
}


def _body_for_event(ev: NotificationEvent) -> str:
    if ev in _DEMO_BODIES:
        return _DEMO_BODIES[ev]
    label = EVENT_DISPLAY_NAMES.get(ev, ev.value.replace("_", " ").title())
    return f"{label} — sample B2B dashboard inbox notification."


async def _run(*, dry_run: bool) -> None:
    events = events_for_notification_type(NotificationType.B2B_CUSTOMER)
    base_time = datetime.now(UTC)

    async with get_async_session() as session:
        result = await session.execute(
            select(User.id, User.email, User.organization_id).where(
                User.role == UserRole.CUSTOMER_B2B,
                User.status == UserStatus.ACTIVE,
            )
        )
        contacts = list(result.all())
        if not contacts:
            print("No active CUSTOMER_B2B users found.")
            return

        total_rows = len(contacts) * len(events)
        if dry_run:
            print(
                f"Would insert {total_rows} notifications "
                f"({len(contacts)} CUSTOMER_B2B users × {len(events)} events, type=B2B_CUSTOMER)."
            )
            for uid, email, org_id in contacts:
                print(f"  user {uid} <{email}> org_id={org_id!r}")
            return

        batch: list[Notification] = []
        for user_idx, (recipient_id, _email, organization_id) in enumerate(contacts):
            for ev_idx, ev in enumerate(events):
                created = base_time - timedelta(
                    hours=4 * user_idx + ev_idx,
                    minutes=ev_idx % 60,
                )
                batch.append(
                    Notification(
                        recipient_id=recipient_id,
                        organization_id=organization_id,
                        event=ev.value,
                        notification_type=NotificationType.B2B_CUSTOMER.value,
                        subject=None,
                        body=_body_for_event(ev),
                        context_json=None,
                        read_at=None,
                        created_at=created,
                        updated_at=created,
                    )
                )
        session.add_all(batch)
        print(
            f"Inserted {len(batch)} notifications for {len(contacts)} CUSTOMER_B2B user(s) "
            f"({len(events)} B2B_CUSTOMER events each)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed B2B customer (dashboard) inbox notifications — body only, no subject"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List target users and counts without writing",
    )
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
