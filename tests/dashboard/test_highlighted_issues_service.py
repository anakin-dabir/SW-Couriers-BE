"""Unit tests for highlighted issues detection logic."""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.dashboard.highlighted_issues_service import _deadline_urgency, _detect_issue, _format_remediation
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus


def test_detect_issue_no_driver_assigned() -> None:
    code, label = _detect_issue(
        stop_status=DeliveryStopStatus.OUT_FOR_DELIVERY.value,
        order_status=OrderStatus.DELIVERY_IN_PROGRESS.value,
        route_driver_id=None,
        scheduled_for=date.today(),
        today=date.today(),
        has_customer_not_home=False,
    )
    assert code == "NO_DRIVER_ASSIGNED"
    assert label == "No driver assigned"


def test_detect_issue_customer_not_available() -> None:
    code, label = _detect_issue(
        stop_status=DeliveryStopStatus.OUT_FOR_DELIVERY.value,
        order_status=OrderStatus.DELIVERY_IN_PROGRESS.value,
        route_driver_id="driver-1",
        scheduled_for=date.today(),
        today=date.today(),
        has_customer_not_home=True,
    )
    assert code == "CUSTOMER_NOT_AVAILABLE"


def test_detect_issue_delivery_attempt_failed() -> None:
    code, _ = _detect_issue(
        stop_status=DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value,
        order_status=OrderStatus.DELIVERY_IN_PROGRESS.value,
        route_driver_id="driver-1",
        scheduled_for=date.today(),
        today=date.today(),
        has_customer_not_home=False,
    )
    assert code == "DELIVERY_ATTEMPT_FAILED"


def test_format_remediation_no_action() -> None:
    text, applied = _format_remediation(
        automation_message=None,
        latest_event_to="OUT_FOR_DELIVERY",
        scheduled_for=None,
        today=date.today(),
    )
    assert text == "No Action"
    assert applied is False


def test_format_remediation_from_automation_log() -> None:
    text, applied = _format_remediation(
        automation_message="Driver reassigned",
        latest_event_to=None,
        scheduled_for=None,
        today=date.today(),
    )
    assert text == "Driver reassigned"
    assert applied is True


def test_deadline_urgency_critical_when_due_today_or_past() -> None:
    today = date.today()
    assert _deadline_urgency(today, today=today) == "critical"
    assert _deadline_urgency(today - timedelta(days=1), today=today) == "critical"


def test_deadline_urgency_warning_within_two_days() -> None:
    today = date.today()
    assert _deadline_urgency(today + timedelta(days=2), today=today) == "warning"
