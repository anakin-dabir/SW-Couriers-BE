"""Reset the *active* driver T&Cs clause text to the default baseline (see driver_terms_content.py).

Use after ``terms_force_reacceptance.py`` to put the same wording as a fresh ``seed`` / ``--in-place`` default.
After restoring, the content hash matches the default again; drivers who re-accepted the *alternate* text
may still need to accept if their stored hash was for the alternate — run this before testing the
“happy path” with default text, or have drivers accept once more.

Usage:
    python scripts/terms_restore_default.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

import app.models  # noqa: F401
from app.core.database import get_async_session
from app.modules.drivers.repository import DriverTermsAndConditionsRepository
from driver_terms_content import DEFAULT_CLAUSES


async def _run() -> str:
    async with get_async_session() as session:
        repo = DriverTermsAndConditionsRepository(session)
        active = await repo.find_current_active()
        if active is None:
            raise SystemExit(
                "No active driver terms. Create some first, e.g. "
                "python scripts/seed_driver_terms_and_conditions.py"
            )
        await repo.replace_clauses(terms_id=active.id, clauses=DEFAULT_CLAUSES)
        await session.commit()
        return str(active.id)


if __name__ == "__main__":
    tid = asyncio.run(_run())
    print("Active terms clause text restored to default baseline.")
    print(f"TERMS_ID={tid}")
    print(f"CLAUSES={len(DEFAULT_CLAUSES)}")
