"""Orchestrator: seed all FE demo data (driver schedules + order scenarios).

Prerequisites on server:
  1. ``demo_data.py`` — depot + drivers Ryan / Fatima.
  2. Billing org + ``CUSTOMER_B2B`` user with invoices already seeded (do **not** re-run
     billing seed unless you intend to reset ``INV-FEBD-*`` demo invoices).

Usage:
  poetry run python scripts/seed_fe_demo_suite.py seed
  poetry run python scripts/seed_fe_demo_suite.py clear

Individual scripts:
  scripts/seed_fe_driver_schedules.py   — Ryan pickups + Fatima deliveries (3 days)
  scripts/seed_fe_order_scenarios.py    — drafts, failed, returned, lifecycle orders
"""

from __future__ import annotations

import argparse
import asyncio

from scripts.fe_demo_lib import run_purge_fe_demo_data
from scripts.seed_fe_driver_schedules import seed_driver_schedules
from scripts.seed_fe_order_scenarios import seed_scenarios


async def seed_all() -> None:
    await seed_driver_schedules()
    await seed_scenarios()


async def clear_all() -> None:
    await run_purge_fe_demo_data()
    print("Cleared all FE demo suite data (routes, orders, drafts on billing + driver orgs).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear full FE demo dataset.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Seed driver schedules then order scenarios")
    sub.add_parser("clear", help="Clear all FE demo tagged data")
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed_all())
    else:
        asyncio.run(clear_all())


if __name__ == "__main__":
    main()
