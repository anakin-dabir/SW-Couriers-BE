"""Seed driver terms and conditions for onboarding consent flow.

Usage:
    python scripts/seed_driver_terms_and_conditions.py
    python scripts/seed_driver_terms_and_conditions.py --title "SW Couriers Driver Terms v1"

    # Refresh clause text on the *current active* document only (same id, new content hash):
    python scripts/seed_driver_terms_and_conditions.py --in-place

    # Default behavior creates a *new* active version (previous actives are deactivated),
    # which is the same idea as POST /v1/drivers/terms-and-conditions/config with is_active true.

See also: terms_restore_default.py, terms_force_reacceptance.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.modules.drivers.models import DriverTermsAndConditions
from app.modules.drivers.repository import DriverTermsAndConditionsRepository
from driver_terms_content import DEFAULT_CLAUSES, DEFAULT_TITLE


async def _seed_new_version(title: str) -> str:
    async with get_async_session() as session:
        repo = DriverTermsAndConditionsRepository(session)

        # One active version so self-onboarding uses this row.
        await repo.deactivate_all()

        terms = DriverTermsAndConditions(
            title=title,
            is_active=True,
            effective_from=datetime.now(UTC),
        )
        session.add(terms)
        await session.flush()

        await repo.replace_clauses(terms_id=terms.id, clauses=DEFAULT_CLAUSES)
        await session.commit()
        return str(terms.id)


async def _replace_active_clauses_only() -> str:
    async with get_async_session() as session:
        repo = DriverTermsAndConditionsRepository(session)
        active = await repo.find_current_active()
        if active is None:
            raise SystemExit(
                "No active driver terms in the database. Run without --in-place to create a new active version."
            )
        await repo.replace_clauses(terms_id=active.id, clauses=DEFAULT_CLAUSES)
        await session.commit()
        return str(active.id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed or refresh driver T&Cs. By default, creates a new active version (deactivates others). "
            "Use --in-place to only replace clauses on the current active document."
        )
    )
    parser.add_argument(
        "--title",
        dest="title",
        type=str,
        default=DEFAULT_TITLE,
        help="Title for a new active version (ignored with --in-place, which does not change the title).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Replace clause text on the current active terms row only (title unchanged; use admin API to rename).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.in_place:
        terms_id = asyncio.run(_replace_active_clauses_only())
        op = "Refreshed clauses in place on active terms."
    else:
        terms_id = asyncio.run(_seed_new_version(title=args.title))
        op = "New active driver terms version created (previous actives deactivated)."

    print(op)
    print(f"TERMS_ID={terms_id}")
    if not args.in_place:
        print(f"TITLE={args.title}")
    print(f"CLAUSES={len(DEFAULT_CLAUSES)}")
