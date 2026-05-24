"""Shared validation helpers."""

import re
from uuid import UUID

from app.common.exceptions import ValidationError

# Canonical UUID string (RFC 4122); used for Python checks and PostgreSQL ~* joins.
UUID_REGEX_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_UUID_REGEX = re.compile(UUID_REGEX_PATTERN, re.IGNORECASE)

_PASSWORD_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9]).+$")

PASSWORD_STRENGTH_MESSAGE = "Password must contain at least one uppercase letter, " "one lowercase letter, one digit, and one special character"


def is_uuid_string(value: str | None) -> bool:
    """Return True when value is a non-empty RFC 4122 UUID string."""
    if value is None:
        return False
    stripped = str(value).strip()
    if not stripped or not _UUID_REGEX.match(stripped):
        return False
    try:
        UUID(stripped)
    except ValueError:
        return False
    return True


def normalize_optional_uuid(value: object | None, *, field: str) -> str | None:
    """Strip and validate optional UUID fields; blank strings become None."""
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    if not is_uuid_string(stripped):
        raise ValueError(f"{field} must be a valid UUID")
    return stripped


def validate_password_strength(value: str) -> str:
    """Enforce strong passwords: upper, lower, digit, special char.

    Raises:
        ValueError: If password does not meet complexity requirements.
    """
    if not _PASSWORD_PATTERN.match(value):
        raise ValueError(PASSWORD_STRENGTH_MESSAGE)
    return value


def validate_files_metadata_match(
    files: list | None,
    metadata: list | None,
    *,
    files_label: str = "files",
    metadata_label: str = "metadata",
) -> None:
    """Ensure uploaded files and their metadata arrays are consistent.

    Call before any DB writes so partial state is never persisted.
    """
    if not files:
        return
    if metadata is None:
        raise ValidationError(f"{metadata_label} is required when {files_label} are provided")
    if len(metadata) != len(files):
        raise ValidationError(
            f"{metadata_label} length ({len(metadata)}) must match {files_label} count ({len(files)})"
        )
