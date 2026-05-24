from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from app.modules.audit.enums import AuditCategory, AuditEventType


class AuditLogSummary(BaseModel):
    total_events_24h: int = 0
    total_events_prev_24h_pct: float = 0.0
    critical_events_7d: int = 0
    critical_events_latest: str | None = None
    warning_events_7d: int = 0
    warning_events_top_category: str | None = None
    data_access_events_7d: int = 0
    data_access_unique_admins: int = 0
    configuration_changes_7d: int = 0
    configuration_changes_latest: str | None = None
    unique_actors_30d: int = 0
    unique_actors_count: int = 0


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    os: str | None = None
    email: str | None = None
    actor: str | None = None
    category: AuditCategory
    event_type: AuditEventType
    severity: str
    audit_ref: str | None = None
    entity_ref: str | None = None
    browser: str | None = None
    device: str | None = None
    event: str
    action_label: str = "View"
    entity_type: str
    entity_id: str | None
    ip_address: str | None
    display_category: str | None = None
    resource: str | None = None
    duration: str | None = None


class AuditLogDetail(AuditLogEntry):
    """Full audit log payload for the per-row details panel.

    Adds the columns intentionally hidden from the list endpoint (raw user agent,
    before/after JSON, session linkage, integrity chain).
    """

    action: str
    reason: str | None = None
    user_agent: str | None = None
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    user_id: str | None = None
    organization_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    integrity_hash: str | None = None
    prev_hash: str | None = None


class RelatedAuditEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    audit_ref: str | None = None
    created_at: datetime
    event_type: str | None = None
    severity: str
    event: str
    actor: str | None = None
    email: str | None = None


class IntegrityVerification(BaseModel):
    id: str
    audit_ref: str | None = None
    ok: bool
    expected: str | None = None
    found: str | None = None
    reason: str | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    size: int


class ActivityTrendPoint(BaseModel):
    date: str  # e.g. "Feb 8"
    info: int = 0
    notice: int = 0
    warning: int = 0
    critical: int = 0


class ActivityTrendResponse(BaseModel):
    points: list[ActivityTrendPoint]


class SavedViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    filters: dict[str, Any]
    is_default: bool = False


class SavedViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    filters: dict[str, Any]
    is_default: bool
    created_at: datetime


class DataAccessSummaryEntry(BaseModel):
    admin: str
    email: str
    events: int
    last_access: datetime | None


class DataAccessSummaryResponse(BaseModel):
    items: list[DataAccessSummaryEntry]


class DataAccessHeatmapEntry(BaseModel):
    day: int
    hour: int
    count: int


class DataAccessHeatmapResponse(BaseModel):
    items: list[DataAccessHeatmapEntry]


class ChangeField(BaseModel):
    field: str
    before: Any
    after: Any


class ChangeHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    category: str
    entity_type: str
    entity_ref: str | None
    action: str
    email: str | None
    actor: str | None
    fields_changed: int = 0
    summary: str | None
    changes: list[ChangeField] = []


class ChangeHistoryResponse(BaseModel):
    items: list[ChangeHistoryEntry]
    total: int
    page: int
    size: int


class FieldHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    timestamp: datetime
    before: Any
    after: Any
    actor: str | None
    reason: str | None
    event_type: str | None = None
    email: str | None = None


class FieldHistoryPoint(BaseModel):
    """One bucket on the Field History Analysis chart (monthly numeric trend)."""
    date: str  # e.g. "Feb 2026"
    value: float | None = None


class FieldHistoryResponse(BaseModel):
    items: list[FieldHistoryEntry]
    total: int = 0
    page: int = 1
    size: int = 50
    points: list[FieldHistoryPoint] = []


class ComparisonRequest(BaseModel):
    snapshot_a: datetime
    snapshot_b: datetime
    fields: list[str] | None = None


class ComparisonResultEntry(BaseModel):
    field: str
    value_a: Any
    value_b: Any
    changes: int


class ComparisonResponse(BaseModel):
    items: list[ComparisonResultEntry]
