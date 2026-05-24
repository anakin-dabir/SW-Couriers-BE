"""Pydantic schemas for status automation rules v1 API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.status_automation_rules.enums import EntityType, StatusAutomationRuleStatus, StatusAutomationScopeType, TimingValue

_PACKAGE_STATUS = {s.value for s in PackageStatus}
_STOP_STATUS = {s.value for s in DeliveryStopStatus}
_ORDER_STATUS = {s.value for s in OrderStatus}


def _status_for_entity(entity: EntityType, value: str) -> bool:
    if entity == EntityType.PACKAGE:
        return value in _PACKAGE_STATUS
    if entity == EntityType.DELIVERY_STOP:
        return value in _STOP_STATUS
    return value in _ORDER_STATUS


class StatusAutomationTriggerInput(BaseSchema):
    entity_type: EntityType
    status: str = Field(..., min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_status(self) -> "StatusAutomationTriggerInput":
        if not _status_for_entity(self.entity_type, self.status):
            raise ValueError("Trigger status is invalid for selected entity type.")
        return self


class StatusAutomationConditionInput(BaseSchema):
    value: TimingValue


class StatusAutomationActionInput(BaseSchema):
    new_status: str = Field(..., min_length=1, max_length=64)


class StatusAutomationRuleSetBase(BaseSchema):
    name: str = Field(..., min_length=1, max_length=255)
    scope_type: StatusAutomationScopeType
    scope_org_id: str | None = None
    status: StatusAutomationRuleStatus = StatusAutomationRuleStatus.ACTIVE
    priority: int = Field(default=100, ge=0, le=1000)
    notes: str | None = None
    trigger: StatusAutomationTriggerInput
    conditions: list[StatusAutomationConditionInput] = Field(default_factory=list, max_length=1)
    actions: list[StatusAutomationActionInput] = Field(..., min_length=1, max_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "StatusAutomationRuleSetBase":
        if self.scope_type == StatusAutomationScopeType.ORG and not self.scope_org_id:
            raise ValueError("scope_org_id is required for ORG rules.")
        if self.scope_type == StatusAutomationScopeType.GLOBAL and self.scope_org_id is not None:
            raise ValueError("scope_org_id must be null for GLOBAL rules.")

        if self.trigger.status == "CANCELLED" and not self.conditions:
            raise ValueError("Timing is required when IF status is CANCELLED.")
        if self.trigger.status != "CANCELLED" and self.conditions:
            raise ValueError("Timing is only allowed when IF status is CANCELLED.")
        if not _status_for_entity(self.trigger.entity_type, self.actions[0].new_status):
            raise ValueError("New status is invalid for selected entity type.")
        return self


class StatusAutomationRuleSetCreateRequest(StatusAutomationRuleSetBase):
    pass


class StatusAutomationRuleSetUpdateRequest(BaseSchema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: StatusAutomationRuleStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    notes: str | None = None
    trigger: StatusAutomationTriggerInput | None = None
    conditions: list[StatusAutomationConditionInput] | None = Field(default=None, max_length=1)
    actions: list[StatusAutomationActionInput] | None = Field(default=None, min_length=1, max_length=1)
    version: int | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "StatusAutomationRuleSetUpdateRequest":
        if self.trigger is not None and self.conditions is not None:
            if self.trigger.status == "CANCELLED" and not self.conditions:
                raise ValueError("Timing is required when IF status is CANCELLED.")
            if self.trigger.status != "CANCELLED" and self.conditions:
                raise ValueError("Timing is only allowed when IF status is CANCELLED.")
        if self.trigger is not None and self.actions:
            if not _status_for_entity(self.trigger.entity_type, self.actions[0].new_status):
                raise ValueError("New status is invalid for selected entity type.")
        return self


class StatusAutomationCustomiseGlobalRequest(BaseSchema):
    body: StatusAutomationRuleSetUpdateRequest


class StatusAutomationStatusUpdateRequest(BaseSchema):
    status: StatusAutomationRuleStatus
    version: int | None = None


class StatusAutomationRestoreDefaultRequest(BaseSchema):
    version: int | None = None


class StatusAutomationActionResponse(BaseSchema):
    new_status: str


class StatusAutomationConditionResponse(BaseSchema):
    value: TimingValue


class StatusAutomationTriggerResponse(BaseSchema):
    entity_type: EntityType
    status: str


class StatusAutomationRuleSetResponse(BaseSchema):
    id: str
    name: str
    scope_type: StatusAutomationScopeType
    scope_org_id: str | None
    status: StatusAutomationRuleStatus
    priority: int
    notes: str | None
    trigger: StatusAutomationTriggerResponse
    conditions: list[StatusAutomationConditionResponse]
    actions: list[StatusAutomationActionResponse]
    created_at: datetime
    updated_at: datetime
    version: int
    rule_kind: Literal["DEFAULT", "CUSTOMISED", "NEW"]
    global_rule_set_id: str | None = None
    is_effective_for_org: bool = False
    can_restore_default: bool = False
    applies_to_label: str | None = None
    trigger_summary: str | None = None
    conditions_summary: str | None = None
    actions_summary: str | None = None
    can_edit: bool = True
    can_delete: bool = False
    can_toggle_status: bool = True


class StatusAutomationRuleSetListResponse(BaseSchema):
    items: list[StatusAutomationRuleSetResponse]
    total: int

