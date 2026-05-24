"""Integration API tests for Audit Log endpoints."""

import pytest
from httpx import AsyncClient

from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.models import AuditLog, AuditSavedView


@pytest.mark.asyncio
class TestAuditApi:
    """Integration tests for audit log management and dashboard APIs."""

    async def test_get_audit_summary_success(
        self, client: AsyncClient, admin_headers: dict, sample_org, audit_log_factory
    ) -> None:
        """Admin can get a summary of audit activity for an organization."""
        # Seed some logs to count
        await audit_log_factory(sample_org.id, action="something.happened", severity="CRITICAL")
        await audit_log_factory(sample_org.id, action="another.thing", severity="WARNING")

        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs/summary", 
            headers=admin_headers
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_events_24h" in data
        assert "critical_events_7d" in data
        assert "warning_events_7d" in data

    async def test_get_audit_logs_paginated(
        self, client: AsyncClient, admin_headers: dict, sample_org, audit_log_factory
    ) -> None:
        """Admin can list and filter paginated audit logs."""
        await audit_log_factory(sample_org.id, action="access.granted", category="Access")
        await audit_log_factory(sample_org.id, action="system.update", category="System")

        # Get all
        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs", 
            headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 2
        assert len(data["items"]) >= 2

        # Filter by category
        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs",
            params={"category": ["Access"]},
            headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(item["category"] == "Access" for item in data["items"])

    async def test_get_data_access_logs(
        self, client: AsyncClient, admin_headers: dict, sample_org, audit_log_factory
    ) -> None:
        """Admin can specifically view logs related to data access / privacy."""
        await audit_log_factory(sample_org.id, action="profile.view", category="Access")
        await audit_log_factory(sample_org.id, action="not.access", category="Account")

        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs/data-access",
            headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # The endpoint should only return category="Access"
        assert all(item["category"] == "Access" for item in data["items"])

    async def test_get_audit_trend_chart(
        self, client: AsyncClient, admin_headers: dict, sample_org, audit_log_factory
    ) -> None:
        """Trend endpoint returns activity point series for charts."""
        await audit_log_factory(sample_org.id, severity="INFO")

        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs/trend",
            headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "points" in data
        assert len(data["points"]) > 0

    async def test_b2b_can_read_audit_for_own_org(
        self, client: AsyncClient, b2b_headers: dict, sample_org, audit_log_factory
    ) -> None:
        """B2B users can access audit logs for their own organization."""
        await audit_log_factory(sample_org.id, action="security.login", category="Security")

        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs",
            headers=b2b_headers,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1

    async def test_b2b_cannot_read_other_org_audit_logs(
        self, client: AsyncClient, b2b_headers: dict, org_factory
    ) -> None:
        """B2B users are denied when path organization is not their JWT organization."""
        other_org = await org_factory(reference="AUDIT-ORG-OTHER")

        resp = await client.get(
            f"/v1/organizations/{other_org.id}/audit-logs",
            headers=b2b_headers,
        )

        assert resp.status_code == 403

    async def test_super_admin_can_read_org_audit(
        self, client: AsyncClient, super_admin_headers: dict, sample_org
    ) -> None:
        """SUPER_ADMIN has audit access parity with ADMIN."""
        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs/summary",
            headers=super_admin_headers,
        )
        assert resp.status_code == 200

    async def test_super_admin_actor_label_is_admin(
        self,
        client: AsyncClient,
        super_admin_headers: dict,
        sample_org,
        audit_log_factory,
        db_session,
    ) -> None:
        """SUPER_ADMIN rows display as Admin in audit list responses."""
        log = await audit_log_factory(sample_org.id, user_role="SUPER_ADMIN", action="org.viewed")
        await db_session.refresh(log)

        resp = await client.get(
            f"/v1/organizations/{sample_org.id}/audit-logs",
            headers=super_admin_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        match = next((i for i in items if i["id"] == log.id), None)
        assert match is not None
        assert match["actor"] == "Admin"

    async def test_global_saved_views_lifecycle(
        self, client: AsyncClient, admin_headers: dict, saved_view_factory
    ) -> None:
        """Users can create, list and delete their global audit dashboard views."""
        # 1. Create a view
        payload = {
            "name": "My Custom Filters",
            "filters": {"severity": ["CRITICAL"], "category": ["Security"]},
            "is_default": False
        }
        create_resp = await client.post(
            "/v1/organizations/audit-logs/saved-views", 
            json=payload, 
            headers=admin_headers
        )
        assert create_resp.status_code == 201
        view_id = create_resp.json()["data"]["id"]

        # 2. List views
        list_resp = await client.get(
            "/v1/organizations/audit-logs/saved-views", 
            headers=admin_headers
        )
        assert list_resp.status_code == 200
        views = list_resp.json()["data"]
        assert any(v["id"] == view_id for v in views)
        assert any(v["name"] == "My Custom Filters" for v in views)

        # 3. Delete view
        del_resp = await client.delete(
            f"/v1/organizations/audit-logs/saved-views/{view_id}", 
            headers=admin_headers
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # 4. Verify deletion
        list_resp_after = await client.get(
            "/v1/organizations/audit-logs/saved-views", 
            headers=admin_headers
        )
        assert not any(v["id"] == view_id for v in list_resp_after.json()["data"])

    async def test_b2b_saved_views_lifecycle(
        self, client: AsyncClient, b2b_headers: dict
    ) -> None:
        """B2B users can create, list and delete their own global saved views."""
        payload = {
            "name": "B2B Filters",
            "filters": {"severity": ["WARNING"], "category": ["Access"]},
            "is_default": False,
        }
        create_resp = await client.post(
            "/v1/organizations/audit-logs/saved-views",
            json=payload,
            headers=b2b_headers,
        )
        assert create_resp.status_code == 201
        view_id = create_resp.json()["data"]["id"]

        list_resp = await client.get(
            "/v1/organizations/audit-logs/saved-views",
            headers=b2b_headers,
        )
        assert list_resp.status_code == 200
        views = list_resp.json()["data"]
        assert any(v["id"] == view_id for v in views)
        assert any(v["name"] == "B2B Filters" for v in views)

        del_resp = await client.delete(
            f"/v1/organizations/audit-logs/saved-views/{view_id}",
            headers=b2b_headers,
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True
