"""Insert inbox rows for every ADMIN (optional SUPER_ADMIN) user.

Run: poetry run python scripts/seed_admin_inbox_notifications.py
     poetry run python scripts/seed_admin_inbox_notifications.py --dry-run
     poetry run python scripts/seed_admin_inbox_notifications.py --include-super-admins
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
    NotificationEvent.ADMIN_INVOICE_OVERDUE: "Card payment failed for Invoice **INV-2026-00218**.",
    NotificationEvent.ADMIN_ORDER_DELIVERY_FAILED: "Shipment **SWC-BK-01234** delivery failed due to incorrect address.",
    NotificationEvent.ADMIN_CREDIT_LIMIT_INCREASED: "Credit Note **CN-2026-00087** issued due to failed refund attempt.",
    NotificationEvent.ADMIN_NEW_ORDER_CREATED: "Courier **DR-12** assigned to Route North Zone A.",
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_SUSPENDED: "Password reset requested for user finance.team@company.com",
    NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE: "Multiple failed login attempts detected for admin@courierco.com",
    NotificationEvent.ADMIN_DATA_SYNC_FAILURE: "Braintree API response delay detected.",
    NotificationEvent.ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS: "System maintenance scheduled for **Sunday 02:00 AM**.",
}


def _body_for_event(ev: NotificationEvent) -> str:
    if ev in _DEMO_BODIES:
        return _DEMO_BODIES[ev]
    label = EVENT_DISPLAY_NAMES.get(ev, ev.value.replace("_", " ").title())
    return f"{label} — sample admin inbox notification."


async def _run(*, dry_run: bool, include_super_admins: bool) -> None:
    roles = [UserRole.ADMIN]
    if include_super_admins:
        roles.append(UserRole.SUPER_ADMIN)
    events = events_for_notification_type(NotificationType.ADMIN_INTERNAL)
    base_time = datetime.now(UTC)

    async with get_async_session() as session:
        result = await session.execute(
            select(User.id, User.email).where(
                User.role.in_(roles),
                User.status == UserStatus.ACTIVE,
            )
        )
        admins = list(result.all())
        if not admins:
            print("No active users found for roles:", [r.value for r in roles])
            return

        total_rows = len(admins) * len(events)
        if dry_run:
            print(f"Would insert {total_rows} notifications ({len(admins)} users × {len(events)} events).")
            for uid, email in admins:
                print(f"  user {uid} <{email}>")
            return

        batch: list[Notification] = []
        for user_idx, (recipient_id, _email) in enumerate(admins):
            for ev_idx, ev in enumerate(events):
                created = base_time - timedelta(
                    hours=4 * user_idx + ev_idx,
                    minutes=ev_idx % 60,
                )
                batch.append(
                    Notification(
                        recipient_id=recipient_id,
                        organization_id=None,
                        event=ev.value,
                        notification_type=NotificationType.ADMIN_INTERNAL.value,
                        subject=None,
                        body=_body_for_event(ev),
                        context_json=None,
                        read_at=None,
                        created_at=created,
                        updated_at=created,
                    )
                )
        session.add_all(batch)
        print(f"Inserted {len(batch)} notifications for {len(admins)} user(s) ({len(events)} events each).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed admin inbox notifications (body only, no subject)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List target users and counts without writing",
    )
    parser.add_argument(
        "--include-super-admins",
        action="store_true",
        help="Also include users with role SUPER_ADMIN",
    )
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run, include_super_admins=args.include_super_admins))


if __name__ == "__main__":
    main()
