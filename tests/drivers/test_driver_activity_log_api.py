"""API tests for GET /v1/drivers/{id}/activity-log and activity-log/{audit_log_id}."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.drivers.models import Driver
from app.modules.user.models import User

DRIVERS = "/v1/drivers"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


@pytest.mark.asyncio
async def test_driver_activity_log_lists_entity_driver_rows(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    log = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.update",
        category="Contact",
        event_type="CONTACT_UPDATED",
        severity="NOTICE",
        entity_type="driver",
        entity_id=driver.id,
        ip_address="10.0.0.1",
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] >= 1
    ids = {item["id"] for item in data["items"]}
    assert log.id in ids
    row = next(i for i in data["items"] if i["id"] == log.id)
    assert row["user_type"] == "Admin"
    assert row["ip_address"] == "10.0.0.1"
    assert "event" in row and row["timestamp"]


@pytest.mark.asyncio
async def test_driver_activity_log_includes_linked_user_actor_rows(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    log = AuditLog(
        user_id=driver_user_with_profile.id,
        user_role="DRIVER",
        action="auth.login_success",
        category="Access",
        event_type="LOGIN_SUCCESS",
        severity="INFO",
        entity_type="user",
        entity_id=driver_user_with_profile.id,
        ip_address="192.168.1.45",
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert any(i["id"] == log.id for i in data["items"])
    row = next(i for i in data["items"] if i["id"] == log.id)
    assert row["user_type"] == "Driver"
    assert driver_user_with_profile.email in (row.get("activity_performed_by") or "")


@pytest.mark.asyncio
async def test_driver_activity_log_json_driver_id(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()
    fake_shift = str(uuid.uuid4())

    log = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.shift.create",
        category="Fleet",
        event_type="SHIFT_CREATED",
        severity="NOTICE",
        entity_type="driver",
        entity_id=fake_shift,
        new_value={"driver_id": driver.id},
        ip_address="10.0.0.2",
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert any(i["id"] == log.id for i in data["items"])


@pytest.mark.asyncio
async def test_driver_activity_log_detail(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    log = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.shift.create",
        category="Fleet",
        event_type="SHIFT_CREATED",
        severity="NOTICE",
        entity_type="driver",
        entity_id=driver.id,
        new_value={"driver_id": driver.id, "status": "PLANNED"},
        ip_address="10.0.0.3",
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log/{log.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["id"] == log.id
    assert d["action"] == "driver.shift.create"
    assert d["event_type"] == "SHIFT_CREATED"
    assert d["new_value"] is not None
    assert d["new_value"].get("driver_id") == driver.id


@pytest.mark.asyncio
async def test_driver_activity_log_detail_404_wrong_id(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)

    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    other = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="org.other",
        category="System",
        event_type="SYSTEM_CONFIG_CHANGED",
        severity="INFO",
        entity_type="organization",
        entity_id=str(uuid.uuid4()),
        ip_address="10.0.0.4",
    )
    db_session.add(other)
    await db_session.flush()

    resp = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log/{other.id}",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_driver_activity_log_login_event_label(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    log = AuditLog(
        user_id=driver_user_with_profile.id,
        user_role="DRIVER",
        action="auth.login_success",
        category="Access",
        event_type="LOGIN_SUCCESS",
        severity="INFO",
        entity_type="user",
        entity_id=driver_user_with_profile.id,
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log", headers=headers)
    assert resp.status_code == 200
    row = next(i for i in resp.json()["data"]["items"] if i["id"] == log.id)
    assert row["event"] == "Login"


@pytest.mark.asyncio
async def test_driver_activity_log_pagination(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    for i in range(3):
        db_session.add(
            AuditLog(
                user_id=admin.id,
                user_role="ADMIN",
                action=f"driver.test.paginate.{i}",
                category="System",
                event_type="SYSTEM_CONFIG_CHANGED",
                severity="INFO",
                entity_type="driver",
                entity_id=driver.id,
            )
        )
    await db_session.flush()

    r1 = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log",
        headers=headers,
        params={"page": 1, "size": 2},
    )
    assert r1.status_code == 200
    d1 = r1.json()["data"]
    assert d1["total"] >= 3
    assert len(d1["items"]) == 2
    assert d1["page"] == 1
    assert d1["size"] == 2


@pytest.mark.asyncio
async def test_driver_activity_log_search_by_action(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    visible = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.unique.search.marker",
        category="Contact",
        event_type="CONTACT_UPDATED",
        severity="NOTICE",
        entity_type="driver",
        entity_id=driver.id,
    )
    db_session.add(visible)
    await db_session.flush()

    resp = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log",
        headers=headers,
        params={"search": "unique.search.marker"},
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["data"]["items"]}
    assert visible.id in ids


@pytest.mark.asyncio
async def test_driver_activity_log_from_date_filter(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    old = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)
    new = datetime(2030, 6, 15, 12, 0, 0, tzinfo=UTC)

    log_old = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.old.event",
        category="System",
        event_type="SYSTEM_CONFIG_CHANGED",
        severity="INFO",
        entity_type="driver",
        entity_id=driver.id,
        created_at=old,
    )
    log_new = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.new.event",
        category="System",
        event_type="SYSTEM_CONFIG_CHANGED",
        severity="INFO",
        entity_type="driver",
        entity_id=driver.id,
        created_at=new,
    )
    db_session.add_all([log_old, log_new])
    await db_session.flush()

    cutoff = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    resp = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log",
        headers=headers,
        params={"from_date": cutoff.isoformat()},
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["data"]["items"]}
    assert log_new.id in ids
    assert log_old.id not in ids


@pytest.mark.asyncio
async def test_driver_activity_log_sort_asc(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    t2 = t1 + timedelta(hours=1)
    first = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.sort.first",
        category="System",
        event_type="SYSTEM_CONFIG_CHANGED",
        severity="INFO",
        entity_type="driver",
        entity_id=driver.id,
        created_at=t1,
    )
    second = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.sort.second",
        category="System",
        event_type="SYSTEM_CONFIG_CHANGED",
        severity="INFO",
        entity_type="driver",
        entity_id=driver.id,
        created_at=t2,
    )
    db_session.add_all([first, second])
    await db_session.flush()

    resp = await client.get(
        f"{DRIVERS}/{driver.id}/activity-log",
        headers=headers,
        params={"sort": "asc", "search": "driver.sort."},
    )
    assert resp.status_code == 200
    data_items = resp.json()["data"]["items"]
    idx_first = next(i for i, row in enumerate(data_items) if row["id"] == first.id)
    idx_second = next(i for i, row in enumerate(data_items) if row["id"] == second.id)
    assert idx_first < idx_second


@pytest.mark.asyncio
async def test_driver_activity_log_detail_redacts_password_in_new_value(
    client: AsyncClient,
    user_factory,
    driver_user_with_profile: User,
    db_session,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    log = AuditLog(
        user_id=admin.id,
        user_role="ADMIN",
        action="driver.test.redact",
        category="Account",
        event_type="ACCOUNT_UPDATED",
        severity="NOTICE",
        entity_type="driver",
        entity_id=driver.id,
        new_value={"driver_id": driver.id, "new_password": "secret123"},
    )
    db_session.add(log)
    await db_session.flush()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log/{log.id}", headers=headers)
    assert resp.status_code == 200
    nv = resp.json()["data"]["new_value"]
    assert nv.get("new_password") == "[REDACTED]"
    assert nv.get("driver_id") == driver.id


@pytest.mark.asyncio
async def test_driver_activity_log_requires_auth(
    client: AsyncClient,
    driver_user_with_profile: User,
    db_session,
) -> None:
    res = await db_session.execute(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
    driver = res.scalar_one()

    resp = await client.get(f"{DRIVERS}/{driver.id}/activity-log")
    assert resp.status_code == 401
