"""Assign depot-local **today's PICKUP** route with a RETURN stop.

Usage::

  poetry run python scripts/assign_today_pickup_with_return.py seed --driver-email ryan.obrien@swcouriers.co.uk
  poetry run python scripts/assign_today_pickup_with_return.py clear

Standalone: creates depot/driver/vehicle on first run if missing. Default password: ``Driver@12345!``
"""

from __future__ import annotations

from scripts.driver_route_assign import AssignScenarioKey, main_for_scenario

if __name__ == "__main__":
    main_for_scenario(
        scenario_key=AssignScenarioKey.PICKUP_RETURN,
        description="Assign today's PICKUP route including one RETURN stop.",
        title="Today's PICKUP + RETURN route assigned.",
    )
