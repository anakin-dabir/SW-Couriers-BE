from __future__ import annotations

from dataclasses import dataclass

from app.modules.crew.models import Crew, RouteCrewAssignment


@dataclass(frozen=True, slots=True)
class CrewReassignmentOutcome:
    previous_crew: Crew
    new_crew: Crew


@dataclass(frozen=True, slots=True)
class RouteCrewSwapOutcome:
    previous_assignment: RouteCrewAssignment
    new_assignment: RouteCrewAssignment


@dataclass(frozen=True, slots=True)
class CascadeCloseOutcome:
    crew: Crew | None
    route_assignment: RouteCrewAssignment | None
