"""Pydantic schemas for Suspension Rules v1 API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema
from app.modules.suspension_rules.enums import (
    RuleScopeType,
    SuspensionActionTaken,
    SuspensionConditionType,
    SuspensionConnector,
    SuspensionLogic,
    SuspensionPrimaryTrigger,
    SuspensionRuleStatus,
    SuspensionRuleType,
    SuspensionType,
)


class AdditionalCondition(BaseSchema):
    metric: Literal[
        "overdue_days",
        "overdue_amount",
        "credit_utilisation_percent",
        "credit_not_cleared_after_clearing_date",
    ]
    operator: Literal[">", ">=", "<", "<=", "==", "!="]
    threshold: float


class AdditionalConditionsConfig(BaseSchema):
    conditions: list[AdditionalCondition]


class SuspensionRuleBase(BaseSchema):
    name: str = Field(..., max_length=255)
    condition_summary: str = Field(..., max_length=255)
    logic: SuspensionLogic = Field(..., description="How additional conditions are evaluated with the primary trigger.")
    status: SuspensionRuleStatus = Field(..., description="Whether this rule is currently active.")
    notes: str | None = Field(default=None, description="Optional notes for admins.")

    primary_trigger: SuspensionPrimaryTrigger

    overdue_days_threshold: int | None = Field(default=None, ge=0)
    overdue_amount_threshold: float | None = Field(default=None, ge=0)
    credit_utilisation_threshold: float | None = Field(default=None, ge=0, le=100)

    additional_conditions: AdditionalConditionsConfig | None = Field(
        default=None,
        description="Optional additional conditions structure. Interpreted by the rule engine.",
    )

    suspension_type: SuspensionType
    grace_period_days: int | None = Field(
        default=None,
        ge=1,
        description="Required when suspension_type is AFTER_GRACE_PERIOD.",
    )

    notify_finance_team: bool = False
    send_warning_to_user: bool = False

    @model_validator(mode="after")
    def _validate_trigger_thresholds(self) -> "SuspensionRuleBase":
        # Enforce that the appropriate thresholds are set for the primary trigger.
        if (
            self.primary_trigger
            in (
                SuspensionPrimaryTrigger.OVERDUE_DAYS,
                SuspensionPrimaryTrigger.OVERDUE_DAYS_AND_AMOUNT,
            )
            and self.overdue_days_threshold is None
        ):
            raise ValueError("overdue_days_threshold is required for this primary_trigger")

        if (
            self.primary_trigger
            in (
                SuspensionPrimaryTrigger.OVERDUE_AMOUNT,
                SuspensionPrimaryTrigger.OVERDUE_DAYS_AND_AMOUNT,
            )
            and self.overdue_amount_threshold is None
        ):
            raise ValueError("overdue_amount_threshold is required for this primary_trigger")

        if self.primary_trigger == SuspensionPrimaryTrigger.CREDIT_UTILISATION_PERCENT and self.credit_utilisation_threshold is None:
            raise ValueError("credit_utilisation_threshold is required for this primary_trigger")

        if self.suspension_type == SuspensionType.AFTER_GRACE_PERIOD and self.grace_period_days is None:
            raise ValueError("grace_period_days is required when suspension_type is AFTER_GRACE_PERIOD")

        return self


class SuspensionRuleCreateRequest(SuspensionRuleBase):
    """Create a new suspension rule."""

    pass


class SuspensionRuleUpdateRequest(BaseSchema):
    """Partial update for an existing suspension rule."""

    name: str | None = Field(default=None, max_length=255)
    condition_summary: str | None = Field(default=None, max_length=255)
    logic: SuspensionLogic | None = None
    status: SuspensionRuleStatus | None = None
    notes: str | None = None

    primary_trigger: SuspensionPrimaryTrigger | None = None

    overdue_days_threshold: int | None = Field(default=None, ge=0)
    overdue_amount_threshold: float | None = Field(default=None, ge=0)
    credit_utilisation_threshold: float | None = Field(default=None, ge=0, le=100)

    additional_conditions: AdditionalConditionsConfig | None = None

    suspension_type: SuspensionType | None = None
    grace_period_days: int | None = Field(default=None, ge=1)

    notify_finance_team: bool | None = None
    send_warning_to_user: bool | None = None
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking. If omitted, update is non-atomic.",
    )


class SuspensionRuleResponse(SuspensionRuleBase):
    id: str
    created_at: datetime
    updated_at: datetime
    version: int


class SuspensionRuleListResponse(BaseSchema):
    items: list[SuspensionRuleResponse]
    total: int


class SuspensionActivityResponse(BaseSchema):
    id: str
    timestamp: datetime
    rule_set_id: str
    rule_id: str | None = Field(default=None, description="Deprecated legacy alias for rule_set_id.")
    rule_name: str
    account_id: str
    conditions_met: dict[str, Any]
    action_taken: SuspensionActionTaken
    notes: str | None = None


class SuspensionActivityListResponse(BaseSchema):
    items: list[SuspensionActivityResponse]
    total: int


class SuspensionRuleConditionV2(BaseSchema):
    position: int = Field(..., ge=1)
    connector: SuspensionConnector | None = Field(default=None, description="Null for first row; AND/OR otherwise.")
    condition_type: SuspensionConditionType
    threshold_value: float = Field(..., ge=0)
    unit: str | None = None


class SuspensionRuleSetCreateRequest(BaseSchema):
    name: str = Field(..., max_length=255)
    condition_summary: str | None = Field(default=None, max_length=255)
    scope_type: RuleScopeType
    scope_org_id: str | None = None
    rule_type: SuspensionRuleType
    status: SuspensionRuleStatus = SuspensionRuleStatus.ACTIVE
    notes: str | None = None
    auto_suspension_enabled: bool = False
    pause_new_bookings: bool = False
    restrict_portal_login: bool = False
    notify_finance_team: bool = False
    notify_account_manager: bool = False
    conditions: list[SuspensionRuleConditionV2] = Field(
        ...,
        description=(
            "Ordered condition rows. Evaluation precedence is AND before OR, derived from row order and connectors. "
            "Nested parenthesis/grouping is not supported."
        ),
    )

    @model_validator(mode="after")
    def validate_conditions(self) -> "SuspensionRuleSetCreateRequest":
        if self.scope_type == RuleScopeType.ORG and not self.scope_org_id:
            raise ValueError("scope_org_id is required when scope_type=ORG")
        if self.scope_type == RuleScopeType.GLOBAL and self.scope_org_id is not None:
            raise ValueError("scope_org_id must be null for GLOBAL rules")
        positions = [c.position for c in self.conditions]
        if sorted(positions) != list(range(1, len(self.conditions) + 1)):
            raise ValueError("conditions positions must be sequential starting from 1")
        if len({c.condition_type for c in self.conditions}) != len(self.conditions):
            raise ValueError("Each condition_type can only appear once per rule")
        for cond in self.conditions:
            if cond.position == 1 and cond.connector not in (None, SuspensionConnector.NONE):
                raise ValueError("First condition connector must be null/NONE")
            if cond.position > 1 and cond.connector not in (SuspensionConnector.AND, SuspensionConnector.OR):
                raise ValueError("Connector for subsequent conditions must be AND or OR")
        return self


class SuspensionRuleSetUpdateRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=255)
    condition_summary: str | None = Field(default=None, max_length=255)
    status: SuspensionRuleStatus | None = None
    notes: str | None = None
    auto_suspension_enabled: bool | None = None
    pause_new_bookings: bool | None = None
    restrict_portal_login: bool | None = None
    notify_finance_team: bool | None = None
    notify_account_manager: bool | None = None
    conditions: list[SuspensionRuleConditionV2] | None = Field(
        default=None,
        description=(
            "Optional full replacement of condition rows. Row order controls evaluation precedence "
            "(AND before OR; no nested grouping)."
        ),
    )
    version: int | None = None

    @model_validator(mode="after")
    def validate_conditions(self) -> "SuspensionRuleSetUpdateRequest":
        if self.conditions is None:
            return self
        positions = [c.position for c in self.conditions]
        if sorted(positions) != list(range(1, len(self.conditions) + 1)):
            raise ValueError("conditions positions must be sequential starting from 1")
        if len({c.condition_type for c in self.conditions}) != len(self.conditions):
            raise ValueError("Each condition_type can only appear once per rule")
        for cond in self.conditions:
            if cond.position == 1 and cond.connector not in (None, SuspensionConnector.NONE):
                raise ValueError("First condition connector must be null/NONE")
            if cond.position > 1 and cond.connector not in (SuspensionConnector.AND, SuspensionConnector.OR):
                raise ValueError("Connector for subsequent conditions must be AND or OR")
        return self


class OrgRuleOverrideUpsertRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=255)
    condition_summary: str | None = Field(default=None, max_length=255)
    status: SuspensionRuleStatus | None = None
    notes: str | None = None
    auto_suspension_enabled: bool | None = None
    pause_new_bookings: bool | None = None
    restrict_portal_login: bool | None = None
    notify_finance_team: bool | None = None
    notify_account_manager: bool | None = None
    conditions: list[SuspensionRuleConditionV2] | None = Field(
        default=None,
        description=(
            "Optional full replacement of condition rows for this org override. "
            "Evaluation precedence is AND before OR."
        ),
    )
    version: int | None = None

    @model_validator(mode="after")
    def validate_conditions(self) -> "OrgRuleOverrideUpsertRequest":
        if self.conditions is None:
            return self
        positions = [c.position for c in self.conditions]
        if sorted(positions) != list(range(1, len(self.conditions) + 1)):
            raise ValueError("conditions positions must be sequential starting from 1")
        if len({c.condition_type for c in self.conditions}) != len(self.conditions):
            raise ValueError("Each condition_type can only appear once per rule")
        for cond in self.conditions:
            if cond.position == 1 and cond.connector not in (None, SuspensionConnector.NONE):
                raise ValueError("First condition connector must be null/NONE")
            if cond.position > 1 and cond.connector not in (SuspensionConnector.AND, SuspensionConnector.OR):
                raise ValueError("Connector for subsequent conditions must be AND or OR")
        return self


class OrgCustomiseGlobalRuleRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=255)
    condition_summary: str | None = Field(default=None, max_length=255)
    status: SuspensionRuleStatus | None = None
    notes: str | None = None
    auto_suspension_enabled: bool | None = None
    pause_new_bookings: bool | None = None
    restrict_portal_login: bool | None = None
    notify_finance_team: bool | None = None
    notify_account_manager: bool | None = None
    conditions: list[SuspensionRuleConditionV2] | None = Field(
        default=None,
        description=(
            "Optional full condition set for customised rule. If omitted, conditions are cloned from global default."
        ),
    )

    @model_validator(mode="after")
    def validate_conditions(self) -> "OrgCustomiseGlobalRuleRequest":
        if self.conditions is None:
            return self
        positions = [c.position for c in self.conditions]
        if sorted(positions) != list(range(1, len(self.conditions) + 1)):
            raise ValueError("conditions positions must be sequential starting from 1")
        if len({c.condition_type for c in self.conditions}) != len(self.conditions):
            raise ValueError("Each condition_type can only appear once per rule")
        for cond in self.conditions:
            if cond.position == 1 and cond.connector not in (None, SuspensionConnector.NONE):
                raise ValueError("First condition connector must be null/NONE")
            if cond.position > 1 and cond.connector not in (SuspensionConnector.AND, SuspensionConnector.OR):
                raise ValueError("Connector for subsequent conditions must be AND or OR")
        return self


class OrgRuleStatusUpdateRequest(BaseSchema):
    status: SuspensionRuleStatus
    version: int | None = Field(default=None, description="Expected version for optimistic locking.")


class OrgRuleRestoreDefaultRequest(BaseSchema):
    version: int | None = Field(default=None, description="Expected version for optimistic locking.")


class OrgGlobalSuppressionPutRequest(BaseSchema):
    suppressed: bool = Field(..., description="When true, hide this GLOBAL rule from effective DEFAULT rows for the org.")


class OrgGlobalSuppressionListResponse(BaseSchema):
    global_rule_set_ids: list[str]


class SuspensionRuleSetResponse(BaseSchema):
    id: str
    name: str
    condition_summary: str | None
    scope_type: RuleScopeType
    scope_org_id: str | None
    rule_type: SuspensionRuleType
    status: SuspensionRuleStatus
    notes: str | None
    auto_suspension_enabled: bool
    pause_new_bookings: bool
    restrict_portal_login: bool
    notify_finance_team: bool
    notify_account_manager: bool
    conditions: list[SuspensionRuleConditionV2]
    created_at: datetime
    updated_at: datetime
    version: int
    is_override: bool = False
    source_scope_type: RuleScopeType | None = None
    source_rule_set_id: str | None = None
    global_rule_set_id: str | None = None
    is_default_rule: bool = False
    is_customised_rule: bool = False
    is_new_rule: bool = False
    is_effective_for_org: bool = False
    can_restore_default: bool = Field(
        default=False,
        description=(
            "True when this row is an org override that can revert to a global default "
            "for the same rule type (use restore-default on the customised org row). "
            "False for pure globals, for org-only rules with no global template, and when not applicable."
        ),
    )


class SuspensionRuleSetListResponse(BaseSchema):
    items: list[SuspensionRuleSetResponse]
    total: int


class SuspensionActivityV2Response(BaseSchema):
    id: str
    timestamp: datetime
    rule_set_id: str
    rule_id: str | None = Field(default=None, description="Deprecated legacy alias for rule_set_id.")
    rule_name: str
    rule_type: str | None = None
    payment_model: str | None = None
    organization_id: str | None = None
    account_id: str
    client_name: str | None = None
    client_email: str | None = None
    conditions_met: dict[str, Any]
    action_taken: SuspensionActionTaken
    notification_status: str | None = None
    notes: str | None = None


class SuspensionActivityV2ListResponse(BaseSchema):
    items: list[SuspensionActivityV2Response]
    total: int


class PaymentRiskEventCreateRequest(BaseSchema):
    organization_id: str
    customer_id: str | None = None
    order_id: str | None = None
    payment_model: Literal["CARD", "BANK_TRANSFER", "CREDIT_ACCOUNT", "CASH"]
    event_type: str = Field(..., description="PAYMENT_FAILED | RETRY_FAILED | PAYMENT_SUCCESS | CHARGEBACK")
    occurred_on: datetime | None = None
    metadata: dict[str, Any] | None = None
