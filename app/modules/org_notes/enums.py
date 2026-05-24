"""Enums for the org_notes module."""

from __future__ import annotations

import enum


class NoteCategory(str, enum.Enum):
    GENERAL = "GENERAL"
    MEETING_NOTES = "MEETING_NOTES"
    PHONE_CALL = "PHONE_CALL"
    ESCALATION = "ESCALATION"
    COMPLIANCE = "COMPLIANCE"
    COMMERCIAL = "COMMERCIAL"
