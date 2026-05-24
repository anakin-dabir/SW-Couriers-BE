"""Schemas for per-organisation credit & suspension configuration."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.org_credit_suspension.enums import SuspensionConditionType

# ── Trigger condition item ─────────────────────────────────────────────────────


class SuspensionTriggerItem(BaseModel):
    """A single suspension trigger condition row.

    position=1 is the leading IF condition and must have logic_operator=None.
    All subsequent positions must have logic_operator set to 'AND' or 'OR'.
    """

    position: int = Field(..., ge=1, description="1-based position; position=1 is always the IF condition")
    logic_operator: str | None = Field(
        None,
        description="Null for position=1 (IF). 'AND' or 'OR' for subsequent conditions.",
    )
    condition_type: SuspensionConditionType
    condition_value: Decimal = Field(..., ge=0, decimal_places=2, description="Threshold value for the condition")

    @field_validator("logic_operator")
    @classmethod
    def validate_logic_operator(cls, v: str | None) -> str | None:
        if v is not None and v not in ("AND", "OR"):
            raise ValueError("logic_operator must be 'AND', 'OR', or null")
        return v


def _validate_trigger_list(triggers: list[SuspensionTriggerItem]) -> list[SuspensionTriggerItem]:
    """Shared trigger list validation used by both Input and Upsert schemas."""
    if not triggers:
        return triggers

    positions = [t.position for t in triggers]

    if len(positions) != len(set(positions)):
        raise ValueError("trigger_conditions positions must be unique.")

    if sorted(positions) != list(range(1, len(positions) + 1)):
        raise ValueError("trigger_conditions positions must be sequential starting from 1.")

    first = next(t for t in triggers if t.position == 1)
    if first.logic_operator is not None:
        raise ValueError("The condition at position=1 must have logic_operator=null (it is the leading IF condition).")

    for t in triggers:
        if t.position > 1 and t.logic_operator is None:
            raise ValueError(f"Condition at position={t.position} must have logic_operator set to 'AND' or 'OR'.")

    return triggers


def _serialize_triggers(triggers: list[SuspensionTriggerItem]) -> list[dict]:
    """Serialize trigger items to JSONB-safe dicts (Decimal → str for precision)."""
    return [
        {
            "position": t.position,
            "logic_operator": t.logic_operator,
            "condition_type": t.condition_type.value,
            "condition_value": str(t.condition_value),
        }
        for t in triggers
    ]


# ── Credit config schemas ──────────────────────────────────────────────────────


class OrgCreditConfigInput(BaseModel):
    """Credit config fields used inline during org creation (no reason required)."""

    approved_credit_limit: Decimal | None = Field(None, gt=0, decimal_places=2)
    credit_clearance_period_days: int | None = Field(None, ge=0, description="Grace period in days before suspension")
    credit_utilization_warning_pct: int | None = Field(None, ge=0, le=100)
    allow_bookings_beyond_limit: bool = False


class OrgCreditConfigUpsert(OrgCreditConfigInput):
    """Standalone PUT endpoint payload — reason required for audit trail."""

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for the update (audit trail)")


class OrgCreditConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    approved_credit_limit: Decimal | None
    credit_clearance_period_days: int | None
    credit_utilization_warning_pct: int | None
    allow_bookings_beyond_limit: bool
    created_at: datetime
    updated_at: datetime
    version: int


# ── Suspension config schemas ──────────────────────────────────────────────────


class OrgSuspensionConfigInput(BaseModel):
    """Suspension config fields used inline during org creation (no reason required)."""

    trigger_conditions: list[SuspensionTriggerItem] = Field(
        default_factory=list,
        description=(
            "Ordered list of suspension trigger conditions. "
            "Position 1 is the leading IF condition (logic_operator must be null). "
            "Subsequent conditions use AND/OR. Empty list clears all triggers."
        ),
    )
    auto_suspension_enabled: bool = False
    pause_new_bookings: bool = False
    restrict_portal_login: bool = False
    notify_finance_team: bool = False
    notify_account_manager: bool = False

    @model_validator(mode="after")
    def validate_triggers(self) -> "OrgSuspensionConfigInput":
        _validate_trigger_list(self.trigger_conditions)
        return self


class OrgSuspensionConfigUpsert(OrgSuspensionConfigInput):
    """Standalone PUT endpoint payload — reason required for audit trail."""

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for the update (audit trail)")


class OrgSuspensionConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    trigger_conditions: list[SuspensionTriggerItem]
    auto_suspension_enabled: bool
    pause_new_bookings: bool
    restrict_portal_login: bool
    notify_finance_team: bool
    notify_account_manager: bool
    created_at: datetime
    updated_at: datetime
    version: int

    @model_validator(mode="before")
    @classmethod
    def coerce_trigger_conditions(cls, data: object) -> object:
        """Coerce None JSONB value to empty list when reading from ORM."""
        if hasattr(data, "__dict__"):
            raw = getattr(data, "trigger_conditions", None)
            if raw is None:
                object.__setattr__(data, "trigger_conditions", [])
        elif isinstance(data, dict) and data.get("trigger_conditions") is None:
            data["trigger_conditions"] = []
        return data


# ── Combined full response ─────────────────────────────────────────────────────


class OrgCreditSuspensionFullResponse(BaseModel):
    """Full credit & suspension config for an organisation (GET endpoint)."""

    credit_config: OrgCreditConfigResponse | None = None
    suspension_config: OrgSuspensionConfigResponse | None = None
