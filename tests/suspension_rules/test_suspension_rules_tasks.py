"""Tests for Arq task wrapper around the suspension rules daily job."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.suspension_rules.tasks import run_daily_suspension_rules_task, send_email_task


@pytest.mark.asyncio
async def test_run_daily_suspension_rules_task_uses_service_and_parses_date(db_session) -> None:  # type: ignore[no-untyped-def]
    # We patch SuspensionRulesService inside the tasks module so the constructor is not executed.
    with patch("app.modules.suspension_rules.tasks.SuspensionRulesService") as mock_service:
        instance = mock_service.return_value
        instance.run_daily_suspension_job = AsyncMock()

        # today as ISO string should be parsed and passed through.
        today_str = "2026-03-15"
        await run_daily_suspension_rules_task({}, today=today_str)

        instance.run_daily_suspension_job.assert_awaited_once()
        kwargs = instance.run_daily_suspension_job.call_args.kwargs
        assert kwargs["today"] == date.fromisoformat(today_str)
        assert kwargs["commit"] is True


@pytest.mark.asyncio
async def test_send_email_task_calls_mailer() -> None:
    with patch("app.modules.suspension_rules.tasks.send_email", new_callable=AsyncMock) as mock_send:
        await send_email_task(
            {},
            "test@example.com",
            "Hello",
            template_name="suspension_warning_b2b.html",
            context={"name": "ACME"},
        )
        mock_send.assert_awaited_once()
