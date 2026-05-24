"""Schema-level validation tests for status automation rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.modules.status_automation_rules.v1.schemas import StatusAutomationRuleSetCreateRequest


def _base_payload() -> dict:
    return {
        "name": "rule-1",
        "scope_type": "GLOBAL",
        "scope_org_id": None,
        "status": "ACTIVE",
        "priority": 100,
        "notes": None,
        "trigger": {
            "entity_type": "PACKAGE",
            "status": "DAMAGED",
        },
        "conditions": [],
        "actions": [
            {
                "new_status": "RETURN_INITIATED",
            }
        ],
    }


def test_schema_rejects_multiple_actions() -> None:
    payload = _base_payload()
    payload["actions"] = [
        {"new_status": "RETURN_INITIATED"},
        {"new_status": "RETURNED"},
    ]
    with pytest.raises(PydanticValidationError):
        StatusAutomationRuleSetCreateRequest(**payload)


def test_schema_rejects_invalid_trigger_status_for_entity() -> None:
    payload = _base_payload()
    payload["trigger"]["status"] = "DELIVERED"
    with pytest.raises(PydanticValidationError):
        StatusAutomationRuleSetCreateRequest(**payload)


def test_schema_rejects_timing_for_non_cancelled_status() -> None:
    payload = _base_payload()
    payload["conditions"] = [{"value": "AFTER_PICKUP"}]
    with pytest.raises(PydanticValidationError):
        StatusAutomationRuleSetCreateRequest(**payload)


def test_schema_requires_after_pickup_timing_for_cancelled_status() -> None:
    payload = _base_payload()
    payload["trigger"]["status"] = "CANCELLED"
    with pytest.raises(PydanticValidationError):
        StatusAutomationRuleSetCreateRequest(**payload)


def test_schema_allows_cancelled_with_after_pickup() -> None:
    payload = _base_payload()
    payload["trigger"]["entity_type"] = "DELIVERY_STOP"
    payload["trigger"]["status"] = "CANCELLED"
    payload["conditions"] = [{"value": "AFTER_PICKUP"}]
    payload["actions"] = [{"new_status": "RETURN_INITIATED"}]
    StatusAutomationRuleSetCreateRequest(**payload)


def test_schema_rejects_new_status_for_other_entity() -> None:
    payload = _base_payload()
    payload["actions"] = [{"new_status": "FAILED"}]
    with pytest.raises(PydanticValidationError):
        StatusAutomationRuleSetCreateRequest(**payload)

