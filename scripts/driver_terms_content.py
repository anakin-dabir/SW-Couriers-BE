"""Shared clause text for driver T&Cs (seed + dev scripts).

`DEFAULT_CLAUSES` is the normal “baseline” text. `ALTERNATE_CLAUSES_FORCE_REACCEPTANCE`
is intentionally different so the content hash changes and `requires_terms_reacceptance`
becomes true for drivers who already accepted the default.
"""

from __future__ import annotations

from copy import deepcopy

DEFAULT_TITLE = "SW Couriers Terms and Conditions"

DEFAULT_CLAUSES: list[dict[str, object]] = [
    {
        "clause_order": 1,
        "heading": "Acceptance of Terms",
        "body": (
            "By accessing and using this application, you acknowledge that you have read, understood, "
            "and agreed to be bound by these Terms and Conditions."
        ),
    },
    {
        "clause_order": 2,
        "heading": "Use of Service",
        "body": (
            "You agree to use the SW Courier application solely for lawful and authorized purposes. "
            "You must not use the app in any way that violates any federal, state, local, international regulations or laws."
        ),
    },
    {
        "clause_order": 3,
        "heading": "Location and privacy",
        "body": (
            "This application requires permission to access your device's location to ensure precise delivery "
            "tracking and optimized routes. Your location data is used only for operational needs and managed "
            "according to our Privacy Policy."
        ),
    },
    {
        "clause_order": 4,
        "heading": "Driver responsibilities",
        "body": (
            "Drivers are responsible for holding valid driving credentials, adhering to traffic regulations, "
            "maintaining vehicle safety standards, and completing all assigned deliveries efficiently."
        ),
    },
    {
        "clause_order": 5,
        "heading": "Cancellations and pickups",
        "body": (
            "Follow the in-app and depot instructions for order cancellations, failed pickups, and exceptions. "
            "Repeated failure to follow process may affect your account standing."
        ),
    },
]


# Same structure, different first-clause text → different SHA-256 content hash.
ALTERNATE_CLAUSES_FORCE_REACCEPTANCE: list[dict[str, object]] = deepcopy(DEFAULT_CLAUSES)
ALTERNATE_CLAUSES_FORCE_REACCEPTANCE[0] = {
    "clause_order": 1,
    "heading": "Acceptance of Terms",
    "body": (
        "By accessing and using this application, you acknowledge that you have read, understood, "
        "and agreed to be bound by these Terms and Conditions. "
        "[Dev/test: this paragraph was updated; you must accept again.]"
    ),
}
