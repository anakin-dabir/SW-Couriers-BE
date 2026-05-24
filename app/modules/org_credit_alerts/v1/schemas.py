from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.common.schemas import BaseSchema, PaginationParams, UserSchema
from app.modules.org_credit_alerts.enums import (
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertSeverity,
    CreditAlertSnoozeDuration,
    CreditAlertStatus,
    CreditAlertType,
)


class AlertConfigValues(BaseSchema):
    enabled: bool
    threshold_pct: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    score_drop_points: int | None = Field(default=None, ge=0, le=1000)
    reminder_days: int | None = Field(default=None, ge=0, le=365)
    late_payment_count: int | None = Field(default=None, ge=0, le=1000)
    cooldown_period: CreditAlertCooldownPeriod
    delivery_channel: CreditAlertDeliveryChannel
    auto_acknowledge: bool


class AlertConfigItem(AlertConfigValues):
    alert_type: CreditAlertType


class AlertConfigPatchRequest(AlertConfigValues):
    """Editable values for one alert type; the alert type comes from the path."""


class AlertConfigResponseItem(BaseSchema):
    alert_type: CreditAlertType
    enabled: bool
    threshold_pct: str | None = None
    score_drop_points: int | None = None
    reminder_days: int | None = None
    late_payment_count: int | None = None
    cooldown_period: CreditAlertCooldownPeriod
    delivery_channel: CreditAlertDeliveryChannel
    auto_acknowledge: bool


class AlertConfigListResponse(BaseSchema):
    items: list[AlertConfigResponseItem]


class AlertConfigUpdateRequest(BaseSchema):
    items: list[AlertConfigItem] = Field(min_length=1, max_length=len(CreditAlertType))

    @field_validator("items")
    @classmethod
    def _unique_types(cls, v: list[AlertConfigItem]) -> list[AlertConfigItem]:
        seen: set[CreditAlertType] = set()
        for item in v:
            if item.alert_type in seen:
                raise ValueError(f"Duplicate alert_type '{item.alert_type.value}' in request.")
            seen.add(item.alert_type)
        return v


class AlertSummaryResponse(BaseSchema):
    active_alerts_count: int
    unacknowledged_alerts_count: int
    last_alert_triggered_at: datetime | None = None


class AlertItem(BaseSchema):
    id: str
    organization_id: str
    alert_type: CreditAlertType
    severity: CreditAlertSeverity
    status: CreditAlertStatus
    title: str
    summary: str
    context: dict[str, Any] | None = None
    triggered_at: datetime
    snoozed_until: datetime | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: UserSchema | None = None
    resolution_notes: str | None = None
    resolved_at: datetime | None = None


class AlertAcknowledgeRequest(BaseSchema):
    resolution_notes: str | None = Field(default=None, max_length=500)


class AlertSnoozeRequest(BaseSchema):
    duration: CreditAlertSnoozeDuration


class GlobalThresholdItem(BaseSchema):
    alert_type: CreditAlertType
    threshold_pct: Decimal = Field(gt=0, le=100, decimal_places=2)


class GlobalThresholdResponse(BaseSchema):
    alert_type: CreditAlertType
    threshold_pct: str


class GlobalThresholdListResponse(BaseSchema):
    items: list[GlobalThresholdResponse]


class GlobalThresholdUpdateRequest(BaseSchema):
    items: list[GlobalThresholdItem] = Field(min_length=1)


class AlertHistoryParams(PaginationParams):
    statuses: list[CreditAlertStatus] | None = Field(default=None, description="Filter by one or more statuses.")
    alert_types: list[CreditAlertType] | None = Field(default=None, description="Filter by one or more alert types.")

    @model_validator(mode="after")
    def _strip_empty_lists(self) -> AlertHistoryParams:
        if self.statuses is not None and len(self.statuses) == 0:
            self.statuses = None
        if self.alert_types is not None and len(self.alert_types) == 0:
            self.alert_types = None
        return self
