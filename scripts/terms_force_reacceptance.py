"""Replace the *active* driver T&Cs with alternate clause text so the content hash changes.

Drivers who already accepted the previous text will see ``requires_terms_reacceptance: true``
until they POST …/onboarding-consents again.

Usage:
    python scripts/terms_force_reacceptance.py

Requires active terms; run ``seed_driver_terms_and_conditions`` first if none exist.
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
from driver_terms_content import ALTERNATE_CLAUSES_FORCE_REACCEPTANCE


async def _run() -> str:
    async with get_async_session() as session:
        repo = DriverTermsAndConditionsRepository(session)
        active = await repo.find_current_active()
        if active is None:
            raise SystemExit(
                "No active driver terms. Create some first, e.g. "
                "python scripts/seed_driver_terms_and_conditions.py"
            )
        await repo.replace_clauses(terms_id=active.id, clauses=ALTERNATE_CLAUSES_FORCE_REACCEPTANCE)
        await session.commit()
        return str(active.id)


if __name__ == "__main__":
    tid = asyncio.run(_run())
    print("Active terms updated with alternate clause text (content hash changed).")
    print(f"TERMS_ID={tid}")
    print("Drivers with a prior acceptance should see requires_terms_reacceptance until they re-accept.")
