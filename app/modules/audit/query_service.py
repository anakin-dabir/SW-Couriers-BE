"""Read-side facade for audit log APIs (wraps AuditRepository)."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import get_db_session
from app.modules.audit.models import AuditLog, AuditSavedView
from app.modules.audit.repository import AuditRepository


class AuditQueryService:
    """Thin read layer used by audit v1 routes."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def get_summary_stats(self, organization_id: str) -> dict[str, Any]:
        return await self._repo.get_summary_stats(organization_id)

    async def get_organization_logs(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 50,
        category: list[str] | None = None,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        actor: str | None = None,
        browser: list[str] | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        sort_by: str = "desc",
        ui_category: list[str] | None = None,
    ) -> tuple[list[AuditLog], int]:
        return await self._repo.get_organization_logs(
            organization_id,
            page=page,
            size=size,
            category=category,
            event_type=event_type,
            severity=severity,
            actor=actor,
            browser=browser,
            search=search,
            from_date=from_date,
            to_date=to_date,
            sort_by=sort_by,
            ui_category=ui_category,
        )

    async def iter_logs_for_export(
        self,
        organization_id: str,
        *,
        category: list[str] | None = None,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        actor: str | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        chunk_size: int = 500,
    ) -> AsyncIterator[AuditLog]:
        async for row in self._repo.iter_logs_for_export(
            organization_id,
            category=category,
            event_type=event_type,
            severity=severity,
            actor=actor,
            search=search,
            from_date=from_date,
            to_date=to_date,
            chunk_size=chunk_size,
        ):
            yield row

    async def get_data_access_summary(self, organization_id: str) -> list[dict[str, Any]]:
        return await self._repo.get_data_access_summary(organization_id)

    async def get_data_access_heatmap(self, organization_id: str) -> list[dict[str, Any]]:
        return await self._repo.get_data_access_heatmap(organization_id)

    async def get_change_history(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        category: list[str] | None = None,
        entity_type: list[str] | None = None,
        action_type: list[str] | None = None,
        actor: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        return await self._repo.get_change_history(
            organization_id,
            page=page,
            size=size,
            search=search,
            from_date=from_date,
            to_date=to_date,
            category=category,
            entity_type=entity_type,
            action_type=action_type,
            actor=actor,
        )

    async def get_point_in_time_comparison(
        self,
        organization_id: str,
        snapshot_a: datetime,
        snapshot_b: datetime,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._repo.get_point_in_time_comparison(
            organization_id,
            snapshot_a,
            snapshot_b,
            fields,
        )

    async def get_field_history(
        self,
        organization_id: str,
        field_name: str,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        event_type: list[str] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.get_field_history(
            organization_id,
            field_name,
            page=page,
            size=size,
            search=search,
            event_type=event_type,
            from_date=from_date,
            to_date=to_date,
        )

    async def get_field_history_trend(
        self,
        organization_id: str,
        field_name: str,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return await self._repo.get_field_history_trend(
            organization_id,
            field_name,
            from_date=from_date,
            to_date=to_date,
        )

    async def get_audit_trend(self, organization_id: str, days: int = 30) -> list[dict[str, Any]]:
        return await self._repo.get_audit_trend(organization_id, days=days)

    async def get_log_by_id(self, organization_id: str, audit_log_id: str) -> AuditLog | None:
        return await self._repo.get_log_by_id(organization_id, audit_log_id)

    async def get_related_events(
        self,
        organization_id: str,
        correlation_id: str,
        exclude_id: str | None = None,
    ) -> list[AuditLog]:
        return await self._repo.get_related_events(organization_id, correlation_id, exclude_id=exclude_id)

    async def get_saved_views(self, user_id: str | None = None) -> list[AuditSavedView]:
        return await self._repo.get_saved_views(user_id=user_id)

    async def create_saved_view(self, user_id: str | None, data: dict) -> AuditSavedView:
        return await self._repo.create_saved_view(user_id, data)

    async def delete_saved_view(self, view_id: str, user_id: str | None = None) -> bool:
        return await self._repo.delete_saved_view(view_id, user_id=user_id)


def get_audit_query_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditQueryService:
    return AuditQueryService(session)


AuditQueryServiceDep = Annotated[AuditQueryService, Depends(get_audit_query_service)]
