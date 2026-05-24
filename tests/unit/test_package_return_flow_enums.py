"""Unit tests for return-leg package terminal / POD gating constants."""

from __future__ import annotations

from app.modules.orders.enums import (
    PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES,
    PACKAGE_DRIVER_PATCH_RETURN_STATUSES,
    PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES,
    PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES,
    PackageStatus,
)


def test_return_hub_complete_includes_sender_not_home_and_returned() -> None:
    assert PackageStatus.CUSTOMER_NOT_HOME in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES
    assert PackageStatus.RETURNED in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES
    assert PackageStatus.DISPOSED in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES
    assert PackageStatus.CANCELLED in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES


def test_only_returned_requires_stop_pod_on_return_flow() -> None:
    assert PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES == frozenset({PackageStatus.RETURNED})
    assert PackageStatus.DISPOSED not in PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES
    assert PackageStatus.CUSTOMER_NOT_HOME not in PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES


def test_return_in_transit_not_terminal_for_hub() -> None:
    assert PackageStatus.RETURN_IN_TRANSIT not in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES


def test_return_pod_gate_subset_of_return_complete() -> None:
    assert PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES <= PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES


def test_driver_patch_delivery_and_return_status_sets_are_disjoint_except_not_home() -> None:
    overlap = PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES & PACKAGE_DRIVER_PATCH_RETURN_STATUSES
    assert overlap == {PackageStatus.CUSTOMER_NOT_HOME}
    assert PackageStatus.RETURNED not in PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES
    assert PackageStatus.DELIVERED_TO_CUSTOMER not in PACKAGE_DRIVER_PATCH_RETURN_STATUSES
    assert PackageStatus.REFUSED_BY_CUSTOMER not in PACKAGE_DRIVER_PATCH_RETURN_STATUSES
    assert PackageStatus.LEFT_AT_SAFE_PLACE not in PACKAGE_DRIVER_PATCH_RETURN_STATUSES
