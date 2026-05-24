"""Tests for permission schema validation — catches bad input before it hits the DB."""

import pytest
from pydantic import ValidationError

from app.modules.permission.v1.schemas import (
    BulkSetPermissionsRequest,
    SetPermissionRequest,
)


class TestSetPermissionRequestValidation:
    """Validate that invalid resource/level values are rejected at schema level."""

    def test_valid_resource_and_level(self) -> None:
        req = SetPermissionRequest(resource="SHIPMENTS", level="READ")
        assert req.resource == "SHIPMENTS"
        assert req.level == "READ"

    def test_invalid_resource_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SetPermissionRequest(resource="NONEXISTENT", level="READ")
        assert "Invalid resource" in str(exc_info.value)

    def test_invalid_level_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SetPermissionRequest(resource="SHIPMENTS", level="SUPERADMIN")
        assert "Invalid level" in str(exc_info.value)

    def test_empty_resource_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SetPermissionRequest(resource="", level="READ")

    def test_lowercase_resource_rejected(self) -> None:
        """Resource values are case-sensitive — must be uppercase."""
        with pytest.raises(ValidationError):
            SetPermissionRequest(resource="shipments", level="READ")

    def test_lowercase_level_rejected(self) -> None:
        """Level values are case-sensitive — must be uppercase."""
        with pytest.raises(ValidationError):
            SetPermissionRequest(resource="SHIPMENTS", level="read")

    def test_all_valid_levels_accepted(self) -> None:
        for level in ("NONE", "READ", "WRITE"):
            req = SetPermissionRequest(resource="DASHBOARD", level=level)
            assert req.level == level

    def test_all_valid_resources_accepted(self) -> None:
        from app.common.enums.permission import Resource

        for resource in Resource:
            req = SetPermissionRequest(resource=resource.value, level="READ")
            assert req.resource == resource.value


class TestBulkSetPermissionsRequestValidation:
    """Validate bulk request constraints."""

    def test_empty_permissions_list_rejected(self) -> None:
        """At least one permission must be provided."""
        with pytest.raises(ValidationError):
            BulkSetPermissionsRequest(permissions=[])

    def test_valid_bulk_request(self) -> None:
        req = BulkSetPermissionsRequest(
            permissions=[
                SetPermissionRequest(resource="SHIPMENTS", level="READ"),
                SetPermissionRequest(resource="DASHBOARD", level="WRITE"),
            ]
        )
        assert len(req.permissions) == 2

    def test_invalid_entry_in_bulk_rejected(self) -> None:
        """One bad entry should reject the entire request."""
        with pytest.raises(ValidationError):
            BulkSetPermissionsRequest(
                permissions=[
                    SetPermissionRequest(resource="SHIPMENTS", level="READ"),
                    SetPermissionRequest(resource="FAKE", level="READ"),
                ]
            )
