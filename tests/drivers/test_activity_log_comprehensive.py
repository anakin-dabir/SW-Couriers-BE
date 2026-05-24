"""Comprehensive tests for driver activity log functionality.

Tests verify activity log entries contain all required fields and are created correctly.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.models import DocAccessToken
from app.modules.user.models import User
from app.core.security import create_access_token, hash_token


@pytest_asyncio.fixture
async def admin_user(user_factory) -> User:
    """Create an admin user for testing."""
    return await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)


@pytest_asyncio.fixture
async def admin_headers(admin_user: User, db_session: AsyncSession) -> dict[str, str]:
    """Create admin headers with JWT and doc access token."""
    token, _ = create_access_token(
        user_id=admin_user.id,
        role=admin_user.role,
        client_type="ADMIN",
        region_id=None,
        organization_id=None,
    )
    raw = secrets.token_hex(32)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    row = DocAccessToken(
        user_id=admin_user.id,
        token_hash=hash_token(raw),
        expires_at=expires_at,
        access_scope=DocAccessScope.DRIVER_DOCUMENTS.value,
    )
    db_session.add(row)
    await db_session.flush()
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
        "X-Driver-Doc-Access-Token": raw,
    }


@pytest_asyncio.fixture
async def sample_driver(user_factory, db_session: AsyncSession) -> Driver:
    """Create a complete driver profile for testing (with linked user)."""
    user = await user_factory(status="ACTIVE", email_verified=True, role="DRIVER")
    driver = Driver(
        user_id=user.id,
        driver_code=f"DR-{user.id[:6].upper()}",
        account_status=DriverAccountStatus.ACTIVE,
    )
    db_session.add(driver)
    await db_session.flush()
    await db_session.refresh(driver)
    return driver


class TestActivityLogFieldCoverage:
    """Verify activity log entries contain all required fields."""

    @pytest.mark.asyncio
    async def test_audit_entry_has_all_required_fields(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify audit log entries have all required fields."""
        now = datetime.now(UTC)
        audit_entry = AuditLog(
            action="driver.profile.update",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_STATUS_CHANGED.value,
            severity="NOTICE",
            entity_type="Driver",
            entity_id=sample_driver.id,
            entity_ref=sample_driver.driver_code,
            user_id=admin_user.id,
            user_role=admin_user.role,
            organization_id=None,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            browser="Chrome",
            device="Desktop",
            os="Windows 11",
            old_value={"account_status": "ACTIVE"},
            new_value={"account_status": "SUSPENDED"},
            reason="Safety concern",
            audit_ref=f"AUD-{now.strftime('%Y')}-TEST001",
            created_at=now,
        )
        db_session.add(audit_entry)
        await db_session.commit()

        # Verify all required fields are present and have correct values
        required_fields = [
            "id", "action", "category", "event_type", "severity",
            "entity_type", "entity_id", "entity_ref", "reason",
            "user_id", "user_role", "ip_address", "user_agent",
            "browser", "device", "os", "old_value", "new_value",
            "audit_ref", "created_at", "organization_id",
        ]
        for field in required_fields:
            assert hasattr(audit_entry, field), f"Missing field: {field}"

        assert audit_entry.action == "driver.profile.update"
        assert audit_entry.category == AuditCategory.FLEET
        assert audit_entry.ip_address == "192.168.1.100"
        assert audit_entry.browser == "Chrome"
        assert audit_entry.device == "Desktop"
        assert audit_entry.os == "Windows 11"
        assert audit_entry.severity == "NOTICE"
        assert audit_entry.entity_id == sample_driver.id
        assert audit_entry.entity_ref == sample_driver.driver_code
        assert audit_entry.reason == "Safety concern"

    @pytest.mark.asyncio
    async def test_audit_entry_stores_old_and_new_values(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify before/after values are stored in audit log."""
        now = datetime.now(UTC)
        audit_entry = AuditLog(
            action="driver.password.change",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ACCOUNT_CONFIG_UPDATED.value,
            severity="NOTICE",
            entity_type="Driver",
            entity_id=sample_driver.id,
            user_id=admin_user.id,
            user_role=admin_user.role,
            ip_address="10.0.0.1",
            old_value={"password_hash": "old_hash"},
            new_value={"password_hash": "new_hash"},
            created_at=now,
        )
        db_session.add(audit_entry)
        await db_session.commit()

        # Verify old/new values are preserved
        assert audit_entry.old_value is not None
        assert audit_entry.new_value is not None
        assert audit_entry.old_value["password_hash"] == "old_hash"
        assert audit_entry.new_value["password_hash"] == "new_hash"

    @pytest.mark.asyncio
    async def test_audit_entries_with_different_event_types(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify different event types are stored correctly."""
        now = datetime.now(UTC)
        event_configs = [
            (AuditEventType.ACCOUNT_CREATED, AuditCategory.ACCOUNT),
            (AuditEventType.ACCOUNT_CONFIG_UPDATED, AuditCategory.ACCOUNT),
            (AuditEventType.ACCOUNT_DEACTIVATED, AuditCategory.SECURITY),
        ]
        
        created_entries = []
        for event_type, category in event_configs:
            audit = AuditLog(
                action=f"driver.{event_type.value.lower()}",
                category=category,
                event_type=event_type.value,
                severity="NOTICE",
                entity_type="Driver",
                entity_id=sample_driver.id,
                user_id=admin_user.id,
                user_role=admin_user.role,
                ip_address="192.168.1.100",
                created_at=now + timedelta(minutes=len(created_entries)),
            )
            db_session.add(audit)
            created_entries.append(audit)

        await db_session.commit()

        # Verify entries were created with correct event types
        assert len(created_entries) == 3
        for entry in created_entries:
            assert entry.id is not None
            assert entry.event_type in [
                AuditEventType.ACCOUNT_CREATED.value,
                AuditEventType.ACCOUNT_CONFIG_UPDATED.value,
                AuditEventType.ACCOUNT_DEACTIVATED.value,
            ]


class TestActivityLogModelValidation:
    """Test audit log model validation and data integrity."""

    @pytest.mark.asyncio
    async def test_audit_entry_with_minimal_fields(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify audit log can be created with minimal required fields."""
        now = datetime.now(UTC)
        audit_entry = AuditLog(
            action="test.action",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED.value,
            severity="INFO",
            entity_type="Driver",
            entity_id=sample_driver.id,
            created_at=now,
        )
        db_session.add(audit_entry)
        await db_session.commit()

        assert audit_entry.id is not None
        assert audit_entry.action == "test.action"
        assert audit_entry.user_id is None
        assert audit_entry.organization_id is None

    @pytest.mark.asyncio
    async def test_audit_entry_with_null_optional_fields(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify audit log supports null values for optional fields."""
        now = datetime.now(UTC)
        audit_entry = AuditLog(
            action="test.action",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED.value,
            severity="INFO",
            entity_type="Driver",
            entity_id=sample_driver.id,
            user_id=admin_user.id,
            user_role=admin_user.role,
            ip_address=None,
            user_agent=None,
            browser=None,
            device=None,
            os=None,
            old_value=None,
            new_value=None,
            created_at=now,
        )
        db_session.add(audit_entry)
        await db_session.commit()

        assert audit_entry.ip_address is None
        assert audit_entry.browser is None
        assert audit_entry.old_value is None

    @pytest.mark.asyncio
    async def test_audit_entry_timestamps_are_preserved(
        self,
        admin_user: User,
        sample_driver: Driver,
        db_session: AsyncSession,
    ):
        """Verify audit log timestamps are set correctly."""
        specific_time = datetime(2026, 4, 1, 12, 30, 45, tzinfo=UTC)
        audit_entry = AuditLog(
            action="test.action",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED.value,
            severity="INFO",
            entity_type="Driver",
            entity_id=sample_driver.id,
            created_at=specific_time,
        )
        db_session.add(audit_entry)
        await db_session.commit()

        assert audit_entry.created_at == specific_time
