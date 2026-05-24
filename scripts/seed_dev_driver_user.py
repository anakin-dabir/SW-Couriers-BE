"""Create a local dev driver user with a known email and password for Postman / manual login.

Run from the repository root so `app` is importable (or set PYTHONPATH to the repo root).

Usage:
    python scripts/seed_dev_driver_user.py
    python scripts/seed_dev_driver_user.py --email "me@example.com" --password "YourStr0ng!Pass"

Login (same as production):
    POST /v1/auth/login
    Headers: X-Client-Type: DRIVER
    Body: { "email": "<printed email>", "password": "<printed password>" }

Requires DATABASE_URL (see .env). Fails if the email is already registered.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/seed_dev_driver_user.py` from repo root without PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app.models  # noqa: F401
from app.common.enums import UserRole, UserStatus
from app.common.validators import validate_password_strength
from app.core.database import get_async_session
from app.core.security import hash_password
from app.modules.drivers.enums import DriverAccountStatus, DriverLiveStatus, DriverType
from app.modules.drivers.models import Driver
from app.modules.user.models import User
from sqlalchemy import select


# Must pass API email validation; `.local` is rejected by Pydantic (special-use TLD).
DEFAULT_EMAIL = "driver.dev@example.com"
# Meets validate_password_strength (upper, lower, digit, special)
DEFAULT_PASSWORD = "SecureTestPass1!"


async def _run(*, email: str, password: str) -> None:
    email_norm = email.strip().lower()
    try:
        validate_password_strength(password)
    except ValueError as e:
        raise SystemExit(str(e)) from e

    async with get_async_session() as session:
        existing = (
            await session.execute(select(User.id).where(User.email == email_norm))
        ).scalar_one_or_none()
        if existing is not None:
            raise SystemExit(
                f"User with email {email_norm!r} already exists. "
                "Use a different --email or remove that user from the database first."
            )

        user = User(
            email=email_norm,
            password_hash=hash_password(password),
            first_name="Dev",
            last_name="Driver",
            phone="07123456789",
            role=UserRole.DRIVER,
            status=UserStatus.ACTIVE,
            email_verified=True,
            force_password_change=False,
        )
        session.add(user)
        await session.flush()

        driver = Driver(
            user_id=user.id,
            capacities=["VAN"],
            driver_type=DriverType.INTERNAL.value,
            address_line1="1 Seed Street",
            city="London",
            postcode="SW1A 1AA",
            state="England",
            account_status=DriverAccountStatus.ACTIVE,
            live_status=DriverLiveStatus.OFFLINE,
        )
        session.add(driver)
        await session.commit()

    print("Dev driver user created. Use these with POST /v1/auth/login (X-Client-Type: DRIVER).")
    print(f"EMAIL={email_norm}")
    print(f"PASSWORD={password}")
    print(f"USER_ID={user.id}")
    print(f"DRIVER_ID={driver.id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a dev driver account for API login testing.")
    parser.add_argument(
        "--email",
        default=DEFAULT_EMAIL,
        help=f"Login email (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help="Login password (must include upper, lower, digit, and special character)",
    )
    args = parser.parse_args()
    asyncio.run(_run(email=args.email, password=args.password))


if __name__ == "__main__":
    main()
