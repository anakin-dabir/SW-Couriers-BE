"""Helpers for session / device list API: UA parsing and optional GeoIP labels."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from typing import Any

from user_agents import parse


def _ua_obj(ua_string: str | None) -> Any:
    return parse(ua_string or "")


def session_device_label(ua_string: str | None) -> str:
    """Human-readable line like 'Chrome on Windows' or 'Safari on iPhone'."""
    if not (ua_string and ua_string.strip()):
        return "Unknown device"
    ua = _ua_obj(ua_string)
    browser = (ua.browser.family or "Browser").strip()
    if browser in ("Mobile Safari",):
        browser = "Safari"
    elif browser.startswith("Chrome Mobile"):
        browser = "Chrome"
    elif browser == "Samsung Internet":
        pass

    if ua.is_mobile or ua.is_tablet:
        dev = (ua.device.family or "").strip()
        if dev and dev != "Other":
            return f"{browser} on {dev}"
        os_name = (ua.os.family or "Mobile").strip()
        return f"{browser} on {os_name}"

    os_name = (ua.os.family or "Unknown OS").strip()
    return f"{browser} on {os_name}"


def session_ua_breakdown(ua_string: str | None) -> tuple[str | None, str | None, str | None, bool, bool, bool]:
    """browser_family, os_family, device_family, is_mobile, is_tablet, is_pc."""
    if not (ua_string and ua_string.strip()):
        return None, None, None, False, False, False
    ua = _ua_obj(ua_string)
    dev = ua.device.family
    device_family = dev if dev and dev != "Other" else None
    return (
        ua.browser.family,
        ua.os.family,
        device_family,
        bool(ua.is_mobile),
        bool(ua.is_tablet),
        bool(ua.is_pc),
    )


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return bool(addr.is_global)


@lru_cache(maxsize=1)
def _geoip_city_reader() -> Any | None:
    from app.core.config import settings

    path = (settings.GEOIP_MAXMIND_CITY_DB_PATH or "").strip()
    if not path or not os.path.isfile(path):
        return None
    import geoip2.database

    return geoip2.database.Reader(path)


def session_ip_location_label(raw_ip: str | None) -> str | None:
    """'City, Country' when GEOIP_MAXMIND_CITY_DB_PATH points to a City .mmdb; else None."""
    if not raw_ip or not raw_ip.strip():
        return None
    ip = raw_ip.strip()
    if not _ip_is_public(ip):
        return None
    reader = _geoip_city_reader()
    if reader is None:
        return None
    try:
        rec = reader.city(ip)
    except Exception:
        return None
    city = rec.city.name
    country = rec.country.name
    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    return None
