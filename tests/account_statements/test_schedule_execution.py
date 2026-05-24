"""Service-level tests for account statement schedule execution."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.account_statements.enums import (
    StatementScheduleFrequency,
    StatementScheduleStatus,
)
from app.modules.account_statements.models import AccountStatementSchedule
from app.modules.account_statements.scheduling import initial_next_run_at_utc, resolve_timezone
from app.modules.account_statements.service import AccountStatementService
from tests.account_statements.test_account_statements_api import _create_org


@pytest.mark.asyncio
async def test_process_due_custom_once_schedule_generates_statement(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _create_org(db_session)
    tz = resolve_timezone("Europe/London")
    valid_from = date(2026, 1, 1)
    valid_to = date(2026, 1, 31)
    now = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
    next_run = initial_next_run_at_utc(
        frequency=StatementScheduleFrequency.CUSTOM,
        tz=tz,
        valid_from=valid_from,
        valid_to=valid_to,
        interval_storage="once",
        now_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert next_run is not None

    schedule = AccountStatementSchedule(
        organization_id=org.id,
        frequency=StatementScheduleFrequency.CUSTOM.value,
        valid_from=valid_from,
        valid_to=valid_to,
        recipient_email="billing@example.com",
        timezone="Europe/London",
        custom_cron="once",
        include_line_item_detail=False,
        include_credit_notes=True,
        include_payment_history=True,
        status=StatementScheduleStatus.ACTIVE.value,
        next_run_at=next_run,
    )
    db_session.add(schedule)
    await db_session.flush()

    enqueued: list[dict] = []

    async def _capture_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        enqueued.append(kwargs)
        return type("Job", (), {"job_id": "job-sched-exec"})()

    monkeypatch.setattr("app.modules.account_statements.service.enqueue", _capture_enqueue)

    service = AccountStatementService(db_session)
    processed = await service.process_due_schedules(now_utc=now)
    assert processed == 1

    await db_session.refresh(schedule)
    assert schedule.status == StatementScheduleStatus.COMPLETED.value
    assert schedule.next_run_at is None
    assert schedule.last_run_at is not None
    deliver_jobs = [job for job in enqueued if job.get("recipient_email")]
    assert len(deliver_jobs) == 1
    assert deliver_jobs[0]["recipient_email"] == "billing@example.com"


@pytest.mark.asyncio
async def test_process_due_overdue_custom_once_still_runs(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _create_org(db_session)
    valid_from = date(2020, 1, 1)
    valid_to = date(2020, 1, 31)
    schedule = AccountStatementSchedule(
        organization_id=org.id,
        frequency=StatementScheduleFrequency.CUSTOM.value,
        valid_from=valid_from,
        valid_to=valid_to,
        recipient_email="late@example.com",
        timezone="Europe/London",
        custom_cron="once",
        include_line_item_detail=False,
        include_credit_notes=True,
        include_payment_history=True,
        status=StatementScheduleStatus.ACTIVE.value,
        next_run_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(schedule)
    await db_session.flush()

    async def _fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return type("Job", (), {"job_id": "job-overdue"})()

    monkeypatch.setattr("app.modules.account_statements.service.enqueue", _fake_enqueue)

    service = AccountStatementService(db_session)
    processed = await service.process_due_schedules(now_utc=datetime.now(UTC))
    assert processed == 1

    await db_session.refresh(schedule)
    assert schedule.status == StatementScheduleStatus.COMPLETED.value
