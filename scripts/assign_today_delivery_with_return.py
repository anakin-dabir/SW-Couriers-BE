"""Assign depot-local **today's DELIVERY** route with a RETURN stop.

Usage::

  poetry run python scripts/assign_today_delivery_with_return.py seed --driver-email ryan.obrien@swcouriers.co.uk
  poetry run python scripts/assign_today_delivery_with_return.py clear

Standalone: creates depot/driver/vehicle on first run if missing. Default password: ``Driver@12345!``
"""

from __future__ import annotations

from scripts.driver_route_assign import AssignScenarioKey, main_for_scenario

if __name__ == "__main__":
    main_for_scenario(
        scenario_key=AssignScenarioKey.DELIVERY_RETURN,
        description="Assign today's DELIVERY route including one RETURN stop.",
        title="Today's DELIVERY + RETURN route assigned.",
    )
