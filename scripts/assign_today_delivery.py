"""Assign depot-local **today's DELIVERY** route to a driver (no RETURN stop).

Usage::

  poetry run python scripts/assign_today_delivery.py seed --driver-email ryan.obrien@swcouriers.co.uk
  poetry run python scripts/assign_today_delivery.py clear

Standalone: creates depot/driver/vehicle on first run if missing. Default password: ``Driver@12345!``
"""

from __future__ import annotations

from scripts.driver_route_assign import AssignScenarioKey, main_for_scenario

if __name__ == "__main__":
    main_for_scenario(
        scenario_key=AssignScenarioKey.DELIVERY,
        description="Assign today's DELIVERY route to a driver.",
        title="Today's DELIVERY route assigned.",
    )
