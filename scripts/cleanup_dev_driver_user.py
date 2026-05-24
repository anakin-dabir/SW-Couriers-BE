"""Delete a dev user created by `seed_dev_driver_user.py` (and related rows via FK CASCADE).

Default removes the *original* seed email (`@swcouriers.local`). Use `--email` to target another
address (e.g. `driver.dev@example.com` if you want a full reset before re-seeding).

Usage:
    python scripts/cleanup_dev_driver_user.py
    python scripts/cleanup_dev_driver_user.py --email driver.dev@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.modules.user.models import User
from sqlalchemy import select

# Original default before `.local` was rejected by email validation.
DEFAULT_EMAIL = "driver.dev@swcouriers.local"


async def _delete_user_by_email(email: str) -> bool:
    email_norm = email.strip().lower()
    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.email == email_norm))
        user = result.scalar_one_or_none()
        if user is None:
            return False
        await session.delete(user)
        await session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete a dev driver user (and dependent rows) by email."
    )
    parser.add_argument(
        "--email",
        default=DEFAULT_EMAIL,
        help=f"Email to remove (default: {DEFAULT_EMAIL})",
    )
    args = parser.parse_args()

    async def _run() -> None:
        removed = await _delete_user_by_email(args.email)
        if not removed:
            print(f"No user found for {args.email!r} — nothing to do.")
        else:
            print(f"Deleted user {args.email!r} and dependent data (CASCADE).")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
