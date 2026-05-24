"""Tests for status automation Arq task wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.modules.status_automation_rules.tasks import (
    evaluate_status_automation_rules_task,
    run_daily_status_automation_reconciliation_task,
)


@pytest.mark.asyncio
async def test_evaluate_status_automation_rules_task_delegates_to_service() -> None:
    payload = {
        "event_id": "evt-1",
        "organization_id": "org-1",
        "entity_type": "PACKAGE",
        "entity_id": "pkg-1",
        "from_status": "OUT_FOR_DELIVERY",
        "to_status": "DAMAGED",
    }
    with patch("app.modules.status_automation_rules.tasks.StatusAutomationRulesService") as mock_service:
        service = mock_service.return_value
        service.evaluate_for_event = AsyncMock()
        await evaluate_status_automation_rules_task({}, payload)
        service.evaluate_for_event.assert_awaited_once_with(payload, commit=True)


@pytest.mark.asyncio
async def test_daily_reconciliation_task_delegates_to_service_with_date() -> None:
    with patch("app.modules.status_automation_rules.tasks.StatusAutomationRulesService") as mock_service:
        service = mock_service.return_value
        service.run_daily_reconciliation = AsyncMock()
        await run_daily_status_automation_reconciliation_task({}, today="2026-05-08")
        service.run_daily_reconciliation.assert_awaited_once()
        kwargs = service.run_daily_reconciliation.await_args.kwargs
        assert kwargs["run_date"].isoformat() == "2026-05-08"
        assert kwargs["commit"] is True

