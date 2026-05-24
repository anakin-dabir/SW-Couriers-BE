"""Scope for document step-up OTP / access tokens (org vs driver compliance docs)."""

from __future__ import annotations

from enum import StrEnum


class DocAccessScope(StrEnum):
    ORG_DOCUMENTS = "ORG_DOCUMENTS"
    DRIVER_DOCUMENTS = "DRIVER_DOCUMENTS"
    VEHICLE_DOCUMENTS = "VEHICLE_DOCUMENTS"
