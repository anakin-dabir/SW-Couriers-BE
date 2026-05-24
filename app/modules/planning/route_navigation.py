"""Route navigation helpers (polyline fingerprint, invalidation).

**Who writes the polyline:** not this module. After route build or whenever ``route_stops``
change order or membership, the planning pipeline or an **async job** should (1) call the
directions provider with waypoints from ordered stops, (2) persist ``Route.navigation_encoded_polyline``
and ``navigation_meta``, and (3) set ``navigation_fingerprint`` to the value from
``compute_route_navigation_fingerprint`` for that same stop list.
"""

from __future__ import annotations

import hashlib


def compute_route_navigation_fingerprint(*, sequences_and_route_stop_ids: list[tuple[int, str]]) -> str:
    """Stable SHA-256 over ordered route stops: ``sequence:route_stop_id`` pairs.

    Callers should pass ``(route_stop.sequence, route_stop.id)`` for every stop on
    the route, in any order; pairs are sorted by ``sequence`` before hashing.

    **Persist** this string on ``Route.navigation_fingerprint`` whenever navigation is recomputed
    so drive-mode reads can treat a mismatched fingerprint as stale (hidden polyline).
    """
    ordered = sorted(sequences_and_route_stop_ids, key=lambda x: x[0])
    payload = ",".join(f"{seq}:{rsid}" for seq, rsid in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
