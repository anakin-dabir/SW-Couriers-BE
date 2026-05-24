"""Pure merge logic for GLOBAL + ORG service tiers (unit-testable, no I/O).

Used by ``ServiceTierService.get_effective_tiers_with_source_for_org``. Callers must
pass rows already restricted to ACTIVE tiers for the intended scope.
"""

from __future__ import annotations

from typing import Any

from app.modules.service_tiers.enums import ServiceTierScopeType


def merge_effective_service_tiers(global_rows: list[Any], org_rows: list[Any]) -> list[dict[str, Any]]:
    """Return resolved tier rows with metadata for an organisation.

    - Every ACTIVE GLOBAL tier appears unless an ORG row exists with the same
      ``(tier_name, available_for)``, in which case the ORG row wins
      (``is_override`` True, ``global_tier_id`` set to the shadowed global id).
    - ACTIVE ORG-only tiers (no matching global key) are appended.
    """
    org_by_key: dict[tuple[str, str], Any] = {(o.tier_name, o.available_for): o for o in org_rows}
    global_keys: set[tuple[str, str]] = {(g.tier_name, g.available_for) for g in global_rows}

    resolved: list[dict[str, Any]] = []
    for g in global_rows:
        key = (g.tier_name, g.available_for)
        if key in org_by_key:
            o = org_by_key[key]
            resolved.append(
                {
                    "tier": o,
                    "is_override": True,
                    "source_scope_type": ServiceTierScopeType.ORG.value,
                    "source_tier_id": o.id,
                    "global_tier_id": g.id,
                }
            )
        else:
            resolved.append(
                {
                    "tier": g,
                    "is_override": False,
                    "source_scope_type": ServiceTierScopeType.GLOBAL.value,
                    "source_tier_id": g.id,
                    "global_tier_id": g.id,
                }
            )

    for o in org_rows:
        key = (o.tier_name, o.available_for)
        if key not in global_keys:
            resolved.append(
                {
                    "tier": o,
                    "is_override": False,
                    "source_scope_type": ServiceTierScopeType.ORG.value,
                    "source_tier_id": o.id,
                    "global_tier_id": None,
                }
            )

    return resolved
