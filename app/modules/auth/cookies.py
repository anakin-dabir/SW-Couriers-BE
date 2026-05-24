"""Cookie helpers for auth — refresh token delivery by client type.

Web clients (admin, customer, warehouse) receive the refresh token in an
HttpOnly cookie scoped to the session path (/api/v1/auth/session).
Driver app receives it in the response body instead.
Access tokens are always returned in the response body (never in cookies).

Cross-site: SameSite=None + Secure (frontend and backend on different origins).
Safari requires the Partitioned (CHIPS) attribute for cross-site credentialed requests.
Starlette only accepts partitioned= on set_cookie in Python 3.14+; on 3.12 we append
the Set-Cookie header manually.
"""

from __future__ import annotations

import sys
from typing import Any, Literal

from fastapi import Response

from app.common.enums import ClientType
from app.core.config import settings
from app.modules.auth.v1.schemas import COOKIE_NAMES

CookieSameSite = Literal["lax", "strict", "none"]

# Cookie is only sent to session endpoints (refresh, logout, logout-all).
SESSION_COOKIE_PATH = f"{settings.API_PREFIX}/v1/auth/session"


def _get_cookie_base_attrs() -> tuple[CookieSameSite, str, int, bool]:
    raw = getattr(settings, "COOKIE_SAMESITE", "none").lower()
    samesite: CookieSameSite = raw if raw in ("lax", "strict", "none") else "none"
    max_age = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
    # CHIPS (Partitioned): required for Safari cross-site credentialed fetch when the
    # admin portal (e.g. *.workers.dev) and API (e.g. *.shiftopus.co.uk) are different sites.
    use_partitioned = samesite == "none"
    return samesite, SESSION_COOKIE_PATH, max_age, use_partitioned


def _samesite_header_value(samesite: CookieSameSite) -> str:
    return "None" if samesite == "none" else samesite.capitalize()


def _set_web_refresh_cookie(
    response: Response,
    *,
    key: str,
    value: str,
    max_age: int,
    path: str,
    samesite: CookieSameSite,
    partitioned: bool,
) -> None:
    """Set refresh cookie; use raw Set-Cookie on Python < 3.14 when Partitioned is needed."""
    if partitioned and sys.version_info < (3, 14):
        header = "; ".join(
            [
                f"{key}={value}",
                f"Max-Age={max_age}",
                f"Path={path}",
                "HttpOnly",
                "Secure",
                f"SameSite={_samesite_header_value(samesite)}",
                "Partitioned",
            ]
        )
        response.headers.append("set-cookie", header)
        return

    cookie_kwargs: dict[str, Any] = {
        "key": key,
        "value": value,
        "max_age": max_age,
        "httponly": True,
        "secure": True,
        "samesite": samesite,
        "path": path,
    }
    if partitioned:
        cookie_kwargs["partitioned"] = True
    response.set_cookie(**cookie_kwargs)


def set_refresh_token_cookie(response: Response, client_type: ClientType, token: str) -> None:
    """Set HttpOnly refresh token cookie for web clients. No-op for driver."""
    if client_type == ClientType.DRIVER:
        return
    cookie_name = COOKIE_NAMES.get(client_type)
    if not cookie_name:
        return
    samesite, path, max_age, partitioned = _get_cookie_base_attrs()
    _set_web_refresh_cookie(
        response,
        key=cookie_name,
        value=token,
        max_age=max_age,
        path=path,
        samesite=samesite,
        partitioned=partitioned,
    )


def clear_refresh_token_cookie(response: Response, client_type: ClientType) -> None:
    """Clear the refresh token cookie for the given client type. No-op for driver.

    Attributes must match set_cookie (including partitioned) or browsers keep the cookie.
    """
    if client_type == ClientType.DRIVER:
        return
    cookie_name = COOKIE_NAMES.get(client_type)
    if not cookie_name:
        return
    samesite, path, _, partitioned = _get_cookie_base_attrs()
    _set_web_refresh_cookie(
        response,
        key=cookie_name,
        value="",
        max_age=0,
        path=path,
        samesite=samesite,
        partitioned=partitioned,
    )
