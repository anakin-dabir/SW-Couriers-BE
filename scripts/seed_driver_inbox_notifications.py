"""Insert DRIVER inbox notifications for every active DRIVER user.

Five demo rows per driver (vehicle change, vehicle issue update, delivery note,
route finalised, route stop cancelled) matching the driver app Notifications UI.
Uses ``notification_type=DRIVER`` with titles in ``subject`` and detail in ``body``.
Route/stop-related rows include ``context_json`` with ``route_id`` and ``stop_id`` for deep links.

Run: poetry run python scripts/seed_driver_inbox_notifications.py
     poetry run python scripts/seed_driver_inbox_notifications.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select

import app.models  # noqa: F401
from app.common.enums.user import UserRole, UserStatus
from app.core.database import get_async_session
from app.modules.notifications.enums import NotificationType
from app.modules.notifications.models import Notification
from app.modules.user.models import User

_ROUTE_STOP_CONTEXT: dict[str, str] = {
    "route_id": "705c87f2-e376-4c9e-8349-8d1c6abbcd88",
    "stop_id": "e5cadb7d-5252-48ab-9788-fbfef9202146",
}

_DRIVER_SEED_ROWS: tuple[tuple[str, str, str, time, dict[str, str] | None], ...] = (
    (
        "DRIVER_ASSIGNED_VEHICLE_CHANGED",
        "Your Assigned Vehicle Has Been Changed",
        "Please review the updated vehicle details before starting.",
        time(8, 0),
        None,
    ),
    (
        "DRIVER_VEHICLE_ISSUE_STATUS_UPDATED",
        "Update on Your Reported Vehicle Issue",
        "Your reported issue has been marked as resolved / allowed / not allowed. Check details for more information.",
        time(10, 45),
        None,
    ),
    (
        "DRIVER_DELIVERY_STOP_NOTE_ADDED",
        "New Note Added to a Delivery Stop",
        "Check the latest note added by admin before proceeding.",
        time(11, 20),
        _ROUTE_STOP_CONTEXT,
    ),
    (
        "DRIVER_ROUTE_FINALISED",
        "Your Route for Tomorrow is Finalised",
        "You can now review all assigned stops and details.",
        time(18, 30),
        _ROUTE_STOP_CONTEXT,
    ),
    (
        "DRIVER_ROUTE_STOP_CANCELLED",
        "A Stop in Tomorrow's Route Has Been Cancelled",
        "Your route for tomorrow has been updated.",
        time(19, 10),
        _ROUTE_STOP_CONTEXT,
    ),
)


def _created_at_for_row(clock_time: time) -> datetime:
    day = datetime.now(UTC).date()
    return datetime.combine(day, clock_time, tzinfo=UTC)


async def _run(*, dry_run: bool) -> None:
    n_rows = len(_DRIVER_SEED_ROWS)
    async with get_async_session() as session:
        result = await session.execute(
            select(User.id, User.email, User.organization_id).where(
                User.role == UserRole.DRIVER,
                User.status == UserStatus.ACTIVE,
            )
        )
        drivers = list(result.all())
        if not drivers:
            print("No active DRIVER users found.")
            return

        total_inserts = len(drivers) * n_rows
        if dry_run:
            print(
                f"Would insert {total_inserts} notifications "
                f"({len(drivers)} drivers × {n_rows} rows, type=DRIVER)."
            )
            for uid, email, org_id in drivers:
                print(f"  user {uid} <{email}> org_id={org_id!r}")
            return

        batch: list[Notification] = []
        for user_idx, (recipient_id, _email, organization_id) in enumerate(drivers):
            skew = timedelta(minutes=user_idx * 3)
            for event, title, body, clock, context_json in _DRIVER_SEED_ROWS:
                created = _created_at_for_row(clock) - skew
                batch.append(
                    Notification(
                        recipient_id=recipient_id,
                        organization_id=organization_id,
                        event=event,
                        notification_type=NotificationType.DRIVER.value,
                        subject=title,
                        body=body,
                        context_json=context_json,
                        read_at=None,
                        created_at=created,
                        updated_at=created,
                    )
                )
        session.add_all(batch)
        print(
            f"Inserted {len(batch)} DRIVER notifications for {len(drivers)} driver(s) "
            f"({n_rows} demo rows each)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed driver app inbox notifications (subject + body, unread)"
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
