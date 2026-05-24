"""Shared dependency: invite-style one-time tokens sent as `X-Invite-Token` (never in URL on API requests)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

InviteTokenDep = Annotated[
    str,
    Header(
        alias="X-Invite-Token",
        min_length=40,
        max_length=256,
        description=(
            "One-time token from the email or deep link (?token= on the landing URL). "
            "Send on `/auth/invites/*` and `/auth/driver-activation/validate|set-password` — not as a query string on the API."
        ),
    ),
]
